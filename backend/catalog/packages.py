"""Explicit adoption into the private library; the installation is read-only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
from uuid import UUID, uuid4, uuid5

from backend.app.paths import overlaps, reject_reparse
from backend.contracts import BaselineSnapshot, ModPackage, ModRelease, Component, FileEntry
from backend.games.probably_stolen.fingerprint import Budget, read_file
from backend.games.probably_stolen.layout import identity
from backend.scanning.inventory import _walk
from backend.storage import Store
from backend.storage.package_repository import PackageRepository, content_hash
from .ownership import classify, target, owners


class PackageDetailError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class AdoptionError(ValueError):
    def __init__(self, code, issues=()):
        self.code = code
        self.issues = list(issues)[:5]
        super().__init__(code)


class _DetailReader:
    """Reuse the repository's typed get protocol on a SQLite read-only handle."""
    get = Store.get

    def __init__(self, connection):
        self.connection = connection


class PackageService:
    def __init__(self, paths):
        self.paths = paths
        self.library = paths.root / 'library'
        self._lock = threading.RLock()

    def _open(self):
        self.paths.check_owned_paths()
        reject_reparse(self.library)
        return Store(self.paths.database, library_root=self.library)

    def _root(self, store, installation_id):
        installation = store.get('Installation', installation_id)
        root = Path(installation.canonical_root)
        reject_reparse(root)
        if overlaps(root, self.paths.root) or identity(root) != installation.file_identity:
            raise ValueError('Installation identity or data root changed')
        return root

    @staticmethod
    def _scan(root, budget, progress=lambda *args: None):
        reject_reparse(root)
        root_identity = identity(root)
        files, directories = _walk(root, budget, errors := [])
        if errors:
            raise AdoptionError('ADOPTION_READ_FAILED', errors)
        output = []
        progress(0, len(files), 'adoption', 'ADOPTION_READING_FILES')
        for index, path in enumerate(sorted(files)):
            try:
                digest, _, stamp = read_file(root, path, budget)
            except (OSError, ValueError) as exc:
                code = 'FILE_UNREADABLE' if isinstance(exc, OSError) else str(exc).upper()
                raise AdoptionError('ADOPTION_READ_FAILED', [{'path': path, 'code': code}]) from None
            if stamp != files[path]:
                raise AdoptionError('ADOPTION_READ_FAILED', [{'path': path, 'code': 'FILE_DRIFT'}])
            output.append(dict(relative_path=path, sha256=digest, size=stamp[2]))
            progress(index + 1, len(files), 'adoption', 'ADOPTION_READING_FILES')
        after, after_directories = _walk(root, budget, errors)
        reject_reparse(root)
        if errors or after != files or after_directories != directories or identity(root) != root_identity:
            raise ValueError('Scan drift')
        return output

    def preview(self, installation_id, *, cancel=None, progress=lambda *args: None):
        with self._lock:
            store = self._open()
            try:
                root = self._root(store, installation_id)
                # Adoption needs file bytes, not assembly analysis or loader predictions.
                # Keep it independent of the optional inventory worker and its cache.
                files = self._scan(root, Budget(cancel), progress)
                snapshot = BaselineSnapshot(snapshot_id=uuid4(), installation_id=installation_id,
                    scan_generation=0, manifest_hash=content_hash(files), files=files, coverage='complete')
                store.put(snapshot)
                return self._preview(store, snapshot)
            finally:
                store.close()

    @staticmethod
    def _preview(store, snapshot, offset=0, limit=20):
        entries = []
        for file in snapshot.files[offset:offset + limit]:
            ownership = owners(store, snapshot.installation_id, file.relative_path, file.sha256)
            entries.append(dict(file.model_dump(mode='json'), lifecycle=classify(file.relative_path),
                                ownership='external' if not ownership['matching'] else 'managed', owners=ownership))
        return dict(snapshot_id=str(snapshot.snapshot_id), manifest_hash=snapshot.manifest_hash,
                    coverage=snapshot.coverage, scope=['Mods', 'Plugins', 'UserLibs'], files=entries,
                    total=len(snapshot.files), next_offset=offset + limit if offset + limit < len(snapshot.files) else None,
                    deployment_generation=None)

    def preview_page(self, snapshot_id, offset=0, limit=20):
        store = self._open()
        try:
            return self._preview(store, store.get('BaselineSnapshot', snapshot_id), offset, limit)
        finally:
            store.close()

    def adopt(self, snapshot_id, manifest_hash, paths, display_name, version_raw=None, package_id=None, *, cancel=None, commit=None):
        """The caller confirms this exact snapshot and explicit file list."""
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 200:
            raise ValueError('Package name required')
        if version_raw is not None and (not isinstance(version_raw, str) or not 1 <= len(version_raw) <= 200):
            raise ValueError('Invalid version')
        if not isinstance(paths, list) or not 1 <= len(paths) <= 128 or any(not isinstance(p, str) for p in paths) or len(paths) != len(set(p.casefold() for p in paths)):
            raise ValueError('Explicit unique file list required')
        with self._lock:
            store = self._open()
            try:
                snapshot = store.get('BaselineSnapshot', snapshot_id)
                if snapshot.coverage != 'complete' or snapshot.manifest_hash != manifest_hash:
                    raise ValueError('Confirmed baseline mismatch')
                if content_hash([dict(relative_path=f.relative_path, sha256=f.sha256, size=f.size) for f in snapshot.files]) != manifest_hash:
                    raise ValueError('Stored baseline hash mismatch')
                root = self._root(store, snapshot.installation_id)
                budget = Budget(cancel)
                selected = {f.relative_path: f for f in snapshot.files}
                files = []
                for path in paths:
                    if path not in selected or classify(path) != 'immutable':
                        raise ValueError('Only explicitly selected immutable files can be adopted')
                    digest, _, stamp = read_file(root, path, budget)
                    if digest != selected[path].sha256 or stamp[2] != selected[path].size:
                        raise ValueError('Selected file drift')
                    kind, relative = target(path)
                    files.append(dict(source_hash=selected[path].sha256, target_root_kind=kind,
                                      relative_path=relative, lifecycle='immutable'))
                files.sort(key=lambda f: (f['target_root_kind'], f['relative_path']))
                release_hash = content_hash(dict(version_raw=version_raw, files=files))
                # Repeating the same confirmation yields the same package/release.
                package_id = UUID(str(package_id)) if package_id else uuid5(snapshot.installation_id, 'local:' + display_name)
                release_id = uuid5(package_id, release_hash)
                repository = PackageRepository(store, self.library)
                artifacts = [repository.copy(root, selected[path], budget.check) for path in paths]
                self._root(store, snapshot.installation_id)
                budget.check()
                store.connection.execute('BEGIN IMMEDIATE')
                try:
                    try:
                        store.get('ModPackage', package_id)
                    except KeyError:
                        store.put(ModPackage(package_id=package_id, display_name=display_name,
                            source=dict(kind='user', reference='adoption:' + str(snapshot.snapshot_id), observed_at=datetime.now(timezone.utc)), trust='unknown'))
                    for artifact in artifacts:
                        store.put(artifact)
                    store.put(ModRelease(release_id=release_id, package_id=package_id, version_raw=version_raw, manifest_hash=release_hash))
                    for file in files:
                        entry_id = uuid5(release_id, file['target_root_kind'] + '/' + file['relative_path'])
                        store.put(FileEntry(file_entry_id=entry_id, release_id=release_id, ownership='shared_identical', **file))
                        if file['relative_path'].lower().endswith('.dll'):
                            store.put(Component(component_id=entry_id, release_id=release_id, artifact_hash=file['source_hash'],
                                file_name=Path(file['relative_path']).name, assembly_name=None, assembly_version=None,
                                melon_info=None, roles={'unknown'}, content_ids=[]))
                    result = repository.release(release_id, release_hash)
                    store.connection.execute('INSERT OR IGNORE INTO package_adoptions VALUES (?,?,?)',
                        (str(snapshot.snapshot_id), str(release_id), datetime.now(timezone.utc).isoformat()))
                    budget.check()
                    (commit or (lambda action: action()))(store.connection.commit)
                except BaseException:
                    store.connection.rollback()
                    raise
                return dict(result, snapshot_id=str(snapshot.snapshot_id), deployment_generation=None)
            finally:
                store.close()

    def list(self, offset=0, limit=20, *, check=lambda: None):
        store = self._open()
        try:
            releases = store.list('ModRelease', offset=offset, limit=limit)
            repository = PackageRepository(store, self.library)
            output = []
            for release in releases:
                check()
                try:
                    repository.release(release.release_id, check=check)
                    integrity = 'verified'
                except (ValueError, OSError, KeyError):
                    integrity = 'corrupt'
                sources = [dict(row) for row in store.connection.execute(
                    'SELECT snapshot_id,adopted_at FROM package_adoptions WHERE release_id=? ORDER BY adopted_at', (str(release.release_id),))]
                package = store.get('ModPackage', release.package_id)
                output.append(dict(release.model_dump(mode='json'), display_name=package.display_name,
                                   integrity=integrity, sources=sources[:20], source_count=len(sources)))
            return {'releases': output}
        finally:
            store.close()

    def resolve_release(self, release_id, manifest_hash):
        store = self._open()
        try:
            return PackageRepository(store, self.library).release(release_id, manifest_hash)
        finally:
            store.close()

    def detail(self, release_id, manifest_hash, offset=0, limit=20, *, check=lambda: None):
        """Verified library facts, never installation state or deployed ownership."""
        try:
            valid = (isinstance(release_id, str) and str(UUID(release_id)) == release_id
                     and isinstance(manifest_hash, str) and re.fullmatch('[a-f0-9]{64}', manifest_hash)
                     and type(offset) is int and 0 <= offset <= 2**53 - 1
                     and type(limit) is int and 1 <= limit <= 20)
        except (TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise PackageDetailError('PACKAGE_DETAIL_INVALID_REQUEST')
        deadline = time.monotonic() + 120
        def bounded_check():
            check()
            if time.monotonic() > deadline:
                raise PackageDetailError('PACKAGE_DETAIL_LIMIT')
        bounded_check()
        self.paths.check_owned_paths()
        reject_reparse(self.library)
        db = sqlite3.connect(self.paths.database.as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        store = _DetailReader(db)
        try:
            db.execute('BEGIN')
            try:
                release = store.get('ModRelease', release_id)
            except KeyError:
                raise PackageDetailError('PACKAGE_DETAIL_NOT_FOUND') from None
            if release.manifest_hash != manifest_hash:
                raise PackageDetailError('PACKAGE_DETAIL_LOCK_CHANGED')
            repository = PackageRepository(store, self.library)
            verified = repository.release(release_id, manifest_hash, check=bounded_check)
            package = store.get('ModPackage', release.package_id)
            component_rows = db.execute('SELECT payload FROM components WHERE release_id=? ORDER BY component_id LIMIT 129', (release_id,))
            components, used = [], 0
            for row in component_rows:
                bounded_check()
                used += len(row['payload'].encode('utf-8'))
                if len(components) >= 128 or used > 2 * 1024 * 1024:
                    raise PackageDetailError('PACKAGE_DETAIL_LIMIT')
                components.append(Component.model_validate_json(row['payload']))
            prefixes = {'mods': 'mods', 'plugins': 'plugins', 'user_data': 'userdata', 'user_libs': 'userlibs', 'game_root': ''}
            def target_key(file):
                value = '/'.join(value for value in (prefixes[file['target_root_kind']], file['relative_path']) if value)
                return unicodedata.normalize('NFC', value).casefold()
            files = sorted(verified['files'], key=lambda row: (target_key(row), row['file_entry_id']))
            wanted = {target_key(row) for row in files}
            ownership_rows = {key: [] for key in wanted}
            owner_count, owner_bytes = 0, 0
            for row in db.execute('SELECT payload FROM file_entries ORDER BY file_entry_id LIMIT 4001'):
                bounded_check()
                owner_count += 1
                owner_bytes += len(row['payload'].encode('utf-8'))
                if owner_count > 4000 or owner_bytes > 2 * 1024 * 1024:
                    raise PackageDetailError('PACKAGE_DETAIL_LIMIT')
                value = FileEntry.model_validate_json(row['payload']).model_dump(mode='json')
                key = target_key(value)
                if value['release_id'] != release_id and key in ownership_rows:
                    ownership_rows[key].append(value)
            owner_releases = {}
            for matches in ownership_rows.values():
                for match in matches:
                    identity = match['release_id']
                    if identity not in owner_releases:
                        if len(owner_releases) >= 200:
                            raise PackageDetailError('PACKAGE_DETAIL_LIMIT')
                        owner_release = store.get('ModRelease', identity)
                        owner_package = store.get('ModPackage', owner_release.package_id)
                        owner_releases[identity] = dict(release_id=identity, manifest_hash=owner_release.manifest_hash,
                            display_name=owner_package.display_name, version_raw=owner_release.version_raw)
            details = []
            for file in files:
                bounded_check()
                artifact = store.get('Artifact', file['source_hash'])
                roles = sorted({role for component in components
                    if component.artifact_hash == file['source_hash']
                    and component.file_name.casefold() == Path(file['relative_path']).name.casefold()
                    for role in component.roles}) or ['unknown']
                same = sorted({row['release_id'] for row in ownership_rows[target_key(file)] if row['source_hash'] == file['source_hash']})
                different = sorted({row['release_id'] for row in ownership_rows[target_key(file)] if row['source_hash'] != file['source_hash']})
                details.append({key: file[key] for key in ('file_entry_id', 'target_root_kind', 'relative_path', 'source_hash', 'lifecycle', 'ownership')}
                    | dict(size=artifact.size, roles=roles, shared_owner_releases=[owner_releases[key] for key in same],
                           conflicting_owner_releases=[owner_releases[key] for key in different]))
            identity = dict(release_id=release_id, manifest_hash=manifest_hash, display_name=package.display_name,
                version_raw=release.version_raw, roles=sorted({role for row in details for role in row['roles']}),
                files=details, ownership_scope='library_file_entries_only', integrity='verified', deployment_generation=None)
            output = identity | dict(detail_hash=content_hash(identity), files=details[offset:offset + limit], total=len(details),
                offset=offset, next_offset=offset + limit if offset + limit < len(details) else None,
                owner_integrity='indexed_claims_not_verified',
                warnings=['归属仅指包库文件清单；同目录不表示归属，也不表示已部署。其他版本的归属来自索引，不代表其库文件已校验。'])
            if len(json.dumps(output, ensure_ascii=False).encode('utf-8')) > 48 * 1024:
                raise PackageDetailError('PACKAGE_DETAIL_LIMIT')
            # Metadata projection must not outlive the verified selected bytes.
            repository.release(release_id, manifest_hash, check=bounded_check)
            return output
        except PackageDetailError:
            raise
        except (ValueError, OSError, KeyError) as exc:
            if isinstance(exc, InterruptedError):
                raise
            raise PackageDetailError('PACKAGE_DETAIL_CORRUPT') from None
        finally:
            db.close()
