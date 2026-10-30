"""Opt-in manager updates; Steam's own subscription downloads stay independent."""
from contextlib import contextmanager
import threading


class WorkshopUpdatePolicy:
    def __init__(self, context, *, interval=15):
        self.context = context
        self.interval = interval
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._thread = None
        self._operation = None
        self._paused = None
        self._permission = threading.Event()
        if self.settings()['auto_update']:
            self._permission.set()

    def settings(self):
        with self._lock:
            config = self.context.preferences.read_config().get('workshop', {})
            return dict(auto_update=config.get('auto_update') is True, source='manager',
                        steam_managed_updates=True, paused_reason=self._paused)

    def set(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Invalid update policy')
        with self._lock:
            self.context.preferences.update('workshop', {'auto_update': enabled})
            self._permission.clear()
            self._permission = threading.Event()
            if enabled:
                self._permission.set()
            self._operation = None
            self._paused = None
            self._wake.set()
            return self.settings()

    def start(self):
        with self._lock:
            if self._thread is not None or self._closed.is_set():
                return
            self._thread = threading.Thread(target=self._run, name='PSModManager-auto-update', daemon=True)
            self._thread.start()

    def _run(self):
        while not self._closed.is_set():
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._closed.is_set():
                break
            try:
                self.tick()
            except Exception:
                # Never silently retry a failed request that might have reached Steam.
                with self._lock:
                    self._paused = '自动更新检查未完成，请检查工坊状态；重新开启可再次检查。'

    def tick(self):
        """One selected installation, one confirmed batch at a time."""
        with self._lock:
            if self._closed.is_set() or not self.settings()['auto_update'] or self._paused:
                return
            storage = getattr(self.context, 'storage', None)
            if storage is not None and storage.blocked:
                return
            value = self.context.installations.list()
            identity = value.get('selected_installation_id')
            if not identity:
                return
            selected = next((row for row in value['installations'] if row['installation_id'] == identity), None)
            if selected is None:
                return
            service = self.context.workshop
            snapshot = service.status(selected)
            if self._operation and self._operation[0] != identity:
                self._operation = None
            if self._operation:
                previous = next((row for row in snapshot.get('operations', [])
                                 if row['operation_id'] == self._operation[1]), None)
                if previous is None or previous['state'] not in ('completed', 'partial', 'failed', 'uncertain'):
                    return
                self._operation = None
                if previous['state'] != 'completed':
                    self._paused = '上一批自动更新未全部完成，请查看操作记录；不会自动重试。'
                    return
            if service.is_busy():
                return
            session, runtime = snapshot.get('session', {}), snapshot.get('runtime', {})
            if (session.get('state') != 'ready' or session.get('stale') or session.get('refreshing')
                    or runtime.get('state') != 'stopped' or runtime.get('writable') is not True
                    or not snapshot.get('capabilities', {}).get('direct_updates', {}).get('available')):
                return
            ids = [row['workshop_id'] for row in snapshot.get('items', [])
                   if row.get('subscribed') is True and row.get('needs_update') is True
                   and not row.get('downloading') and not row.get('download_pending')][:100]
            if ids:
                operation = service.action(selected, action='update', ids=ids, source='automatic',
                    submission_allowed=self._permission.is_set)
                self._operation = (identity, operation['operation_id'])

    @contextmanager
    def migration_gate(self):
        # The caller publishes storage.blocked before releasing this gate.
        # No automatic submission can slip past the migration's busy check.
        with self._lock:
            yield

    def close(self):
        self._closed.set()
        self._permission.clear()
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError('Automatic update check did not stop')
