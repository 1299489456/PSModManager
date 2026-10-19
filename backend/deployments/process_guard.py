"""Shared product guard for apply, rollback and launch; no executor or launcher."""
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

import psutil

from backend.games.probably_stolen.layout import EXE
from backend.storage import Store
from .lease import InstallationLease
from .paths import (GuardRejected, capture_target, file_identity, guarded_target,
                    local_path, open_handle, pinned_chain, pinned_file, _information, _IoStatus)


class _FileProcessIds(ctypes.Structure):
    _fields_ = [('Count', wintypes.ULONG), ('Ids', ctypes.c_size_t * 1)]


def same_process_image(process, executable):
    """Legacy test helper: compare mapped image sections.

    NT class 44 (ProcessImageFileMapping) consumes a FILE_EXECUTE handle.
    Unsupported versions/access errors fail closed; there is no name fallback.
    """
    query = ctypes.WinDLL('ntdll').NtQueryInformationProcess
    query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                      ctypes.POINTER(wintypes.ULONG)]
    query.restype = ctypes.c_long
    value = wintypes.HANDLE(int(executable))
    result = query(int(process), 44, ctypes.byref(value), ctypes.sizeof(value), None) & 0xffffffff
    if result == 0:
        return True
    if result == 0xc0000001:  # STATUS_UNSUCCESSFUL: different image section
        return False
    raise GuardRejected('GUARD_PROCESS_UNKNOWN')


def file_process_ids(executable):
    """Legacy test helper: query mapped users including deleted hardlink images.

    NT class 47 is reserved for system use, not a stable Win32 contract. Reject
    unsupported, partial or malformed results instead of assuming an empty set.
    Every inspection also checks this capability against our own live image.
    """
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    volume = kernel.GetVolumeInformationByHandleW
    volume.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD,
                       ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                       ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    volume.restype = wintypes.BOOL
    filesystem = ctypes.create_unicode_buffer(64)
    if (not volume(int(executable), None, 0, None, None, None, filesystem, len(filesystem))
            or filesystem.value != 'NTFS'):
        raise GuardRejected('GUARD_PROCESS_UNKNOWN')

    query = ctypes.WinDLL('ntdll').NtQueryInformationFile
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatus), ctypes.c_void_p,
                      wintypes.ULONG, wintypes.ULONG]
    query.restype = ctypes.c_long
    size = 4096
    while size <= 1024 * 1024:
        buffer, io = ctypes.create_string_buffer(size), _IoStatus()
        result = query(int(executable), ctypes.byref(io), buffer, size, 47) & 0xffffffff
        if result in (0xc0000004, 0xc0000023, 0x80000005):
            size *= 2
            continue
        if (result != 0 or io.Status not in (None, 0)
                or not _FileProcessIds.Ids.offset <= io.Information <= size):
            raise GuardRejected('GUARD_PROCESS_UNKNOWN')
        count = wintypes.ULONG.from_buffer(buffer).value
        required = _FileProcessIds.Ids.offset + count * ctypes.sizeof(ctypes.c_size_t)
        if count > 32768 or required != io.Information:
            raise GuardRejected('GUARD_PROCESS_UNKNOWN')
        pids = list((ctypes.c_size_t * count).from_buffer(buffer, _FileProcessIds.Ids.offset))
        if any(pid <= 4 or pid > 0xffffffff for pid in pids) or len(set(pids)) != count:
            raise GuardRejected('GUARD_PROCESS_UNKNOWN')
        return pids
    raise GuardRejected('GUARD_PROCESS_UNKNOWN')


def check_file_process_query():
    """A successful empty target list requires a real positive control."""
    import os
    import win32api
    try:
        with pinned_file(win32api.GetModuleFileName(0), execute=True) as control:
            if (not same_process_image(win32api.GetCurrentProcess(), control)
                    or os.getpid() not in file_process_ids(control)):
                raise GuardRejected('GUARD_PROCESS_UNKNOWN')
    except win32api.error:
        raise GuardRejected('GUARD_PROCESS_UNKNOWN') from None


