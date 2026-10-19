"""CASE-036 actual RPC/native process, fixed image/cwd, errors and process exit."""
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
import win32api
import win32con
import win32event
import win32process
import win32security

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.deployments.paths import pinned_file
from backend.deployments.lease import InstallationLease
from backend.deployments.process_guard import same_process_image
from tests.integration.harness.launch_probe import build_probe, setup_installation
from tests.unit.test_installation_layout import make_installation
from tests.integration.test_deployment_guards import register

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows native game launcher')


@pytest.fixture(scope='session')
def probe(tmp_path_factory):
    return build_probe(tmp_path_factory.mktemp('launch-probe-compiler'))


@pytest.fixture
def launch_app(tmp_path, probe):
    context = DesktopContext(resolve_paths(str(tmp_path / 'isolated-data')))
    context.initialize()
    game = tmp_path / '中文 空格 & 100% Game'
    installation_id = setup_installation(context, game, probe)
    launcher = context.launcher
    try:
        yield context, installation_id, game
    finally:
        (game / 'exit-request.txt').write_text('0', encoding='ascii')
        for record in launcher._records.values():
            handle = record.get('handle')
            if handle is not None:
                if win32event.WaitForSingleObject(handle, 5000) != win32event.WAIT_OBJECT_0:
                    win32process.TerminateProcess(handle, 1)  # This test's own verified probe only.
        context.close()
        assert (game / 'synthetic-save.dat').read_bytes() == b'untouched synthetic save'


def rpc(context, operation, installation_id, **extra):
    return context.api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=operation,
        payload=dict(installation_id=installation_id, **extra)))


def ok(context, command, installation_id):
    response = rpc(context, command, installation_id)
    assert response['ok'], response
    return response['data']


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError('Synthetic probe condition timed out')


def test_case036_real_process_fixed_image_unicode_cwd_and_no_arguments(launch_app):
    context, installation_id, game = launch_app
    assert ok(context, 'game.status', installation_id)['state'] == 'not_started'
    result = ok(context, 'game.launch', installation_id)
    assert result['state'] == 'running' and result['pid'] > 0 and result['started_at']
    wait_for(lambda: (game / 'launch-probe.txt').exists())
    values = (game / 'launch-probe.txt').read_text(encoding='utf-8-sig').splitlines()
    assert int(values[0]) == result['pid']
    assert Path(values[1]) == game / 'Probably Stolen.exe'
    assert Path(values[2]) == game
    assert values[3] == '0'
    handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, result['pid'])
    try:
        with pinned_file(game / 'Probably Stolen.exe', execute=True) as image:
            assert same_process_image(handle, image)
    finally:
        handle.Close()
    assert context.store.connection.execute('SELECT count(*) FROM action_plans').fetchone()[0] == 0


def test_case036_repeated_launch_does_not_create_second_process(launch_app):
    context, installation_id, game = launch_app
    first = ok(context, 'game.launch', installation_id)
    second = ok(context, 'game.launch', installation_id)
    assert second['already_running'] and second['pid'] == first['pid']
    wait_for(lambda: (game / 'launch-count.txt').exists())
    assert (game / 'launch-count.txt').read_text() == 'started\n'


@pytest.mark.parametrize('exit_code', [0, 7, 259])
def test_case036_reports_actual_exit_code_and_can_launch_again(launch_app, exit_code):
    context, installation_id, game = launch_app
    first = ok(context, 'game.launch', installation_id)
    wait_for(lambda: (game / 'launch-probe.txt').exists())
    (game / 'exit-request.txt').write_text(str(exit_code))
    result = wait_for(lambda: (value if (value := ok(context, 'game.status', installation_id))['state'] == 'exited' else None))
    assert result['exit_code'] == exit_code and result['pid'] == first['pid']
    (game / 'exit-request.txt').unlink()
    second = ok(context, 'game.launch', installation_id)
    assert second['state'] == 'running' and second['pid'] != first['pid']


@pytest.mark.parametrize('extra', [{'path': 'C:/arbitrary.exe'}, {'cwd': 'C:/Windows'}, {'args': ['--save']},
    {'command': 'cmd.exe'}, {'shell': True}, {'env': {'PATH': 'arbitrary'}}, {'skip_guard': True}])
def test_case036_rpc_rejects_arbitrary_execution_options(launch_app, extra):
    context, installation_id, game = launch_app
    result = rpc(context, 'game.launch', installation_id, **extra)
    assert not result['ok'] and result['error']['code'] == 'INVALID_REQUEST'
    assert not (game / 'launch-probe.txt').exists()


def test_case036_unknown_installation_does_not_launch_or_cache_fake_history(launch_app):
    context, _, game = launch_app
    unknown = str(uuid4())
    result = rpc(context, 'game.launch', unknown)
    assert not result['ok'] and result['error']['code'] == 'GUARD_INSTALLATION_CHANGED'
    assert unknown not in context.launcher._records
    assert not (game / 'launch-probe.txt').exists()


