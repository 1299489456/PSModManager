"""Persistent editor drafts; only explicit commit publishes the planner Profile."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from backend.app.paths import REFERENCE_ROOT, KNOWN_GAME_ROOT, overlaps
from backend.catalog.archive_policy import locked_path, canonical_name
from backend.contracts import Profile
from backend.games.probably_stolen.layout import signature
from backend.storage import Store
from backend.storage.package_repository import PackageRepository
from . import serialization as wire
from .serialization import ProfileError, require


class ProfileService:
    def __init__(self, paths):
        self.paths = paths
        self._sources = {}
        self._lock = threading.RLock()
        self._closed = False

    @contextmanager
    def _store(self, write=False):
        require(not self._closed, 'PROFILE_CLOSED')
        self.paths.check_owned_paths()
        store = Store(self.paths.database, library_root=self.paths.root / 'library')
        try:
            if write:
                store.connection.execute('BEGIN IMMEDIATE')
            yield store
            if write and store.connection.in_transaction:
                store.connection.commit()
        except BaseException:
            if store.connection.in_transaction:
                store.connection.rollback()
            raise
        finally:
            store.close()

    @staticmethod
    def _load(store, profile_id):
        wire.identifier(profile_id)
        row = store.connection.execute('SELECT * FROM profile_drafts WHERE profile_id=?', (profile_id,)).fetchone()
        require(row is not None, 'PROFILE_NOT_FOUND')
        return dict(row) | {'data': json.loads(row['payload'])}

    @staticmethod
    def _expected(row, revision):
        require(type(revision) is int and revision >= 0)
        require(row['revision'] == revision, 'PROFILE_REVISION_CONFLICT')

    @staticmethod
    def _resolved(store, item):
        try:
            release = store.get('ModRelease', item['release_id'])
            return release.manifest_hash == item['manifest_hash']
        except KeyError:
            return False

    def _state(self, store, row):
        current = row['data']['current']
        selections = []
        for item in current['selections']:
            label = None
            if self._resolved(store, item):
                release = store.get('ModRelease', item['release_id'])
                label = store.get('ModPackage', release.package_id).display_name
            selections.append(deepcopy(item) | {'resolved': label is not None, 'display_name': label})
        saved = store.connection.execute('SELECT revision FROM profiles WHERE profile_id=?', (row['profile_id'],)).fetchone()
        return dict(profile_id=row['profile_id'], installation_id=row['installation_id'],
                    name=current['name'], revision=row['revision'], committed_revision=row['committed_revision'],
                    profile_revision=saved['revision'] if saved else None,
                    selections=selections,
                    groups=list(current['groups']), can_undo=bool(row['data']['undo']), can_redo=bool(row['data']['redo']),
                    dirty=current != row['data']['committed'])

    @staticmethod
    def _activate(store, row):
        store.connection.execute('''INSERT INTO profile_editor_selection(installation_id,profile_id) VALUES (?,?)
            ON CONFLICT(installation_id) DO UPDATE SET profile_id=excluded.profile_id''',
            (row['installation_id'], row['profile_id']))

    def _create(self, store, installation_id, manifest):
        wire.identifier(installation_id)
        store.get('Installation', installation_id)
        wire.validate(manifest)
        require(store.connection.execute('SELECT count(*) FROM profile_drafts WHERE installation_id=?',
                                        (installation_id,)).fetchone()[0] < 100, 'PROFILE_CAPACITY')
        profile_id = str(uuid4())
        data = dict(current=manifest, committed=None, undo=[], redo=[])
        store.connection.execute('INSERT INTO profile_drafts VALUES (?,?,0,NULL,?)',
                                 (profile_id, installation_id, json.dumps(data, ensure_ascii=False)))
        row = self._load(store, profile_id)
        self._activate(store, row)
        return self._state(store, row)

    def list(self, installation_id):
        wire.identifier(installation_id)
        with self._store() as store:
            store.get('Installation', installation_id)
            rows = store.connection.execute('SELECT profile_id FROM profile_drafts WHERE installation_id=? ORDER BY rowid', (installation_id,)).fetchall()
            selected = store.connection.execute('SELECT profile_id FROM profile_editor_selection WHERE installation_id=?', (installation_id,)).fetchone()
            states = [self._state(store, self._load(store, row[0])) for row in rows]
            return {'profiles': [{key: state[key] for key in ('profile_id','installation_id','name','revision','committed_revision','dirty')} for state in states],
                    'selected_profile_id': selected[0] if selected else None}

    def create(self, installation_id, name):
        with self._store(write=True) as store:
            return self._create(store, installation_id, dict(schema_version=2, name=wire.text(name), groups=[], selections=[]))

    def get(self, profile_id):
        with self._store() as store:
            return self._state(store, self._load(store, profile_id))

    def select(self, profile_id):
        with self._store(write=True) as store:
            row = self._load(store, profile_id)
            self._activate(store, row)
            return self._state(store, row)

    def clone(self, profile_id, expected_revision, name):
        with self._store(write=True) as store:
            row = self._load(store, profile_id)
            self._expected(row, expected_revision)
            manifest = deepcopy(row['data']['current'])
            manifest['name'] = wire.text(name)
            return self._create(store, row['installation_id'], manifest)

    @staticmethod
    def _new_selection(store, request):
        wire.keys(request, ('release_id', 'manifest_hash', 'selected'))
        wire.identifier(request['release_id'])
        require(type(request['selected']) is bool)
        try:
            release = store.get('ModRelease', request['release_id'])
        except KeyError:
            raise ProfileError('PROFILE_UNRESOLVED') from None
        require(release.manifest_hash == request['manifest_hash'], 'PROFILE_UNRESOLVED')
        package = store.get('ModPackage', release.package_id)
        result = dict(request, version_raw=release.version_raw, group=None,
                      source={'kind': package.source.kind, 'observed_at': package.source.observed_at.isoformat().replace('+00:00', 'Z')})
        wire.selection(result)
        return result

    def edit(self, profile_id, expected_revision, action):
        require(type(action) is dict and isinstance(action.get('op'), str))
        op = action['op']
        fields = {'set_selection': ('op', 'selection'), 'set_enabled': ('op', 'release_ids', 'selected'),
                  'move_selections': ('op', 'release_ids', 'selected', 'before_release_id'),
                  'rename': ('op', 'name'), 'set_group': ('op', 'release_ids', 'group'), 'undo': ('op',), 'redo': ('op',)}
        require(op in fields)
        wire.keys(action, fields[op])
        with self._store(write=True) as store:
            row = self._load(store, profile_id)
            self._expected(row, expected_revision)
            data = row['data']
            old = deepcopy(data['current'])
            if op in ('undo', 'redo'):
                source, destination = (data['undo'], data['redo']) if op == 'undo' else (data['redo'], data['undo'])
                require(bool(source), 'PROFILE_HISTORY_EMPTY')
                destination.append(old)
                data['current'] = source.pop()
            else:
                current = data['current']
                if op == 'rename':
                    current['name'] = wire.text(action['name'])
                elif op == 'set_selection':
                    item = self._new_selection(store, action['selection'])
                    existing = next((value for value in current['selections'] if value['release_id'] == item['release_id']), None)
                    if existing is not None:
                        item['group'] = existing['group']
                        current['selections'][current['selections'].index(existing)] = item
                    else:
                        current['selections'].append(item)
                else:
                    ids = action['release_ids']
                    require(type(ids) is list and 0 < len(ids) <= wire.MAX_SELECTIONS)
                    for identifier in ids:
                        wire.identifier(identifier)
                    require(len(set(ids)) == len(ids))
                    selected = {item['release_id']: item for item in current['selections']}
                    require(all(identifier in selected for identifier in ids), 'PROFILE_UNRESOLVED')
                    if op in ('set_enabled', 'move_selections'):
                        require(type(action['selected']) is bool)
                        if op == 'move_selections':
                            # Move one visual block; release IDs are not Component
                            # display_order and never become loader ordering rules.
                            require(len({selected[identifier]['selected'] for identifier in ids}) == 1)
                            before = action['before_release_id']
                            if before is not None:
                                wire.identifier(before)
                                require(before in selected and before not in ids
                                        and selected[before]['selected'] == action['selected'])
                            moved = [item for item in current['selections'] if item['release_id'] in ids]
                            remaining = [item for item in current['selections'] if item['release_id'] not in ids]
                            if before is not None:
                                index = next(index for index, item in enumerate(remaining) if item['release_id'] == before)
                            else:
                                same_lane = [index for index, item in enumerate(remaining) if item['selected'] == action['selected']]
                                index = same_lane[-1] + 1 if same_lane else len(remaining)
                            current['selections'] = remaining[:index] + moved + remaining[index:]
                        for identifier in ids:
                            selected[identifier]['selected'] = action['selected']
                    else:
                        group = action['group']
                        if group is not None:
                            wire.text(group, 80)
                            if group not in current['groups']:
                                current['groups'].append(group)
                        for identifier in ids:
                            selected[identifier]['group'] = group
                wire.validate(current)
                if current == old:
                    return self._state(store, row)
                data['undo'].append(old)
                data['undo'] = data['undo'][-100:]
                data['redo'] = []
            changed = store.connection.execute('UPDATE profile_drafts SET revision=revision+1,payload=? WHERE profile_id=? AND revision=?',
                (json.dumps(data, ensure_ascii=False), profile_id, expected_revision))
            require(changed.rowcount == 1, 'PROFILE_REVISION_CONFLICT')
            return self._state(store, self._load(store, profile_id))

    def commit(self, profile_id, expected_revision, *, check=lambda: None, commit=None):
        with self._store(write=True) as store:
            store.verification_check = check
            row = self._load(store, profile_id)
            self._expected(row, expected_revision)
            result = self._publish(store, row, check=check, verify_bytes=True)
            check()
            (commit or (lambda action: action()))(store.connection.commit)
            return result

    def _publish(self, store, row, *, check, verify_bytes):
        """Publish one fixed manifest; don't verify the same bytes twice."""
        current, profile_id = row['data']['current'], row['profile_id']
        require(all(self._resolved(store, item) for item in current['selections']), 'PROFILE_UNRESOLVED')
        repository = PackageRepository(store, self.paths.root / 'library')
        checked = set()
        for item in current['selections']:
            check()
            try:
                release = repository.release(item['release_id'], item['manifest_hash'], check=check, verify_bytes=verify_bytes)
                if not verify_bytes:
                    # Capturing a list fixes immutable release references. Existing
                    # bytes are read again when exported/deployed, not while naming
                    # a scheme. Missing, linked or resized artifacts still fail now.
                    for file in release['files']:
                        if file['source_hash'] in checked:
                            continue
                        checked.add(file['source_hash'])
                        path = repository.ordinary_path(file['source_hash'])
                        require(path.is_file() and path.stat().st_size == store.get('Artifact', file['source_hash']).size,
                                'PROFILE_UNRESOLVED')
            except (ValueError, OSError) as error:
                if isinstance(error, InterruptedError):
                    raise
                raise ProfileError('PROFILE_UNRESOLVED') from None
        try:
            previous = store.get('Profile', profile_id)
        except KeyError:
            previous = None
        dto = Profile(profile_id=profile_id, installation_id=row['installation_id'],
            revision=previous.revision + 1 if previous else 0,
            selections=[{key: item[key] for key in ('release_id','manifest_hash','selected')} for item in current['selections']],
            config_overrides={}, display_order=[])
        check()
        previous_policy = store.verify_library_bytes
        store.verify_library_bytes = False  # release locks were validated above
        try:
            if row['data']['committed'] != current:
                if previous is None:
                    store.profiles.create(dto)
                else:
                    store.profiles.update(dto, expected_revision=previous.revision)
        finally:
            store.verify_library_bytes = previous_policy
        row['data']['committed'] = deepcopy(current)
        store.connection.execute('UPDATE profile_drafts SET committed_revision=?,payload=? WHERE profile_id=? AND revision=?',
            (row['revision'], json.dumps(row['data'], ensure_ascii=False), profile_id, row['revision']))
        return self._state(store, self._load(store, profile_id))

    def publish_capture(self, store, installation_id, manifest, profile_id=None, expected_revision=None, *, check=lambda: None):
        """Reuse the workspace's one transaction and its captured library bytes."""
        wire.validate(manifest)
        if profile_id:
            row = self._load(store, profile_id)
            self._expected(row, expected_revision)
            require(row['installation_id'] == installation_id, 'PROFILE_REVISION_CONFLICT')
            result = self._replace(store, row, manifest)
        else:
            result = self._create(store, installation_id, manifest)
        return self._publish(store, self._load(store, result['profile_id']), check=check, verify_bytes=False)

    def export(self, profile_id):
        with self._store() as store:
            manifest = deepcopy(self._load(store, profile_id)['data']['current'])
            wire.encode(manifest)
            return manifest

    def replace(self, profile_id, expected_revision, manifest):
        """Replace the complete draft from a confirmed workspace capture."""
        wire.validate(manifest)
        with self._store(write=True) as store:
            row = self._load(store, profile_id)
            self._expected(row, expected_revision)
            require(all(self._resolved(store, item) for item in manifest['selections']), 'PROFILE_UNRESOLVED')
            return self._replace(store, row, manifest)

    def _replace(self, store, row, manifest):
        data = row['data']
        data['undo'] = (data['undo'] + [deepcopy(data['current'])])[-100:]
        data['redo'] = []
        data['current'] = deepcopy(manifest)
        store.connection.execute('UPDATE profile_drafts SET revision=revision+1,payload=? WHERE profile_id=?',
            (json.dumps(data, ensure_ascii=False), row['profile_id']))
        self._activate(store, row)
        return self._state(store, self._load(store, row['profile_id']))

    def _expire(self):
        require(not self._closed, 'PROFILE_CLOSED')
        self._sources = {key: value for key, value in self._sources.items() if value['expires'] > time.monotonic()}

    def _source_bytes(self, path):
        with locked_path(path), path.open('rb') as reader:
            before = signature(path.stat())
            require(before[2] <= wire.MAX_BYTES, 'PROFILE_MANIFEST_INVALID')
            raw = reader.read(wire.MAX_BYTES + 1)
            require(len(raw) <= wire.MAX_BYTES and signature(path.stat()) == before, 'PROFILE_SOURCE_CHANGED')
            return raw, before

    def grant(self, selected):
        with self._lock:
            self._expire()
            if not selected:
                return {'source_handle': None, 'display_name': None}
            require(len(self._sources) < 8, 'PROFILE_CAPACITY')
            path = Path(selected)
            require(path.is_absolute() and not str(path).startswith(('\\\\', '//')), 'PROFILE_MANIFEST_UNSAFE')
            canonical_name('/'.join(path.parts[1:]))
            require(not any(overlaps(path, root) for root in (self.paths.root, REFERENCE_ROOT, KNOWN_GAME_ROOT)), 'PROFILE_MANIFEST_UNSAFE')
            with self._store() as store:
                require(not any(overlaps(path, Path(row[0])) for row in store.connection.execute('SELECT canonical_root FROM installations')), 'PROFILE_MANIFEST_UNSAFE')
            raw, stamp = self._source_bytes(path)
            handle = str(uuid4())
            self._sources[handle] = dict(path=path, signature=stamp, sha256=hashlib.sha256(raw).hexdigest(), expires=time.monotonic() + 300)
            return {'source_handle': handle, 'display_name': path.name}

    def _read_handle(self, handle):
        self._expire()
        require(handle in self._sources, 'PROFILE_SOURCE_EXPIRED')
        record = self._sources[handle]
        raw, stamp = self._source_bytes(record['path'])
        require(stamp == record['signature'] and hashlib.sha256(raw).hexdigest() == record['sha256'], 'PROFILE_SOURCE_CHANGED')
        return record, wire.decode(raw)

    def import_preview(self, source_handle, installation_id):
        with self._lock:
            record, manifest = self._read_handle(source_handle)
            wire.identifier(installation_id)
            preview_hash = hashlib.sha256((source_handle + installation_id + record['sha256'] + wire.digest(manifest)).encode()).hexdigest()
            record['preview_hash'] = preview_hash
            record['installation_id'] = installation_id
            with self._store() as store:
                store.get('Installation', installation_id)
                selections = []
                for item in manifest['selections']:
                    resolved = self._resolved(store, item)
                    label = None
                    if resolved:
                        release = store.get('ModRelease', item['release_id'])
                        label = store.get('ModPackage', release.package_id).display_name
                    selections.append(deepcopy(item) | {'resolved': resolved, 'display_name': label})
            return {'source_handle': source_handle, 'preview_hash': preview_hash, 'name': manifest['name'],
                    'groups': manifest['groups'], 'selections': selections, 'schema_version': 2}

    def import_commit(self, source_handle, preview_hash, installation_id, name):
        with self._lock:
            self._expire()
            require(source_handle in self._sources, 'PROFILE_SOURCE_EXPIRED')
            with locked_path(self._sources[source_handle]['path']):
                record, manifest = self._read_handle(source_handle)
                require(isinstance(preview_hash, str) and record.get('preview_hash') == preview_hash
                        and record.get('installation_id') == installation_id, 'PROFILE_PREVIEW_CHANGED')
                manifest['name'] = wire.text(name)
                with self._store(write=True) as store:
                    result = self._create(store, installation_id, manifest)
            del self._sources[source_handle]
            return result

    def close(self):
        with self._lock:
            self._sources.clear()
            self._closed = True
