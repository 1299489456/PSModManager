"""Isolated Steamworks session and callback-confirmed operation state machine."""
from copy import deepcopy
import ctypes
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time

from .errors import MESSAGES, WorkshopError
from .local import item_id, now

TERMINAL = frozenset(('completed', 'already_current', 'failed', 'uncertain'))


def error(code, retryable=False):
    return dict(code=code, message=MESSAGES[code], retryable=retryable)


def operation_state(items):
    states = [row['state'] for row in items]
    if any(value not in TERMINAL for value in states):
        return 'running'
    if 'uncertain' in states:
        return 'uncertain'
    if all(value == 'failed' for value in states):
        return 'failed'
    return 'partial' if 'failed' in states else 'completed'


class SessionEngine:
    """Native adapter is injected in tests; no direct account tests are required."""
    def __init__(self, api, *, clock=time.monotonic, timeout=1200, call_timeout=45):
        self.api, self.clock = api, clock
        self.timeout, self.call_timeout = timeout, call_timeout
        self.operations, self.pending = {}, {}

    def snapshot(self, ids=()):
        self.tick()
        subscribed = self.api.subscription_ids()
        identities = list(dict.fromkeys([*subscribed, *(item_id(x) for x in ids)]))
        if len(identities) > 1124:
            raise WorkshopError('WORKSHOP_SESSION_LIMIT', MESSAGES['WORKSHOP_SESSION_LIMIT'])
        return dict(session=dict(state='ready', reason_code=None, message='已读取当前 Steam 客户端的正式版工坊状态。', observed_at=now()),
                    items=[self.api.item(identity) for identity in identities], subscribed_ids=subscribed,
                    operations=[deepcopy(op) for op in self.operations.values()])

    def begin(self, operation_id, action, ids):
        if action not in ('subscribe', 'unsubscribe', 'update') or not 1 <= len(ids) <= 100:
            raise WorkshopError('WORKSHOP_INVALID_QUERY', MESSAGES['WORKSHOP_INVALID_QUERY'])
        identities = list(dict.fromkeys(item_id(x) for x in ids))
        if operation_id in self.operations:
            previous = self.operations[operation_id]
            if previous['action'] != action or [row['workshop_id'] for row in previous['items']] != identities:
                raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'])
            return deepcopy(previous)
        busy = {key[1] for key in self.pending}
        if busy.intersection(identities):
            raise WorkshopError('WORKSHOP_OPERATION_BUSY', MESSAGES['WORKSHOP_OPERATION_BUSY'])
        self.api.ensure_online()
        app_subscriptions = set(self.api.subscription_ids())
        self.tick()  # Drain old download callbacks before registering this request.
        op = dict(operation_id=operation_id, action=action, state='running', items=[], created_at=now(), updated_at=now())
        self.operations[operation_id] = op
        for identity in identities:
            state = self.api.item(identity)
            row = dict(workshop_id=identity, state='queued', error=None, bytes_downloaded=state['bytes_downloaded'],
                       bytes_total=state['bytes_total'], progress=state['progress'], callback_confirmed=False,
                       subscription_confirmed=False, download_completed=False)
            op['items'].append(row)
            if action == 'update' and identity not in app_subscriptions:
                row.update(state='failed', error=error('WORKSHOP_NOT_SUBSCRIBED'))
                continue
            if ((action == 'subscribe' and identity in app_subscriptions) or
                    (action == 'unsubscribe' and identity not in app_subscriptions) or
                    (action == 'update' and state['steam_installed'] and not state['needs_update'] and not state['downloading'] and not state['download_pending'])):
                row['state'] = 'already_current'
                continue
            try:
                # Observe Steam's existing download; never enqueue a duplicate.
                existing = action == 'update' and (state['downloading'] or state['download_pending'])
                handle = True if existing else self.api.begin(action, identity)
                if not handle:
                    row.update(state='failed', error=error('WORKSHOP_OPERATION_REJECTED', True))
                    continue
                row['state'] = 'download_pending' if action == 'update' else ('subscribing' if action == 'subscribe' else 'unsubscribing')
                self.pending[(operation_id, identity)] = dict(action=action, handle=handle, row=row, started=self.clock(), callback=None, existing=existing)
            except WorkshopError as exc:
                row.update(state='failed', error=exc.as_dict())
        op['state'] = operation_state(op['items'])
        return deepcopy(op)

    def tick(self):
        events = self.api.callbacks()
        for key, pending in list(self.pending.items()):
            identity, action, row = key[1], pending['action'], pending['row']
            for event in events:
                matched = (event['kind'] == 'download' and action == 'update' and event['workshop_id'] == identity)
                matched |= (event['kind'] == 'call' and action != 'update' and event.get('call') == pending['handle']
                            and event.get('callback') == (1313 if action == 'subscribe' else 1315))
                if matched:
                    if event.get('io_failure') or event.get('workshop_id') != identity:
                        row.update(state='uncertain', error=error('WORKSHOP_SESSION_LOST', True))
                    elif event.get('result') != 1:
                        row.update(state='failed', error=error('WORKSHOP_OPERATION_FAILED', True), steam_result=event.get('result'))
                    else:
                        pending['callback'] = True
                        row['callback_confirmed'] = True
            current = self.api.item(identity)
            row.update({field: current[field] for field in ('bytes_downloaded', 'bytes_total', 'progress')})
            if row['state'] == 'failed':
                del self.pending[key]
            elif pending['callback'] and ((action == 'subscribe' and current['subscribed']) or
                    (action == 'unsubscribe' and not current['subscribed']) or
                    (action == 'update' and current['steam_installed'] and not current['needs_update'] and not current['downloading'] and not current['download_pending'])):
                row.update(state='completed', error=None, subscription_confirmed=action != 'update', download_completed=action == 'update')
                del self.pending[key]
            elif self.clock() - pending['started'] >= (self.timeout if action == 'update' else self.call_timeout):
                # Preserve the pending handle: a late callback may still resolve
                # it and duplicate operations must remain blocked meanwhile.
                row.update(state='uncertain', error=error('WORKSHOP_OPERATION_TIMEOUT', True))
            elif row['state'] != 'uncertain' and action == 'update':
                row['state'] = 'downloading' if current['downloading'] else 'download_pending'
            op = self.operations[key[0]]
            op.update(state=operation_state(op['items']), updated_at=now())

    def operation(self, identity):
        self.tick()
        if identity not in self.operations:
            raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'], retryable=True)
        return deepcopy(self.operations[identity])


