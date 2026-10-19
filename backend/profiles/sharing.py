"""Portable scheme bundles: fixed release locks and content-addressed Mod bytes.

The ZIP is data, not an installer. Nothing is extracted under archive names;
publication uses the existing private library and deployment uses workspace.save.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from uuid import UUID, uuid4, uuid5
import zipfile

from backend.app.paths import KNOWN_GAME_ROOT, REFERENCE_ROOT, overlaps, reject_reparse
from backend.catalog import archive_policy as policy
from backend.catalog.versions import library_attachment
from backend.catalog.file_policy import credential_file, scoped_supplement
from backend.contracts import Artifact, Component, FileEntry, ModPackage, ModRelease
from backend.deployments.policy import PlanRejected
from backend.storage import Store
from backend.storage.package_repository import PackageRepository, content_hash
from . import serialization as wire

MAX_FILES = 4000
MAX_BYTES = 1024**3
MAX_MANIFEST = 8*1024**2
HASH = re.compile(r'[a-f0-9]{64}')
PREFIX = {'mods': 'Mods', 'plugins': 'Plugins', 'user_libs': 'UserLibs'}


class BundleFileError(wire.ProfileError):
    def __init__(self, file, reason):
        super().__init__('PROFILE_BUNDLE_UNSAFE')
        self.issues = [dict(relative_path=file.get('relative_path', ''), reason=reason)]


def safe_file(file):
    try:
        policy.canonical_name(file['relative_path'])
        policy.check_content_name(file['relative_path'])
    except policy.ImportRejected as error:
        raise BundleFileError(file, {'IMPORT_SCRIPT_REFUSED': 'script', 'IMPORT_NESTED_ARCHIVE': 'nested_archive'}.get(error.code, 'unsafe_path')) from None
    if credential_file(file['relative_path']):
        raise BundleFileError(file, 'credential_file')
    if not library_attachment(file) and not scoped_supplement(file) and (file['target_root_kind'] not in PREFIX or file['lifecycle'] != 'immutable'):
        raise BundleFileError(file, 'game_or_unsupported_target' if file['target_root_kind'] not in PREFIX else 'unsupported_lifecycle')


def destination(paths, selected, suffix):
    target = Path(selected)
    wire.require(target.is_absolute() and not str(target).startswith(('\\\\', '//'))
                 and target.suffix.lower() == suffix, 'PROFILE_EXPORT_REFUSED')
    policy.canonical_name('/'.join(target.parts[1:]))
    forbidden = [paths.root, REFERENCE_ROOT, KNOWN_GAME_ROOT]
    store = Store(paths.database)
    try:
        forbidden.extend(Path(row[0]) for row in store.connection.execute('SELECT canonical_root FROM installations'))
    finally:
        store.close()
    wire.require(not any(overlaps(target, root) for root in forbidden), 'PROFILE_EXPORT_REFUSED')
    return target


class ProfileSharingService:
    def __init__(self, paths, profiles, workspace):
        self.paths, self.profiles, self.workspace = paths, profiles, workspace
        self._lock = threading.RLock()
        self._sources = {}

    def capture(self, installation_id, name, profile_id=None, expected_revision=None, *, check=lambda: None,
                commit=None, progress=lambda *args: None):
        name = wire.text(name)
        if profile_id:
            previous = self.profiles.get(profile_id)
            wire.require(previous['installation_id'] == installation_id and previous['revision'] == expected_revision,
                         'PROFILE_REVISION_CONFLICT')
        workspace = self.workspace
        selections = []
        with workspace.plans._lock, workspace.plans._store(check, verify_library_bytes=False) as store:
            with workspace.plans.guards._installation(installation_id, check=check) as (installation, _):
                view = workspace._view(store, installation, check)
                total = sum(len(item['_files']) for item in view['items'] if item['source'] == 'external')
                completed = 0
                for item in view['items']:
                    if item['state'] == 'not_installed':
                        continue
                    wire.require(item['manageable'] and item['state'] != 'drifted', 'PROFILE_CAPTURE_UNSAFE')
                    def report(index, *_):
                        progress(completed+index+1, total, 'capturing', 'PROFILE_CAPTURE_COPYING', dict(mod=item['display_name']))
                    release = workspace._adopt(store, installation, item, check, report, ordinary=True) if item['source'] == 'external' else item
                    if item['source'] == 'external':
                        completed += len(item['_files'])
                    selections.append(self.profiles._new_selection(store, dict(release_id=release['release_id'],
                        manifest_hash=release['manifest_hash'], selected=item['id'] in view['enabled_ids'])))
                manifest = dict(schema_version=2, name=name, groups=[], selections=selections)
                result = self.profiles.publish_capture(store, installation_id, manifest, profile_id, expected_revision, check=check)
                check()
                (commit or (lambda action: action()))(store.connection.commit)
                return result

    def apply(self, profile_id, expected_revision, snapshot_id, idempotency_key, *, check=lambda: None,
              commit=None, progress=lambda *args: None):
        profile = self.profiles.get(profile_id)
        wire.require(profile['revision'] == expected_revision, 'PROFILE_REVISION_CONFLICT')
        wire.require(not profile['dirty'], 'PROFILE_UNSAVED')
        wire.require(all(item['resolved'] for item in profile['selections']), 'PROFILE_UNRESOLVED')
        view = self.workspace.list(profile['installation_id'], check=check)
        wire.require(view['snapshot_id'] == snapshot_id, 'PROFILE_PREVIEW_CHANGED')
        by_release = {item['release_id']: item for item in view['items']}
        enabled = []
        for item in profile['selections']:
            if item['selected']:
                wire.require(item['release_id'] in by_release, 'PROFILE_UNRESOLVED')
                enabled.append(by_release[item['release_id']]['id'])
        return self.workspace.save(dict(installation_id=profile['installation_id'], snapshot_id=snapshot_id,
            enabled_ids=enabled, idempotency_key=idempotency_key), check=check, commit=commit, progress=progress)

    def export_bundle(self, profile_id, selected, *, check=lambda: None):
        if not selected:
            return dict(saved=False, cancelled=True)
        target = destination(self.paths, selected, '.zip')
        manifest = self.profiles.export(profile_id)
        wire.validate(manifest)
        self.paths.check_owned_paths()
        store = Store(self.paths.database, library_root=self.paths.root/'library')
        repository = PackageRepository(store, self.paths.root/'library')
        releases, blobs, count, size = [], {}, 0, 0
        try:
            for selection in manifest['selections']:
                check()
                verified = repository.release(selection['release_id'], selection['manifest_hash'], check=check, verify_bytes=False)
                release = store.get('ModRelease', selection['release_id'])
                package = store.get('ModPackage', release.package_id)
                files = []
                for file in verified['files']:
                    try:
                        safe_file(file)
                    except BundleFileError as error:
                        error.issues[0]['mod'] = package.display_name
                        raise
                    artifact = store.get('Artifact', file['source_hash'])
                    files.append({key: file[key] for key in ('source_hash', 'target_root_kind', 'relative_path', 'lifecycle')})
                    if artifact.sha256 not in blobs:
                        blobs[artifact.sha256] = artifact.size
                        size += artifact.size
                    count += 1
                releases.append(dict(release_id=str(release.release_id), package_id=str(package.package_id),
                    display_name=package.display_name, version_raw=release.version_raw, manifest_hash=release.manifest_hash,
                    files=files))
            wire.require(count <= MAX_FILES and size <= MAX_BYTES, 'PROFILE_BUNDLE_LIMIT')
            bundle = dict(format='psmm-modpack', schema_version=1, game='probably_stolen', profile=manifest,
                          releases=releases, artifacts=blobs)
            payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')
            wire.require(len(payload) <= MAX_MANIFEST, 'PROFILE_BUNDLE_LIMIT')
            with policy.locked_path(target.parent, directory=True):
                try:
                    # ZIP_STORED makes large, highly repetitive assets safe for
                    # the same bounded reader; no self-generated ratio bombs.
                    with target.open('xb') as output:
                        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
                            archive.writestr('modpack.json', payload)
                            for digest, length in blobs.items():
                                check()
                                with policy.locked_path(repository.path(digest)), repository.path(digest).open('rb') as reader, archive.open('blobs/'+digest, 'w') as writer:
                                    checksum, copied = hashlib.sha256(), 0
                                    for block in iter(lambda: reader.read(policy.CHUNK), b''):
                                        check(); checksum.update(block); copied += len(block)
                                        wire.require(copied <= length, 'PROFILE_SOURCE_CHANGED')
                                        writer.write(block)
                                    wire.require(copied == length and checksum.hexdigest() == digest, 'PROFILE_SOURCE_CHANGED')
                        output.flush(); os.fsync(output.fileno())
                except BaseException:
                    # Remove only the output that this call exclusively created.
                    if 'output' in locals() and output.closed:
                        target.unlink(missing_ok=True)
                    raise
            return dict(saved=True, cancelled=False, mod_count=len(releases), file_count=count, bytes=size)
        finally:
            store.close()

    def grant(self, selected):
        if not selected:
            return dict(source_handle=None, display_name=None)
        path = Path(selected)
        wire.require(path.is_absolute() and path.suffix.lower() in {'.zip', '.json'}
                     and not str(path).startswith(('\\\\', '//')), 'PROFILE_BUNDLE_UNSAFE')
        # Share files are selected explicitly, including the user's download
        # folder. Sources can never be the manager's own data or a game tree.
        destination(self.paths, path, path.suffix.lower())
        with self._lock, policy.locked_path(path):
            self._expire()
            wire.require(len(self._sources) < 8, 'PROFILE_CAPACITY')
            handle = str(uuid4())
            self._sources[handle] = dict(path=path, stamp=self._stamp(path), expires=time.monotonic()+300)
            return dict(source_handle=handle, display_name=path.name)

    def _expire(self):
        self._sources = {key: value for key, value in self._sources.items() if value['expires'] > time.monotonic()}

    @staticmethod
    def _stamp(path):
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _read(self, record, check, consume=None):
        path = record['path']
        if path.suffix.lower() == '.json':
            wire.require(path.stat().st_size <= wire.MAX_BYTES, 'PROFILE_MANIFEST_INVALID')
            return wire.decode(path.read_bytes()), []
        with path.open('rb') as stream:
            archive, entries = policy.zip_entries(stream, max_archive_bytes=MAX_BYTES+MAX_MANIFEST+2*1024**2,
                max_total_bytes=MAX_BYTES+MAX_MANIFEST, max_file_bytes=512*1024**2, max_files=MAX_FILES+1)
            try:
                by_name = dict((name, entry) for entry, name in entries)
                wire.require('modpack.json' in by_name and by_name['modpack.json'].file_size <= MAX_MANIFEST, 'PROFILE_BUNDLE_INVALID')
                budget = policy.Budget(check)
                policy.validate_member(archive, by_name['modpack.json'], budget)
                try:
                    bundle = json.loads(archive.read(by_name['modpack.json']).decode('utf8'), object_pairs_hook=wire._pairs)
                except (UnicodeError, ValueError):
                    raise wire.ProfileError('PROFILE_BUNDLE_INVALID') from None
                self._validate_bundle(bundle)
                wire.require(set(by_name) == {'modpack.json'} | {'blobs/'+digest for digest in bundle['artifacts']}, 'PROFILE_BUNDLE_INVALID')
                for digest, length in bundle['artifacts'].items():
                    check()
                    entry = by_name['blobs/'+digest]
                    wire.require(entry.file_size == length, 'PROFILE_BUNDLE_TAMPERED')
                    policy.validate_member(archive, entry, budget)
                    checksum, copied = hashlib.sha256(), 0
                    with archive.open(entry) as reader:
                        writer = consume(digest, length) if consume else None
                        try:
                            for block in iter(lambda: reader.read(policy.CHUNK), b''):
                                check(); checksum.update(block); copied += len(block)
                                if writer:
                                    writer.write(block)
                        finally:
                            if writer:
                                writer.close()
                    wire.require(copied == length and checksum.hexdigest() == digest, 'PROFILE_BUNDLE_TAMPERED')
                return bundle['profile'], bundle['releases']
            finally:
                archive.close()

    @staticmethod
    def _validate_bundle(bundle):
        wire.keys(bundle, ('format', 'schema_version', 'game', 'profile', 'releases', 'artifacts'))
        wire.require(bundle['format'] == 'psmm-modpack' and bundle['schema_version'] == 1 and bundle['game'] == 'probably_stolen', 'PROFILE_BUNDLE_INVALID')
        wire.validate(bundle['profile'])
        wire.require(type(bundle['releases']) is list and len(bundle['releases']) <= wire.MAX_SELECTIONS
            and type(bundle['artifacts']) is dict and len(bundle['artifacts']) <= MAX_FILES, 'PROFILE_BUNDLE_LIMIT')
        for digest, size in bundle['artifacts'].items():
            wire.require(HASH.fullmatch(digest) and type(size) is int and 0 <= size <= 512*1024**2, 'PROFILE_BUNDLE_INVALID')
        wire.require(sum(bundle['artifacts'].values()) <= MAX_BYTES, 'PROFILE_BUNDLE_LIMIT')
        releases, used, count = {}, set(), 0
        for release in bundle['releases']:
            wire.keys(release, ('release_id', 'package_id', 'display_name', 'version_raw', 'manifest_hash', 'files'))
            wire.identifier(release['release_id']); wire.identifier(release['package_id'])
            wire.require(isinstance(release['display_name'], str) and 0 < len(release['display_name']) <= 200, 'PROFILE_BUNDLE_INVALID')
            if release['version_raw'] is not None:
                wire.version(release['version_raw'])
            wire.require(type(release['files']) is list and 0 < len(release['files']) <= MAX_FILES, 'PROFILE_BUNDLE_LIMIT')
            targets = set()
            for file in release['files']:
                wire.keys(file, ('source_hash', 'target_root_kind', 'relative_path', 'lifecycle'))
                safe_file(file)
                key = file['target_root_kind']+'/'+file['relative_path'].casefold()
                wire.require(key not in targets and file['source_hash'] in bundle['artifacts'], 'PROFILE_BUNDLE_INVALID')
                targets.add(key); used.add(file['source_hash']); count += 1
            ordered = sorted(release['files'], key=lambda file: (file['target_root_kind'], file['relative_path']))
            wire.require(content_hash(dict(version_raw=release['version_raw'], files=ordered)) == release['manifest_hash'], 'PROFILE_BUNDLE_TAMPERED')
            wire.require(release['release_id'] not in releases, 'PROFILE_BUNDLE_INVALID')
            releases[release['release_id']] = release
        wire.require(count <= MAX_FILES and used == set(bundle['artifacts']), 'PROFILE_BUNDLE_LIMIT')
        wire.require(set(releases) == {item['release_id'] for item in bundle['profile']['selections']}, 'PROFILE_BUNDLE_INVALID')
        for item in bundle['profile']['selections']:
            release = releases[item['release_id']]
            wire.require(item['manifest_hash'] == release['manifest_hash'] and item['version_raw'] == release['version_raw'], 'PROFILE_BUNDLE_TAMPERED')

    def preview(self, source_handle, installation_id, *, check=lambda: None):
        with self._lock:
            self._expire()
            wire.require(source_handle in self._sources, 'PROFILE_SOURCE_EXPIRED')
            record = self._sources[source_handle]
            with policy.locked_path(record['path']):
                stamp = self._stamp(record['path'])
                wire.require(record['stamp'] == stamp, 'PROFILE_SOURCE_CHANGED')
                manifest, releases = self._read(record, check)
                wire.require(stamp == self._stamp(record['path']), 'PROFILE_SOURCE_CHANGED')
            with self.profiles._store() as store:
                store.get('Installation', installation_id)
                names = {row['release_id']: row['display_name'] for row in releases}
                selections = [deepcopy(item) | dict(resolved=self.profiles._resolved(store, item) or item['release_id'] in names,
                    included=item['release_id'] in names, display_name=names.get(item['release_id'])) for item in manifest['selections']]
            preview_hash = content_hash(dict(manifest=manifest, releases=releases, installation=installation_id, stamp=stamp))
            record.update(preview_hash=preview_hash, installation_id=installation_id, stamp=stamp,
                content_digest=content_hash(dict(manifest=manifest, releases=releases)))
            return dict(source_handle=source_handle, preview_hash=preview_hash, name=manifest['name'], groups=manifest['groups'],
                selections=selections, includes_mods=bool(releases), total_mods=len(selections),
                missing_count=sum(not row['resolved'] for row in selections), schema_version=2)

    def import_bundle(self, source_handle, preview_hash, installation_id, name, *, check=lambda: None):
        with self._lock:
            self._expire()
            wire.require(source_handle in self._sources, 'PROFILE_SOURCE_EXPIRED')
            record = self._sources[source_handle]
            wire.require(record.get('preview_hash') == preview_hash and record.get('installation_id') == installation_id,
                         'PROFILE_PREVIEW_CHANGED')
            parent = self.paths.root/'sharing'
            reject_reparse(parent); parent.mkdir(exist_ok=True)
            stage = parent/uuid4().hex; stage.mkdir()
            try:
                with policy.locked_path(record['path']), policy.locked_path(stage, directory=True):
                    wire.require(self._stamp(record['path']) == record['stamp'], 'PROFILE_SOURCE_CHANGED')
                    manifest, releases = self._read(record, check, lambda digest, _: (stage/digest).open('xb'))
                    wire.require(content_hash(dict(manifest=manifest, releases=releases)) == record['content_digest'], 'PROFILE_SOURCE_CHANGED')
                    manifest['name'] = wire.text(name)
                    with self.profiles._store(write=True) as store:
                        repository = PackageRepository(store, self.paths.root/'library')
                        for release in releases:
                            check()
                            package_id, release_id = UUID(release['package_id']), UUID(release['release_id'])
                            try:
                                existing = store.get('ModRelease', release_id)
                                wire.require(existing.manifest_hash == release['manifest_hash'] and str(existing.package_id) == str(package_id), 'PROFILE_BUNDLE_CONFLICT')
                            except KeyError:
                                pass
                            try:
                                store.get('ModPackage', package_id)
                            except KeyError:
                                store.put(ModPackage(package_id=package_id, display_name=release['display_name'], trust='unknown',
                                    source=dict(kind='user', reference='shared-modpack', observed_at=datetime.now(timezone.utc))))
                            for file in release['files']:
                                digest = file['source_hash']
                                artifact = repository.capture_ordinary(stage/digest, check)
                                wire.require(artifact.sha256 == digest, 'PROFILE_BUNDLE_TAMPERED')
                                store.put(artifact)
                            store.put(ModRelease(**{key: release[key] for key in ('release_id', 'package_id', 'version_raw', 'manifest_hash')}))
                            for file in release['files']:
                                store.put(FileEntry(file_entry_id=uuid5(release_id, file['target_root_kind']+'/'+file['relative_path']),
                                    release_id=release_id, ownership='unknown' if file['lifecycle'] == 'unowned' else 'shared_identical', **file))
                            repository.release(release_id, release['manifest_hash'], check=check)
                        result = self.profiles._create(store, installation_id, manifest)
                # JSON-only imports keep unresolved drafts editable; a complete
                # bundle is immediately saved and ready for one-click apply.
                if all(item['resolved'] for item in result['selections']):
                    result = self.profiles.commit(result['profile_id'], result['revision'], check=check)
                del self._sources[source_handle]
                return result
            finally:
                # stage is an owned flat UUID directory, never archive paths.
                with policy.locked_path(parent, directory=True):
                    for child in stage.iterdir():
                        reject_reparse(child); child.unlink()
                    stage.rmdir()

    def close(self):
        with self._lock:
            self._sources.clear()
