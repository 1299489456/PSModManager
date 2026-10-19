"""Windows handle-bound deployment paths; no raw path from a bridge is writable."""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import re
import time

from backend.contracts.models import relative_path

ROOTS = {'mods': 'Mods', 'plugins': 'Plugins', 'user_libs': 'UserLibs'}
EXECUTABLES = {'.exe', '.com', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.msi', '.scr', '.lnk'}


# ctypes retains POINTER(T) types in a process-wide cache. Define the native
# ABI once: defining T inside each file open grows that cache on every check
# and makes Python.NET's repeated shutdown collections increasingly expensive.
class _UnicodeString(ctypes.Structure):
    _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT),
                ('Buffer', wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [('Length', wintypes.ULONG), ('RootDirectory', wintypes.HANDLE),
                ('ObjectName', ctypes.POINTER(_UnicodeString)), ('Attributes', wintypes.ULONG),
                ('SecurityDescriptor', ctypes.c_void_p), ('SecurityQualityOfService', ctypes.c_void_p)]


class _IoStatus(ctypes.Structure):
    _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]


class _RenameInformation(ctypes.Structure):
    _fields_ = [('ReplaceIfExists', wintypes.ULONG), ('RootDirectory', wintypes.HANDLE),
                ('FileNameLength', wintypes.ULONG), ('FileName', wintypes.WCHAR * 1)]


class GuardRejected(ValueError):
    def __init__(self, code, native_error=None, attempts=1):
        super().__init__(code)
        self.code = code
        self.native_error = native_error
        self.attempts = attempts


def canonical_relative(value):
    try:
        relative_path(value)
        if len(value) > 240 or len(value.split('/')) > 16 or any(
                '~' in part or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?', part)
                for part in value.split('/')):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise GuardRejected('GUARD_PATH_INVALID') from None
    return value


def local_path(value):
    # Check raw components BEFORE pathlib can discard a dot or duplicate separator.
    raw = str(value).replace('\\', '/')
    if not re.match(r'^[A-Za-z]:/', raw) or raw.startswith('//'):
        raise GuardRejected('GUARD_PATH_INVALID')
    if raw[3:]:
        canonical_relative(raw[3:])
    if os.name != 'nt':
        raise GuardRejected('GUARD_PATH_UNAVAILABLE')
    return Path(raw)


def file_identity(info):
    return f'{info[4]:x}:{((info[8] << 32) | info[9]):x}'


def _information(handle, directory):
    import win32file
    info = win32file.GetFileInformationByHandle(handle)
    if info[0] & 0x400:
        raise GuardRejected('GUARD_REPARSE_POINT')
    if bool(info[0] & 0x10) != directory:
        raise GuardRejected('GUARD_PATH_UNAVAILABLE')
    if not directory and info[7] != 1:
        raise GuardRejected('GUARD_HARDLINK')
    if not (info[8] or info[9]):
        raise GuardRejected('GUARD_PATH_UNAVAILABLE')
    return info


def _relative_handle(parent, name, access, *, directory, create, recovery_reparse=False):
    """Open one component relative to a live directory, never a checked string.

    Sharing modes cannot prohibit attribute-only FSCTL_SET_REPARSE_POINT.
    RootDirectory + OBJ_DONT_REPARSE is the safety boundary for those races.
    """
    import pywintypes
    canonical_relative(name)
    if '/' in name:
        raise GuardRejected('GUARD_PATH_INVALID')

    buffer = ctypes.create_unicode_buffer(name)
    encoded_size = len(name.encode('utf-16-le'))
    text = _UnicodeString(encoded_size, encoded_size + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), int(parent), ctypes.pointer(text),
        0x40 | (0 if recovery_reparse else 0x1000), None, None)  # CASE_INSENSITIVE | DONT_REPARSE
    result, io = wintypes.HANDLE(), _IoStatus()
    native = ctypes.WinDLL('ntdll').NtCreateFile
    native.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
                       ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatus), ctypes.c_void_p,
                       wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                       ctypes.c_void_p, wintypes.ULONG]
    native.restype = ctypes.c_long
    # Rename's kernel target-directory open needs write sharing. Directory
    # handles still deny DELETE, so an ancestor cannot be renamed/rebound.
    # Actual files retain READ-only sharing and prohibit other writers/deleters.
    sharing = 3 if directory else 1
    for attempt in range(3):
        status = native(ctypes.byref(result), access | 0x100000, ctypes.byref(attributes),
                        ctypes.byref(io), None, 0x80, sharing, 2 if create else 1,
                        0x00200000 | 0x20 | (1 if directory else 0x40), None, 0)
        if status & 0xffffffff != 0xc0000043 or attempt==2:
            break
        time.sleep(.02*(attempt+1))
    if status < 0:
        code = status & 0xffffffff
        if code in (0xc0000034, 0xc000003a, 0xc000000f):
            raise FileNotFoundError(name)
        if code in (0xc000050b, 0xc0000279, 0x8000002d):
            raise GuardRejected('GUARD_REPARSE_POINT')
        raise GuardRejected('GUARD_PATH_UNAVAILABLE',native_error={0xc0000043:32,0xc0000022:5,0xc000007f:112}.get(code),attempts=attempt+1)
    return pywintypes.HANDLE(result.value)