class EngineProvider:
    """Constructor-only in-process seam for UI/integration tests, not a page flag."""
    def __init__(self, engine):
        self.engine, self.lock = engine, threading.RLock()

    def snapshot(self, ids=()):
        with self.lock:
            return self.engine.snapshot(ids)

    def begin(self, operation_id, action, ids):
        with self.lock:
            return self.engine.begin(operation_id, action, ids)

    def operation(self, identity):
        with self.lock:
            return self.engine.operation(identity)

    def close(self):
        self.engine.api.close()


def _silence_native_output():
    # Steam's native diagnostic output includes account IDs. Suppress native
    # stdout/stderr before LoadLibrary, not merely Python's print functions.
    import msvcrt
    stream = open(os.devnull, 'wb', buffering=0)
    for number, kind in ((1, -11), (2, -12)):
        try:
            os.dup2(stream.fileno(), number)
        except OSError:
            pass
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        kernel.SetStdHandle.restype = ctypes.c_int
        kernel.SetStdHandle(kind & 0xffffffff, msvcrt.get_osfhandle(stream.fileno()))
    return stream


def _send(connection, value):
    connection.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def helper_main(connection, root):
    """Spawn target; only JSON travels over the inherited private pipe."""
    api, quiet = None, None
    try:
        quiet = _silence_native_output()
        from .steam_native import SteamNative
        api = SteamNative(root)
        engine = SessionEngine(api)
        _send(connection, dict(ok=True))
        last = time.monotonic()
        while True:
            if connection.poll(0.05):
                raw = connection.recv_bytes(65536)
                request = json.loads(raw)
                last = time.monotonic()
                if request.get('method') == 'close':
                    _send(connection, dict(ok=True, result=None))
                    break
                try:
                    method = request.get('method')
                    if method == 'snapshot':
                        result = engine.snapshot(request.get('ids', []))
                    elif method == 'begin':
                        result = engine.begin(request['operation_id'], request['action'], request['ids'])
                    elif method == 'operation':
                        result = engine.operation(request['operation_id'])
                    else:
                        raise WorkshopError('WORKSHOP_INVALID_QUERY', MESSAGES['WORKSHOP_INVALID_QUERY'])
                    _send(connection, dict(ok=True, result=result))
                except WorkshopError as exc:
                    _send(connection, dict(ok=False, error=exc.as_dict()))
            else:
                engine.tick()
            # Passive list views do not keep a game-associated Steam session
            # alive indefinitely. Active calls keep pumping until resolved.
            if not engine.pending and time.monotonic() - last > 3:
                break
    except WorkshopError as exc:
        try:
            _send(connection, dict(ok=False, error=exc.as_dict()))
        except (OSError, EOFError):
            pass
    except (OSError, EOFError, ValueError, KeyError):
        pass
    finally:
        if api:
            api.close()
        connection.close()
        if quiet:
            quiet.close()


