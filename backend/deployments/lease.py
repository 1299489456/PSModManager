"""Cross-data-root, cross-session installation mutex. No lock files are deleted."""
import hashlib
import os
import threading

from .paths import GuardRejected

_active = set()
_registry_lock = threading.Lock()


def process_started(pid):
    import win32api
    import win32process
    handle = win32api.OpenProcess(0x1000, False, pid)
    try:
        return win32process.GetProcessTimes(handle)['CreationTime'].isoformat()
    finally:
        handle.Close()


class InstallationLease:
    def __init__(self, installation_identity):
        self.installation_identity = installation_identity
        self.name = 'Global\\PSModManager.Installation.' + hashlib.sha256(
            installation_identity.encode('utf-8')).hexdigest()
        self.owner_pid = os.getpid()
        self.owner_started = process_started(self.owner_pid)
        self.owner_thread = threading.get_ident()
        self.abandoned = False
        self._handle = None

    def __enter__(self):
        import win32event
        import pywintypes
        key = (self.name, self.owner_thread)
        with _registry_lock:
            if key in _active or self._handle is not None:
                raise GuardRejected('GUARD_LEASE_BUSY')
            _active.add(key)
        handle = None
        try:
            handle = win32event.CreateMutex(None, False, self.name)
            result = win32event.WaitForSingleObject(handle, 0)
            if result not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
                raise GuardRejected('GUARD_LEASE_BUSY')
            self.abandoned = result == win32event.WAIT_ABANDONED
            self._handle = handle
            return self
        except BaseException as exc:
            if handle is not None:
                handle.Close()
            with _registry_lock:
                _active.discard(key)
            if isinstance(exc, pywintypes.error):
                raise GuardRejected('GUARD_LEASE_BUSY') from None
            raise

    def check(self):
        import win32event
        try:
            if (self._handle is None or os.getpid() != self.owner_pid
                    or threading.get_ident() != self.owner_thread
                    or process_started(self.owner_pid) != self.owner_started):
                raise GuardRejected('GUARD_LEASE_LOST')
            # This is recursive only for the owning thread. Balance the extra wait.
            result = win32event.WaitForSingleObject(self._handle, 0)
            if result != win32event.WAIT_OBJECT_0:
                if result == win32event.WAIT_ABANDONED:
                    win32event.ReleaseMutex(self._handle)
                raise GuardRejected('GUARD_LEASE_LOST')
            win32event.ReleaseMutex(self._handle)
        except GuardRejected:
            raise
        except Exception:
            raise GuardRejected('GUARD_LEASE_LOST') from None

    def close(self):
        import win32event
        if self._handle is not None:
            if threading.get_ident() != self.owner_thread:
                raise GuardRejected('GUARD_LEASE_LOST')
            try:
                win32event.ReleaseMutex(self._handle)
            finally:
                self._handle.Close()
                self._handle = None
                with _registry_lock:
                    _active.discard((self.name, self.owner_thread))

    def __exit__(self, *_):
        self.close()