def open_handle(path, *, directory=False, update=False, create=False, parent=None, execute=False,
                delete=False):
    import win32con
    import win32file
    import pywintypes
    access = win32con.GENERIC_READ
    if execute:
        access |= win32con.GENERIC_EXECUTE
    if update or create or delete:
        access |= win32con.GENERIC_WRITE
    if delete:
        access |= win32con.DELETE
    try:
        if parent is not None:
            handle = _relative_handle(parent, Path(path).name, access, directory=directory, create=create)
        else:
            if create:
                raise GuardRejected('GUARD_PATH_INVALID')
            sharing = win32con.FILE_SHARE_READ | (win32con.FILE_SHARE_WRITE if directory else 0)
            handle = win32file.CreateFile(str(path), access, sharing, None,
                win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS | 0x00200000, None)
    except pywintypes.error as exc:
        if exc.winerror in (2, 3):
            raise FileNotFoundError(str(path)) from None
        raise GuardRejected('GUARD_PATH_UNAVAILABLE',native_error=exc.winerror) from None
    try:
        _information(handle, directory)
    except BaseException:
        handle.Close()
        raise
    return handle


class DirectoryChain(tuple):
    def __new__(cls, identities, handles):
        value = super().__new__(cls, identities)
        value.handles = handles
        return value

    def check(self):
        for (_, expected), handle in zip(self, self.handles):
            if file_identity(_information(handle, True)) != expected:
                raise GuardRejected('GUARD_IDENTITY_CHANGED')


@contextmanager
def pinned_chain(path):
    """Pin every directory against rename; reject in-place reparse changes.

    Attribute-only reparse changes cannot be prevented by sharing. Relative
    opens use OBJ_DONT_REPARSE; native rename uses the actual parent handle and
    refuses a parent that becomes a reparse point instead of following it.
    """
    path = local_path(path)
    with ExitStack() as stack:
        identities, handles = [], []
        for parent in (*reversed(path.parents), path):
            handle = open_handle(parent, directory=True, parent=handles[-1] if handles else None)
            stack.callback(handle.Close)
            handles.append(handle)
            identities.append((str(parent).casefold(), file_identity(_information(handle, True))))
        chain = DirectoryChain(identities, handles)
        chain.check()
        yield chain


@contextmanager
def pinned_file(path, *, execute=False):
    path = local_path(path)
    with pinned_chain(path.parent) as chain:
        handle = open_handle(path, parent=chain.handles[-1], execute=execute)
        try:
            chain.check()
            yield handle
        finally:
            handle.Close()


def strong_hash(handle, *, limit=512 * 1024 * 1024, deadline=None, check=lambda: None):
    import win32file
    deadline = deadline or time.monotonic() + 30
    info = _information(handle, False)
    size = (info[5] << 32) | info[6]
    if size > limit:
        raise GuardRejected('GUARD_PATH_UNAVAILABLE')
    win32file.SetFilePointer(handle, 0, 0)
    digest = hashlib.sha256()
    remaining = size
    check()
    while remaining:
        check()
        if time.monotonic() > deadline:
            raise GuardRejected('GUARD_PATH_UNAVAILABLE')
        _, data = win32file.ReadFile(handle, min(256 * 1024, remaining))
        if not data:
            raise GuardRejected('GUARD_IDENTITY_CHANGED')
        digest.update(data)
        remaining -= len(data)
    win32file.SetFilePointer(handle, 0, 0)
    check()
    return digest.hexdigest()


def native_rename(handle, parent_handle, leaf):
    """Move one actual pinned file to an absent name under a pinned directory.

    Internal consumers must retain both complete directory chains. This helper
    never opens a pathname and never replaces an existing destination. One
    rename is a namespace operation; a two-rename replacement requires a
    persistent journal for the interval in which the original name is absent.
    """
    canonical_relative(leaf)
    if '/' in leaf:
        raise GuardRejected('GUARD_PATH_INVALID')
    source_info = _information(handle, False)
    parent_info = _information(parent_handle, True)
    if source_info[4] != parent_info[4]:
        raise GuardRejected('GUARD_PATH_UNAVAILABLE')
    _rename_handle(handle, parent_handle, leaf)
    # The caller retains ownership of the same handle even if a concurrent
    # attribute-only attack makes this postcheck fail after the native move.
    # Only quarantine_file may then move that known identity out of the scan
    # tree; the normal deployment path continues to reject reparse objects.
    if file_identity(_information(handle, False)) != file_identity(source_info):
        raise GuardRejected('GUARD_IDENTITY_CHANGED')
    if file_identity(_information(parent_handle, True)) != file_identity(parent_info):
        raise GuardRejected('GUARD_IDENTITY_CHANGED')


