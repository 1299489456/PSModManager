"""No real registry writes, game data, or external Nexus requests."""
import json
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from backend.online import nxm_protocol as nxm


def link(mod=12, file=34, key='synthetic', expires=9999999999):
    return f'nxm://probablystolen/mods/{mod}/files/{file}?key={key}&expires={expires}&user_id=5'


@pytest.mark.parametrize('value', [
    '', 'https://probablystolen/mods/12/files/34', link().replace('probablystolen', 'skyrim'),
    link().replace('/12/', '/0/'), link() + '&other=1', link() + '&key=other', link() + '#fragment',
    link().replace('key=synthetic', 'key='), link().replace('key=synthetic', 'key=bad%22value'),
    link().replace('user_id=5', 'user_id=0'), link().replace('expires=9999999999', 'expires=1'),
    link() + '\n', link().replace('probablystolen', 'user@probablystolen'), 'x' * 9000,
])
def test_rejects_invalid_without_echoing_authorization(value):
    with pytest.raises(ValueError) as error:
        nxm.parse_nxm(value)
    assert 'synthetic' not in str(error.value)
    assert 'nxm://' not in str(error.value)


def test_inbox_one_time_redacted_deduplicated_and_closed():
    inbox = nxm.NxmInbox()
    inbox.add(link())
    inbox.add(link())
    assert len(inbox.peek()['items']) == 1
    item = inbox.peek()['items'][0]
    assert set(item) == {'id', 'mod_id', 'file_id'}
    assert (item['mod_id'], item['file_id']) == (12, 34)
    assert inbox.take(item['id']) == link()
    with pytest.raises(ValueError):
        inbox.take(item['id'])
    inbox.add(link())
    assert inbox.peek() == {'items': []}
    inbox.add(link(file=35))
    inbox.discard(inbox.peek()['items'][0]['id'])
    assert inbox.peek() == {'items': []}
    inbox.close()
    with pytest.raises(ValueError):
        inbox.add(link(file=36))


def test_queue_bound_and_expiry(monkeypatch):
    inbox = nxm.NxmInbox()
    for file in range(1, nxm.MAX_PENDING + 1):
        inbox.add(link(file=file, expires=2000000000))
    with pytest.raises(ValueError, match='待处理'):
        inbox.add(link(file=99))
    monkeypatch.setattr(nxm.time, 'time', lambda: 2000000001)
    assert inbox.peek() == {'items': []}
    inbox.add(link(file=99))
    assert len(inbox.peek()['items']) == 1


def test_wake_for_cold_and_running_links_and_failed_callback_keeps_item():
    inbox = nxm.NxmInbox()
    inbox.add(link())
    calls = []
    inbox.set_wake(lambda: calls.append(True))
    assert calls == [True]
    inbox.add(link(file=35))
    assert len(calls) == 2
    inbox.set_wake(lambda: 1 / 0)
    inbox.add(link(file=36))
    assert len(inbox.peek()['items']) == 3
    inbox.close()


def test_claim_failure_retries_same_receipt_success_consumes_and_discard_clears_lease():
    inbox = nxm.NxmInbox()
    inbox.add(link())
    receipt = inbox.peek()['items'][0]['id']
    assert inbox.claim(receipt) == link()
    assert inbox.peek()['items'] == []
    with pytest.raises(ValueError):
        inbox.claim(receipt)
    with pytest.raises(ValueError):
        inbox.take(receipt)
    inbox.finish(receipt, False)
    assert inbox.peek()['items'][0]['id'] == receipt
    inbox.claim(receipt)
    inbox.finish(receipt, True)
    assert inbox.peek()['items'] == []
    inbox.add(link(file=35))
    receipt = inbox.peek()['items'][0]['id']
    inbox.claim(receipt)
    inbox.discard(receipt)
    inbox.finish(receipt, False)
    assert inbox.peek()['items'] == []


def test_stop_accepting_rejects_enqueue_and_claim_without_waiting():
    inbox = nxm.NxmInbox()
    inbox.add(link())
    receipt = inbox.peek()['items'][0]['id']
    inbox.stop_accepting()
    with pytest.raises(ValueError):
        inbox.add(link(file=35))
    with pytest.raises(ValueError):
        inbox.claim(receipt)
    inbox.close()
    inbox.close()
    assert inbox.peek()['items'] == []


