"""Static rules: declared permissions versus code, and concrete system-impact combinations."""
from backend.mod_security.rules import RULE_VERSION, analyze


def call(method, token, category, target):
    return dict(method=method, method_token=token, offset=4, category=category, target=target)


def parsed(calls=(), strings=(), pinvokes=()):
    return {'Main.dll': dict(status='managed', metadata=dict(security=dict(
        coverage=dict(status='complete'), calls=list(calls), strings=list(strings), pinvokes=list(pinvokes))))}


def run(capabilities, **parts):
    package = dict(assemblies=['Main.dll'], capabilities=capabilities)
    findings, gaps, _ = analyze(package, [dict(path='Main.dll', kind='pe')], parsed(**parts))
    return {f['rule']: f for f in findings}, gaps


def test_rule_version_changed_so_old_reports_are_marked_for_recheck():
    assert RULE_VERSION == 'local-security-v3'


def test_undeclared_and_unused_capabilities_are_reported_separately():
    rules, _ = run(['files'], calls=[call('A::Send', '0x06000001', 'network', 'System.Net.Http.HttpClient::PostAsync'),
                                     call('A::Run', '0x06000002', 'process_start', 'System.Diagnostics.Process::Start')])
    assert rules['undeclared_capability']['severity'] == 'warning'
    findings, _, _ = analyze(dict(assemblies=['Main.dll'], capabilities=['files']), [dict(path='Main.dll', kind='pe')],
                             parsed(calls=[call('A::Send', '0x06000001', 'network', 'System.Net.Http.HttpClient::PostAsync'),
                                           call('A::Run', '0x06000002', 'process_start', 'System.Diagnostics.Process::Start')]))
    assert sorted(f['title'] for f in findings if f['rule'] == 'undeclared_capability') == [
        '代码使用了清单未声明的权限：启动程序或打开链接（1 处）', '代码使用了清单未声明的权限：访问互联网（1 处）']
    assert rules['declared_without_static_use']['evidence'][0]['detail'] == 'files'


def test_declared_capabilities_that_match_raise_no_mismatch():
    rules, _ = run(['network', 'native'], calls=[call('A::Get', '0x06000001', 'network', 'System.Net.WebClient::DownloadString')],
                   pinvokes=[dict(method='A::Beep', module='kernel32.dll', entry_point='Beep')])
    assert 'undeclared_capability' not in rules and 'declared_without_static_use' not in rules


def test_reflection_mismatch_is_only_a_hint():
    rules, _ = run([], calls=[call('A::Patch', '0x06000001', 'reflection', 'System.Reflection.MethodBase::Invoke')])
    assert rules['undeclared_capability']['severity'] == 'info'


def test_system_command_with_process_start_in_same_method_is_high():
    rules, _ = run(['process'], calls=[call('A::Persist', '0x06000003', 'process_start', 'System.Diagnostics.Process::Start')],
                   strings=[dict(method='A::Persist', method_token='0x06000003', offset=2, kind='command_or_persistence', value='schtasks /create /tn x')])
    assert rules['system_command_action']['severity'] == 'high'
    assert 'system_command_string' not in rules


def test_command_string_alone_and_registry_and_credentials():
    rules, _ = run(['network'], calls=[
        call('A::Reg', '0x06000004', 'registry', 'Microsoft.Win32.RegistryKey::SetValue'),
        call('A::Steal', '0x06000005', 'credential_access', 'System.Security.Cryptography.ProtectedData::Unprotect'),
        call('A::Steal', '0x06000005', 'network', 'System.Net.Http.HttpClient::PostAsync')],
        strings=[dict(method='A::Help', method_token='0x06000006', offset=1, kind='command_or_persistence', value='open cmd.exe for help'),
                 dict(method='A::Get', method_token='0x06000007', offset=1, kind='url', value='https://example.invalid/api')])
    assert rules['system_command_string']['severity'] == 'warning'
    assert rules['registry_write']['severity'] == 'warning'
    assert rules['credential_decrypt']['severity'] == 'warning'
    assert rules['credential_decrypt_and_send']['severity'] == 'high'
    assert rules['network_endpoints']['evidence'][0]['detail'] == 'https://example.invalid/api'