def _rename_handle(handle, parent_handle, leaf):
    """Native NO_REPLACE operation; callers validate their actual handles first."""

    encoded = leaf.encode('utf-16-le')
    # Include the entire declared structure and trailing UTF-16 name. The
    # zeroed ReplaceIfExists union is FALSE; POSIX/replace flags are never used.
    size = ctypes.sizeof(_RenameInformation) + len(encoded)
    buffer = ctypes.create_string_buffer(size)
    information = _RenameInformation.from_buffer(buffer)
    information.RootDirectory = int(parent_handle)
    information.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + _RenameInformation.FileName.offset,
                   encoded, len(encoded))
    io = _IoStatus()
    native = ctypes.WinDLL('ntdll').NtSetInformationFile
    native.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatus), ctypes.c_void_p,
                       wintypes.ULONG, wintypes.ULONG]
    native.restype = ctypes.c_long
    status = native(int(handle), ctypes.byref(io), buffer, size, 10)  # FileRenameInformation
    if status != 0:
        code = status & 0xffffffff
        if code == 0xc0000035:  # STATUS_OBJECT_NAME_COLLISION
            raise GuardRejected('GUARD_IDENTITY_CHANGED')
        if code in (0xc0000280, 0xc000050b, 0xc0000279, 0x8000002d):
            raise GuardRejected('GUARD_REPARSE_POINT')
        raise GuardRejected('GUARD_PATH_UNAVAILABLE',native_error={0xc0000043:32,0xc0000022:5,0xc000007f:112}.get(code))


def _quarantine_information(handle, expected_identity):
    """Inspect the file object itself; never follow or read a reparse target."""
    import win32file
    if not isinstance(expected_identity, str) or not re.fullmatch(r'[0-9a-f]+:[0-9a-f]+', expected_identity):
        raise GuardRejected('GUARD_IDENTITY_CHANGED')
    information = win32file.GetFileInformationByHandle(handle)
    if not (information[8] or information[9]):
        raise GuardRejected('GUARD_PATH_UNAVAILABLE')
    if information[0] & 0x10 or file_identity(information) != expected_identity:
        raise GuardRejected('GUARD_IDENTITY_CHANGED')
    if information[7] < 1:
        raise GuardRejected('GUARD_IDENTITY_CHANGED')
    return information


def quarantine_file(handle, private_parent_handle, leaf, expected_identity):
    """Isolate one journal-authenticated file object, even if it became reparse.

    Recovery callers must retain/validate the complete private directory chain
    outside every game scan root. This is only a same-volume NO_REPLACE move:
    it does not open a pathname, read a target, delete metadata, or restore this
    object into a game directory. A newly created hardlink does not prevent
    isolating the fixed primary entry; unknown aliases are never touched.
    Normal apply must use native_rename, which still rejects multiple links.
    """
    canonical_relative(leaf)
    if '/' in leaf:
        raise GuardRejected('GUARD_PATH_INVALID')
    source_info = _quarantine_information(handle, expected_identity)
    parent_info = _information(private_parent_handle, True)
    if source_info[4] != parent_info[4]:
        raise GuardRejected('GUARD_PATH_UNAVAILABLE')
    _rename_handle(handle, private_parent_handle, leaf)
    _quarantine_information(handle, expected_identity)
    if file_identity(_information(private_parent_handle, True)) != file_identity(parent_info):
        raise GuardRejected('GUARD_IDENTITY_CHANGED')


def open_recovery_handle(parent_handle, leaf, expected_identity):
    """Reopen only a journal-authenticated leaf object for quarantine after crash.

    The final component uses OPEN_REPARSE_POINT and permits opening its reparse
    object itself. The parent remains a checked, pinned directory. No writable
    data access is requested, and a handle is returned only after matching the
    expected physical file identity and rechecking the parent. This internal
    capability is never a general apply/bridge open or a reparse-target reader.
    """
    canonical_relative(leaf)
    if '/' in leaf:
        raise GuardRejected('GUARD_PATH_INVALID')
    if not isinstance(expected_identity, str) or not re.fullmatch(r'[0-9a-f]+:[0-9a-f]+', expected_identity):
        raise GuardRejected('GUARD_IDENTITY_CHANGED')
    parent_identity = file_identity(_information(parent_handle, True))
    handle = _relative_handle(parent_handle, leaf, 0x80 | 0x10000, directory=False,
                              create=False, recovery_reparse=True)  # READ_ATTRIBUTES | DELETE
    try:
        _quarantine_information(handle, expected_identity)
        if file_identity(_information(parent_handle, True)) != parent_identity:
            raise GuardRejected('GUARD_IDENTITY_CHANGED')
    except BaseException:
        handle.Close()
        raise
    return handle