class Registry:
    HKEY_CURRENT_USER = 'CU'
    HKEY_CLASSES_ROOT = 'CR'
    REG_SZ = 1
    REG_BINARY = 3
    KEY_SET_VALUE = 2

    def __init__(self):
        self.data = {}

    def path(self, root, path):
        return 'Software\\Classes\\' + path if root == self.HKEY_CLASSES_ROOT else path

    @contextmanager
    def OpenKey(self, root, path, *args):
        path = self.path(root, path)
        if path not in self.data:
            raise FileNotFoundError
        yield path

    @contextmanager
    def CreateKey(self, root, path):
        path = self.path(root, path)
        pieces = path.split('\\')
        for i in range(1, len(pieces) + 1):
            self.data.setdefault('\\'.join(pieces[:i]), {})
        yield path

    def QueryInfoKey(self, key):
        return len(self.children(key)), len(self.data[key]), 0

    def children(self, key):
        return [k[len(key) + 1:] for k in self.data if k.startswith(key + '\\') and '\\' not in k[len(key) + 1:]]

    def EnumKey(self, key, index):
        return self.children(key)[index]

    def EnumValue(self, key, index):
        name, (value, kind) = list(self.data[key].items())[index]
        return name, value, kind

    def QueryValueEx(self, key, name):
        if name not in self.data[key]:
            raise FileNotFoundError
        return self.data[key][name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.data[key][name] = (value, kind)

    def DeleteValue(self, key, name):
        if name not in self.data[key]:
            raise FileNotFoundError
        del self.data[key][name]

    def DeleteKey(self, root, path):
        assert not self.children(path)
        del self.data[path]


@pytest.fixture
def registry(monkeypatch):
    reg = Registry()
    monkeypatch.setattr(nxm, '_registry', lambda: reg)
    monkeypatch.setattr(nxm, '_notify_association', lambda: None)
    monkeypatch.setattr(nxm, '_command', lambda root: '"C:\\Program Files\\PSMM.exe" --psmm-data-root "C:\\Data" --nxm "%1"')
    return reg


def test_opt_in_register_unregister_restores_existing_handler(registry):
    reg = registry
    old = {'values': [('', 'Existing handler', 1), ('binary', [1, 2], 3)], 'children': {
        'shell': {'values': [], 'children': {'open': {'values': [], 'children': {
            'command': {'values': [('', '"C:\\Other.exe" "%1"', 1)], 'children': {}}}}}}}}
    nxm._restore(reg, nxm.CLASSES, old)
    before = nxm._tree(reg, nxm.CLASSES)
    assert nxm.register_protocol('root')['conflict']
    assert nxm._tree(reg, nxm.CLASSES) == before
    assert nxm.register_protocol('root', replace=True)['registered']
    assert nxm.register_protocol('root')['registered']
    nxm.unregister_protocol('root')
    assert nxm._tree(reg, nxm.CLASSES) == before
    assert nxm._tree(reg, nxm.BACKUP) is None


def test_unregister_preserves_handler_changed_by_another_app(registry):
    nxm.register_protocol('root')
    with registry.CreateKey('CU', nxm.CLASSES + r'\shell\open\command') as key:
        registry.SetValueEx(key, '', 0, 1, 'other')
    assert nxm.unregister_protocol('root')['conflict']
    assert nxm._value(registry, 'CU', nxm.CLASSES + r'\shell\open\command') == 'other'


def test_fresh_registration_removed_without_touching_other_keys(registry):
    with registry.CreateKey('CU', 'Software\\Other') as key:
        registry.SetValueEx(key, 'Keep', 0, 1, 'yes')
    assert nxm.register_protocol('root')['registered']
    assert not nxm.unregister_protocol('root')['registered']
    assert nxm._tree(registry, nxm.CLASSES) is None
    assert nxm._value(registry, 'CU', 'Software\\Other', 'Keep') == 'yes'


def test_windows_user_choice_not_overwritten(registry):
    choice = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\nxm\UserChoice'
    with registry.CreateKey('CU', choice) as key:
        registry.SetValueEx(key, 'ProgId', 0, 1, 'Other.App')
    result = nxm.register_protocol('root', replace=True)
    assert result['default_choice_required'] and not result['registered']
    assert nxm._value(registry, 'CU', choice, 'ProgId') == 'Other.App'
    assert result['owned'] and result['selectable']
    assert nxm._value(registry, 'CU', nxm.REGISTERED_APPS, nxm.REGISTERED_NAME) == nxm.CAPABILITIES
    assert nxm._value(registry, 'CU', nxm.CAPABILITIES + r'\URLAssociations', 'nxm') == nxm.PROGID
    nxm.unregister_protocol('root')
    assert nxm._value(registry, 'CU', choice, 'ProgId') == 'Other.App'
    assert not nxm.protocol_status('root')['owned']
    assert nxm._tree(registry, nxm.PROG_KEY) is None
    assert nxm._tree(registry, nxm.CAPABILITIES) is None


def test_selected_dedicated_progid_is_effective(registry):
    nxm.register_protocol('root')
    choice = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\nxm\UserChoice'
    with registry.CreateKey('CU', choice) as key:
        registry.SetValueEx(key, 'ProgId', 0, 1, nxm.PROGID)
    assert nxm.protocol_status('root')['registered']
    assert not nxm.protocol_status('root')['default_choice_required']


def test_unregistration_selected_progid_requires_user_default_selection(registry):
    with registry.CreateKey('CU', nxm.CLASSES + r'\shell\open\command') as key:
        registry.SetValueEx(key, '', 0, 1, 'OtherManager')
    nxm.register_protocol('root', replace=True)
    choice = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\nxm\UserChoice'
    with registry.CreateKey('CU', choice) as key:
        registry.SetValueEx(key, 'ProgId', 0, 1, nxm.PROGID)
    result = nxm.unregister_protocol('root')
    assert not result['owned'] and not result['registered']
    assert result['default_choice_required']
    assert '默认应用设置' in result['message']
    assert nxm._value(registry, 'CU', choice, 'ProgId') == nxm.PROGID
    assert nxm._tree(registry, nxm.PROG_KEY) is None
    assert nxm._value(registry, 'CU', nxm.CLASSES + r'\shell\open\command') == 'OtherManager'


def test_settings_link_is_fixed_and_only_explicitly_opened(monkeypatch):
    opened = []
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(nxm.os, 'startfile', lambda value: opened.append(value), raising=False)
    assert opened == []
    assert nxm.open_protocol_settings() == {'opened': True}
    assert opened == ['ms-settings:defaultapps']


def test_replace_after_external_handler_change_restores_latest_handler(registry):
    command_key = nxm.CLASSES + r'\shell\open\command'
    with registry.CreateKey('CU', command_key) as key:
        registry.SetValueEx(key, '', 0, 1, 'Other1')
    nxm.register_protocol('root', replace=True)
    with registry.CreateKey('CU', command_key) as key:
        registry.SetValueEx(key, '', 0, 1, 'Other2')
    nxm.register_protocol('root', replace=True)
    nxm.unregister_protocol('root')
    assert nxm._value(registry, 'CU', command_key) == 'Other2'
    assert not nxm.protocol_status('root')['owned']


@pytest.mark.parametrize('phase', ['register', 'unregister'])
def test_registration_failure_rolls_back_complete_owned_state(registry, monkeypatch, phase):
    command_key = nxm.CLASSES + r'\shell\open\command'
    with registry.CreateKey('CU', command_key) as key:
        registry.SetValueEx(key, '', 0, 1, 'Other')
    if phase == 'unregister':
        nxm.register_protocol('root', replace=True)
    before = nxm._snapshot_registration(registry)
    original = registry.SetValueEx
    failed = []
    def fail_once(key, name, reserved, kind, value):
        target = key == nxm.CAPABILITIES if phase == 'register' else key == command_key and value == 'Other'
        if target and not failed:
            failed.append(True)
            raise PermissionError('synthetic registry write failure')
        return original(key, name, reserved, kind, value)
    monkeypatch.setattr(registry, 'SetValueEx', fail_once)
    with pytest.raises(ValueError):
        (nxm.register_protocol('root', replace=True) if phase == 'register' else nxm.unregister_protocol('root'))
    assert failed
    assert nxm._snapshot_registration(registry) == before
    if phase == 'unregister':
        nxm.unregister_protocol('root')
        assert nxm._value(registry, 'CU', command_key) == 'Other'


def test_command_quotes_executable_root_and_url(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    executable = tmp_path / 'Program Files' / 'PSModManager.exe'
    monkeypatch.setattr(sys, 'executable', str(executable))
    root = tmp_path / 'User Data'
    assert nxm._command(root) == f'"{executable}" --psmm-data-root "{root}" --nxm "%1"'


def test_source_registration_unavailable(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert nxm.protocol_status('anything')['available'] is False


def test_registration_upgrade_rebinds_executable_keeps_original_restore(registry, monkeypatch):
    assert nxm.register_protocol('root')['registered']
    monkeypatch.setattr(nxm, '_command', lambda root: '"C:\\New PSMM.exe" --psmm-data-root "C:\\Data" --nxm "%1"')
    assert nxm.register_protocol('root')['conflict']
    assert nxm.register_protocol('root', replace=True)['registered']
    nxm.unregister_protocol('root')
    assert nxm._tree(registry, nxm.CLASSES) is None


def test_parse_ids_match_safe_integer_contract():
    assert nxm.parse_nxm(link(mod=2 ** 53 - 1))['mod_id'] == 2 ** 53 - 1
    with pytest.raises(nxm.NxmProtocolError) as error:
        nxm.parse_nxm(link(mod=2 ** 53))
    assert error.value.code == 'NEXUS_URL_REJECTED'


def test_expired_take_and_consumed_take_have_safe_error_codes(monkeypatch):
    inbox = nxm.NxmInbox()
    inbox.add(link(expires=2000000000))
    item_id = inbox.peek()['items'][0]['id']
    monkeypatch.setattr(nxm.time, 'time', lambda: 2000000001)
    with pytest.raises(nxm.NxmProtocolError) as error:
        inbox.take(item_id)
    assert error.value.code == 'NEXUS_LINK_EXPIRED'
    with pytest.raises(nxm.NxmProtocolError) as error:
        inbox.take(item_id)
    assert error.value.code == 'NEXUS_URL_REJECTED'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named pipe')
def test_real_same_user_pipe_secondary_process_and_cleanup(tmp_path):
    inbox = nxm.start_or_forward(tmp_path)
    try:
        code = 'from backend.online.nxm_protocol import start_or_forward; import sys; assert start_or_forward(sys.argv[1], sys.argv[2]) is None'
        child = subprocess.run([sys.executable, '-c', code, str(tmp_path), link()], capture_output=True, timeout=10)
        assert child.returncode == 0, child.stderr.decode(errors='replace')
        assert child.stdout == b''
        assert len(inbox.peek()['items']) == 1
        assert nxm.start_or_forward(tmp_path, link()) is None
        assert len(inbox.peek()['items']) == 1
        assert nxm.start_or_forward(tmp_path) is None
        isolated = nxm.start_or_forward(tmp_path / 'different')
        assert isolated is not None
        isolated.close()
        assert not isolated._thread.is_alive()
    finally:
        inbox.close()
    assert not inbox._thread.is_alive()
    restarted = nxm.start_or_forward(tmp_path)
    assert restarted is not None
    restarted.close()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named pipe ACL')
def test_pipe_dacl_only_current_user_and_remote_clients_rejected(tmp_path):
    inbox = nxm.start_or_forward(tmp_path)
    try:
        security = nxm._windows()[-1]
        sid, _ = nxm._identity(tmp_path)
        descriptor = security.GetSecurityInfo(inbox._pipe, security.SE_KERNEL_OBJECT, security.DACL_SECURITY_INFORMATION)
        acl = descriptor.GetSecurityDescriptorDacl()
        assert acl.GetAceCount() == 1
        assert acl.GetAce(0)[2] == sid
        assert not nxm._windows()[1].GetHandleInformation(inbox._pipe) & 1
    finally:
        inbox.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows handle inheritance')
def test_inheriting_child_cannot_keep_primary_pipe_alive_after_manager_close(tmp_path):
    inbox = nxm.start_or_forward(tmp_path)
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'],
        close_fds=False, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        inbox.close()
        assert child.poll() is None
        replacement = nxm.start_or_forward(tmp_path)
        assert replacement is not None
        replacement.close()
    finally:
        inbox.close()
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named pipe migration')
def test_legacy_orphan_pipe_does_not_block_normal_or_protocol_startup(tmp_path):
    py, api, con, event, file, pipe, security = nxm._windows()
    sid, current = nxm._identity(tmp_path)
    legacy = current.replace('PSModManager-activation-v2-', 'PSModManager-nxm-')
    attributes = nxm._security_attributes(sid)
    attributes.bInheritHandle = True  # Reproduce the released v1 bug, not production behavior.
    old = pipe.CreateNamedPipe(legacy, pipe.PIPE_ACCESS_DUPLEX | con.FILE_FLAG_OVERLAPPED | 0x00080000,
        pipe.PIPE_TYPE_MESSAGE | pipe.PIPE_READMODE_MESSAGE | 0x8, 1,
        nxm.MAX_MESSAGE, nxm.MAX_MESSAGE, 1000, attributes)
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'],
        close_fds=False, creationflags=subprocess.CREATE_NO_WINDOW)
    old.Close()
    try:
        assert child.poll() is None
        inbox = nxm.start_or_forward(tmp_path)
        assert inbox is not None
        try:
            assert nxm.start_or_forward(tmp_path, link()) is None
            assert len(inbox.peek()['items']) == 1
            assert nxm.start_or_forward(tmp_path) is None
        finally:
            inbox.close()
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named pipe')
def test_normal_activation_failure_does_not_claim_to_be_a_download_failure(tmp_path, monkeypatch):
    inbox = nxm.start_or_forward(tmp_path)
    def unavailable(*args):
        raise ValueError(nxm.PIPE_ERROR)
    monkeypatch.setattr(nxm, '_forward', unavailable)
    try:
        with pytest.raises(ValueError, match='已有管理器实例未响应'):
            nxm.start_or_forward(tmp_path)
        with pytest.raises(ValueError, match='下载请求'):
            nxm.start_or_forward(tmp_path, link())
    finally:
        inbox.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named pipe')
def test_pipe_rejects_malformed_message_and_remains_usable(tmp_path):
    inbox = nxm.start_or_forward(tmp_path)
    sid, name = nxm._identity(tmp_path)
    try:
        with pytest.raises(ValueError):
            nxm._forward(name, sid, 'nxm://wrong/mods/12/files/34?key=synthetic')
        assert inbox.peek() == {'items': []}
        assert nxm.start_or_forward(tmp_path, link()) is None
        assert len(inbox.peek()['items']) == 1
    finally:
        inbox.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named pipe')
def test_unresponsive_pipe_client_times_out_without_blocking_next_link(tmp_path, monkeypatch):
    monkeypatch.setattr(nxm, 'TIMEOUT', .25)
    inbox = nxm.start_or_forward(tmp_path)
    py, api, con, event, file, pipe, security = nxm._windows()
    _, name = nxm._identity(tmp_path)
    handle = file.CreateFile(name, con.GENERIC_READ | con.GENERIC_WRITE, 0, None, con.OPEN_EXISTING, con.FILE_FLAG_OVERLAPPED, None)
    try:
        time.sleep(.4)
        assert nxm.start_or_forward(tmp_path, link()) is None
        assert len(inbox.peek()['items']) == 1
    finally:
        handle.Close()
        inbox.close()


def test_main_argument_error_redacts_link(capsys):
    from main import main
    with pytest.raises(SystemExit):
        main(['--unknown', link()])
    assert 'synthetic' not in capsys.readouterr().err


def test_main_rejects_protocol_trailing_option_injection(capsys):
    from main import main
    with pytest.raises(SystemExit):
        main(['--nxm', link(), '--psmm-data-root', 'C:\\unexpected'])
    assert 'synthetic' not in capsys.readouterr().err


def test_main_resolves_then_runs_and_closes_inbox(monkeypatch, tmp_path):
    import backend.app.composition as composition
    from main import main
    inbox = nxm.NxmInbox()
    calls = []
    monkeypatch.setattr(nxm, 'start_or_forward', lambda root, url: (calls.append((root, url)), inbox)[1])
    monkeypatch.setattr(composition, 'run_desktop', lambda root, protected, *, nxm_inbox: calls.append(nxm_inbox) or 0)
    assert main(['--psmm-data-root', str(tmp_path), '--nxm', link()]) == 0
    assert calls[0] == (tmp_path, link())
    assert calls[1] is inbox
    assert inbox._stop.is_set()


def test_main_secondary_does_not_start_desktop(monkeypatch, tmp_path):
    import backend.app.composition as composition
    from main import main
    monkeypatch.setattr(nxm, 'start_or_forward', lambda root, url: None)
    monkeypatch.setattr(composition, 'run_desktop', lambda *a, **k: pytest.fail('secondary desktop'))
    assert main(['--psmm-data-root', str(tmp_path), '--nxm', link()]) == 0