def test_case036_missing_executable_returns_error_without_creating_child(launch_app):
    context, installation_id, game = launch_app
    (game / 'Probably Stolen.exe').unlink()
    result = rpc(context, 'game.launch', installation_id)
    assert not result['ok'] and result['error']['code'] == 'GUARD_INSTALLATION_CHANGED'
    assert not (game / 'launch-probe.txt').exists()


def test_case036_actual_execute_permission_denial_is_reported_without_child(launch_app):
    context, installation_id, game = launch_app
    executable = str(game / 'Probably Stolen.exe')
    previous = win32security.GetFileSecurity(executable, win32security.DACL_SECURITY_INFORMATION)
    denied = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor('D:(D;;GX;;;WD)(A;;GA;;;WD)', 1)
    try:
        win32security.SetFileSecurity(executable, win32security.DACL_SECURITY_INFORMATION, denied)
        result = rpc(context, 'game.launch', installation_id)
        assert not result['ok'] and result['error']['code'] in ('GAME_PROCESS_UNKNOWN', 'GAME_LAUNCH_FAILED'), result
        assert '权限' in result['error']['message']
        assert not (game / 'launch-probe.txt').exists()
    finally:
        win32security.SetFileSecurity(executable, win32security.DACL_SECURITY_INFORMATION, previous)


def test_case036_existing_installation_operation_blocks_launch_without_child(launch_app):
    context, installation_id, game = launch_app
    installation = context.store.get('Installation', installation_id)
    with InstallationLease(installation.file_identity):
        result = rpc(context, 'game.launch', installation_id)
        assert not result['ok'] and result['error']['code'] == 'GUARD_LEASE_BUSY'
        assert not (game / 'launch-probe.txt').exists()


def test_case036_native_bad_executable_returns_failed_state(tmp_path):
    game = make_installation(tmp_path / 'invalid executable')
    context = DesktopContext(resolve_paths(str(tmp_path / 'isolated-data')))
    context.initialize()
    try:
        installation_id = register(context, game)
        result = rpc(context, 'game.launch', installation_id)
        assert not result['ok'] and result['error']['code'] == 'GAME_LAUNCH_FAILED', result
        assert ok(context, 'game.status', installation_id)['state'] == 'failed'
    finally:
        context.close()


def test_case036_existing_external_process_is_not_duplicated_or_stopped(launch_app):
    context, installation_id, game = launch_app
    process = subprocess.Popen([str(game / 'Probably Stolen.exe')], cwd=game,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        wait_for(lambda: (game / 'launch-probe.txt').exists())
        assert ok(context, 'game.status', installation_id)['state'] == 'running'
        response = rpc(context, 'game.launch', installation_id)
        assert not response['ok'] and response['error']['code'] == 'GAME_ALREADY_RUNNING'
        assert process.poll() is None
        assert (game / 'launch-count.txt').read_text() == 'started\n'
    finally:
        (game / 'exit-request.txt').write_text('0')
        process.wait(timeout=10)


def test_case036_manager_close_does_not_stop_game_and_reopen_detects_it(launch_app):
    context, installation_id, game = launch_app
    pid = ok(context, 'game.launch', installation_id)['pid']
    wait_for(lambda: (game / 'launch-probe.txt').exists())
    handle = win32api.OpenProcess(win32con.SYNCHRONIZE | win32con.PROCESS_QUERY_INFORMATION, False, pid)
    paths = context.paths
    context.close()
    assert win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT
    reopened = DesktopContext(paths)
    reopened.initialize()
    reopened.api = DesktopApi(reopened)
    try:
        assert ok(reopened, 'game.status', installation_id)['state'] == 'running'
        response = rpc(reopened, 'game.launch', installation_id)
        assert not response['ok'] and response['error']['code'] == 'GAME_ALREADY_RUNNING'
        (game / 'exit-request.txt').write_text('0')
        assert win32event.WaitForSingleObject(handle, 5000) == win32event.WAIT_OBJECT_0
    finally:
        reopened.close()
        handle.Close()


def test_case036_different_real_suspended_image_is_rejected_before_code_runs(launch_app, probe, tmp_path, monkeypatch):
    context, installation_id, game = launch_app
    other = tmp_path / 'other.exe'
    other.write_bytes(probe.read_bytes())
    original = win32process.CreateProcess
    handles = []
    def redirect(application, command, *values):
        result = original(str(other), subprocess.list2cmdline([str(other)]), *values)
        handles.append(win32api.OpenProcess(win32con.SYNCHRONIZE, False, result[2]))
        return result
    monkeypatch.setattr(win32process, 'CreateProcess', redirect)
    try:
        result = rpc(context, 'game.launch', installation_id)
        assert not result['ok'] and result['error']['code'] == 'GAME_IMAGE_CHANGED', result
        assert handles and win32event.WaitForSingleObject(handles[0], 0) == win32event.WAIT_OBJECT_0
        assert not (game / 'launch-probe.txt').exists()
    finally:
        for handle in handles:
            handle.Close()