class ProcessGuard:
    def __init__(self, exe_path, exe_identity):
        self.exe_path = local_path(exe_path)
        self.exe_identity = exe_identity

    def inspect(self):
        try:
            with pinned_file(self.exe_path, execute=True) as handle:
                if file_identity(_information(handle, False)) != self.exe_identity:
                    raise GuardRejected('GUARD_IDENTITY_CHANGED')
                result = self._inspect_processes(handle)
                # A hardlink can be created after pinning an EXE. Recheck instead
                # of assuming its initial link count stays one for the session.
                _information(handle, False)
                return result
        except (OSError, ValueError):
            return {'status': 'unknown', 'pids': [], 'unknown_pids': []}

    def _inspect_processes(self, executable):
        """Find the selected game's image using ordinary Windows process paths.

        Unrelated protected system processes do not affect Mod management.
        Renamed/hardlinked/deleted images are outside this normal-game check.
        """
        running, unknown = [], []
        expected_name = self.exe_path.name.casefold()
        expected_path = os.path.normcase(os.path.abspath(self.exe_path))
        try:
            for process in psutil.process_iter(['name']):
                if (process.info.get('name') or '').casefold() != expected_name:
                    continue
                try:
                    image = process.exe()
                    if os.path.normcase(os.path.abspath(image)) == expected_path:
                        running.append(process.pid)
                except psutil.NoSuchProcess:
                    continue
                except (psutil.AccessDenied, OSError, ValueError):
                    unknown.append(process.pid)
        except (OSError, ValueError, psutil.Error):
            return {'status': 'unknown', 'pids': running, 'unknown_pids': unknown}
        return {'status': 'running' if running else 'unknown' if unknown else 'clear',
                'pids': running, 'unknown_pids': unknown}

    def check(self):
        status = self.inspect()
        if status['status'] != 'clear':
            raise GuardRejected('GUARD_PROCESS_RUNNING' if status['status'] == 'running' else 'GUARD_PROCESS_UNKNOWN')
        return status


class GuardSession:
    def __init__(self, service, installation, lease, process, intent):
        self.service = service
        self.installation = installation
        self.lease = lease
        self.process = process
        self.intent = intent
        self.closed = False
        self.recovery_required = False
        self.permit = None

    def check(self):
        if self.closed:
            raise GuardRejected('GUARD_LEASE_LOST')
        self.lease.check()
        current, _ = self.service._facts(str(self.installation.installation_id))
        if current != self.installation:
            raise GuardRejected('GUARD_INSTALLATION_CHANGED')
        self.service._check_recovery(self.installation, self.permit)
        if self.recovery_required:
            raise GuardRejected('GUARD_RECOVERY_REQUIRED')
        return self.process.check()

    @contextmanager
    def step(self, snapshot, *, delete=False):
        if self.intent == 'launch':
            raise GuardRejected('GUARD_TARGET_NOT_ALLOWED')
        if (snapshot.installation_identity != self.installation.file_identity
                or local_path(snapshot.root) != local_path(self.installation.canonical_root)):
            raise GuardRejected('GUARD_INSTALLATION_CHANGED')
        consumer_entered = False
        try:
            # guarded_target checks the session after pinning/validating the
            # target and immediately before yielding to the writer. Repeating
            # that full DB/recovery/process query before read-only pinning adds
            # no protected write boundary.
            with guarded_target(snapshot, self.check, delete=delete) as target:
                consumer_entered = True
                yield target
                # External launch is possible even with an installation mutex.
                # After a consumer ran, detection requires executor recovery.
                self.check()
        except BaseException:
            if consumer_entered:
                self.recovery_required = True
            raise


