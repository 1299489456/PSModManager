"""Authorized discovery and durable Installation selection; no game writes."""
import json
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4

from backend.contracts import Installation, RuntimeFingerprint
from backend.runtime.events import RuntimePolicyError, json_copy
from backend.storage import Store
from backend.storage.repositories import encoded


class InstallationService:
    def __init__(self, paths, *, lifetime=300, allow_manual_manifest_probe=True):
        if type(allow_manual_manifest_probe) is not bool:
            raise ValueError('Invalid manual manifest policy')
        # Host resource policy, never a caller-supplied bridge permission bypass.
        # Disabled probing leaves Steam channel/build unknown for manual roots.
        self.allow_manual_manifest_probe = allow_manual_manifest_probe
        self.paths = paths
        self._lock = threading.RLock()
        self._sources = {}
        self._candidates = {}
        self._selected = None
        self._active = False
        self._active_job = None
        self._operation = None
        self._lifetime = lifetime

    def _expire(self):
        now = time.monotonic()
        self._sources = {key: row for key, row in self._sources.items() if row['expires'] > now}
        self._candidates = {key: row for key, row in self._candidates.items() if row['expires'] > now}

    def grant(self, kind, path):
        """Only the host's native folder picker calls this, never JS paths."""
        if kind not in ('steam', 'manual'):
            raise ValueError('Invalid installation source kind')
        if not path:
            return {'source_handle': None}
        value = Path(path)
        if not value.is_absolute():
            raise ValueError('Expected an absolute selected directory')
        with self._lock:
            self._expire()
            if len(self._sources) >= 16:
                raise RuntimePolicyError('CAPACITY_EXCEEDED')
            handle = str(uuid4())
            self._sources[handle] = dict(kind=kind, root=str(value), expires=time.monotonic() + self._lifetime)
            return {'source_handle': handle}

    def _start(self, jobs, request_id, operation):
        with self._lock:
            if self._active and self._active_job is not None:
                manager, identity = self._active_job
                if manager.snapshot(identity)['state'] in ('succeeded', 'failed', 'cancelled'):
                    self._active = False
            if self._active:
                raise RuntimePolicyError('INSTALLATION_BUSY')
            self._active = True
            operation_token = object()
            self._operation = operation_token
            def run(task):
                try:
                    return operation(task)
                finally:
                    with self._lock:
                        if self._operation is operation_token:
                            self._active = False
            try:
                # Publish ownership before a fast worker can release it.
                snapshot = jobs.start(request_id, run)
                self._active_job = (jobs, snapshot['task_id'])
                return snapshot
            except BaseException:
                if self._operation is operation_token:
                    self._active = False
                raise

    def discover(self, jobs, request_id, handles):
        with self._lock:
            self._expire()
            if not handles or len(handles) > 8 or any(handle not in self._sources for handle in handles):
                raise RuntimePolicyError('SOURCE_EXPIRED')
            sources = [dict(self._sources[handle]) for handle in handles]
        def scan(task):
            from backend.games.probably_stolen.discovery import discover, DiscoveryCancelled
            task.check_cancelled()
            task.progress(0, None, 'discovery', 'INSTALLATION_DISCOVERING')
            try:
                candidates = discover([s['root'] for s in sources if s['kind'] == 'steam'],
                                      [s['root'] for s in sources if s['kind'] == 'manual'],
                                      data_root=self.paths.root, cancel=lambda: task.cancelled,
                                      allow_manual_manifest_probe=self.allow_manual_manifest_probe, lightweight=True)
            except DiscoveryCancelled:
                task.check_cancelled()
                raise
            task.check_cancelled()
            result = []
            entries = {}
            for candidate in candidates:
                identity = str(uuid4())
                result.append({**candidate, 'candidate_id': identity})
                entries[identity] = {'candidate': candidate, 'sources': sources, 'expires': time.monotonic() + self._lifetime}
            value = {'candidates': result, 'selected_installation_id': self._selected}
            json_copy(value)  # Validate bounded output before publishing handles.
            with self._lock:
                self._candidates = entries
            task.progress(len(result), len(result), 'complete', 'INSTALLATION_DISCOVERED')
            return value
        return self._start(jobs, request_id, scan)

    def auto_discover(self, jobs, request_id):
        """Find Steam installations and reuse a unique usable saved directory."""
        def scan(task):
            from backend.games.probably_stolen.discovery import automatic_steam_roots, discover
            task.check_cancelled()
            task.progress(0, None, 'discovery', 'INSTALLATION_DISCOVERING')
            saved = self.list()['installations']
            steam = automatic_steam_roots()
            manual = [row['canonical_root'] for row in saved][:max(0, 16 - len(steam))]
            candidates = discover(steam, manual, data_root=self.paths.root, cancel=lambda: task.cancelled,
                                  allow_manual_manifest_probe=self.allow_manual_manifest_probe, lightweight=True)
            task.check_cancelled()
            result, entries = [], {}
            for candidate in candidates:
                identity = str(uuid4())
                result.append({**candidate, 'candidate_id': identity})
                sources = [{'kind': 'manual', 'root': candidate['canonical_root']}]
                entries[identity] = {'candidate': candidate, 'sources': sources, 'expires': time.monotonic() + self._lifetime}
            with self._lock:
                self._candidates = entries
            available = [candidate for candidate in candidates if candidate['valid']]
            value = {'candidates': result, 'selected_installation_id': self._selected}
            if len(available) == 1:
                candidate = available[0]
                value.update(self._select(task, candidate, [{'kind': 'manual', 'root': candidate['canonical_root']}]))
                value['selected_installation_id'] = self._selected
            else:
                task.progress(len(result), len(result), 'complete', 'INSTALLATION_DISCOVERED')
            json_copy(value)
            return value
        return self._start(jobs, request_id, scan)

    @staticmethod
    def _signature(candidate):
        return tuple(candidate.get(key) for key in ('canonical_root', 'file_identity'))

    def select(self, jobs, request_id, candidate_id):
        with self._lock:
            self._expire()
            record = self._candidates.get(candidate_id)
            if record is None:
                raise RuntimePolicyError('CANDIDATE_EXPIRED')
            if not record['candidate']['valid']:
                raise RuntimePolicyError('INSTALLATION_INCOMPLETE')
            expected = record['candidate']
            sources = record['sources']
        return self._start(jobs, request_id, lambda task: self._select(task, expected, sources))

    def _select(self, task, expected, sources):
        from backend.games.probably_stolen.discovery import discover, DiscoveryCancelled
        task.check_cancelled()
        task.progress(0, 2, 'directory', 'INSTALLATION_REVALIDATING')
        try:
            # Check the selected directory only. Steam updates and incomplete
            # version metadata do not invalidate the user's directory choice.
            candidates = discover([], [expected['canonical_root']],
                                  data_root=self.paths.root, cancel=lambda: task.cancelled,
                                  allow_manual_manifest_probe=False, lightweight=True)
        except DiscoveryCancelled:
            task.check_cancelled()
            raise
        fresh = next((candidate for candidate in candidates if candidate.get('file_identity') == expected['file_identity']), None)
        if fresh is None or not fresh['valid'] or self._signature(fresh) != self._signature(expected):
            return {'installation': None, 'fingerprint': None, 'error_code': 'INSTALLATION_CHANGED'}
        for key in ('channel', 'app_id', 'build_id'):
            fresh[key] = expected.get(key)
        task.check_cancelled()
        task.progress(1, 2, 'register', 'INSTALLATION_REGISTERING')
        self.paths.check_owned_paths()
        store = Store(self.paths.database)
        try:
            fingerprint = RuntimeFingerprint.model_validate(fresh['fingerprint'])
            fingerprint.source.reference = json.dumps({
                'format': 'psmm.installation-source.v1',
                'discovery_sources': [{'kind': 'manual', 'root': fresh['canonical_root']}],
                'file_facts': fingerprint.source.reference,
            }, ensure_ascii=False, separators=(',', ':'))
            def register():
                with store.transaction():
                    existing = store.connection.execute('SELECT payload FROM installations WHERE file_identity=?', (fresh['file_identity'],)).fetchone()
                    old = Installation.model_validate_json(existing['payload']) if existing else None
                    stored_fingerprint = store.get('RuntimeFingerprint', old.fingerprint_id) if old else fingerprint
                    installation = Installation(installation_id=old.installation_id if old else uuid4(),
                        canonical_root=fresh['canonical_root'], file_identity=fresh['file_identity'],
                        channel=old.channel if old and fresh['channel'] == 'unknown' else fresh['channel'],
                        app_id=fresh['app_id'] or (old.app_id if old else None),
                        build_id=fresh['build_id'] or (old.build_id if old else None),
                        fingerprint_id=stored_fingerprint.fingerprint_id)
                    if old:
                        # Reusing a directory must not create a new fingerprint
                        # revision or invalidate the user's existing Mod state.
                        store.connection.execute('UPDATE installations SET canonical_root=?,fingerprint_id=?,payload=? WHERE installation_id=?',
                            (installation.canonical_root, str(installation.fingerprint_id), encoded(installation), str(installation.installation_id)))
                    else:
                        store.inventory.add(fingerprint)
                        store.inventory.add(installation)
                return installation, stored_fingerprint
            installation, fingerprint = task.commit(register)
            with self._lock:
                self._selected = str(installation.installation_id)
            task.progress(2, 2, 'complete', 'INSTALLATION_SELECTED')
            return {'installation': installation.model_dump(mode='json'), 'fingerprint': fingerprint.model_dump(mode='json')}
        finally:
            store.close()

    def list(self):
        self.paths.check_owned_paths()
        store = Store(self.paths.database)
        try:
            rows = [item.model_dump(mode='json') for item in store.inventory.list('Installation', limit=200)]
            return {'installations': rows, 'selected_installation_id': self._selected}
        finally:
            store.close()

    def use(self, jobs, request_id, installation_id):
        self.paths.check_owned_paths()
        store = Store(self.paths.database)
        try:
            try:
                installation = store.get('Installation', installation_id)
                fingerprint = store.get('RuntimeFingerprint', installation.fingerprint_id)
            except KeyError:
                raise RuntimePolicyError('INSTALLATION_NOT_FOUND') from None
        finally:
            store.close()
        # A saved ID authorizes only its registered directory; no binary hashing.
        expected = {**installation.model_dump(mode='json'), 'fingerprint': fingerprint.model_dump(mode='json')}
        sources = [{'kind': 'manual', 'root': installation.canonical_root}]
        return self._start(jobs, request_id, lambda task: self._select(task, expected, sources))

    @staticmethod
    def _saved_sources(reference, canonical_root):
        from backend.games.probably_stolen.layout import approved_root
        try:
            value = json.loads(reference)
        except (ValueError, TypeError):
            value = None
        if not isinstance(value, dict) or value.get('format') != 'psmm.installation-source.v1':
            return [{'kind': 'manual', 'root': canonical_root}]
        sources = value.get('discovery_sources')
        if set(value) != {'format', 'discovery_sources', 'file_facts'} or not isinstance(value['file_facts'], str) or not value['file_facts'] or not isinstance(sources, list) or not 1 <= len(sources) <= 8:
            raise RuntimePolicyError('INVALID_INSTALLATION_SOURCE')
        for source in sources:
            if not isinstance(source, dict) or set(source) != {'kind', 'root'} or source['kind'] not in ('manual', 'steam') or not isinstance(source['root'], str):
                raise RuntimePolicyError('INVALID_INSTALLATION_SOURCE')
            try:
                approved_root(source['root'])
            except (ValueError, OSError):
                raise RuntimePolicyError('INVALID_INSTALLATION_SOURCE') from None
        return sources
