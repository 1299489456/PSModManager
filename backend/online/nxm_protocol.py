"""Opt-in Windows NXM association and same-user, memory-only activation inbox."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

MAX_MESSAGE = 8192
MAX_PENDING = 16
TIMEOUT = 3.0
INVALID = '下载链接不是本游戏的有效 Nexus 授权链接。'
PIPE_ERROR = '无法把下载请求交给已运行的管理器，请稍后重试。'
ACTIVATION_ERROR = '已有管理器实例未响应，请正常关闭已有窗口后重新打开。'
CLASSES = r'Software\Classes\nxm'
BACKUP = r'Software\PSModManager\NxmProtocolBackup'
PROGID = 'PSModManager.NXM'
PROG_KEY = 'Software\\Classes\\' + PROGID
CAPABILITIES = r'Software\PSModManager\NxmCapabilities'
REGISTERED_APPS = r'Software\RegisteredApplications'
REGISTERED_NAME = 'PSModManager'


class NxmProtocolError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _authorization(value):
    if not isinstance(value, str) or not value or len(value) > MAX_MESSAGE or any(ord(c) < 33 for c in value):
        raise NxmProtocolError('NEXUS_URL_REJECTED', INVALID)
    try:
        parsed = urlsplit(value)
        match = re.fullmatch(r'/mods/([1-9][0-9]{0,15})/files/([1-9][0-9]{0,15})', parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if (parsed.scheme != 'nxm' or parsed.netloc != 'probablystolen' or not match or parsed.fragment
                or set(query) != {'key', 'expires', 'user_id'} or any(len(v) != 1 for v in query.values())):
            raise ValueError
        if any(int(value) > 2 ** 53 - 1 for value in match.groups()):
            raise ValueError
        key, expires, user = (query[k][0] for k in ('key', 'expires', 'user_id'))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', key) or not re.fullmatch(r'[0-9]{1,12}', expires) or not re.fullmatch(r'[1-9][0-9]{0,11}', user):
            raise ValueError
    except (ValueError, TypeError):
        raise NxmProtocolError('NEXUS_URL_REJECTED', INVALID) from None
    if int(expires) <= time.time():
        raise NxmProtocolError('NEXUS_LINK_EXPIRED', '管理器下载链接已过期，请在官网重新生成。')
    return int(match[1]), int(match[2]), key, int(expires), user


def parse_nxm(value):
    mod, file, *_ = _authorization(value)
    return {'mod_id': mod, 'file_id': file}


class NxmInbox:
    def __init__(self):
        self._items = OrderedDict()
        self._leased = set()
        self._seen = OrderedDict()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._pipe = None
        self._wake = None

    def set_wake(self, callback):
        with self._lock:
            self._wake = callback
            pending = bool(self._items)
        if pending:
            self.wake()

    def wake(self):
        with self._lock:
            callback = None if self._stop.is_set() else self._wake
        if callback:
            try:
                callback()
            except Exception:
                # A temporarily unavailable window must not discard its download.
                pass

    def add(self, value):
        identity = _authorization(value)
        with self._lock:
            if self._stop.is_set():
                raise ValueError('管理器已关闭，请重新打开。')
            now = time.time()
            self._items = OrderedDict((k, v) for k, v in self._items.items() if v[1][3] > now)
            self._seen = OrderedDict((k, v) for k, v in self._seen.items() if k[3] > now)
            if identity in self._seen:
                return
            if len(self._items) >= MAX_PENDING:
                raise ValueError('待处理下载过多，请先完成或取消已有下载。')
            self._items[uuid4().hex] = (value, identity)
            self._seen[identity] = None
            while len(self._seen) > 256:
                self._seen.popitem(last=False)
        self.wake()

    def peek(self):
        with self._lock:
            self._items = OrderedDict((k, v) for k, v in self._items.items() if v[1][3] > time.time())
            return {'items': [{'id': k, 'mod_id': v[1][0], 'file_id': v[1][1]} for k, v in self._items.items() if k not in self._leased]}

    def claim(self, item_id):
        with self._lock:
            item = self._items.get(item_id)
            if self._stop.is_set() or item is None or item_id in self._leased:
                raise NxmProtocolError('NEXUS_URL_REJECTED', '下载请求已处理或正在下载。')
            _authorization(item[0])
            self._leased.add(item_id)
            return item[0]

    def finish(self, item_id, success):
        with self._lock:
            if item_id not in self._leased:
                return
            self._leased.discard(item_id)
            if success:
                self._items.pop(item_id, None)

    def take(self, item_id):
        with self._lock:
            if item_id in self._leased:
                raise NxmProtocolError('NEXUS_URL_REJECTED', '下载请求正在下载。')
            item = self._items.pop(item_id, None)
        if item is None:
            raise NxmProtocolError('NEXUS_URL_REJECTED', '下载请求已处理或已取消。')
        _authorization(item[0])
        return item[0]

    def discard(self, item_id):
        with self._lock:
            self._items.pop(item_id, None)
            self._leased.discard(item_id)

    def stop_accepting(self):
        self._stop.set()

    def close(self):
        self.stop_accepting()
        if self._thread:
            self._thread.join(TIMEOUT + 1)
        with self._lock:
            self._items.clear()
            self._seen.clear()
            self._leased.clear()
            self._wake = None


def _windows():
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32file
    import win32pipe
    import win32security
    return pywintypes, win32api, win32con, win32event, win32file, win32pipe, win32security


def _identity(data_root):
    py, api, con, event, file, pipe, security = _windows()
    token = security.OpenProcessToken(api.GetCurrentProcess(), con.TOKEN_QUERY)
    try:
        sid = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    # This digest is a stable synchronization namespace, never a link/credential fingerprint.
    name = hashlib.sha256((security.ConvertSidToStringSid(sid) + '\0' + os.path.normcase(str(Path(data_root).resolve()))).encode()).hexdigest()
    # v1 pipes were inheritable: a WebView/helper could keep their name alive
    # after its manager exited. A fresh namespace avoids those orphan handles;
    # v2 handles must never be inherited by any child process.
    return sid, rf'\\.\pipe\PSModManager-activation-v2-{name}'


def _security_attributes(sid):
    py, api, con, event, file, pipe, security = _windows()
    acl = security.ACL()
    acl.AddAccessAllowedAce(security.ACL_REVISION, con.GENERIC_ALL, sid)
    descriptor = security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(sid, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    attributes = py.SECURITY_ATTRIBUTES()
    attributes.bInheritHandle = False
    attributes.SECURITY_DESCRIPTOR = descriptor
    return attributes


def _io(handle, operation, stop=None):
    py, api, con, event, file, pipe, security = _windows()
    overlapped = py.OVERLAPPED()
    overlapped.hEvent = event.CreateEvent(None, True, False, None)
    try:
        result = operation(overlapped)
        deadline = time.monotonic() + TIMEOUT
        while event.WaitForSingleObject(overlapped.hEvent, 50) != con.WAIT_OBJECT_0:
            if time.monotonic() >= deadline or (stop and stop.is_set()):
                file.CancelIo(handle)
                event.WaitForSingleObject(overlapped.hEvent, event.INFINITE)
                raise OSError('pipe operation timed out')
        count = file.GetOverlappedResult(handle, overlapped, False)
        return result, count
    finally:
        overlapped.hEvent.Close()


def _read(handle, stop=None):
    py, api, con, event, file, pipe, security = _windows()
    buffer = file.AllocateReadBuffer(MAX_MESSAGE)
    _, count = _io(handle, lambda ov: file.ReadFile(handle, buffer, ov), stop)
    return bytes(buffer[:count])


def _write(handle, value, stop=None):
    file = _windows()[4]
    _io(handle, lambda ov: file.WriteFile(handle, value, ov), stop)


def _serve(inbox):
    py, api, con, event, file, pipe, security = _windows()
    handle = inbox._pipe
    try:
        while not inbox._stop.is_set():
            try:
                try:
                    _io(handle, lambda ov: pipe.ConnectNamedPipe(handle, ov), inbox._stop)
                except py.error as exc:
                    if exc.winerror != 535:  # Client connected before ConnectNamedPipe.
                        raise
                raw = _read(handle, inbox._stop).decode('utf-8')
                response = b'OK'
                try:
                    if raw != '\0':
                        inbox.add(raw)
                    else:
                        inbox.wake()
                except ValueError:
                    response = b'REJECTED'
                _write(handle, response, inbox._stop)
                # Wait for receipt before disconnecting; otherwise unread ACK bytes may
                # be discarded. Unlike FlushFileBuffers this read has a deadline.
                _read(handle, inbox._stop)
                # Do not FlushFileBuffers: an unresponsive client must not block shutdown.
            except (OSError, UnicodeError, py.error):
                pass
            finally:
                try:
                    pipe.DisconnectNamedPipe(handle)
                except py.error:
                    pass
    finally:
        handle.Close()


def _forward(name, sid, value):
    py, api, con, event, file, pipe, security = _windows()
    deadline = time.monotonic() + TIMEOUT
    while True:
        try:
            # READ_CONTROL permits checking the server owner before sending authorization.
            handle = file.CreateFile(name, con.GENERIC_READ | con.GENERIC_WRITE | con.READ_CONTROL, 0,
                                     None, con.OPEN_EXISTING, con.FILE_FLAG_OVERLAPPED | 0x00110000, None)
            break
        except py.error as exc:
            if exc.winerror not in (2, 231) or time.monotonic() >= deadline:
                raise ValueError(PIPE_ERROR) from None
            time.sleep(.05)
    try:
        owner = security.GetSecurityInfo(handle, security.SE_KERNEL_OBJECT, security.OWNER_SECURITY_INFORMATION).GetSecurityDescriptorOwner()
        if owner != sid:
            raise ValueError(PIPE_ERROR)
        pipe.SetNamedPipeHandleState(handle, pipe.PIPE_READMODE_MESSAGE, None, None)
        _write(handle, value.encode('utf-8') if value else b'\0')
        response = _read(handle)
        _write(handle, b'DONE')
        if response != b'OK':
            raise ValueError('管理器未接收下载请求，请检查链接或先处理待下载项目。')
    except (OSError, py.error):
        raise ValueError(PIPE_ERROR) from None
    finally:
        handle.Close()


def start_or_forward(data_root, nxm_url=None):
    if nxm_url is not None:
        _authorization(nxm_url)
    inbox = NxmInbox()
    if sys.platform != 'win32':
        if nxm_url:
            inbox.add(nxm_url)
        return inbox
    py, api, con, event, file, pipe, security = _windows()
    sid, name = _identity(data_root)
    try:
        inbox._pipe = pipe.CreateNamedPipe(name, pipe.PIPE_ACCESS_DUPLEX | con.FILE_FLAG_OVERLAPPED | 0x00080000,
                                          pipe.PIPE_TYPE_MESSAGE | pipe.PIPE_READMODE_MESSAGE | 0x8,
                                          1, MAX_MESSAGE, MAX_MESSAGE, int(TIMEOUT * 1000), _security_attributes(sid))
    except py.error as exc:
        if exc.winerror not in (5, 231):
            raise ValueError('无法启动管理器下载接收通道。') from None
        try:
            _forward(name, sid, nxm_url or '')
        except ValueError:
            if nxm_url is None:
                raise ValueError(ACTIVATION_ERROR) from None
            raise
        return None
    try:
        if nxm_url:
            inbox.add(nxm_url)
    except ValueError:
        inbox._pipe.Close()
        raise
    inbox._thread = threading.Thread(target=_serve, args=(inbox,), name='nxm-inbox', daemon=True)
    inbox._thread.start()
    return inbox


def _command(data_root):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return None
    root = str(Path(data_root).resolve())
    executable = str(Path(sys.executable).resolve())
    if any(c in root + executable for c in ('"', '\r', '\n', '%')):
        raise ValueError('程序或数据目录路径不支持协议注册。')
    return f'"{executable}" --psmm-data-root "{root}" --nxm "%1"'


def _registry():
    import winreg
    return winreg


def _notify_association():
    from win32com.shell import shell, shellcon
    shell.SHChangeNotify(shellcon.SHCNE_ASSOCCHANGED, shellcon.SHCNF_IDLIST, None, None)


def _tree(reg, path):
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, path) as key:
            children, values, _ = reg.QueryInfoKey(key)
            result = {'values': [reg.EnumValue(key, i) for i in range(values)], 'children': {}}
            # JSON handles all normal protocol values; REG_BINARY needs a reversible encoding.
            result['values'] = [(n, list(v) if isinstance(v, bytes) else v, t) for n, v, t in result['values']]
            result['children'] = {reg.EnumKey(key, i): _tree(reg, path + '\\' + reg.EnumKey(key, i)) for i in range(children)}
            return result
    except FileNotFoundError:
        return None


def _remove(reg, path):
    tree = _tree(reg, path)
    if tree is not None:
        for child in tree['children']:
            _remove(reg, path + '\\' + child)
        reg.DeleteKey(reg.HKEY_CURRENT_USER, path)


def _restore(reg, path, tree):
    if tree is None:
        return
    with reg.CreateKey(reg.HKEY_CURRENT_USER, path) as key:
        for name, value, kind in tree['values']:
            reg.SetValueEx(key, name, 0, kind, bytes(value) if kind in (reg.REG_BINARY, 0) and isinstance(value, list) else value)
    for name, child in tree['children'].items():
        _restore(reg, path + '\\' + name, child)


def _value(reg, root, path, name=''):
    try:
        with reg.OpenKey(root, path) as key:
            return reg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def protocol_status(data_root):
    command = _command(data_root)
    result = {'available': command is not None, 'owned': False, 'registered': False, 'conflict': False}
    if command is None:
        return {**result, 'message': '请在打包后的 Windows 程序中启用官网下载接回。'}
    reg = _registry()
    current = _value(reg, reg.HKEY_CLASSES_ROOT, r'nxm\shell\open\command')
    choice = _value(reg, reg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\nxm\UserChoice', 'ProgId')
    choice_command = _value(reg, reg.HKEY_CLASSES_ROOT, str(choice) + r'\shell\open\command') if choice else None
    effective = choice_command if choice else current
    result['owned'] = (any(_value(reg, reg.HKEY_CURRENT_USER, path, 'PSModManagerCommand') == command
                           and _value(reg, reg.HKEY_CURRENT_USER, path + r'\shell\open\command') == command
                           for path in (CLASSES, PROG_KEY))
                       or _value(reg, reg.HKEY_CURRENT_USER, CAPABILITIES, 'PSModManagerCommand') == command)
    result['selectable'] = (_value(reg, reg.HKEY_CURRENT_USER, PROG_KEY + r'\shell\open\command') == command
                            and _value(reg, reg.HKEY_CURRENT_USER, CAPABILITIES + r'\URLAssociations', 'nxm') == PROGID
                            and _value(reg, reg.HKEY_CURRENT_USER, REGISTERED_APPS, REGISTERED_NAME) == CAPABILITIES)
    result['registered'] = result['owned'] and effective == command
    result['conflict'] = bool((effective and effective != command) or (choice and choice_command != command))
    result['default_choice_required'] = bool(choice and choice_command != command)
    return result


def open_protocol_settings():
    if sys.platform != 'win32':
        raise ValueError('默认应用设置仅适用于 Windows。')
    os.startfile('ms-settings:defaultapps')
    return {'opened': True}


def _registered_value(reg):
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, REGISTERED_APPS) as key:
            return reg.QueryValueEx(key, REGISTERED_NAME)
    except FileNotFoundError:
        return None


def _set_registered_value(reg, value):
    if value is None:
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, REGISTERED_APPS, 0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, REGISTERED_NAME)
        except FileNotFoundError:
            pass
    else:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, REGISTERED_APPS) as key:
            reg.SetValueEx(key, REGISTERED_NAME, 0, value[1], value[0])


def _snapshot_registration(reg):
    return ({path: _tree(reg, path) for path in (CLASSES, PROG_KEY, CAPABILITIES, BACKUP)}, _registered_value(reg))


def _rollback_registration(reg, snapshot):
    trees, registered = snapshot
    for path, tree in trees.items():
        _remove(reg, path)
        _restore(reg, path, tree)
    _set_registered_value(reg, registered)


def register_protocol(data_root, replace=False):
    status = protocol_status(data_root)
    if not status['available'] or (status['registered'] and status['selectable']) or (status['conflict'] and not replace):
        return status
    reg, command = _registry(), _command(data_root)
    saved = _value(reg, reg.HKEY_CURRENT_USER, BACKUP, 'Command')
    if saved is not None and saved != command and not replace:
        return {**status, 'conflict': True, 'message': '另一份管理器已注册接收链接，是否改用当前管理器？'}
    old_manager = (saved is not None and _value(reg, reg.HKEY_CURRENT_USER, CLASSES, 'PSModManagerCommand') == saved
                   and _value(reg, reg.HKEY_CURRENT_USER, CLASSES + r'\shell\open\command') == saved)
    snapshot = _snapshot_registration(reg)
    auxiliary = json.loads(_value(reg, reg.HKEY_CURRENT_USER, BACKUP, 'Auxiliary') or '{}')
    for path in (PROG_KEY, CAPABILITIES):
        if not saved or _value(reg, reg.HKEY_CURRENT_USER, path, 'PSModManagerCommand') != saved:
            auxiliary[path] = _tree(reg, path)
    if not saved or _value(reg, reg.HKEY_CURRENT_USER, CAPABILITIES, 'PSModManagerCommand') != saved:
        auxiliary['registered'] = _registered_value(reg)
    association = {'values': [('', 'URL:Nexus Mod Download', reg.REG_SZ), ('URL Protocol', '', reg.REG_SZ),
                                    ('PSModManagerCommand', command, reg.REG_SZ)], 'children': {
        'shell': {'values': [], 'children': {'open': {'values': [], 'children': {
            'command': {'values': [('', command, reg.REG_SZ)], 'children': {}}}}}}}}
    try:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, BACKUP) as key:
            if not old_manager:
                reg.SetValueEx(key, 'Previous', 0, reg.REG_SZ, json.dumps(_tree(reg, CLASSES)))
            reg.SetValueEx(key, 'Command', 0, reg.REG_SZ, command)
            reg.SetValueEx(key, 'Auxiliary', 0, reg.REG_SZ, json.dumps(auxiliary))
        _remove(reg, CLASSES)
        _restore(reg, CLASSES, association)
        _remove(reg, PROG_KEY)
        _restore(reg, PROG_KEY, association)
        _remove(reg, CAPABILITIES)
        _restore(reg, CAPABILITIES, {'values': [('ApplicationName', 'PSModManager', reg.REG_SZ),
            ('ApplicationDescription', '接收 Nexus Mod 下载链接', reg.REG_SZ), ('PSModManagerCommand', command, reg.REG_SZ)],
            'children': {'URLAssociations': {'values': [('nxm', PROGID, reg.REG_SZ)], 'children': {}}}})
        _set_registered_value(reg, (CAPABILITIES, reg.REG_SZ))
    except OSError:
        _rollback_registration(reg, snapshot)
        raise ValueError('未能启用链接接收，原处理程序已恢复。') from None
    _notify_association()
    result = protocol_status(data_root)
    if result.get('default_choice_required'):
        result['message'] = 'Windows 已指定其他默认程序，请在默认应用设置中把 nxm 链接改为本管理器。'
    return result


def unregister_protocol(data_root):
    status = protocol_status(data_root)
    if not status['available']:
        return status
    reg, command = _registry(), _command(data_root)
    if not status['owned']:
        return {**status, 'message': '当前链接处理程序已改变，未修改其他程序的设置。'}
    if _value(reg, reg.HKEY_CURRENT_USER, BACKUP, 'Command') != command:
        raise ValueError('找不到原链接处理程序记录，未移除现有设置。')
    previous = json.loads(_value(reg, reg.HKEY_CURRENT_USER, BACKUP, 'Previous'))
    auxiliary = json.loads(_value(reg, reg.HKEY_CURRENT_USER, BACKUP, 'Auxiliary') or '{}')
    snapshot = _snapshot_registration(reg)
    try:
        if (_value(reg, reg.HKEY_CURRENT_USER, CLASSES, 'PSModManagerCommand') == command
                and _value(reg, reg.HKEY_CURRENT_USER, CLASSES + r'\shell\open\command') == command):
            _remove(reg, CLASSES)
            _restore(reg, CLASSES, previous)
        if _value(reg, reg.HKEY_CURRENT_USER, CAPABILITIES, 'PSModManagerCommand') == command:
            if _value(reg, reg.HKEY_CURRENT_USER, REGISTERED_APPS, REGISTERED_NAME) == CAPABILITIES:
                _set_registered_value(reg, auxiliary.get('registered'))
            _remove(reg, CAPABILITIES)
            _restore(reg, CAPABILITIES, auxiliary.get(CAPABILITIES))
        if (_value(reg, reg.HKEY_CURRENT_USER, PROG_KEY, 'PSModManagerCommand') == command
                and _value(reg, reg.HKEY_CURRENT_USER, PROG_KEY + r'\shell\open\command') == command):
            _remove(reg, PROG_KEY)
            _restore(reg, PROG_KEY, auxiliary.get(PROG_KEY))
        _remove(reg, BACKUP)
    except OSError:
        _rollback_registration(reg, snapshot)
        raise ValueError('未能停用链接接收，现有设置已恢复，可重试。') from None
    _notify_association()
    result = protocol_status(data_root)
    choice = _value(reg, reg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\nxm\UserChoice', 'ProgId')
    if choice == PROGID and result['default_choice_required']:
        result['message'] = '已停用本管理器的链接接收；Windows 仍将 nxm 默认应用指向本管理器，请打开默认应用设置，改选原来的 Mod 管理器。'
    return result