class DeploymentGuards:
    def __init__(self, paths):
        self.paths = paths
        self.recovery_check = None

    def _facts(self, installation_id):
        self.paths.check_owned_paths()
        store = Store(self.paths.database, validate=False)
        try:
            installation = store.get('Installation', installation_id)
            fingerprint = store.get('RuntimeFingerprint', installation.fingerprint_id)
            root = local_path(installation.canonical_root)
            data = local_path(self.paths.root)
            if root == data or root in data.parents or data in root.parents:
                raise GuardRejected('GUARD_TARGET_NOT_ALLOWED')
            return installation, fingerprint
        except KeyError:
            raise GuardRejected('GUARD_INSTALLATION_CHANGED') from None
        finally:
            store.close()

    def _check_recovery(self, installation, permit=None):
        if self.recovery_check is not None:
            return self.recovery_check(installation, permit)
        store = Store(self.paths.database)
        try:
            # Do not paginate: a blocking transaction beyond row 200 still blocks.
            rows = store.connection.execute('''SELECT t.payload FROM transactions t
                JOIN action_plans p ON p.plan_id=t.plan_id WHERE p.installation_id=?''',
                (str(installation.installation_id),))
            for row in rows:
                from backend.contracts.models import Transaction
                try:
                    transaction = Transaction.model_validate_json(row['payload'])
                except ValueError:
                    raise GuardRejected('GUARD_RECOVERY_REQUIRED') from None
                if (transaction.state in {'staging', 'ready', 'applying', 'rolling_back', 'recovery_required'}
                        or transaction.state in {'failed', 'cancelled'} and transaction.last_sequence > 0):
                    raise GuardRejected('GUARD_RECOVERY_REQUIRED')
            # Until PS-03-03 defines durable journal reconciliation, any retained
            # journal is unverified recovery state. Never delete/parse it as success.
            # exists() hides dangling junctions. Open each optional component
            # relative to the checked parent; reparse/malformed state blocks.
            try:
                with ExitStack() as stack:
                    chain = stack.enter_context(pinned_chain(self.paths.root))
                    parent = chain.handles[-1]
                    journal_root = self.paths.root
                    for name in ('deployments', 'journals'):
                        journal_root = journal_root / name
                        try:
                            parent = open_handle(journal_root, directory=True, parent=parent)
                        except FileNotFoundError:
                            return
                        stack.callback(parent.Close)
                    if next(journal_root.iterdir(), None) is not None:
                        raise GuardRejected('GUARD_RECOVERY_REQUIRED')
                    chain.check()
                    _information(parent, True)
            except (OSError, ValueError):
                raise GuardRejected('GUARD_RECOVERY_REQUIRED') from None
        finally:
            store.close()

    @contextmanager
    def _installation(self, installation_id, *, check=lambda: None):
        check()
        installation, _ = self._facts(installation_id)
        root = local_path(installation.canonical_root)
        try:
            with ExitStack() as stack:
                ancestors = stack.enter_context(pinned_chain(root))
                if ancestors[-1][1] != installation.file_identity:
                    raise GuardRejected('GUARD_INSTALLATION_CHANGED')
                # Directory identity and the executable's file identity protect
                # the operation boundary. Unrelated game updates do not require
                # hashing EXE, GameAssembly and metadata before every Mod action.
                handle = stack.enter_context(pinned_file(root / EXE))
                exe_identity = file_identity(_information(handle, False))
                check()
                yield installation, ProcessGuard(root / EXE, exe_identity)
        except FileNotFoundError:
            raise GuardRejected('GUARD_INSTALLATION_CHANGED') from None

    def prepare_target(self, installation_id, root_kind, relative_path):
        # Preparation is read-only and remains possible while game/recovery blocks.
        with self._installation(installation_id) as (installation, _):
            return capture_target(installation.canonical_root, installation.file_identity,
                                  root_kind, relative_path)

    @contextmanager
    def operation(self, installation_id, intent='apply', *, permit=None):
        if intent not in {'apply', 'rollback', 'launch'}:
            raise GuardRejected('GUARD_TARGET_NOT_ALLOWED')
        with self._installation(installation_id) as (installation, process):
            with InstallationLease(installation.file_identity) as lease:
                session = GuardSession(self, installation, lease, process, intent)
                session.permit = permit
                try:
                    session.check()
                    yield session
                finally:
                    session.closed = True

    def status(self, installation_id):
        result = {'installation_id': installation_id, 'ready': False, 'blockers': [],
                  'process_status': 'unknown', 'execution_available': False,
                  'boundary': 'Snapshot only; no approval, deployment or launch permission.'}
        try:
            with self._installation(installation_id) as (installation, process):
                observed = process.inspect()
                result['process_status'] = observed['status']
                if observed['status'] != 'clear':
                    result['blockers'].append('GUARD_PROCESS_RUNNING' if observed['status'] == 'running' else 'GUARD_PROCESS_UNKNOWN')
                try:
                    self._check_recovery(installation)
                except GuardRejected as exc:
                    result['blockers'].append(exc.code)
                try:
                    with InstallationLease(installation.file_identity):
                        pass
                except GuardRejected as exc:
                    result['blockers'].append(exc.code)
        except GuardRejected as exc:
            result['blockers'].append(exc.code)
        result['ready'] = not result['blockers']
        return result
