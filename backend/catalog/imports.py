"""Native source handles -> bounded private preview -> atomic library publish."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
import zipfile

from backend.app.paths import REFERENCE_ROOT, overlaps, reject_reparse
from backend.contracts import Artifact, Component, FileEntry, ModPackage, ModRelease, Relation, SnapshotFile
from backend.games.probably_stolen.layout import signature
from backend.scanning.worker_client import MetadataWorker
from backend.scanning.content_manifests import analyze
from backend.storage import Store
from backend.storage.package_repository import PackageRepository, content_hash
from . import archive_policy as policy
from .ownership import classify
from . import versions


class ImportService:
    def __init__(self, paths):
        self.paths = paths
        self._sources = {}
        self._previews = {}
        self._lock = threading.RLock()
        self._closed = False

    @contextmanager
    def _request_lock(self):
        # A synchronous page/chooser request must not hold the bridge lock
        # waiting behind an import job and thereby prevent task.cancel.
        if not self._lock.acquire(blocking=False):
            raise policy.ImportRejected('IMPORT_BUSY')
        try:
            yield
        finally:
            self._lock.release()

    def _cleanup(self, stage):
        parent = self.paths.root / 'imports'
        if stage.parent != parent or len(stage.name) != 32 or any(c not in '0123456789abcdef' for c in stage.name):
            raise policy.ImportRejected('IMPORT_CLEANUP_UNSAFE')
        if not stage.exists():
            return
        with policy.locked_path(parent, directory=True):
            with policy.locked_path(stage, directory=True):
                for file in stage.iterdir():
                    info = file.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_file_attributes & 0x400:
                        raise policy.ImportRejected('IMPORT_CLEANUP_UNSAFE')
                    # Unlink only the owned flat name; never recurse into a link.
                    file.unlink()
            stage.rmdir()

    def _expire(self):
        if self._closed:
            raise policy.ImportRejected('IMPORT_CLOSED')
        now = time.monotonic()
        self._sources = {key: value for key, value in self._sources.items() if value['expires'] > now}
        for key, value in list(self._previews.items()):
            if value['expires'] <= now:
                self._cleanup(value['stage'])
                del self._previews[key]

    def grant(self, kind, selected):
        """Called by the native picker; the RPC never accepts filesystem paths."""
        return self._grant(kind, selected)

    def grant_download(self, kind, selected):
        """Private Nexus consumer; never grants arbitrary owned-data access."""
        source = Path(selected)
        root = self.paths.root / 'downloads' / 'nexus'
        try:
            relative = source.relative_to(root)
            valid = (kind in {'zip', 'dll'} and len(relative.parts) == 2
                     and len(relative.parts[0]) == 32 and all(c in '0123456789abcdef' for c in relative.parts[0])
                     and relative.parts[1] == 'download.' + kind)
        except ValueError:
            valid = False
        if not valid:
            raise policy.ImportRejected('IMPORT_SOURCE_PROTECTED_LIBRARY')
        return self._grant(kind, selected, owned_download=True)

    def revoke_download(self, source_handle):
        """Revoke only an internal Nexus receipt; its owner removes the file."""
        with self._lock:
            record = self._sources.get(str(source_handle))
            if not record or record.get('owned_download') is not True:
                return False
            del self._sources[str(source_handle)]
            return True

    def _grant(self, kind, selected, *, owned_download=False):
        with self._request_lock():
            self._expire()
            if kind not in ('dll', 'directory', 'zip'):
                raise policy.ImportRejected('IMPORT_FORMAT_UNSUPPORTED')
            if not selected:
                return {'source_handle': None, 'display_name': None}
            if len(self._sources) >= 8:
                raise policy.ImportRejected('IMPORT_CAPACITY')
            source = Path(selected)
            if not source.is_absolute() or str(source).startswith(('\\\\', '//')):
                raise policy.ImportRejected('IMPORT_PATH_INVALID')
            policy.canonical_name('/'.join(source.parts[1:]))
            try:
                reject_reparse(source)
            except ValueError:
                raise policy.ImportRejected('IMPORT_LINK_REFUSED') from None
            if overlaps(source, self.paths.root) and not owned_download:
                raise policy.ImportRejected('IMPORT_SOURCE_PROTECTED_LIBRARY')
            if overlaps(source, REFERENCE_ROOT):
                raise policy.ImportRejected('IMPORT_SOURCE_PROTECTED_REFERENCE')
            # An explicitly selected installed mod is a read-only source. The
            # regular locked-path copy and parser checks still apply; importing
            # a private copy never grants ownership of the installed original.
            with policy.locked_path(source, directory=kind == 'directory'):
                if kind != 'directory' and source.suffix.lower() != '.' + kind:
                    raise policy.ImportRejected('IMPORT_FORMAT_UNSUPPORTED')
                before = signature(source.stat())
            handle = str(uuid4())
            self._sources[handle] = dict(path=source, kind=kind, signature=before, expires=time.monotonic() + 300,
                                         owned_download=owned_download)
            return {'source_handle': handle, 'display_name': source.name}

    @staticmethod
    def _directory_files(root, budget):
        queue, output, seen, count = [root], [], set(), 0
        while queue:
            budget.check()
            directory = queue.pop()
            with policy.locked_path(directory, directory=True):
                for entry in directory.iterdir():
                    count += 1
                    if count > policy.MAX_ENTRIES:
                        raise policy.ImportRejected('IMPORT_ENTRY_LIMIT')
                    raw = entry.relative_to(root).as_posix()
                    name = policy.canonical_name(raw)
                    if name.casefold() in seen:
                        raise policy.ImportRejected('IMPORT_NAME_CONFLICT')
                    seen.add(name.casefold())
                    info = entry.lstat()
                    if info.st_file_attributes & 0x400 or stat.S_ISLNK(info.st_mode):
                        raise policy.ImportRejected('IMPORT_LINK_REFUSED')
                    if stat.S_ISDIR(info.st_mode):
                        queue.append(entry)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        policy.check_content_name(name)
                        if info.st_size > policy.MAX_FILE_BYTES:
                            raise policy.ImportRejected('IMPORT_FILE_LIMIT')
                        output.append((entry, name, signature(info)))
                        if len(output) > policy.MAX_FILES:
                            raise policy.ImportRejected('IMPORT_ENTRY_LIMIT')
                    else:
                        raise policy.ImportRejected('IMPORT_LINK_REFUSED')
        if sum(row[2][2] for row in output) > policy.MAX_TOTAL_BYTES:
            raise policy.ImportRejected('IMPORT_TOTAL_LIMIT')
        return sorted(output, key=lambda row: row[1])

    @staticmethod
    def _copy(reader, target, expected_size, budget):
        if expected_size > policy.MAX_FILE_BYTES:
            raise policy.ImportRejected('IMPORT_FILE_LIMIT')
        digest, count = hashlib.sha256(), 0
        with target.open('xb') as output:
            while True:
                budget.check()
                block = reader.read(min(policy.CHUNK, expected_size - count + 1))
                if not block:
                    break
                if count == 0 and block.startswith((b'PK\x03\x04', b'7z\xbc\xaf\x27\x1c', b'Rar!')):
                    raise policy.ImportRejected('IMPORT_NESTED_ARCHIVE')
                count += len(block)
                if count > expected_size or count > policy.MAX_FILE_BYTES:
                    raise policy.ImportRejected('IMPORT_SIZE_MISMATCH')
                budget.consume(len(block))
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if count != expected_size:
            raise policy.ImportRejected('IMPORT_SIZE_MISMATCH')
        return digest.hexdigest()

    @staticmethod
    def _target(name):
        parts = name.split('/')
        roots = {'mods': 'mods', 'plugins': 'plugins', 'userlibs': 'user_libs'}
        if len(parts) > 1 and parts[0].lower() in roots:
            return roots[parts[0].lower()], '/'.join(parts[1:]), classify(name)
        if len(parts) > 1 and parts[0].lower() == 'userdata':
            return 'user_data', '/'.join(parts[1:]), classify(name)
        if len(parts) == 1 and name.lower().endswith('.dll'):
            return 'mods', name, 'immutable'
        return 'game_root', name, 'unowned'

    def preview(self, source_handle, *, check=lambda: None):
        with self._lock:
            self._expire()
            if source_handle not in self._sources:
                raise policy.ImportRejected('IMPORT_SOURCE_EXPIRED')
            if len(self._previews) >= 8:
                # Keep pending previews intact. Completed imports are only
                # short-lived replay receipts; retire the oldest at capacity.
                completed = next((key for key, value in self._previews.items()
                                  if value['committed'] is not None), None)
                if completed is None:
                    raise policy.ImportRejected('IMPORT_CAPACITY')
                self._cleanup(self._previews[completed]['stage'])
                del self._previews[completed]
            self.paths.check_owned_paths()
            record = self._sources[source_handle]
            source = record['path']
            parent = self.paths.root / 'imports'
            stage = parent / uuid4().hex
            budget, rows = policy.Budget(check), []
            try:
                with policy.locked_path(self.paths.root, directory=True):
                    reject_reparse(parent)
                    parent.mkdir(exist_ok=True)
                    with policy.locked_path(parent, directory=True):
                        stage.mkdir()
                        with policy.locked_path(stage, directory=True), policy.locked_path(source, directory=record['kind'] == 'directory'):
                            if signature(source.stat()) != record['signature']:
                                raise policy.ImportRejected('IMPORT_SOURCE_CHANGED')
                            with ExitStack() as stack:
                                if record['kind'] == 'zip':
                                    stream = stack.enter_context(source.open('rb'))
                                    archive, entries = policy.zip_entries(stream)
                                    stack.callback(archive.close)
                                    def open_member(entry):
                                        policy.validate_member(archive, entry, budget)
                                        return archive.open(entry)
                                    sources = [(name, entry.file_size, lambda e=entry: open_member(e)) for entry, name in entries]
                                else:
                                    original = self._directory_files(source, budget) if record['kind'] == 'directory' else [(source, policy.canonical_name(source.name), signature(source.stat()))]
                                    sources = [(name, stamp[2], path) for path, name, stamp in original]
                                if not sources:
                                    raise policy.ImportRejected('IMPORT_EMPTY')
                                targets = set()
                                for index, (name, size, opener) in enumerate(sources):
                                    budget.check()
                                    blob = stage / (str(index) + '.blob')
                                    if isinstance(opener, Path):
                                        with policy.locked_path(opener), opener.open('rb') as reader:
                                            digest = self._copy(reader, blob, size, budget)
                                    else:
                                        with opener() as reader:
                                            digest = self._copy(reader, blob, size, budget)
                                    kind, relative, lifecycle = self._target(name)
                                    key = (kind, relative.casefold())
                                    if key in targets:
                                        raise policy.ImportRejected('IMPORT_NAME_CONFLICT')
                                    targets.add(key)
                                    rows.append(dict(path=name, blob=blob.name, sha256=digest, size=size,
                                        target_root_kind=kind, relative_path=relative, lifecycle=lifecycle,
                                        ownership='unknown' if lifecycle == 'unowned' else 'external', roles=['unknown'], parser_status='not_applicable'))
                                if record['kind'] == 'directory' and self._directory_files(source, budget) != original:
                                    raise policy.ImportRejected('IMPORT_SOURCE_CHANGED')
                            # Metadata worker receives only the staged, bounded bytes.
                            worker = MetadataWorker(self.paths.root / 'import-worker')
                            for row in rows:
                                if row['path'].lower().endswith('.dll'):
                                    budget.check()
                                    parsed = worker.inspect(stage, row['blob'], cancelled=lambda: (budget.check() or False))
                                    if parsed['status'] not in ('managed', 'native') or parsed['sha256'] != row['sha256']:
                                        raise policy.ImportRejected('IMPORT_METADATA_REJECTED')
                                    row['parser_status'] = parsed['status']
                                    analysis = analyze(parsed['metadata'], artifact_sha256=row['sha256'], observed_at=datetime.now(timezone.utc).isoformat())
                                    row['roles'] = sorted({'content' if role == 'characterforge_content' else role for role in analysis['roles']}) or ['unknown']
                                    row['analysis'] = analysis
                                    references = parsed['metadata'].get('references', [])
                                    if (not isinstance(references, list) or len(references) > 256
                                            or any(not isinstance(ref, dict) or not isinstance(ref.get('name'), str)
                                                or not 1 <= len(ref['name']) <= 256
                                                or (ref.get('version') is not None and (not isinstance(ref['version'], str)
                                                    or not 1 <= len(ref['version']) <= 256)) for ref in references)):
                                        raise policy.ImportRejected('IMPORT_METADATA_REJECTED')
                                    row['references'] = [{'name': ref['name'], 'version': ref.get('version')} for ref in references]
                                    # Preserve actual worker process evidence privately, not through RPC.
                                    with (stage / (row['blob'] + '.metadata.json')).open('x', encoding='utf-8') as report:
                                        json.dump(parsed, report)
                            budget.check()
                            preview_id = str(uuid4())
                            value = dict(preview_id=preview_id, source_handle=source_handle, stage=stage, display_name=source.stem,
                                files=rows, expires=time.monotonic() + 300, committed=None)
                            value['preview_hash'] = content_hash(rows)
                            value['version'] = versions.observe(rows, source.stem)
                            version_store = Store(self.paths.database, library_root=self.paths.root / 'library')
                            try:
                                value['version_candidates'] = versions.candidates(version_store, value['version'], rows)
                            finally:
                                version_store.close()
                            self._previews[preview_id] = value
                            return self._page(value, 0, 20)
            except BaseException:
                self._cleanup(stage)
                raise

    @staticmethod
    def _page(value, offset, limit):
        files = [{key: row[key] for key in ('path','size','sha256','target_root_kind','relative_path','lifecycle','ownership','roles','parser_status')} for row in value['files'][offset:offset + limit]]
        return dict(preview_id=value['preview_id'], preview_hash=value['preview_hash'], display_name=value['display_name'],
                    files=files, total=len(value['files']), next_offset=offset + limit if offset + limit < len(value['files']) else None,
                    version=value.get('version'), version_candidates=value.get('version_candidates', []),
                    can_commit=True, warnings=['根目录说明文档仅保存在包库，随整合包分享，不部署到游戏。未知归属文件不会自动启用。'])

    def page(self, preview_id, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise policy.ImportRejected('IMPORT_REQUEST_INVALID')
        with self._request_lock():
            self._expire()
            if preview_id not in self._previews:
                raise policy.ImportRejected('IMPORT_PREVIEW_EXPIRED')
            return self._page(self._previews[preview_id], offset, limit)

    @contextmanager
    def _library_locks(self, files):
        """Pin writable library directories before any publication file opens."""
        library = self.paths.root / 'library'
        with ExitStack() as stack:
            stack.enter_context(policy.locked_path(self.paths.root, directory=True))
            reject_reparse(library)
            library.mkdir(exist_ok=True)
            stack.enter_context(policy.locked_path(library, directory=True))
            for prefix in sorted({row['sha256'][:2] for row in files}):
                shard = library / prefix
                reject_reparse(shard)
                shard.mkdir(exist_ok=True)
                stack.enter_context(policy.locked_path(shard, directory=True))
            yield

    def commit(self, preview_id, preview_hash, display_name, version_raw=None, *, package_id=None, check=lambda: None, commit=None):
        with self._lock:
            self._expire()
            value = self._previews.get(preview_id)
            if value is None:
                raise policy.ImportRejected('IMPORT_PREVIEW_EXPIRED')
            if preview_hash != value['preview_hash'] or preview_hash != content_hash(value['files']):
                raise policy.ImportRejected('IMPORT_PREVIEW_CHANGED')
            if version_raw is None:
                version_raw = (value.get('version') or {}).get('version_raw')
            if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 200 or (version_raw is not None and (not isinstance(version_raw, str) or not 1 <= len(version_raw) <= 200)):
                raise policy.ImportRejected('IMPORT_REQUEST_INVALID')
            if value['committed'] is not None:
                previous = value['committed']
                if previous[0] != (display_name, version_raw, package_id):
                    raise policy.ImportRejected('IMPORT_PREVIEW_CHANGED')
                return previous[1]
            store = Store(self.paths.database, library_root=self.paths.root / 'library')
            repository = PackageRepository(store, self.paths.root / 'library')
            try:
                with policy.locked_path(value['stage'], directory=True), self._library_locks(value['files']):
                    files = sorted([{k: row[k] for k in ('target_root_kind','relative_path','lifecycle')} | {'source_hash': row['sha256']} for row in value['files']], key=lambda f:(f['target_root_kind'],f['relative_path']))
                    manifest = content_hash(dict(version_raw=version_raw, files=files))
                    requested_package = package_id
                    try:
                        package_id = UUID(str(versions.package_identity(value['version'], display_name,
                            versions.candidates(store, value['version'], value['files']), package_id)))
                    except ValueError:
                        raise policy.ImportRejected('IMPORT_VERSION_SELECTION_CHANGED') from None
                    release_id = uuid5(package_id, manifest)
                    artifacts = []
                    for row in value['files']:
                        check()
                        with policy.locked_path(value['stage'] / row['blob']):
                            blob = value['stage'] / row['blob']
                            if blob.stat().st_size != row['size']:
                                raise policy.ImportRejected('IMPORT_PREVIEW_CHANGED')
                            digest = hashlib.sha256()
                            with blob.open('rb') as reader:
                                for block in iter(lambda: reader.read(policy.CHUNK), b''):
                                    check()
                                    digest.update(block)
                            if digest.hexdigest() != row['sha256']:
                                raise policy.ImportRejected('IMPORT_PREVIEW_CHANGED')
                            artifacts.append(repository.copy(value['stage'], SnapshotFile(relative_path=row['blob'], sha256=row['sha256'], size=row['size']), check))
                    store.connection.execute('BEGIN IMMEDIATE')
                    try:
                        try:
                            store.get('ModPackage', package_id)
                        except KeyError:
                            store.put(ModPackage(package_id=package_id, display_name=display_name,
                                source=dict(kind='user', reference='local-import:' + manifest, observed_at=datetime.now(timezone.utc)), trust='unknown'))
                        for artifact in artifacts:
                            store.put(artifact)
                        store.put(ModRelease(release_id=release_id, package_id=package_id, version_raw=version_raw, manifest_hash=manifest))
                        # Stable package observation keeps repeated publication
                        # byte-identical while retaining original evidence time.
                        observed_at = store.get('ModPackage', package_id).source.observed_at
                        for row in value['files']:
                            file_id = uuid5(release_id, row['target_root_kind'] + '/' + row['relative_path'])
                            store.put(FileEntry(file_entry_id=file_id, release_id=release_id, source_hash=row['sha256'],
                                target_root_kind=row['target_root_kind'], relative_path=row['relative_path'], lifecycle=row['lifecycle'],
                                ownership='unknown' if row['lifecycle']=='unowned' else 'shared_identical'))
                            if row['path'].lower().endswith('.dll'):
                                analysis = row['analysis']
                                store.put(Component(component_id=file_id, release_id=release_id, artifact_hash=row['sha256'],
                                    file_name=Path(row['path']).name, assembly_name=analysis['assembly_name'], assembly_version=analysis['assembly_version'],
                                    melon_info=({key: analysis['melon_info'][key] for key in ('entry_type','name','version_raw','author')} if analysis['melon_info'] else None), roles=set(row['roles']),
                                    content_ids=[{key: item[key] for key in ('framework','type','value')} for item in analysis['content_ids']]))
                                for index, requirement in enumerate(analysis['requirements']):
                                    store.put(Relation(relation_id=uuid5(file_id, 'capability:' + str(index)),
                                        from_id=file_id, capability=requirement['capability'], kind='capability_requirement',
                                        requirement_raw=requirement['requirement_raw'],
                                        source=dict(kind='embedded_metadata', reference=requirement['source']['reference'], observed_at=observed_at),
                                        confidence='confirmed', fingerprint_scope=None))
                                for index, reference in enumerate(row['references']):
                                    store.put(Relation(relation_id=uuid5(file_id, 'assembly-reference:' + str(index)),
                                        from_id=file_id, capability='assembly:' + reference['name'], kind='soft_dependency',
                                        requirement_raw=reference['version'], source=dict(kind='embedded_metadata',
                                            reference=f"artifact:{row['sha256']}#AssemblyRef/{index}", observed_at=observed_at),
                                        confidence='inferred', fingerprint_scope=None))
                        repository.release(release_id, manifest, check=check)
                        check()
                        (commit or (lambda action: action()))(store.connection.commit)
                    except BaseException:
                        store.connection.rollback()
                        raise
                    result = dict(release_id=str(release_id), package_id=str(package_id), version_raw=version_raw,
                        manifest_hash=manifest, file_count=len(files), enabled=False, deployed=False)
                    value['committed'] = ((display_name, version_raw, requested_package), result)
                    self._sources.pop(value['source_handle'], None)
                try:
                    self._cleanup(value['stage'])
                except (OSError, ValueError):
                    result['cleanup_pending'] = True
                return result
            except BaseException:
                self._cleanup(value['stage'])
                self._previews.pop(preview_id, None)
                raise
            finally:
                store.close()

    def close(self):
        with self._lock:
            for value in self._previews.values():
                self._cleanup(value['stage'])
            self._previews.clear()
            self._sources.clear()
            self._closed = True
