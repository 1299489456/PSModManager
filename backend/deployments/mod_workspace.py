"""Mod-sized browsing and one batch save for the ordinary loading list.

Browsing reads names/stat metadata only. A changed external Mod is copied to the
library before its first disable. Ordinary saves retain ownership and a batch
recovery record without per-file durable transactions. Unchanged Mods remain
external; loose configuration files are never guessed into a package.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Annotated
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.app.paths import reject_reparse
from backend.contracts import BaselineSnapshot, FileEntry, ModPackage, ModRelease
from backend.contracts.plans import ModWorkspaceSave
from backend.storage.package_repository import PackageRepository, content_hash
from backend.catalog.versions import library_attachment
from backend.catalog.file_policy import ordinary_file
from .paths import ROOTS, EXECUTABLES, canonical_relative, pinned_chain
from .planner import WORKSPACE_TTL
from .policy import PlanRejected, owned_after, target_key


WorkspaceSave = ModWorkspaceSave
MAX_FILES = 4000
MAX_ENTRIES = 20000
SAVE_SECONDS = WORKSPACE_TTL.total_seconds()
ORDER_MESSAGE = '列表顺序用于整理显示；实际加载顺序由 MelonLoader 和 Mod 依赖决定。'
MESSAGES = {
    'MOD_WORKSPACE_STALE': 'Mod 文件或加载状态已变化，请刷新列表后重新保存。',
    'MOD_WORKSPACE_NOT_FOUND': '这个 Mod 已不在当前工作区，请刷新列表。',
    'MOD_WORKSPACE_INVALID': '加载列表包含重复或未知的 Mod，请刷新后重试。',
    'MOD_WORKSPACE_LIMIT': 'Mod 工作区超出本次管理容量，请缩小所选安装范围。',
    'MOD_WORKSPACE_UNSAFE': 'Mod 包含不支持的链接或文件，已保留原文件。',
    'MOD_WORKSPACE_OVERLAP': '这个 Mod 的部分文件已属于其他已管理 Mod，无法整体停用。',
    'MOD_WORKSPACE_BACKING_UP': '正在保存 Mod 文件副本，以便停用后完整恢复。',
}


class WorkspaceQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID


class WorkspaceDetail(WorkspaceQuery):
    id: UUID
    offset: Annotated[StrictInt, Field(ge=0)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=200)] = 200


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _display_path(file):
    prefix = ROOTS.get(file['target_root_kind'], 'UserData' if file['target_root_kind'] == 'user_data' else '')
    return '/'.join(value for value in (prefix, file['relative_path']) if value)


class ModWorkspaceService:
    def __init__(self, mods):
        self.mods = mods
        self.plans, self.executor = mods.plans, mods.executor

    @staticmethod
    def _files(root, check):
        """No file opens, content hashes, assembly parsers, or followed links."""
        files, entries = {}, 0

        def walk(directory, kind, prefix=''):
            nonlocal entries
            with pinned_chain(directory) as chain:
                with os.scandir(directory) as stream:
                    children = sorted(stream, key=lambda value: value.name.casefold())
                for child in children:
                    check()
                    entries += 1
                    if entries > MAX_ENTRIES:
                        raise PlanRejected('MOD_WORKSPACE_LIMIT')
                    relative = prefix + child.name
                    canonical_relative(relative)
                    # Windows DirEntry.stat omits nlink/inode on cached directory
                    # entries. os.stat still reads metadata only and supplies both.
                    info = os.stat(child.path, follow_symlinks=False)
                    if child.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise PlanRejected('MOD_WORKSPACE_UNSAFE')
                    if stat.S_ISDIR(info.st_mode):
                        walk(Path(child.path), kind, relative + '/')
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        files[target_key(kind, relative)] = dict(target_root_kind=kind,
                            relative_path=relative, size=info.st_size, stamp=_stamp(info))
                    else:
                        raise PlanRejected('MOD_WORKSPACE_UNSAFE')
                chain.check()

        # Standalone UserLibs files are dependencies, not user-selectable Mods.
        for kind in ('mods', 'plugins'):
            directory = root / ROOTS[kind]
            if directory.exists():
                walk(directory, kind)
        return files

    @staticmethod
    def _metadata_owners(store, installation_id):
        """Read committed ownership and explicit adoptions without library I/O."""
        row = store.connection.execute(
            'SELECT payload FROM deployment_generations WHERE installation_id=? ORDER BY sequence DESC LIMIT 1',
            (str(installation_id),)).fetchone()
        managed, committed_at, generation_id = {}, None, None
        if row:
            generation = json.loads(row['payload'])
            transaction = store.get('Transaction', generation['transaction_id'])
            managed = owned_after(store.get('ActionPlan', transaction.plan_id))
            committed_at = datetime.fromisoformat(generation['committed_at'])
            generation_id = generation['generation_id']
        adopted = set()
        rows = store.connection.execute('''SELECT a.release_id,a.adopted_at FROM package_adoptions a
            JOIN baseline_snapshots b ON b.snapshot_id=a.snapshot_id WHERE b.installation_id=?''',
            (str(installation_id),))
        for receipt in rows:
            if committed_at and datetime.fromisoformat(receipt['adopted_at']) <= committed_at:
                continue
            adopted.add(receipt['release_id'])
            for row in store.connection.execute('SELECT payload FROM file_entries WHERE release_id=?', (receipt['release_id'],)):
                file = json.loads(row['payload'])
                key = target_key(file['target_root_kind'], file['relative_path'])
                current = managed.setdefault(key, dict(target_root_kind=file['target_root_kind'],
                    relative_path=file['relative_path'], sha256=file['source_hash'], owner_ids=[]))
                if receipt['release_id'] not in current['owner_ids']:
                    current['owner_ids'].append(receipt['release_id'])
        return managed, adopted, generation_id

    @staticmethod
    def _external_groups(installation_id, disk, managed):
        groups = []
        for kind in ('mods', 'plugins'):
            relevant = {key: file for key, file in disk.items() if file['target_root_kind'] == kind}
            root_dlls = [file for key, file in relevant.items()
                if '/' not in file['relative_path'] and file['relative_path'].lower().endswith('.dll') and key not in managed]
            attached = set()
            for dll in root_dlls:
                stem = Path(dll['relative_path']).stem
                prefix = stem.casefold() + '/'
                members = [file for file in relevant.values()
                    if file is dll or file['relative_path'].casefold().startswith(prefix)]
                attached.add(stem.casefold())
                groups.append((kind, dll['relative_path'], stem, members))
            directories = sorted({file['relative_path'].split('/')[0]
                for file in relevant.values() if '/' in file['relative_path']})
            for directory in directories:
                if directory.casefold() in attached:
                    continue
                members = [file for file in relevant.values()
                    if file['relative_path'].casefold().startswith(directory.casefold() + '/')]
                evidence = [file for file in members if file['relative_path'].lower().endswith('.dll')
                    or Path(file['relative_path']).name.casefold() in
                    {'mod.json', 'itemforge.items.json', 'characterforge.characters.json'}]
                # A managed primary DLL/package already represents this directory.
                if not evidence or all(target_key(file['target_root_kind'], file['relative_path']) in managed for file in evidence):
                    continue
                groups.append((kind, directory + '/', directory, members))
        output = []
        for kind, anchor, name, files in groups:
            overlap = any(target_key(file['target_root_kind'], file['relative_path']) in managed for file in files)
            identity = str(uuid5(UUID(str(installation_id)), 'PSModManager.workspace.external.v1:' + kind + '/' + anchor.casefold()))
            unsafe = any(Path(file['relative_path']).suffix.casefold() in EXECUTABLES for file in files)
            message = MESSAGES['MOD_WORKSPACE_OVERLAP'] if overlap else MESSAGES['MOD_WORKSPACE_UNSAFE'] if unsafe else ''
            output.append(dict(id=identity, release_id=None, display_name=name, version_raw=None,
                state='enabled', source='external', file_count=len(files), message=message,
                manageable=not (overlap or unsafe), _files=files))
        return output

    def _view(self, store, installation, check):
        root = Path(installation.canonical_root)
        state = self.mods._state(store, installation.installation_id)
        disk = self._files(root, check)
        managed, adopted, generation = self._metadata_owners(store, installation.installation_id)
        active = {identity for file in managed.values() for identity in file['owner_ids']} | adopted
        disabled = {str(item.release_id) for item in state.selections if not item.selected}
        rows = store.connection.execute('SELECT payload FROM mod_releases ORDER BY release_id').fetchall()
        if len(rows) > 200:
            raise PlanRejected('MOD_WORKSPACE_LIMIT')
        items = []
        for row in rows:
            check()
            release = ModRelease.model_validate_json(row['payload'])
            identity = str(release.release_id)
            package = store.get('ModPackage', release.package_id)
            source = 'library'
            if package.source.reference.startswith('workspace:'):
                _, source_installation, workspace_id = package.source.reference.split(':', 2)
                if source_installation != str(installation.installation_id):
                    continue
                identity, source = workspace_id, 'local'
            files = []
            missing = False
            supported = True
            for raw in store.connection.execute('SELECT payload FROM file_entries WHERE release_id=?', (str(release.release_id),)):
                file = json.loads(raw['payload'])
                if library_attachment(file):
                    artifact = store.get('Artifact', file['source_hash'])
                    files.append(dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                        size=artifact.size, stamp=None, library_only=True))
                    continue
                artifact = store.get('Artifact', file['source_hash'])
                permitted = ordinary_file(file)
                supported &= permitted
                observed = disk.get(target_key(file['target_root_kind'], file['relative_path'])) if permitted else None
                # Mods/Plugins were enumerated completely above. Only UserLibs
                # needs a separate lookup, not every absent disabled resource.
                if observed is None and permitted and file['target_root_kind'] == 'user_libs':
                    path = root / ROOTS[file['target_root_kind']] / file['relative_path']
                    reject_reparse(path)
                    if path.is_file():
                        info = path.stat()
                        observed = dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                            size=info.st_size, stamp=_stamp(info))
                if str(release.release_id) in active:
                    missing |= observed is None or observed['size'] != artifact.size
                files.append(dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                    size=artifact.size, stamp=observed['stamp'] if observed else None))
            status = 'enabled' if str(release.release_id) in active else 'disabled' if str(release.release_id) in disabled else 'not_installed'
            if missing:
                status = 'drifted'
            deployable = any(not file.get('library_only') for file in files)
            items.append(dict(id=identity, release_id=str(release.release_id), package_id=str(release.package_id), manifest_hash=release.manifest_hash,
                display_name=package.display_name, version_raw=release.version_raw, state=status, source=source,
                file_count=len(files), manageable=deployable and supported, _files=files,
                message=('包中有无法自动部署的文件，请在详情中检查目标和文件类型。' if not supported else
                    '包中只有说明文档，没有可部署的 Mod 文件。' if not deployable else
                    '安装文件缺失或大小改变，保存时将校验并保护外部修改。' if missing else '')))
        by_id = {item['id']: item for item in items}
        for external in self._external_groups(installation.installation_id, disk, managed):
            existing = by_id.get(external['id'])
            if existing is not None:
                # A disabled local Mod may have been manually copied back. It
                # remains one row; don't guess ownership or erase its new bytes.
                existing.update(state='drifted', message='停用后的 Mod 文件又出现在游戏目录中；保存时将保护这些外部文件。')
            else:
                items.append(external)
        if len(items) > 200 or len({item['id'] for item in items}) != len(items):
            raise PlanRejected('MOD_WORKSPACE_LIMIT')
        items.sort(key=lambda item: (item['display_name'].casefold(), item['id']))
        enabled = [item['id'] for item in items if item['state'] in {'enabled', 'drifted'}]
        ordered = [str(identity) for identity in state.workspace_order if str(identity) in enabled]
        ordered += [identity for identity in enabled if identity not in ordered]
        snapshot = content_hash(dict(items=items, enabled_ids=ordered, revision=state.revision, generation=generation))
        return dict(items=items, enabled_ids=ordered, snapshot_id=snapshot,
            order_policy='display_only', order_message=ORDER_MESSAGE)

    @staticmethod
    def _public(view):
        return dict(view, items=[{key: value for key, value in item.items() if not key.startswith('_')} for item in view['items']])

    def list(self, installation_id, *, check=lambda: None):
        check = self.plans._budget(check)
        with self.plans._lock, self.plans._store(check) as store:
            with self.plans.guards._installation(str(installation_id), check=check) as (installation, _):
                return self._public(self._view(store, installation, check))

    def detail(self, installation_id, id, offset=0, limit=200, *, check=lambda: None):
        request = WorkspaceDetail(installation_id=installation_id, id=id, offset=offset, limit=limit)
        check = self.plans._budget(check)
        with self.plans._lock, self.plans._store(check) as store:
            with self.plans.guards._installation(str(installation_id), check=check) as (installation, _):
                view = self._view(store, installation, check)
                item = next((item for item in view['items'] if item['id'] == str(request.id)), None)
                if item is None:
                    raise PlanRejected('MOD_WORKSPACE_NOT_FOUND')
                files = sorted(item['_files'], key=lambda file: (file['target_root_kind'], file['relative_path'].casefold()))
                return {key: value for key, value in item.items() if not key.startswith('_')} | dict(
                    snapshot_id=view['snapshot_id'], files=[dict(relative_path=_display_path(file),
                        target_root_kind=file['target_root_kind'], size=file['size']) for file in files[offset:offset+limit]],
                    total=len(files), offset=offset, next_offset=offset+limit if offset+limit < len(files) else None)

    def _adopt(self, store, installation, item, check, progress, *, ordinary=False):
        root = Path(installation.canonical_root)
        scope = [dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path']) for file in item['_files']]
        if not 1 <= len(scope) <= MAX_FILES:
            raise PlanRejected('MOD_WORKSPACE_LIMIT')
        if ordinary:
            repository = PackageRepository(store, self.plans.library)
            disk = {}
            for index, file in enumerate(item['_files']):
                check()
                path = root/ROOTS[file['target_root_kind']]/file['relative_path']
                if _stamp(path.stat(follow_symlinks=False)) != file['stamp']:
                    raise PlanRejected('MOD_WORKSPACE_STALE')
                artifact = repository.capture_ordinary(path, check)
                store.put(artifact)
                disk[target_key(file['target_root_kind'], file['relative_path'])] = dict(
                    target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                    sha256=artifact.sha256, size=artifact.size)
                progress(index, len(scope), 'preparing', 'MOD_WORKSPACE_BACKING_UP')
        else:
            disk, _ = self.plans._scan(root, installation.file_identity, check, scope=scope)
        if len(disk) != len(scope):
            raise PlanRejected('MOD_WORKSPACE_STALE')
        for file in item['_files']:
            info = os.stat(root / ROOTS[file['target_root_kind']] / file['relative_path'], follow_symlinks=False)
            if _stamp(info) != file['stamp']:
                raise PlanRejected('MOD_WORKSPACE_STALE')
        baseline_files = [dict(relative_path=ROOTS[file['target_root_kind']] + '/' + file['relative_path'],
            sha256=file['sha256'], size=file['size']) for _, file in sorted(disk.items())]
        snapshot = BaselineSnapshot(snapshot_id=uuid5(UUID(item['id']), content_hash(baseline_files)),
            installation_id=installation.installation_id, scan_generation=0, manifest_hash=content_hash(baseline_files),
            files=baseline_files, coverage='complete')
        files = [dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
            source_hash=file['sha256'], lifecycle='immutable') for _, file in sorted(disk.items())]
        files.sort(key=lambda file: (file['target_root_kind'], file['relative_path']))
        manifest_hash = content_hash(dict(version_raw=None, files=files))
        package_id = uuid5(UUID(item['id']), 'PSModManager.workspace.package.v1')
        release_id = uuid5(package_id, manifest_hash)
        repository = PackageRepository(store, self.plans.library)
        if not ordinary:
            copied = set()
            for index, file in enumerate(snapshot.files):
                check()
                progress(index, len(files), 'preparing', 'MOD_WORKSPACE_BACKING_UP')
                if file.sha256 not in copied:
                    store.put(repository.copy(root, file, check))
                    copied.add(file.sha256)
        store.put(snapshot)
        store.put(ModPackage(package_id=package_id, display_name=item['display_name'],
            source=dict(kind='user', reference=f'workspace:{installation.installation_id}:{item["id"]}',
                observed_at=datetime.now(timezone.utc)), trust='unknown'))
        store.put(ModRelease(release_id=release_id, package_id=package_id, version_raw=None, manifest_hash=manifest_hash))
        for file in files:
            store.put(FileEntry(file_entry_id=uuid5(release_id, file['target_root_kind'] + '/' + file['relative_path']),
                release_id=release_id, ownership='shared_identical', **file))
        repository.release(release_id, manifest_hash, check=check, verify_bytes=not ordinary)
        store.connection.execute('INSERT OR IGNORE INTO package_adoptions VALUES (?,?,?)',
            (str(snapshot.snapshot_id), str(release_id), datetime.now(timezone.utc).isoformat()))
        return dict(release_id=str(release_id), manifest_hash=manifest_hash, selected=False)

    def save(self, payload, *, check=lambda: None, commit=None, progress=lambda *args: None):
        request = WorkspaceSave.model_validate(payload)
        check = self.plans._budget(check, seconds=SAVE_SECONDS)
        # Job progress is monotonic across all preparation/transaction phases.
        # Keep each phase's actual file counts in data instead of resetting the
        # job's completed counter when a second Mod or the executor starts.
        progress_count = 0
        def report(completed, total, stage, message):
            nonlocal progress_count
            progress_count += 1
            progress(progress_count, None, stage, message,
                dict(completed=completed, total=total, unit='files'))
        with self.executor._lock, self.plans._lock:
            from .ordinary import OrdinaryMods
            result = OrdinaryMods(self.mods).save(request, workspace=self, check=check, commit=commit, progress=report)
            if result.get('error_code'):
                return result
            if not (result.get('no_op') or result.get('deployed') or result.get('idempotent')):
                raise PlanRejected('MOD_ACTION_FAILED')
            return dict(completed=True, no_op=result.get('no_op', False), idempotent=result.get('idempotent', False),
                workspace=self.list(str(request.installation_id), check=check))