class SteamHelper:
    def __init__(self, installation):
        self.root = installation['canonical_root']
        self._process, self._connection = None, None
        self._lock = threading.RLock()
        self._probe_cancelled = threading.Event()

    def _receive(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self._probe_cancelled.is_set():
                raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'], retryable=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                code = 'WORKSHOP_SESSION_LOST' if not self._process.is_alive() else 'WORKSHOP_SESSION_TIMEOUT'
                raise WorkshopError(code, MESSAGES[code], retryable=True)
            if self._connection.poll(min(.05, remaining)):
                break
        result = json.loads(self._connection.recv_bytes(2 * 1024 * 1024))
        if not result.get('ok'):
            value = result.get('error', {})
            code = value.get('code') if value.get('code') in MESSAGES else 'WORKSHOP_SESSION_LOST'
            raise WorkshopError(code, MESSAGES[code], retryable=bool(value.get('retryable')))
        return result.get('result')

    def _start(self):
        if self._probe_cancelled.is_set():
            raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'], retryable=True)
        if self._process is not None and self._process.is_alive():
            return
        self.close()
        if os.name != 'nt':
            raise WorkshopError('WORKSHOP_SESSION_UNSUPPORTED', MESSAGES['WORKSHOP_SESSION_UNSUPPORTED'])
        # Root is host-validated again immediately before native loading.
        root = Path(self.root)
        from .local import workshop_root
        workshop_root(dict(canonical_root=str(root), app_id='4348910'))
        context = multiprocessing.get_context('spawn')
        self._connection, child = context.Pipe(duplex=True)
        self._process = context.Process(target=helper_main, args=(child, str(root)), daemon=True, name='PSMM-SteamUGC')
        try:
            self._process.start()
            self._receive(20)
        except Exception:
            self.close()
            raise
        finally:
            child.close()

    def _request(self, method, **values):
        with self._lock:
            try:
                self._start()
                _send(self._connection, dict(method=method, **values))
                return self._receive(10)
            except WorkshopError as exc:
                if exc.code in ('WORKSHOP_SESSION_LOST', 'WORKSHOP_SESSION_TIMEOUT'):
                    # No request IDs are reused: discard the whole transport so
                    # a late reply cannot become the next request's response.
                    self.close()
                raise
            except (OSError, EOFError, ValueError):
                self.close()
                raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'], retryable=True) from None

    def snapshot(self, ids=()):
        return self._request('snapshot', ids=list(ids))

    def cancel_probe(self):
        # Only service-owned read-only probes call this. Never interrupt an
        # accepted subscribe/download operation or touch the user's Steam/game.
        self._probe_cancelled.set()

    def begin(self, operation_id, action, ids):
        return self._request('begin', operation_id=operation_id, action=action, ids=list(ids))

    def operation(self, operation_id):
        # Never restart and replay an operation after a helper/process loss.
        if self._process is None or not self._process.is_alive():
            raise WorkshopError('WORKSHOP_SESSION_LOST', MESSAGES['WORKSHOP_SESSION_LOST'], retryable=True)
        return self._request('operation', operation_id=operation_id)

    def close(self):
        with self._lock:
            if self._connection is not None:
                try:
                    _send(self._connection, dict(method='close'))
                except (OSError, EOFError):
                    pass
            if self._process is not None:
                if self._process.pid is not None:
                    self._process.join(2)
                    if self._process.is_alive():
                        self._process.terminate()  # Only the helper created by us.
                        self._process.join(2)
                self._process.close()
            if self._connection is not None:
                self._connection.close()
            self._process, self._connection = None, None
