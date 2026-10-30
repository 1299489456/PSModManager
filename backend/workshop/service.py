"""Public metadata, local inventory, and callback-confirmed Steam operations."""
from copy import deepcopy
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
import base64
import os
import threading
import time
import webbrowser
from uuid import uuid4

from .errors import MESSAGES, WorkshopError
from .cache import PublicCache, stamp
from .local import APP_ID, item_id, now, read_status, text, workshop_root
from .remote import PublicWorkshopClient, SORTS, page_url
from .steam_session import SteamHelper, operation_state, TERMINAL


class WorkshopService:
    def __init__(self, paths, *, session=None, opener=None, process_status=None, write_guard=None, steam_factory=None):
        self.paths = paths
        self.client = PublicWorkshopClient(session)
        self.opener = opener or self._open_url
        self.process_status = process_status
        self.write_guard = write_guard
        self.steam_factory = steam_factory or SteamHelper
        self.cache = PublicCache(paths.root)
        self._lock = threading.RLock()
        self._public_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='PSMM-WorkshopPublic')
        self._catalog_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='PSMM-WorkshopCatalog')
        self._image_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='PSMM-WorkshopImage')
        self._fetches, self._fetch_errors, self._probes = {}, {}, set()
        self._probe_controls, self._foreground = {}, {}
        self._image_sources = {}
        self._providers, self._steam_cache, self._operations = {}, {}, {}
        self._active, self._threads = {}, []
        self._closed = threading.Event()

    @staticmethod
    def _guard_reason(runtime, error_code=None):
        """Translate host guard codes, never native errors or exception text."""
        blockers = set(code for code in runtime.get('blockers', []) if isinstance(code, str))
        if isinstance(error_code, str):
            blockers.add(error_code)
        if runtime.get('state') == 'running' or 'GUARD_PROCESS_RUNNING' in blockers:
            return 'WORKSHOP_PROCESS_RUNNING'
        if 'GUARD_RECOVERY_REQUIRED' in blockers:
            return 'WORKSHOP_RECOVERY_REQUIRED'
        if blockers.intersection(('GUARD_INSTALLATION_CHANGED', 'GUARD_IDENTITY_CHANGED')):
            return 'WORKSHOP_INSTALLATION_CHANGED'
        if blockers.intersection(('GUARD_PATH_INVALID', 'GUARD_PATH_UNAVAILABLE', 'GUARD_REPARSE_POINT',
                                  'GUARD_HARDLINK', 'GUARD_TARGET_NOT_ALLOWED')):
            return 'WORKSHOP_INSTALLATION_UNAVAILABLE'
        if 'GUARD_PROCESS_UNKNOWN' in blockers or runtime.get('state') != 'stopped':
            return 'WORKSHOP_PROCESS_UNKNOWN'
        if 'GUARD_LEASE_LOST' in blockers:
            return 'WORKSHOP_OPERATION_INTERRUPTED'
        if blockers - {'GUARD_LEASE_BUSY'}:
            return 'WORKSHOP_GUARD_UNAVAILABLE'
        if not runtime.get('writable') or 'GUARD_LEASE_BUSY' in blockers:
            if runtime.get('busy_owner') == 'workshop_probe':
                return 'WORKSHOP_SESSION_CONNECTING'
            if runtime.get('busy_owner') == 'workshop_action':
                return 'WORKSHOP_OPERATION_BUSY'
            return 'WORKSHOP_INSTALLATION_BUSY' if 'GUARD_LEASE_BUSY' in blockers else 'WORKSHOP_GUARD_UNAVAILABLE'
        return None

    @staticmethod
    def _capabilities(runtime, session):
        ready = session.get('state') == 'ready'
        guard_reason = WorkshopService._guard_reason(runtime)
        reason_code = guard_reason or session.get('reason_code')
        available = ready and guard_reason is None and runtime.get('writable') is True
        capability = dict(available=available, reason_code=None if available else reason_code,
                          reason=None if available else MESSAGES.get(reason_code, '请先选择正式版游戏并连接 Steam。'),
                          blockers=deepcopy(runtime.get('blockers', [])))
        return dict(public_browse=True, official_handoff=True,
                    account=dict(state='verified' if ready else 'unverified', uses='steam_client', password_required=False,
                                 message=session.get('message', '管理器复用 Steam 客户端会话，不读取账号凭据。')),
                    direct_subscription=capability, direct_updates=deepcopy(capability),
                    anonymous_download=dict(available=False, reason='本游戏的匿名下载与更新能力尚未验证；请使用 Steam 官方订阅流程。'))

    def capabilities(self, installation=None):
        if installation is None:
            return self._capabilities(dict(state='unknown', writable=False),
                                      dict(state='unavailable', reason_code='WORKSHOP_INSTALLATION_REQUIRED'))
        runtime = self._runtime(installation)
        snapshot = self._steam_snapshot(installation, runtime)
        return self._capabilities(runtime, snapshot['session'])

    def _runtime(self, installation):
        try:
            value = dict(self.process_status(installation)) if self.process_status else {}
            if value.get('state') in ('running', 'stopped', 'unknown'):
                value['writable'] = value['state'] == 'stopped' and value.get('writable', False) is True
                return value
        except Exception:
            pass
        return dict(state='unknown', writable=False)

    @staticmethod
    def _key(installation):
        workshop_root(installation)
        return installation['canonical_root'].casefold()

    def _provider(self, installation):
        key = self._key(installation)
        with self._lock:
            if self._closed.is_set():
                raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'])
            if key not in self._providers:
                self._providers[key] = self.steam_factory(installation)
            return self._providers[key]

    @staticmethod
    def _steam_call(method, *args):
        # Adapter/IPC failures are not evidence that the game started. Keep
        # guard errors outside this boundary and never expose native details.
        try:
            return method(*args)
        except WorkshopError:
            raise
        except Exception:
            raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'], retryable=True) from None

    def _invalidate_steam_cache(self, key, code='WORKSHOP_SESSION_CONNECTING'):
        """Expire observations without erasing what was actually last seen."""
        with self._lock:
            previous = self._steam_cache.get(key)
            if previous is None:
                return
            value = deepcopy(previous[1])
            value['session'].update(state='unavailable', reason_code=code, message=MESSAGES[code], stale=True)
            if code != 'WORKSHOP_SESSION_CONNECTING':
                value['session']['last_error'] = dict(code=code, message=MESSAGES[code], retryable=True)
            # Separate refresh scheduling from observed_at: the next permitted
            # status probes immediately, while offline rows keep their age.
            self._steam_cache[key] = (0.0, value)

    # Probe Steam often only while something is changing; idle sessions are rechecked less
    # often, and a cached observation is stale only well after the next probe was due.
    PROBE_BUSY_SECONDS, PROBE_IDLE_SECONDS, STALE_SECONDS = 30, 120, 180

    def _steam_snapshot(self, installation, runtime, ids=(), *, refresh=False, probe=True):
        key = self._key(installation)
        if workshop_root(installation) is None:
            code = 'STEAM_LIBRARY_NOT_DETECTED'
            return dict(session=dict(state='unavailable', reason_code=code, message=MESSAGES[code], observed_at=now()), items=[])
        with self._lock:
            cached = deepcopy(self._steam_cache.get(key))
            active = key in self._active
            probing = key in self._probes
        age = max(0, time.monotonic() - cached[0]) if cached else None
        value = cached[1] if cached else dict(session=dict(state='unavailable', reason_code='WORKSHOP_SESSION_CONNECTING',
            message=MESSAGES['WORKSHOP_SESSION_CONNECTING'], observed_at=None), items=[], subscribed_ids=[])
        observed_age = age
        if value['session'].get('observed_at'):
            observed_age = max(0, time.time() - datetime.fromisoformat(value['session']['observed_at']).timestamp())
        guard_runtime = dict(runtime)
        if active or probing:
            guard_runtime['busy_owner'] = 'workshop_action' if active else 'workshop_probe'
        guard_reason = self._guard_reason(guard_runtime)
        own_lease = (active or probing) and guard_reason in ('WORKSHOP_OPERATION_BUSY', 'WORKSHOP_SESSION_CONNECTING')
        allowed = self.write_guard is not None and (guard_reason is None or own_lease)
        if not allowed:
            code = guard_reason or 'WORKSHOP_GUARD_UNAVAILABLE'
            value['session'].update(state='unavailable', reason_code=code, message=MESSAGES[code])
        elif probe and not active and not probing:
            known = {row['workshop_id'] for row in value['items']}
            changing = active or any(row.get('downloading') or row.get('download_pending') for row in value['items'])
            with self._lock:
                changing = changing or any(owner == key and operation['state'] not in ('completed', 'partial', 'failed')
                                           for owner, operation in self._operations.values())
            interval = self.PROBE_BUSY_SECONDS if changing else self.PROBE_IDLE_SECONDS
            if refresh or not cached or age >= interval or not set(ids).issubset(known):
                with self._lock:
                    if key not in self._active and key not in self._probes and not self._foreground.get(key) and not self._closed.is_set():
                        self._probes.add(key)
                        self._probe_controls[key] = dict(cancel=threading.Event(), done=threading.Event(), provider=None, lease_held=False)
                        thread = threading.Thread(target=self._probe_steam, args=(deepcopy(installation), list(ids)),
                                                  name='PSMM-WorkshopProbe', daemon=True)
                        self._threads = [t for t in self._threads if t.is_alive()]
                        self._threads.append(thread)
                        thread.start()
                        probing = True
        value['session'].update(refreshing=probing, stale=not allowed or observed_age is None or observed_age >= self.STALE_SECONDS or value['session']['state'] != 'ready',
                                age_seconds=round(observed_age, 1) if observed_age is not None else None,
                                probe_owned_busy=probing and not runtime['writable'])
        return value

    def probe_owns_lease(self, installation):
        """Read-only proof for the host's preemptible-probe UI admission rule."""
        root = installation.get('canonical_root') if isinstance(installation, dict) else None
        if not isinstance(root, str):
            return False
        key = root.casefold()
        with self._lock:
            control = self._probe_controls.get(key)
            return bool(control and control.get('lease_held') and key in self._probes and
                        key not in self._active and not self._foreground.get(key) and not self._closed.is_set())

    @contextmanager
    def _probe_lease(self, installation, control):
        with self.write_guard(installation) as lease:
            with self._lock:
                control['lease_held'] = True
            try:
                yield lease
            finally:
                # Clear before the underlying lease can be acquired by another
                # writer; queued/finishing probe threads are never ownership proof.
                with self._lock:
                    control['lease_held'] = False

    def _probe_steam(self, installation, ids):
        key = self._key(installation)
        provider = None
        with self._lock:
            control = self._probe_controls[key]
        try:
            with self._probe_lease(installation, control) as lease:
                lease.check()
                with self._lock:
                    if key in self._active or self._closed.is_set() or control['cancel'].is_set() or self._foreground.get(key):
                        return
                    provider = self._provider(installation)
                    control['provider'] = provider
                # Never hold the service/UI lock while IPC initializes Steam.
                try:
                    value = self._steam_call(provider.snapshot, ids)
                    with self._lock:
                        if not control['cancel'].is_set():
                            self._steam_cache[key] = (time.monotonic(), deepcopy(value))
                            self._reconcile(key, value)
                finally:
                    with self._lock:
                        if self._providers.get(key) is provider:
                            self._providers.pop(key, None)
                    provider.close()
                    provider = None
        except Exception as exc:
            if control['cancel'].is_set():
                return  # Foreground work superseded this read; it is not a disconnection.
            code = getattr(exc, 'code', None)
            if code not in MESSAGES:
                code = self._guard_reason(self._runtime(installation), code) or 'WORKSHOP_GUARD_UNAVAILABLE'
            with self._lock:
                previous = self._steam_cache.get(key)
                value = deepcopy(previous[1]) if previous else dict(items=[], subscribed_ids=[], session={})
                value['session'].update(state='unavailable', reason_code=code, message=MESSAGES[code],
                    last_error=dict(code=code, message=MESSAGES[code], retryable=True))
                self._steam_cache[key] = (time.monotonic(), value)
        finally:
            with self._lock:
                if provider is not None and self._providers.get(key) is provider:
                    self._providers.pop(key, None)
            if provider is not None:
                provider.close()
            with self._lock:
                self._probes.discard(key)
                self._probe_controls.pop(key, None)
                control['done'].set()  # Only after helper cleanup and lease release.

    @contextmanager
    def foreground_operation(self, installation):
        """Give host writes/launch priority over our cancellable read-only probe."""
        if isinstance(installation, dict) and installation.get('app_id') not in (None, APP_ID):
            # The host shares this entry point with legacy/demo game launches.
            # Workshop prerequisites must not become new prerequisites there.
            root = installation.get('canonical_root')
            key = root.casefold() if isinstance(root, str) else None
            with self._lock:
                occupied = (key in self._providers or key in self._active or key in self._probes or
                            any(owner == key and any(row['state'] == 'uncertain' for row in op['items'])
                                for owner, op in self._operations.values()))
            if occupied:
                raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'], retryable=True)
            yield
            return
        key = self._key(installation)
        with self._lock:
            if key in self._active or any(owner == key and any(row['state'] == 'uncertain' for row in op['items'])
                                         for owner, op in self._operations.values()):
                raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'], retryable=True)
            self._foreground[key] = self._foreground.get(key, 0) + 1
            control = self._probe_controls.get(key)
            if control:
                control['cancel'].set()
                provider = control['provider']
            else:
                provider = None
        try:
            if provider is not None and callable(getattr(provider, 'cancel_probe', None)):
                provider.cancel_probe()
            if control and not control['done'].wait(6):
                raise WorkshopError('WORKSHOP_INSTALLATION_BUSY', MESSAGES['WORKSHOP_INSTALLATION_BUSY'], retryable=True)
            yield
        finally:
            with self._lock:
                self._foreground[key] -= 1
                if not self._foreground[key]:
                    self._foreground.pop(key, None)

    def _reconcile(self, key, snapshot):
        """A fresh observed desired state may resolve uncertainty, not invent a callback."""
        if snapshot['session']['state'] != 'ready':
            return
        rows = {row['workshop_id']: row for row in snapshot['items']}
        subscribed = set(snapshot.get('subscribed_ids', []))
        for owner, operation in self._operations.values():
            if owner != key or operation['state'] != 'uncertain':
                continue
            changed = False
            for item in operation['items']:
                if item['state'] != 'uncertain':
                    continue
                current = rows.get(item['workshop_id'])
                if operation['action'] == 'unsubscribe':
                    confirmed = item['workshop_id'] not in subscribed
                elif operation['action'] == 'subscribe':
                    confirmed = item['workshop_id'] in subscribed
                else:
                    confirmed = current and current['steam_installed'] and not any(current[field] for field in ('needs_update', 'downloading', 'download_pending'))
                if confirmed:
                    item.update(state='completed', error=None, completion_source='steam_state_readback',
                                subscription_confirmed=operation['action'] != 'update', download_completed=operation['action'] == 'update')
                    changed = True
            if changed:
                operation.update(state=operation_state(operation['items']), updated_at=now())

    def status(self, installation, *, workshop_id=None, refresh=False, _probe=True):
        runtime = self._runtime(installation)
        snapshot = self._steam_snapshot(installation, runtime, [item_id(workshop_id)] if workshop_id else [], refresh=refresh, probe=_probe)
        steam_items = {row['workshop_id']: row for row in snapshot['items']}
        active_content = [identity for identity, row in steam_items.items() if row.get('downloading') or row.get('download_pending')]
        key = self._key(installation)
        with self._lock:
            for owner, operation in self._operations.values():
                if owner == key and operation['action'] in ('update', 'subscribe'):
                    active_content.extend(row['workshop_id'] for row in operation['items'] if row['state'] not in ('completed', 'already_current', 'failed'))
        result = read_status(installation, workshop_id=workshop_id, skip_content_ids=active_content)
        local_items = {row['workshop_id']: row for row in result['items']}
        for identity, row in steam_items.items():
            if workshop_id and identity != workshop_id:
                continue
            local = local_items.get(identity)
            if local is None:
                local = dict(workshop_id=identity, native_mod_id=None, title=None, author=None, mod_version=None, description='',
                             installed=False, content_state='missing', issues=[], evidence={})
                result['items'].append(local)
            local.update(deepcopy(row))
            local['subscription_state'] = 'subscribed' if row['subscribed'] else 'not_subscribed'
            local['update_available'] = row['needs_update']
            local['download_state'] = ('downloading' if row['downloading'] else 'download_pending' if row['download_pending']
                                       else 'update_available' if row['needs_update'] else 'installed' if row['steam_installed'] else 'not_installed')
            local['evidence']['account_subscription_verified'] = snapshot['session']['state'] == 'ready'
            local['evidence']['steam_observed_at'] = snapshot['session'].get('observed_at')
            local['evidence']['steam_stale'] = snapshot['session'].get('stale', False) or snapshot['session']['state'] != 'ready'
        for local in result['items']:
            if local['workshop_id'] not in steam_items:
                local.update(subscribed=None, steam_installed=None, needs_update=None, downloading=None, download_pending=None,
                             bytes_downloaded=None, bytes_total=None)
                if snapshot['session']['state'] == 'ready':
                    local.update(subscribed=False, subscription_state='not_subscribed')
                    local['evidence']['account_subscription_verified'] = True
        result['downloads'] = [deepcopy(row) for row in result['items'] if row.get('downloading') or row.get('download_pending')]
        result.update(session=snapshot['session'], runtime=runtime, capabilities=self._capabilities(runtime, snapshot['session']))
        key = self._key(installation)
        with self._lock:
            result['operations'] = [deepcopy(value) for owner, value in self._operations.values() if owner == key]
            active = key in self._active
        if not active:
            result['runtime'] = self._runtime(installation)
        with self._lock:
            probing = key in self._probes
        if not result['runtime']['writable'] and probing:
            result['runtime']['busy_owner'] = 'workshop_probe'
            result['session']['probe_owned_busy'] = True
        elif active:
            result['runtime']['busy_owner'] = 'workshop_action'
        result['capabilities'] = self._capabilities(result['runtime'], snapshot['session'])
        return result

    def action(self, installation, *, action, ids, source='manual', submission_allowed=None):
        if source not in ('manual', 'automatic'):
            raise WorkshopError('WORKSHOP_INVALID_QUERY', MESSAGES['WORKSHOP_INVALID_QUERY'])
        if submission_allowed is not None and not callable(submission_allowed):
            raise WorkshopError('WORKSHOP_INVALID_QUERY', MESSAGES['WORKSHOP_INVALID_QUERY'])
        if action not in ('subscribe', 'unsubscribe', 'update') or not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= 100:
            raise WorkshopError('WORKSHOP_INVALID_QUERY', MESSAGES['WORKSHOP_INVALID_QUERY'])
        identities = list(dict.fromkeys(item_id(value) for value in ids))
        key = self._key(installation)
        if workshop_root(installation) is None:
            raise WorkshopError('STEAM_LIBRARY_NOT_DETECTED', MESSAGES['STEAM_LIBRARY_NOT_DETECTED'])
        with self._lock:
            if self._foreground.get(key):
                raise WorkshopError('WORKSHOP_INSTALLATION_BUSY', MESSAGES['WORKSHOP_INSTALLATION_BUSY'], retryable=True)
            if key in self._active:
                previous = self._operations[self._active[key]][1]
                if previous['action'] == action and [row['workshop_id'] for row in previous['items']] == identities:
                    return deepcopy(previous)
                raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'], retryable=True)
        runtime = self._runtime(installation)
        guard_reason = self._guard_reason(runtime)
        if guard_reason is not None or self.write_guard is None:
            code = guard_reason or 'WORKSHOP_GUARD_UNAVAILABLE'
            raise WorkshopError(code, MESSAGES[code], retryable=True)
        with self._lock:
            if self._closed.is_set():
                raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'])
            if self._foreground.get(key):
                raise WorkshopError('WORKSHOP_INSTALLATION_BUSY', MESSAGES['WORKSHOP_INSTALLATION_BUSY'], retryable=True)
            if key in self._active:
                previous = self._operations[self._active[key]][1]
                if previous['action'] == action and [row['workshop_id'] for row in previous['items']] == identities:
                    return deepcopy(previous)
                raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'], retryable=True)
            # A lost callback does not authorize replay; block those identities
            # until a later real session observes the requested outcome.
            for owner, previous in self._operations.values():
                if owner == key and any(row['state'] == 'uncertain' and row['workshop_id'] in identities for row in previous['items']):
                    raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'], retryable=True)
            identity = uuid4().hex
            operation = dict(operation_id=identity, action=action, source=source, state='queued', created_at=now(), updated_at=now(),
                             items=[dict(workshop_id=value, state='queued', error=None, bytes_downloaded=None,
                                         bytes_total=None, progress=None, callback_confirmed=False,
                                         subscription_confirmed=False, download_completed=False) for value in identities])
            self._operations[identity] = (key, operation)
            self._active[key] = identity
            thread = threading.Thread(target=self._run_action, args=(deepcopy(installation), identity, submission_allowed), daemon=True, name='PSMM-WorkshopOperation')
            self._threads = [value for value in self._threads if value.is_alive()]
            self._threads.append(thread)
            thread.start()
            return deepcopy(operation)

    def _run_action(self, installation, operation_id, submission_allowed=None):
        key = self._key(installation)
        with self._lock:
            operation = deepcopy(self._operations[operation_id][1])
        provider, attempted = None, set()
        session_failure = None
        try:
            # The owning thread holds the shared installation lease through the
            # callback. Its own guard.check(), not a second lease/status probe,
            # is authoritative while this operation owns the mutex.
            with self.write_guard(installation) as lease:
                lease.check()
                provider = self._provider(installation)
                ids = [row['workshop_id'] for row in operation['items']]
                before = self._steam_call(provider.snapshot, ids)
                with self._lock:
                    self._steam_cache[key] = (time.monotonic(), deepcopy(before))
                if before['session']['state'] != 'ready':
                    code = before['session'].get('reason_code') or 'WORKSHOP_STEAM_INIT_FAILED'
                    raise WorkshopError(code, MESSAGES.get(code, MESSAGES['WORKSHOP_STEAM_INIT_FAILED']), retryable=True)
                # New subscriptions must have a fresh, successful public result
                # for app 4348910. Existing current-app subscriptions come from
                # GetSubscribedItems, not a guessed directory or numeric ID.
                subscribed = set(before.get('subscribed_ids', []))
                if operation['action'] == 'subscribe':
                    new_ids = [value for value in ids if value not in subscribed]
                    for offset in range(0, len(new_ids), 30):
                        lease.check()
                        rows = self.client.query(new_ids[offset:offset + 30])
                        unavailable = {row['workshop_id'] for row in rows if not row.get('available')}
                        for row in operation['items']:
                            if row['workshop_id'] in unavailable:
                                row.update(state='failed', error=dict(code='WORKSHOP_ITEM_UNAVAILABLE', message=MESSAGES['WORKSHOP_ITEM_UNAVAILABLE'], retryable=False))
                pending_ids = [row['workshop_id'] for row in operation['items'] if row['state'] != 'failed']
                lease.check()
                if pending_ids:
                    children, current_items = {}, {}
                    for index, identity in enumerate(pending_ids):
                        lease.check()
                        if self._closed.is_set():
                            raise WorkshopError('WORKSHOP_OPERATION_INTERRUPTED', MESSAGES['WORKSHOP_OPERATION_INTERRUPTED'])
                        if submission_allowed is not None and not submission_allowed():
                            row = next(row for row in operation['items'] if row['workshop_id'] == identity)
                            row.update(state='failed', error=dict(code='WORKSHOP_AUTO_UPDATE_DISABLED',
                                message=MESSAGES['WORKSHOP_AUTO_UPDATE_DISABLED'], retryable=False))
                            current_items[identity] = deepcopy(row)
                            continue
                        # Each native mutation is preceded by a fresh process
                        # check; a large batch is not one unchecked SDK loop.
                        child_id = operation_id + ':' + str(index)
                        attempted.add(identity)  # Timeout after send is uncertain.
                        current = self._steam_call(provider.begin, child_id, operation['action'], [identity])
                        children[child_id] = current
                        current_items.update({row['workshop_id']: row for row in current['items']})
                        operation['items'] = [deepcopy(current_items.get(row['workshop_id'], row)) for row in operation['items']]
                    while True:
                        operation['items'] = [deepcopy(current_items.get(row['workshop_id'], row)) for row in operation['items']]
                        operation.update(state=operation_state(operation['items']), updated_at=now())
                        if operation['state'] in ('completed', 'partial', 'failed', 'uncertain'):
                            break
                        with self._lock:
                            self._operations[operation_id] = (key, deepcopy(operation))
                        if self._closed.wait(0.2):
                            raise WorkshopError('WORKSHOP_OPERATION_INTERRUPTED', MESSAGES['WORKSHOP_OPERATION_INTERRUPTED'])
                        lease.check()
                        for child_id, current in list(children.items()):
                            if current['state'] not in ('completed', 'partial', 'failed', 'uncertain'):
                                current = self._steam_call(provider.operation, child_id)
                                children[child_id] = current
                                current_items.update({row['workshop_id']: row for row in current['items']})
                                operation['items'] = [deepcopy(current_items.get(row['workshop_id'], row)) for row in operation['items']]
                        snapshot = self._steam_call(provider.snapshot, ids)
                        with self._lock:
                            self._steam_cache[key] = (time.monotonic(), deepcopy(snapshot))
                else:
                    operation.update(state=operation_state(operation['items']), updated_at=now())
            # Terminal states become observable only after releasing the lease.
        except Exception as exc:
            code = getattr(exc, 'code', None)
            if code not in MESSAGES:
                code = (self._guard_reason(self._runtime(installation), code) if isinstance(code, str) and code.startswith('GUARD_') else
                        'WORKSHOP_OPERATION_INTERRUPTED' if attempted else 'WORKSHOP_PROCESS_UNKNOWN')
            if code.startswith(('WORKSHOP_SESSION_', 'WORKSHOP_STEAM_')):
                session_failure = code
            for row in operation['items']:
                if row['state'] not in ('completed', 'already_current', 'failed'):
                    row.update(state='uncertain' if row['workshop_id'] in attempted else 'failed',
                               error=dict(code=code, message=MESSAGES[code], retryable=True))
            operation.update(state=operation_state(operation['items']), updated_at=now())
        finally:
            # Shutdown the game-associated helper after the operation. Steam may
            # still finish accepted requests after a timeout; never claim cancel.
            with self._lock:
                self._providers.pop(key, None)
            if provider is not None:
                provider.close()
            with self._lock:
                self._invalidate_steam_cache(key, session_failure or 'WORKSHOP_SESSION_CONNECTING')
                self._active.pop(key, None)
                self._operations[operation_id] = (key, deepcopy(operation))
                if len(self._operations) > 64:
                    for identity, (_, old) in list(self._operations.items()):
                        if len(self._operations) <= 64:
                            break
                        if old['state'] in ('completed', 'partial', 'failed'):
                            del self._operations[identity]

    def release(self, installation=None):
        failure = None
        with self._lock:
            keys = [self._key(installation)] if installation else list(set(self._providers) | set(self._active))
            if installation is not None and workshop_root(installation) is None:
                key = keys[0]
                if key in self._providers or key in self._active or any(
                    owner == key and any(row['state'] == 'uncertain' for row in op['items'])
                    for owner, op in self._operations.values()
                ):
                    raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'], retryable=True)
                # Manual native installations keep their existing launch path;
                # Steam prerequisites apply only to Steam-managed operations.
                return
            if any(key in self._active for key in keys):
                raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'], retryable=True)
            if installation is not None:
                key = keys[0]
                uncertain = [row['workshop_id'] for owner, op in self._operations.values() if owner == key
                             for row in op['items'] if row['state'] == 'uncertain']
                try:
                    # The launch caller already owns the installation lease.
                    # Fresh state excludes Steam transfers still running after
                    # an earlier helper/operation timed out.
                    snapshot = self._steam_call(self._provider(installation).snapshot, uncertain)
                    if snapshot['session']['state'] == 'ready':
                        self._steam_cache[key] = (time.monotonic(), deepcopy(snapshot))
                    if snapshot['session']['state'] != 'ready':
                        if uncertain:
                            failure = 'WORKSHOP_OPERATION_BUSY'
                    elif any(row['downloading'] or row['download_pending'] for row in snapshot['items']):
                        failure = 'WORKSHOP_OPERATION_BUSY'
                    else:
                        self._reconcile(key, snapshot)
                        if any(owner == key and any(row['state'] == 'uncertain' for row in op['items'])
                               for owner, op in self._operations.values()):
                            failure = 'WORKSHOP_OPERATION_BUSY'
                except WorkshopError:
                    if uncertain:
                        failure = 'WORKSHOP_OPERATION_BUSY'
            providers = [self._providers.pop(key) for key in keys if key in self._providers]
            for key in keys:
                self._invalidate_steam_cache(key)
        for provider in providers:
            provider.close()
        if failure:
            raise WorkshopError(failure, MESSAGES[failure], retryable=True)

    def is_busy(self):
        """No SDK calls: storage migration must not abandon outstanding receipts."""
        with self._lock:
            return bool(self._active or self._probes) or any(not f.done() for f in self._fetches.values()) or any(row['state'] == 'uncertain' for _, operation in self._operations.values()
                                             for row in operation['items'])

    def close(self):
        self._closed.set()
        with self._lock:
            controls = list(self._probe_controls.values())
            for control in controls:
                control['cancel'].set()
        for control in controls:
            provider = control['provider']
            if provider is not None and callable(getattr(provider, 'cancel_probe', None)):
                provider.cancel_probe()
        # Migration copies the data root only after close returns. Drain running
        # SWR/image writes; cancel queued work, never leave a late cache writer.
        self._public_pool.shutdown(wait=True, cancel_futures=True)
        self._catalog_pool.shutdown(wait=True, cancel_futures=True)
        self._image_pool.shutdown(wait=True, cancel_futures=True)
        for thread in self._threads:
            thread.join(12)
        with self._lock:
            providers, self._providers = list(self._providers.values()), {}
        for provider in providers:
            provider.close()
        self.client.session.close() if callable(getattr(self.client.session, 'close', None)) else None

    def _fetch(self, key, action, *, image=False, catalog=False):
        with self._lock:
            previous = self._fetches.get(key)
            if previous is not None and not previous.done():
                return previous
            if self._closed.is_set():
                raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'])
            if len(self._fetches) >= 256:
                self._fetches = {k: v for k, v in self._fetches.items() if not v.done()}
            def run():
                try:
                    result = action()
                    with self._lock:
                        self._fetch_errors.pop(key, None)
                    return result
                except Exception as exc:
                    safe = exc if isinstance(exc, WorkshopError) else WorkshopError('WORKSHOP_NETWORK_ERROR', MESSAGES['WORKSHOP_NETWORK_ERROR'], retryable=True)
                    with self._lock:
                        self._fetch_errors[key] = (time.monotonic(), safe.as_dict())
                    raise safe from None
            future = (self._image_pool if image else self._catalog_pool if catalog else self._public_pool).submit(run)
            self._fetches[key] = future
            return future

    def _metadata(self, key, previous, *, refreshing=False, stale=False):
        with self._lock:
            error = self._fetch_errors.get(key)
            future = self._fetches.get(key)
        return dict(cache_hit=previous is not None, stale=stale or bool(error),
            refreshing=refreshing or (future is not None and not future.done()),
            source_updated_at=stamp(previous[0]) if previous else None,
            network_error=deepcopy(error[1]) if error else None)

    def _cached(self, key, action, refresh, *, background=False, catalog=False):
        previous = self.cache.get(key)
        stale = bool(previous and time.time() - previous[0] >= 300)
        with self._lock:
            failure = self._fetch_errors.get(key)
        needed = refresh or previous is None or stale
        if needed and (refresh or not failure or time.monotonic() - failure[0] >= 30):
            def fetch():
                value = action()
                self.cache.put(key, value)
                return value
            future = self._fetch(key, fetch, catalog=catalog)
            if previous is None and not background:
                value = future.result()
                return value, dict(cache_hit=False, stale=False, refreshing=False,
                                   source_updated_at=stamp(self.cache.get(key)[0]), network_error=None)
        if previous is None and not background and failure:
            raise WorkshopError(failure[1]['code'], failure[1]['message'], retryable=failure[1]['retryable'])
        if background:
            previous = self.cache.get(key)  # A fast task may already have completed.
            stale = bool(previous and time.time() - previous[0] >= 300)
        return (deepcopy(previous[1]) if previous else None,
                self._metadata(key, previous, stale=stale))

    def _remember_items(self, items):
        for value in items:
            item = deepcopy(value)
            previous = self.cache.get(('item', item['workshop_id']))
            if previous:
                if not item.get('author'):
                    item['author'] = previous[1].get('author')
                if item.get('description_scope') != 'detail' and previous[1].get('description_scope') == 'detail' and item.get('time_updated') == previous[1].get('time_updated'):
                    item['description'] = previous[1].get('description', '')
                    item['description_truncated'] = previous[1].get('description_truncated', False)
                    item['description_scope'] = 'detail'
            self.cache.put(('item', item['workshop_id']), item)

    def _public_query(self, ids, refresh, *, background=False):
        previous = [self.cache.get(('item', identity)) for identity in ids]
        key = ('query', *ids)
        stale = any(value is None or time.time() - value[0] >= 300 for value in previous)
        incomplete = len(ids) == 1 and previous[0] and previous[0][1].get('description_scope') != 'detail'
        with self._lock:
            failure = self._fetch_errors.get(key)
        if (refresh or stale or incomplete) and (refresh or not failure or time.monotonic() - failure[0] >= 30):
            def fetch():
                values = self.client.query(ids)
                self._remember_items(values)
                return values
            future = self._fetch(key, fetch)
            if any(value is None for value in previous) and not background:
                values = future.result()
                return values, dict(cache_hit=False, stale=False, refreshing=False, source_updated_at=now(), network_error=None)
        if background:
            previous = [self.cache.get(('item', identity)) for identity in ids]
            known = [value for value in previous if value]
            meta = self._metadata(key, min(known, key=lambda v: v[0]) if known else None,
                                  stale=any(time.time() - value[0] >= 300 for value in known))
            return [value[1] if value else None for value in previous], meta
        if any(value is None for value in previous):
            raise WorkshopError(failure[1]['code'], failure[1]['message'], retryable=failure[1]['retryable'])
        meta = self._metadata(key, min(previous, key=lambda v: v[0]), stale=stale or refresh)
        return [value[1] for value in previous], meta

    def _local(self, installation):
        return {row['workshop_id']: row for row in self.status(installation, _probe=False)['items']} if installation else {}

    def _public_capabilities(self, installation):
        if installation is None:
            return self.capabilities()
        runtime = self._runtime(installation)
        return self._capabilities(runtime, self._steam_snapshot(installation, runtime, probe=False)['session'])

    def _decorate(self, item, local):
        result = self._merge(item, local)
        entry = self.cache.get(('item', item['workshop_id']))
        result['source_updated_at'] = stamp(entry[0]) if entry else None
        result['preview_cached'] = self.cache.get(('image', item['workshop_id'])) is not None
        return result

    @staticmethod
    def _merge(item, local):
        result = deepcopy(item)
        row = local.get(result['workshop_id'])
        result['local'] = deepcopy(row) if row else None
        result['native_mod_id'] = row.get('native_mod_id') if row else None
        if row and not result.get('author'):
            result['author'] = row.get('author')
        if row and not result.get('title'):
            result['title'] = row.get('title') or result['workshop_id']
        return result

    @staticmethod
    def _local_item(row):
        return dict(workshop_id=row['workshop_id'], app_id=APP_ID, native_mod_id=row.get('native_mod_id'),
                    title=row.get('title') or row['workshop_id'], description=row.get('description', ''),
                    description_truncated=row.get('description_truncated', False),
                    author=row.get('author'), preview_url=None, page_url=page_url(row['workshop_id']),
                    file_size=row.get('file_size'), time_updated=row.get('time_updated'), subscriptions=None,
                    rating=None, tags=[], required_items=[], children_kind='unknown', local=deepcopy(row),
                    metadata_source='local_manifest', available=row.get('installed', False))

    def browse(self, installation=None, *, query='', page=1, sort='hot', refresh=False, page_size=24):
        if not isinstance(query, str) or len(query) > 200 or type(page) is not int or not 1 <= page <= 10000 or sort not in (*SORTS, 'mine') or type(refresh) is not bool or type(page_size) is not int or page_size not in (12, 24, 30, 48):
            raise WorkshopError('WORKSHOP_INVALID_QUERY', '搜索条件或页码无效。')
        query = text(query, 200)
        local = self._local(installation)
        if sort == 'mine':
            rows = [self._local_item(row) for row in local.values() if not query or query.casefold() in (row.get('title') or '').casefold()]
            start = (page - 1) * page_size
            items = rows[start:start + page_size]
            metadata, loaded = [], []
            for offset in range(0, len(items), 30):
                batch = items[offset:offset + 30]
                values, meta = self._public_query([row['workshop_id'] for row in batch], refresh, background=True)
                loaded.extend(self._decorate(value, local) if value else fallback for value, fallback in zip(values, batch))
                metadata.append(meta)
            meta = self._catalog_metadata(metadata)
            return dict(items=loaded, page=page, page_size=page_size, total=len(rows), has_more=start + page_size < len(rows),
                        query=query, sort=sort, loading=False, partial=False,
                        source='local_and_steam', **meta, capabilities=self._public_capabilities(installation),
                        message='本机内容及 Steam 已确认订阅；订阅状态以每项最近一次会话观察为准。')
        start, end = (page - 1) * page_size, page * page_size
        source_pages, metadata = [], []
        for source_page in range(start // 30 + 1, (end - 1) // 30 + 2):
            def fetch(source_page=source_page):
                value = self.client.browse(query=query, page=source_page, sort=sort)
                self._remember_items(value['items'])
                return value
            value, meta = self._cached(('browse', query, source_page, sort), fetch, refresh, background=True, catalog=True)
            source_pages.append((source_page, value))
            metadata.append(meta)
            if value is not None and not value['has_more']:
                break
        indexed = {30 * (p - 1) + i: row for p, value in source_pages if value for i, row in enumerate(value['items'])}
        known = [value for _, value in source_pages if value is not None]
        total = known[0].get('total') if known else None
        missing = any(value is None for _, value in source_pages)
        meta = self._catalog_metadata(metadata)
        result = dict(items=[self._decorate(indexed[i], local) for i in range(start, end) if i in indexed],
            page=page, page_size=page_size, total=total,
            query=query, sort=sort, loading=missing and meta['refreshing'], partial=missing and bool(indexed),
            has_more=end < total if total is not None else bool(known and known[-1]['has_more']),
            source='steam_community', **meta,
            capabilities=self._public_capabilities(installation))
        return result

    @staticmethod
    def _catalog_metadata(metadata):
        times = [m['source_updated_at'] for m in metadata if m['source_updated_at']]
        refreshing = any(m['refreshing'] for m in metadata)
        return dict(cache_hit=bool(metadata) and all(m['cache_hit'] for m in metadata),
                    stale=any(m['stale'] for m in metadata), refreshing=refreshing,
                    source_updated_at=min(times) if times else None,
                    network_error=next((m['network_error'] for m in metadata if m['network_error']), None),
                    retry_after_ms=750 if refreshing else None)

    def query(self, workshop_ids, installation=None, *, refresh=False, _local_rows=None):
        if not isinstance(workshop_ids, (tuple, list)) or not 1 <= len(workshop_ids) <= 30 or type(refresh) is not bool:
            raise WorkshopError('WORKSHOP_INVALID_QUERY', '每次可查询 1 至 30 个工坊条目。')
        ids = list(dict.fromkeys(item_id(value) for value in workshop_ids))
        local = self._local(installation) if _local_rows is None else _local_rows
        try:
            items, meta = self._public_query(ids, refresh)
            return dict(items=[self._decorate(item, local) for item in items], source='steam_public_api', **meta)
        except WorkshopError as exc:
            if not all(identity in local for identity in ids) or exc.code not in ('WORKSHOP_NETWORK_ERROR', 'WORKSHOP_RATE_LIMITED', 'WORKSHOP_UNAVAILABLE'):
                raise
            return dict(items=[self._local_item(local[identity]) for identity in ids], source='local_manifest',
                        stale=True, network_error=exc.as_dict(), cache_hit=False, refreshing=False, source_updated_at=None)

    def detail(self, workshop_id, installation=None, *, refresh=False):
        identity = item_id(workshop_id)
        targeted = self.status(installation, workshop_id=identity, _probe=False) if installation else None
        response = self.query([identity], installation, refresh=refresh,
                              _local_rows=({row['workshop_id']: row for row in targeted['items']} if targeted else None))
        result = response['items'][0]
        result.update({name: response[name] for name in ('stale', 'network_error', 'cache_hit', 'refreshing', 'source_updated_at')})
        result['source_pending'] = result.get('description_scope') != 'detail' and response['source'] != 'local_manifest'
        if installation:
            result['local'] = targeted['items'][0] if targeted['items'] else result.get('local')
            result['native_mod_id'] = result['local'].get('native_mod_id') if result.get('local') else None
            result['capabilities'] = targeted['capabilities']
        if not result.get('available') or response['source'] == 'local_manifest':
            return result
        try:
            extras, meta = self._cached(('extras', identity), lambda: self.client.detail_extras(identity), refresh, background=True)
            if extras:
                result.update({key: value for key, value in extras.items() if value is not None})
            result['refreshing'] = result['refreshing'] or meta['refreshing']
            result['details_error'] = meta['network_error']
        except WorkshopError as exc:
            # API metadata remains valid; dependency observation failed and stays unknown.
            result['details_error'] = exc.as_dict()
        return result

    def cached_detail(self, workshop_id, installation=None):
        identity = item_id(workshop_id)
        cached = self.cache.get(('item', identity))
        if not cached or cached[1].get('description_scope') != 'detail':
            return None
        result = self._decorate(cached[1], self._local(installation))
        extras = self.cache.get(('extras', identity))
        if extras:
            result.update({key: value for key, value in extras[1].items() if value is not None})
        result.update(cache_hit=True, stale=time.time() - cached[0] >= 300, refreshing=False,
                      source_pending=False, source_updated_at=stamp(cached[0]), network_error=None)
        return result

    def translation_detail(self, workshop_id, installation=None, *, check=lambda: None):
        identity = item_id(workshop_id)
        check()
        cached = self.cached_detail(identity, installation)
        if cached is not None:
            return cached
        def fetch():
            values = self.client.query([identity])
            self._remember_items(values)
            return values
        future = self._fetch(('query', identity), fetch)
        while True:
            check()
            try:
                future.result(timeout=.1)
                break
            except FutureTimeout:
                continue
        check()
        result = self.cached_detail(identity, installation)
        if result is None:
            raise WorkshopError('WORKSHOP_ITEM_UNAVAILABLE', MESSAGES['WORKSHOP_ITEM_UNAVAILABLE'])
        return result

    def image(self, workshop_id, *, refresh=False):
        identity = item_id(workshop_id)
        if type(refresh) is not bool:
            raise WorkshopError('WORKSHOP_INVALID_QUERY', MESSAGES['WORKSHOP_INVALID_QUERY'])
        item = self.cache.get(('item', identity))
        cached = self.cache.image(identity)
        url = item[1].get('preview_url') if item else None
        key = ('image', identity)
        stale = bool(cached and (time.time() - cached[0] > 86400 or (url and cached[1]['url'] != url)))
        with self._lock:
            failure = self._fetch_errors.get(key)
            source_changed = bool(url and self._image_sources.get(identity) != url)
        if (url or item is None) and (refresh or cached is None or stale) and (refresh or source_changed or not failure or time.monotonic() - failure[0] >= 30):
            def fetch():
                resolved = url
                with self._lock:
                    self._image_sources[identity] = resolved
                if item is None:
                    # Cover requests can arrive before the page's metadata batch.
                    # Resolve the ID through the same verified public API; never
                    # depend on a later prop change to make an offline cover.
                    values, _ = self._public_query([identity], False)
                    resolved = values[0].get('preview_url')
                if resolved:
                    with self._lock:
                        self._image_sources[identity] = resolved
                    self.cache.put_image(identity, resolved, self.client.image(resolved))
            self._fetch(key, fetch, image=True)
        with self._lock:
            future = self._fetches.get(key)
            loading = future is not None and not future.done()
            failure = self._fetch_errors.get(key)
        if cached is None and future is not None and future.done() and not failure:
            cached = self.cache.image(identity)
        return dict(workshop_id=identity, state='ready' if cached else 'loading' if loading else 'unavailable',
                    data_url=('data:' + cached[1]['mime'] + ';base64,' + base64.b64encode(cached[2]).decode('ascii')) if cached else None,
                    stale=stale or bool(failure), refreshing=loading,
                    source_updated_at=stamp(cached[0]) if cached else None, error=deepcopy(failure[1]) if failure else None)

    @staticmethod
    def _open_url(url):
        if os.name == 'nt':
            os.startfile(url)
            return True
        return webbrowser.open(url)

    def open(self, workshop_id=None, *, intent='detail', target='steam'):
        if intent not in ('detail', 'subscribe', 'unsubscribe', 'workshop') or target not in ('steam', 'browser'):
            raise WorkshopError('WORKSHOP_INVALID_HANDOFF', '工坊交接操作无效。')
        if workshop_id is None and intent != 'workshop':
            raise WorkshopError('WORKSHOP_INVALID_ID', '请指定工坊条目。')
        identity = item_id(workshop_id) if workshop_id is not None else None
        official = page_url(identity) if identity else f'https://steamcommunity.com/workshop/browse/?appid={APP_ID}'
        url = ('steam://url/CommunityFilePage/' + identity if identity else 'steam://openurl/' + official) if target == 'steam' else official
        try:
            if self.opener(url) is False:
                raise OSError()
        except (OSError, ValueError):
            raise WorkshopError('WORKSHOP_OPEN_FAILED', '无法打开 Steam 官方页面，请确认客户端或默认浏览器可用。', retryable=True) from None
        return dict(state='handoff_opened', intent=intent, workshop_id=identity, url=url,
                    confirmation_required=True, subscription_state='unknown',
                    message='已交给 Steam 官方页面。请在那里完成操作，再刷新核对本机安装状态；此处未确认订阅结果。')

    def anonymous_download(self, workshop_id, installation=None):
        item_id(workshop_id)
        raise WorkshopError('WORKSHOP_ANONYMOUS_UNAVAILABLE', self.capabilities()['anonymous_download']['reason'])
