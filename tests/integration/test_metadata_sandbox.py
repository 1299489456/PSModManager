"""Actual Windows kernel boundary tests using our own trusted C# negative probe."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from backend.scanning.worker_sandbox import run_sandbox, MAX_OUTPUT, MEMORY_LIMIT
from backend.scanning.worker_sandbox import NONINTERACTIVE_ERROR_MODE
from scripts.build_fixtures import compiler_inputs

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows AppContainer boundary')


@pytest.fixture
def probe(tmp_path):
    stage = tmp_path / 'stage'
    stage.mkdir()
    host, compiler, refs = compiler_inputs()
    output = stage / 'probe.dll'
    command = [str(host), str(compiler), '/nologo', '/target:exe', '/deterministic+',
               '/nostdlib+', '/out:' + str(output)]
    command += ['/reference:' + str(p) for p in refs]
    command.append(str(Path(__file__).with_name('metadata_probe.cs')))
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (tmp_path / 'compiler.json').write_text(json.dumps({'command':command, 'exit_code':result.returncode,
        'stdout':result.stdout,'stderr':result.stderr}, indent=2), encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    (stage / 'probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {
        'tfm':'net10.0','framework':{'name':'Microsoft.NETCore.App','version':'10.0.0'}}}), encoding='utf-8')
    (stage / 'input.txt').write_text('owned input', encoding='utf-8')
    (tmp_path / 'outside.txt').write_text('owned outside', encoding='utf-8')
    # ALL APPLICATION PACKAGES alone must NOT grant an LPAC read access.
    # Only this owned sentinel is modified; no real resource/runtime ACL edits.
    import win32api
    import win32security as sec
    import ntsecuritycon as rights
    token = sec.OpenProcessToken(win32api.GetCurrentProcess(), 8)
    try:
        owner = sec.GetTokenInformation(token, sec.TokenUser)[0]
    finally:
        token.Close()
    acl = sec.ACL()
    acl.AddAccessAllowedAce(sec.ACL_REVISION, rights.FILE_ALL_ACCESS, owner)
    acl.AddAccessAllowedAce(sec.ACL_REVISION, rights.FILE_GENERIC_READ, sec.ConvertStringSidToSid('S-1-15-2-1'))
    sec.SetNamedSecurityInfo(str(tmp_path / 'outside.txt'), sec.SE_FILE_OBJECT,
        sec.DACL_SECURITY_INFORMATION | sec.PROTECTED_DACL_SECURITY_INFORMATION, None, None, acl, None)
    return stage, host


def invoke(probe, mode, **kwargs):
    stage, host = probe
    request = dict(mode=mode, outside=str(stage.parent / 'outside.txt'), input=str(stage / 'input.txt'), port=kwargs.pop('port',0))
    result = run_sandbox([host, stage / 'probe.dll'], json.dumps(request).encode(), stage=stage, **kwargs)
    (stage.parent / ('result-' + mode + '.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    assert result['started'], result
    assert result['appcontainer'] is True and result['capabilities'] == 0, result
    assert result['lpac_policy_applied'] is True, result
    assert result['cleanup_remaining'] == 0, result
    assert result['profile_deleted'] is True, result
    assert len(result['stdout'].encode()) <= MAX_OUTPUT, result
    return result


def test_actual_token_files_network_and_child_boundaries(probe):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); listener.listen(1)
        stage, host = probe
        control_request = dict(mode='network_control',outside=str(stage.parent/'outside.txt'),
            input=str(stage/'input.txt'),port=listener.getsockname()[1])
        control = subprocess.run([str(host),str(stage/'probe.dll')], input=json.dumps(control_request),
            text=True,capture_output=True,timeout=8)
        (stage.parent/'network-control.json').write_text(json.dumps({'command':[str(host),str(stage/'probe.dll')],
            'exit_code':control.returncode,'stdout':control.stdout,'stderr':control.stderr},indent=2),encoding='utf-8')
        assert control.returncode == 0, control.stderr
        assert all(not value['denied'] for value in json.loads(control.stdout).values())
        listener.accept()[0].close()
        result = invoke(probe, 'boundaries', port=listener.getsockname()[1])
    assert result['status'] == 'exited' and result['exit_code'] == 0, result
    checks = json.loads(result['stdout'])
    assert checks.pop('input_read') == 'owned input'
    assert len(checks) == 7
    for name, check in checks.items():
        assert check['denied'] is True, (name,check)
    assert checks['connect_loopback']['error'] == 'SocketException'
    # LPAC can deny the provider's initialization (WSASYSCALLFAILURE 10107)
    # before socket creation, or the operation itself (WSAEACCES 10013).
    # Neither absent-listener/refused-port errors nor a probe timeout count.
    assert checks['connect_loopback']['code'] in (10013, 10107)
    assert checks['bind_loopback']['error'] == 'SocketException'
    assert checks['bind_loopback']['code'] in (10013, 10107)
    stage, _ = probe
    assert (stage / 'input.txt').read_text() == 'owned input'
    assert (stage.parent / 'outside.txt').read_text() == 'owned outside'


@pytest.mark.parametrize('mode,expected', [('boundaries','exited'), ('hang','timeout'), ('cancel','cancelled')])
def test_pythonw_host_keeps_sandbox_boundaries_and_cleanup(probe, mode, expected):
    stage, host = probe
    payload = stage.parent / 'gui-request.json'
    output = stage.parent / 'gui-result.json'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); listener.listen(1)
        request = dict(mode='hang' if mode == 'cancel' else mode, outside=str(stage.parent / 'outside.txt'),
                       input=str(stage / 'input.txt'), port=listener.getsockname()[1])
        payload.write_text(json.dumps(dict(argv=[str(host), str(stage / 'probe.dll')],
            request=request, stage=str(stage), timeout=2)), encoding='utf-8')
        command = [str(Path(sys.executable).with_name('pythonw.exe')), '-I', '-B',
                   str(Path(__file__).with_name('gui_metadata_host.py')), mode, str(payload), str(output)]
        process = subprocess.run(command, timeout=20, creationflags=subprocess.DETACHED_PROCESS)
    report = json.loads(output.read_text(encoding='utf-8'))
    assert process.returncode == 0, report
    assert report['console_window'] == 0
    result = report['process']
    assert result['status'] == expected, result
    assert result['appcontainer'] and result['lpac_policy_applied'] and result['capabilities'] == 0
    assert result['cleanup_remaining'] == result['console_host']['cleanup_remaining'] == 0
    assert result['profile_deleted'] and result['desktop_closed']
    assert result['console_host']['exit_code'] == 0
    if mode == 'boundaries':
        assert result['exit_code'] == 0
        checks = json.loads(result['stdout'])
        assert checks.pop('input_read') == 'owned input'
        assert len(checks) == 7 and all(check['denied'] for check in checks.values())
    else:
        assert result['exit_code'] != 0


def test_hang_deadline_and_cleanup(probe):
    result = invoke(probe,'hang',timeout=2)
    assert result['status'] == 'timeout' and result['exit_code'] != 0, result
    assert result['duration_seconds'] < 8, result


def test_child_inherits_noninteractive_error_mode_and_host_restores(probe):
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    original = kernel.GetErrorMode()
    result = invoke(probe, 'error_mode')
    assert result['status'] == 'exited' and result['exit_code'] == 0, result
    assert json.loads(result['stdout'])['error_mode'] & NONINTERACTIVE_ERROR_MODE == NONINTERACTIVE_ERROR_MODE
    assert result['host_error_mode_restored'] is True
    assert result['die_on_unhandled_exception'] is True
    assert kernel.GetErrorMode() == original


def test_concurrent_workers_inherit_mode_without_changing_host(probe):
    import ctypes
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    original = kernel.GetErrorMode()
    stage, host = probe
    other = stage.parent / 'second-stage'
    shutil.copytree(stage, other)
    def inspect(path):
        return run_sandbox([host, path / 'probe.dll'], b'{"mode":"error_mode"}', stage=path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(inspect, [stage, other]))
    (stage.parent / 'concurrent-error-modes.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    assert kernel.GetErrorMode() == original
    for result in results:
        assert result['exit_code'] == 0 and result['status'] == 'exited', result
        assert json.loads(result['stdout'])['error_mode'] & NONINTERACTIVE_ERROR_MODE == NONINTERACTIVE_ERROR_MODE
        assert result['host_error_mode_restored'] and result['cleanup_remaining'] == 0
        assert result['profile_deleted'] and result['desktop_closed']


def test_failed_create_restores_host_error_mode(probe):
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    original = kernel.GetErrorMode()
    stage, _ = probe
    result = run_sandbox([stage / 'nonexistent-owned-helper.exe'], b'{}', stage=stage)
    assert not result['started'] and result['phase'] == 'create_process'
    assert result['host_error_mode_restored'] and kernel.GetErrorMode() == original
    assert result['cleanup_remaining'] == 0 and result['profile_deleted'] and result['desktop_closed']


def test_cancel_and_cleanup(probe):
    began = time.monotonic()
    result = invoke(probe,'hang',timeout=8,cancelled=lambda: time.monotonic()-began > 1)
    assert result['status'] == 'cancelled' and result['exit_code'] != 0, result


def test_crash_and_cleanup(probe):
    result = invoke(probe,'crash')
    assert result['status'] == 'exited' and result['exit_code'] != 0, result
    assert result['exit_code'] == 0xC0000005, result


def test_output_flood_is_bounded(probe):
    result = invoke(probe,'flood')
    assert result['status'] == 'output_limit' and result['exit_code'] != 0, result
    assert len(result['stdout']) == MAX_OUTPUT


def test_memory_is_bounded(probe):
    result = invoke(probe,'memory',timeout=15)
    assert result['status'] == 'exited' and result['exit_code'] != 0, result
    assert 0 < result['peak_memory'] <= MEMORY_LIMIT, result
    if result['stdout']:
        assert json.loads(result['stdout'])['memory_denied'] is True


def test_assignment_failure_terminates_actual_suspended_process(probe,monkeypatch):
    import win32job
    import psutil
    def reject(*args): raise OSError('owned injected assignment failure')
    monkeypatch.setattr(win32job,'AssignProcessToJobObject',reject)
    stage,host=probe
    # Exercise the inner suspended parser assignment even when pytest itself
    # has no console and the public wrapper would first create a host.
    from backend.scanning.worker_sandbox import _run_sandbox
    result=_run_sandbox([host,stage/'probe.dll'],b'{"mode":"hang"}',stage=stage)
    (stage.parent/'assignment-failure.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    assert result['started'] is False and result['phase']=='assign_job'
    assert result['unassigned_process_terminated'] is True
    assert result['cleanup_remaining']==0 and not psutil.pid_exists(result['pid'])
    assert result['profile_deleted'] and result['desktop_closed']


def test_console_host_assignment_failure_terminates_gated_host(probe, monkeypatch):
    import psutil
    import win32job
    from backend.scanning.worker_host import run_console_host
    def reject(*args):
        raise OSError('owned injected assignment failure')
    monkeypatch.setattr(win32job, 'AssignProcessToJobObject', reject)
    stage, host = probe
    result = run_console_host([host, stage / 'probe.dll'], b'{"mode":"hang"}',
                              stage=stage, timeout=2, cancelled=lambda: False)
    (stage.parent / 'host-assignment-failure.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['started'] is False and result['phase'] == 'assign_job'
    assert result['unassigned_process_terminated'] is True
    assert result['cleanup_remaining'] == result['console_host']['cleanup_remaining'] == 0
    assert not psutil.pid_exists(result['console_host']['pid'])


def test_console_host_cancellation_exception_waits_for_sandbox_cleanup(probe, monkeypatch):
    from backend.scanning import worker_host
    decoded = []
    parse = json.loads
    def capture(value, *args, **kwargs):
        result = parse(value, *args, **kwargs)
        if isinstance(result, dict) and 'appcontainer' in result:
            decoded.append(result)
        return result
    monkeypatch.setattr(worker_host.json, 'loads', capture)
    began = time.monotonic()
    def cancelled():
        if time.monotonic() - began > .5:
            raise ValueError('owned cancellation signal')
        return False
    stage, host = probe
    with pytest.raises(ValueError, match='owned cancellation signal'):
        worker_host.run_console_host([host, stage / 'probe.dll'], b'{"mode":"hang"}',
                                     stage=stage, timeout=3, cancelled=cancelled)
    assert len(decoded) == 1
    result = decoded[0]
    assert result['status'] == 'cancelled'
    assert result['cleanup_remaining'] == result['console_host']['cleanup_remaining'] == 0
    assert result['profile_deleted'] and result['desktop_closed']