def flush_file(handle):
    """Flush a live writable file; this does not promise hardware power-loss durability."""
    import pywintypes
    import win32file
    before = file_identity(_information(handle, False))
    try:
        win32file.FlushFileBuffers(handle)
    except pywintypes.error as exc:
        raise GuardRejected('GUARD_PATH_UNAVAILABLE',native_error=exc.winerror) from None
    if file_identity(_information(handle, False)) != before:
        raise GuardRejected('GUARD_IDENTITY_CHANGED')


@dataclass(frozen=True)
class TargetSnapshot:
    installation_identity: str
    root: str
    root_kind: str
    relative_path: str
    ancestors: tuple
    target_identity: str | None
    before_hash: str | None


def target_path(root, root_kind, relative):
    if root_kind not in ROOTS:
        # Supported configuration fields require a future explicit adapter.
        raise GuardRejected('GUARD_TARGET_NOT_ALLOWED')
    relative = canonical_relative(relative)
    if Path(relative).suffix.casefold() in EXECUTABLES:
        raise GuardRejected('GUARD_TARGET_NOT_ALLOWED')
    return local_path(root) / ROOTS[root_kind] / relative


def capture_target(root, installation_identity, root_kind, relative):
    target = target_path(root, root_kind, relative)
    try:
        with pinned_chain(target.parent) as ancestors:
            actual_root = dict(ancestors).get(str(local_path(root)).casefold())
            if actual_root != installation_identity:
                raise GuardRejected('GUARD_INSTALLATION_CHANGED')
            try:
                handle = open_handle(target, parent=ancestors.handles[-1])
            except FileNotFoundError:
                identity, digest = None, None
            else:
                try:
                    identity = file_identity(_information(handle, False))
                    digest = strong_hash(handle)
                finally:
                    handle.Close()
            ancestors.check()
            return TargetSnapshot(installation_identity, str(local_path(root)), root_kind,
                                  relative, tuple(ancestors), identity, digest)
    except FileNotFoundError:
        # No implicit mkdir in a guard; the executor must guard directory creation.
        raise GuardRejected('GUARD_PATH_UNAVAILABLE') from None


class PinnedTarget:
    """Internal consumer uses this live handle, never reopens a checked pathname."""
    def __init__(self, path, handle, check, ancestors):
        self.path = path
        self.handle = handle
        self._check = check
        self._ancestors = ancestors
        self._closed = False

    @property
    def parent_handle(self):
        if self._closed:
            raise GuardRejected('GUARD_IDENTITY_CHANGED')
        self._ancestors.check()
        return self._ancestors.handles[-1]

    @property
    def leaf(self):
        return self.path.name

    def create_new(self):
        if self._closed or self.handle is not None:
            raise GuardRejected('GUARD_IDENTITY_CHANGED')
        self._check()
        self._ancestors.check()
        # CREATE_NEW refuses a late file/link instead of following or truncating it.
        self.handle = open_handle(self.path, create=True, parent=self._ancestors.handles[-1])
        self._ancestors.check()
        return self.handle

    def rename_to(self, parent_handle, leaf):
        if self._closed or self.handle is None:
            raise GuardRejected('GUARD_IDENTITY_CHANGED')
        self._check()
        self._ancestors.check()
        native_rename(self.handle, parent_handle, leaf)
        self._ancestors.check()

    def close(self):
        self._closed = True
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


@contextmanager
def guarded_target(snapshot, check, *, delete=False):
    target = target_path(snapshot.root, snapshot.root_kind, snapshot.relative_path)
    try:
        with pinned_chain(target.parent) as ancestors:
            if ancestors != snapshot.ancestors:
                raise GuardRejected('GUARD_IDENTITY_CHANGED')
            try:
                handle = open_handle(target, update=True, parent=ancestors.handles[-1], delete=delete)
            except FileNotFoundError:
                handle = None
            pinned = PinnedTarget(target, handle, check, ancestors)
            try:
                actual = file_identity(_information(handle, False)) if handle is not None else None
                if actual != snapshot.target_identity:
                    raise GuardRejected('GUARD_IDENTITY_CHANGED')
                if handle is not None and strong_hash(handle) != snapshot.before_hash:
                    raise GuardRejected('GUARD_IDENTITY_CHANGED')
                ancestors.check()
                check()
                yield pinned
                ancestors.check()
                if pinned.handle is not None:
                    _information(pinned.handle, False)
            finally:
                pinned.close()
    except FileNotFoundError:
        raise GuardRejected('GUARD_PATH_UNAVAILABLE') from None
