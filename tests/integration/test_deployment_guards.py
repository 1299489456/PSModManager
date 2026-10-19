"""CASE-028: Windows synthetic consumers, real handles, processes and mutexes.

The writer below is an explicitly bounded executor probe, not a deployment or
journal implementation. Every executable is self-authored and bounded; no real
game, source checkout, save or externally owned process is used.
"""
from contextlib import contextmanager
from copy import deepcopy
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
from uuid import uuid4

import pytest
import pywintypes
import win32con
import win32file
import win32api
import win32security
import win32process

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.contracts import ENTITY_MODELS
from backend.deployments.lease import InstallationLease
from backend.deployments.paths import GuardRejected, pinned_file, file_identity, _information
from backend.deployments import process_guard as guard_module
from backend.deployments.process_guard import file_process_ids
from backend.games.probably_stolen.layout import EXE, identity
from tests.unit.test_installation_discovery import discover, request, wait
from tests.unit.test_installation_layout import make_installation


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='CASE-028 requires real Windows primitives')
ROOT = Path(__file__).resolve().parents[2]
UNCHANGED = b'original sentinel'
UPDATED = b'guarded mutation!'
ROOTS = {'mods': 'Mods', 'plugins': 'Plugins', 'user_libs': 'UserLibs'}

SIMULATOR_SOURCE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
public static class SyntheticGame {
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr value);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool SetKernelObjectSecurity(IntPtr handle, uint info, IntPtr descriptor);
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string text, uint revision, out IntPtr descriptor, out uint size);
    public static int Main(string[] args) {
        if (args.Length != 2) return 10;
        if (args[1] == "deny-query") {
            IntPtr descriptor; uint size;
            // Deny query and VM_READ to everyone. Existing Popen handles retain
            // their rights, allowing the owning test to stop only this child.
            if (!ConvertStringSecurityDescriptorToSecurityDescriptor("D:(D;;0x1410;;;WD)(A;;GA;;;SY)(A;;GA;;;BA)", 1, out descriptor, out size)) return 11;
            try { if (!SetKernelObjectSecurity(GetCurrentProcess(), 4, descriptor)) return 12; }
            finally { LocalFree(descriptor); }
        }
        File.WriteAllText(args[0], Process.GetCurrentProcess().Id.ToString());
        Thread.Sleep(90000);
        return 0;
    }
}
'''


@pytest.fixture(scope='session')
def simulator(tmp_path_factory):
    directory = tmp_path_factory.mktemp('case028-self-authored-executable')
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    assert compiler.is_file(), 'Existing Windows Framework compiler required; dependencies are not installed automatically'
    source = directory / 'SyntheticGame.cs'
    source.write_text(SIMULATOR_SOURCE, encoding='utf-8')
    executable = directory / EXE
    command = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/out:' + str(executable), str(source)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        privileges = {win32security.LookupPrivilegeName(None, luid): flags
            for luid, flags in win32security.GetTokenInformation(token, win32security.TokenPrivileges)}
        token_facts = dict(elevated=bool(win32security.GetTokenInformation(token, win32security.TokenElevation)),
            debug_privilege_present='SeDebugPrivilege' in privileges,
            debug_privilege_enabled=bool(privileges.get('SeDebugPrivilege', 0) & win32con.SE_PRIVILEGE_ENABLED),
            symbolic_link_privilege_present='SeCreateSymbolicLinkPrivilege' in privileges,
            windows_build=sys.getwindowsversion().build, pid=os.getpid())
    finally:
        token.Close()
    (directory / 'compiler.json').write_text(json.dumps(dict(command=command, exit_code=result.returncode,
        stdout=result.stdout, stderr=result.stderr, token=token_facts), ensure_ascii=False, indent=2), encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


def register(context, game):
    """Only replace the native folder picker; discovery and selection are real."""
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    candidate = discover(api)
    assert candidate['valid'] and candidate['fingerprint']['coverage'] == 'partial', candidate
    response = api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))
    assert response['ok'], response
    selected = wait(api, response['data'])
    assert selected['state'] == 'succeeded', selected
    return selected['data']['installation']['installation_id']


@pytest.fixture
def app(tmp_path, simulator):
    game = make_installation(tmp_path / 'synthetic-game')
    shutil.copyfile(simulator, game / EXE)
    for directory in ROOTS.values():
        target = game / directory / 'nested'
        target.mkdir(parents=True)
        (target / 'sentinel.dll').write_bytes(UNCHANGED)
    (game / 'synthetic-save.dat').write_bytes(b'untouched synthetic save')
    context = DesktopContext(resolve_paths(str(tmp_path / 'private-data')))
    context.initialize()
    try:
        installation_id = register(context, game)
        yield context, installation_id, game
    finally:
        context.close()
        assert (game / 'synthetic-save.dat').read_bytes() == b'untouched synthetic save'


def status(app):
    context, installation_id, _ = app
    response = context.api.dispatch(request('deployment.guard_status', {'installation_id': installation_id}))
    assert response['ok'], response
    assert response['data']['installation_id'] == installation_id
    return response['data']


def file_tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def write_guarded(session, snapshot, payload=UPDATED):
    """The bytes are written using the very handle pinned by production code."""
    with session.step(snapshot) as target:
        assert target.handle is not None
        win32file.SetFilePointer(target.handle, 0, win32con.FILE_BEGIN)
        win32file.WriteFile(target.handle, payload)
        win32file.FlushFileBuffers(target.handle)


@contextmanager
def rejected(code):
    with pytest.raises(GuardRejected) as caught:
        yield
    assert caught.value.code == code


def await_file(path, process, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return path.read_text(encoding='utf-8')
        assert process.poll() is None, f'Synthetic child exited early: {process.returncode}'
        time.sleep(.02)
    raise AssertionError('Synthetic child readiness timed out')


@contextmanager
def running(executable, directory, *, protected=False):
    ready = directory / ('process-' + uuid4().hex + '.ready')
    process = subprocess.Popen([str(executable), str(ready), 'deny-query' if protected else 'normal'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert int(await_file(ready, process)) == process.pid
        yield process
    finally:
        if process.poll() is None:
            process.kill()  # The exact self-authored Popen handle, never a name/PID search.
        process.wait(timeout=10)


@pytest.mark.parametrize('root_kind', ROOTS)
@pytest.mark.parametrize('intent', ['apply', 'rollback'])
def test_case028_actual_context_consumer_pins_and_writes_synthetic_handle(app, root_kind, intent):
    context, installation_id, game = app
    before = file_tree(game)
    report = status(app)
    assert report['ready'] and report['process_status'] == 'clear' and report['blockers'] == []
    assert report['execution_available'] is False
    assert file_tree(game) == before  # The public status RPC is read-only.
    snapshot = context.deployment_guards.prepare_target(installation_id, root_kind, 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, intent=intent) as session:
        session.check()
        write_guarded(session, snapshot)
    assert (game / ROOTS[root_kind] / 'nested/sentinel.dll').read_bytes() == UPDATED
    del before[str(Path(ROOTS[root_kind]) / 'nested/sentinel.dll')]
    after = file_tree(game)
    del after[str(Path(ROOTS[root_kind]) / 'nested/sentinel.dll')]
    assert after == before


def test_case028_launch_guard_does_not_grant_a_file_writer(app):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, intent='launch') as session:
        session.check()
        with rejected('GUARD_TARGET_NOT_ALLOWED'):
            write_guarded(session, snapshot)
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


def test_case028_missing_target_uses_create_new_under_pinned_parent(app):
    context, installation_id, game = app
    target = game / 'Mods/nested/new.dll'
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/new.dll')
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        with session.step(snapshot) as pinned:
            assert pinned.handle is None
            handle = pinned.create_new()
            win32file.WriteFile(handle, UPDATED)
            win32file.FlushFileBuffers(handle)
    assert target.read_bytes() == UPDATED


@pytest.mark.parametrize('relative', [
    '../synthetic-game-other/sentinel.dll', '../../synthetic-game-other/sentinel.dll',
    '/absolute.dll', r'C:\outside.dll', r'\\server\share\outside.dll', r'\\?\C:\outside.dll',
    'nested/sentinel.dll:stream', 'nested/NUL', 'nested/con.dll', 'nested/COM1.dll',
    'nested/LPT9', 'nested/trailing.', 'nested/trailing ', 'nested//double.dll',
    './nested/sentinel.dll', 'nested/../sentinel.dll', 'nested/<invalid>.dll',
])
def test_case028_ambiguous_and_escaping_paths_rejected_without_writes(app, tmp_path, relative):
    context, installation_id, game = app
    similar = tmp_path / 'synthetic-game-other'
    similar.mkdir()
    (similar / 'sentinel.dll').write_bytes(UNCHANGED)
    before = file_tree(game), file_tree(similar)
    with rejected('GUARD_PATH_INVALID'):
        context.deployment_guards.prepare_target(installation_id, 'mods', relative)
    assert (file_tree(game), file_tree(similar)) == before


@pytest.mark.parametrize('root_kind', ['game', 'saves', 'supported_config', 'Mods', '../Mods'])
def test_case028_only_three_content_roots_are_enabled(app, root_kind):
    context, installation_id, game = app
    before = file_tree(game)
    with rejected('GUARD_TARGET_NOT_ALLOWED'):
        context.deployment_guards.prepare_target(installation_id, root_kind, 'sentinel.dll')
    assert file_tree(game) == before


def test_case028_hardlinked_existing_target_rejected(app, tmp_path):
    context, installation_id, game = app
    sentinel = game / 'Mods/nested/sentinel.dll'
    outside = tmp_path / 'outside.dll'
    os.link(sentinel, outside)
    before = sentinel.read_bytes()
    with rejected('GUARD_HARDLINK'):
        context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    assert outside.read_bytes() == sentinel.read_bytes() == before


def test_case028_same_identity_target_bytes_changed_after_prepare_refuses_write(app):
    context, installation_id, game = app
    sentinel = game / 'Mods/nested/sentinel.dll'
    original_identity = identity(sentinel)
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    external = b'external mutation'
    sentinel.write_bytes(external)
    assert identity(sentinel) == original_identity
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        with rejected('GUARD_IDENTITY_CHANGED'):
            write_guarded(session, snapshot)
    assert sentinel.read_bytes() == external


def test_case028_similar_prefix_registered_installation_cannot_consume_other_snapshot(app, tmp_path, simulator):
    context, installation_id, game = app
    sibling = make_installation(tmp_path / 'synthetic-game-other')
    shutil.copyfile(simulator, sibling / EXE)
    sentinel = sibling / 'Mods/nested/sentinel.dll'
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(UNCHANGED)
    sibling_id = register(context, sibling)
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(sibling_id, intent='apply') as session:
        with rejected('GUARD_INSTALLATION_CHANGED'):
            write_guarded(session, snapshot)
    assert sentinel.read_bytes() == (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


@pytest.mark.parametrize('changed', [EXE, 'GameAssembly.dll', 'Probably Stolen_Data/il2cpp_data/Metadata/global-metadata.dat'])
def test_case028_game_updates_do_not_block_registered_directory(app, changed):
    context, installation_id, game = app
    target = game / changed
    with target.open('ab') as stream:
        stream.write(b'external update')
    before = file_tree(game)
    assert 'GUARD_INSTALLATION_CHANGED' not in status(app)['blockers']
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        assert session.installation.canonical_root == str(game)
    assert file_tree(game) == before


def junction(link, destination):
    result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(destination)],
        capture_output=True, text=True, errors='replace', timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr


def test_case028_junction_escape_rejected_before_prepare(app, tmp_path):
    context, installation_id, game = app
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'sentinel.dll'
    sentinel.write_bytes(UNCHANGED)
    link = game / 'Mods/redirect'
    junction(link, outside)
    try:
        with rejected('GUARD_REPARSE_POINT'):
            context.deployment_guards.prepare_target(installation_id, 'mods', 'redirect/sentinel.dll')
        assert sentinel.read_bytes() == UNCHANGED
    finally:
        os.rmdir(link)  # Remove only our junction, never traverse its destination.


@pytest.mark.parametrize('replacement', ['junction', 'directory', 'file', 'hardlink'])
def test_case028_prepared_target_replacement_refuses_actual_write(app, tmp_path, replacement):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    directory = game / 'Mods/nested'
    sentinel = directory / 'sentinel.dll'
    original = tmp_path / 'original'
    link = None
    if replacement in ('junction', 'directory'):
        directory.rename(original)
        if replacement == 'junction':
            link = directory
            junction(link, original)
        else:
            directory.mkdir()
            sentinel.write_bytes(UNCHANGED)
    else:
        sentinel.rename(original)
        if replacement == 'hardlink':
            os.link(original, sentinel)
        else:
            sentinel.write_bytes(UNCHANGED)
    try:
        before = file_tree(game)
        expected = {'junction': 'GUARD_REPARSE_POINT', 'directory': 'GUARD_IDENTITY_CHANGED',
                    'file': 'GUARD_IDENTITY_CHANGED', 'hardlink': 'GUARD_HARDLINK'}[replacement]
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            with rejected(expected):
                write_guarded(session, snapshot)
        assert file_tree(game) == before
        assert (original / 'sentinel.dll' if original.is_dir() else original).read_bytes() == UNCHANGED
    finally:
        if link is not None:
            os.rmdir(link)


@pytest.mark.parametrize('relative', ['', 'Mods', 'Mods/nested', 'Mods/nested/sentinel.dll', EXE])
def test_case028_real_handles_prevent_ancestor_target_and_exe_rename(app, tmp_path, relative):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    source = game / relative
    destination = tmp_path / ('renamed-' + uuid4().hex)
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        with session.step(snapshot):
            with pytest.raises(OSError) as caught:
                source.rename(destination)
            assert caught.value.winerror in (5, 32), caught.value
            assert source.exists() and not destination.exists()
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


def test_case028_missing_target_created_after_prepare_refuses_overwrite(app):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/new.dll')
    sentinel = game / 'Mods/nested/new.dll'
    sentinel.write_bytes(UNCHANGED)
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        with rejected('GUARD_IDENTITY_CHANGED'):
            with session.step(snapshot) as pinned:
                handle = pinned.create_new()
                try:
                    win32file.WriteFile(handle, UPDATED)
                finally:
                    handle.Close()
    assert sentinel.read_bytes() == UNCHANGED


@pytest.mark.parametrize('late_kind', ['file', 'hardlink'])
def test_case028_create_new_refuses_target_appearing_after_step_yields(app, tmp_path, late_kind):
    context, installation_id, game = app
    sentinel = game / 'Mods/nested/new.dll'
    outside = tmp_path / 'outside-sentinel.dll'
    outside.write_bytes(UNCHANGED)
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/new.dll')
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        with session.step(snapshot) as pinned:
            assert pinned.handle is None
            if late_kind == 'hardlink':
                try:
                    os.link(outside, sentinel)
                except OSError as exc:
                    # The pinned directory can reject CreateHardLink itself.
                    assert exc.winerror in (5, 32), exc
                    assert not sentinel.exists() and outside.read_bytes() == UNCHANGED
                    (tmp_path / 'late-link-proof.json').write_text(json.dumps(
                        dict(attack='os_rejected', winerror=exc.winerror, outside_unchanged=True)), encoding='utf-8')
                    return
            else:
                sentinel.write_bytes(UNCHANGED)
            with pytest.raises(GuardRejected) as caught:
                handle = pinned.create_new()
                win32file.WriteFile(handle, UPDATED)
            assert caught.value.code in ('GUARD_IDENTITY_CHANGED', 'GUARD_PATH_UNAVAILABLE', 'GUARD_HARDLINK')
    assert sentinel.read_bytes() == outside.read_bytes() == UNCHANGED


def set_junction_in_place(directory, destination, access=win32con.GENERIC_WRITE):
    """Use the actual NTFS control operation, not pathname rename or a mock."""
    substitute = ('\\??\\' + str(destination)).encode('utf-16-le')
    display = str(destination).encode('utf-16-le')
    payload = substitute + b'\0\0' + display + b'\0\0'
    buffer = struct.pack('<LHHHHHH', 0xa0000003, len(payload) + 8, 0,
                         0, len(substitute), len(substitute) + 2, len(display)) + payload
    handle = win32file.CreateFile(str(directory), access,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
        None, win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS | 0x00200000, None)
    try:
        win32file.DeviceIoControl(handle, 0x900A4, buffer, None)
    finally:
        handle.Close()


@pytest.mark.parametrize('access', [win32con.GENERIC_WRITE, 0x0100], ids=['generic-write', 'write-attributes-only'])
def test_case028_in_place_reparse_attempt_never_writes_outside_pinned_parent(app, tmp_path, access):
    context, installation_id, game = app
    outside = tmp_path / 'outside'
    outside.mkdir()
    control = tmp_path / 'unprotected-control'
    control.mkdir()
    set_junction_in_place(control, outside, access)
    assert control.lstat().st_file_attributes & 0x400
    os.rmdir(control)
    directory = game / 'Mods/empty'
    directory.mkdir()
    original_info = directory.lstat()
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'empty/new.dll')
    changed_to_reparse = False
    trace = {'requested_access': hex(access), 'unprotected_control_succeeded': True}
    try:
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            try:
                with session.step(snapshot) as pinned:
                    try:
                        set_junction_in_place(directory, outside, access)
                    except pywintypes.error as exc:
                        assert exc.winerror in (5, 32), exc
                        trace.update(attack='os_rejected', winerror=exc.winerror)
                        assert not directory.lstat().st_file_attributes & 0x400
                        handle = pinned.create_new()
                        win32file.WriteFile(handle, UPDATED)
                        win32file.FlushFileBuffers(handle)
                    else:
                        changed_to_reparse = True
                        assert directory.lstat().st_file_attributes & 0x400
                        with pytest.raises(GuardRejected) as caught:
                            handle = pinned.create_new()
                            win32file.WriteFile(handle, UPDATED)
                            win32file.FlushFileBuffers(handle)
                        assert caught.value.code in ('GUARD_REPARSE_POINT', 'GUARD_IDENTITY_CHANGED', 'GUARD_PATH_UNAVAILABLE')
                        trace.update(attack='reparse_installed_but_guard_refused', code=caught.value.code)
                    current = directory.lstat()
                    assert (current.st_dev, current.st_ino) == (original_info.st_dev, original_info.st_ino)
            except GuardRejected as exc:
                assert changed_to_reparse and exc.code in ('GUARD_REPARSE_POINT', 'GUARD_IDENTITY_CHANGED', 'GUARD_PATH_UNAVAILABLE')
        assert not (outside / 'new.dll').exists()
        trace['outside_file_created'] = False
        (tmp_path / 'reparse-proof.json').write_text(json.dumps(trace, indent=2), encoding='utf-8')
    finally:
        if directory.lstat().st_file_attributes & 0x400:
            os.rmdir(directory)  # Clean only a link created by this attack probe.


def test_case028_directory_write_share_still_denies_parent_rebinding(app):
    context, installation_id, game = app
    directory = game / 'Mods/nested'
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    handle = win32file.CreateFile(str(directory), win32con.GENERIC_WRITE,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
        None, win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS | 0x00200000, None)
    try:
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            # Native target-relative rename requires directory write sharing;
            # the pinned chain still denies DELETE and changing its binding.
            with session.step(snapshot):
                with pytest.raises(OSError):
                    directory.rename(game / 'Mods/rebound')
                session.check()
    finally:
        handle.Close()
    assert (directory / 'sentinel.dll').read_bytes() == UNCHANGED


def test_case028_pinned_target_disallows_other_writer_handle(app):
    context, installation_id, game = app
    sentinel = game / 'Mods/nested/sentinel.dll'
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        with session.step(snapshot):
            with pytest.raises(pywintypes.error) as caught:
                handle = win32file.CreateFile(str(sentinel), win32con.GENERIC_WRITE,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                    None, win32con.OPEN_EXISTING, 0, None)
                handle.Close()
            assert caught.value.winerror in (5, 32), caught.value
    assert sentinel.read_bytes() == UNCHANGED


@pytest.mark.parametrize('intent', ['apply', 'rollback', 'launch'])
def test_case028_real_target_process_blocks_each_operation_but_prepare_is_readonly(app, tmp_path, intent):
    context, installation_id, game = app
    before = file_tree(game)
    with running(game / EXE, tmp_path) as process:
        report = status(app)
        assert not report['ready'] and report['process_status'] == 'running'
        assert 'GUARD_PROCESS_RUNNING' in report['blockers']
        snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
        assert snapshot is not None and process.poll() is None
        with rejected('GUARD_PROCESS_RUNNING'):
            with context.deployment_guards.operation(installation_id, intent=intent) as session:
                write_guarded(session, snapshot)
        assert file_tree(game) == before
        assert process.poll() is None  # The product must not terminate the game.


def test_case028_same_name_different_path_and_similar_prefix_are_not_target(app, tmp_path, simulator):
    context, installation_id, game = app
    sibling = tmp_path / 'synthetic-game-other'
    sibling.mkdir()
    shutil.copyfile(simulator, sibling / EXE)
    with running(sibling / EXE, tmp_path) as process:
        report = status(app)
        assert report['ready'] and report['process_status'] == 'clear', report
        snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            write_guarded(session, snapshot)
        assert process.poll() is None
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UPDATED


def test_case028_target_starts_after_preflight_no_write_at_next_boundary(app, tmp_path):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        session.check()
        with running(game / EXE, tmp_path):
            with rejected('GUARD_PROCESS_RUNNING'):
                write_guarded(session, snapshot)
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


def test_case028_exe_gets_differently_named_hardlink_after_preflight_no_write(app, tmp_path):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    alias = tmp_path / 'outside-process/Different-Synthetic-Process.exe'
    alias.parent.mkdir()
    try:
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            os.link(game / EXE, alias)
            assert alias.stat().st_nlink == 2
            with running(alias, tmp_path) as process:
                report = status(app)
                assert not report['ready']
                assert set(report['blockers']) & {'GUARD_PROCESS_UNKNOWN', 'GUARD_HARDLINK'}
                for attempt in (session.check, lambda: write_guarded(session, snapshot)):
                    with pytest.raises(GuardRejected) as caught:
                        attempt()
                    assert caught.value.code in ('GUARD_PROCESS_UNKNOWN', 'GUARD_HARDLINK')
                assert process.poll() is None
        assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED
    finally:
        if alias.exists():
            alias.unlink()


def test_case028_external_launch_inside_consumer_requires_recovery_at_step_exit(app, tmp_path):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        process_scope = None
        try:
            with rejected('GUARD_PROCESS_RUNNING'):
                with session.step(snapshot) as pinned:
                    win32file.SetFilePointer(pinned.handle, 0, win32con.FILE_BEGIN)
                    win32file.WriteFile(pinned.handle, UPDATED)
                    win32file.FlushFileBuffers(pinned.handle)
                    process_scope = running(game / EXE, tmp_path)
                    process_scope.__enter__()
            assert session.recovery_required
            with rejected('GUARD_RECOVERY_REQUIRED'):
                session.check()
        finally:
            if process_scope is not None:
                process_scope.__exit__(None, None, None)
    # A guard cannot undo a consumer write. This is evidence of the external
    # launch window requiring the later journal/executor recovery contract.
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UPDATED


@contextmanager
def without_debug_privilege():
    """Keep the real DACL-denial negative meaningful in an elevated test run."""
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
        win32con.TOKEN_QUERY | win32con.TOKEN_ADJUST_PRIVILEGES)
    debug = win32security.LookupPrivilegeValue(None, 'SeDebugPrivilege')
    previous = dict(win32security.GetTokenInformation(token, win32security.TokenPrivileges)).get(debug)
    try:
        if previous is not None:
            win32security.AdjustTokenPrivileges(token, False, [(debug, 0)])
        yield
    finally:
        if previous is not None:
            win32security.AdjustTokenPrivileges(token, False, [(debug, previous)])
        token.Close()


def test_case028_real_process_acl_denial_is_unknown_and_refuses_write(app, tmp_path):
    context, installation_id, game = app
    before = file_tree(game)
    with without_debug_privilege(), running(game / EXE, tmp_path, protected=True) as process:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, process.pid)
        if handle:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
        assert not handle and ctypes.get_last_error() == 5, 'Fixture must demonstrate real ERROR_ACCESS_DENIED'
        report = status(app)
        (tmp_path / 'process-acl-proof.json').write_text(json.dumps(dict(pid=process.pid,
            open_process_winerror=5, guard_status=report), indent=2), encoding='utf-8')
        assert report['process_status'] == 'unknown' and not report['ready'], report
        assert 'GUARD_PROCESS_UNKNOWN' in report['blockers']
        with rejected('GUARD_PROCESS_UNKNOWN'):
            with context.deployment_guards.operation(installation_id, intent='apply'):
                pytest.fail('Unknown process state must never grant an operation')
        assert process.poll() is None
    assert file_tree(game) == before


@pytest.mark.parametrize('rebound', [False, True])
@pytest.mark.parametrize('restricted', [False, True])
def test_case028_deleted_or_rebound_image_alias_still_blocks(app, tmp_path, rebound, restricted):
    context, installation_id, game = app
    before = file_tree(game)
    alias = tmp_path / 'Different-Image-Name.exe'
    os.link(game / EXE, alias)
    try:
        with without_debug_privilege(), running(alias, tmp_path, protected=restricted) as process:
            alias.unlink()
            if rebound:
                shutil.copyfile(game / EXE, alias)
                assert alias.stat().st_ino != (game / EXE).stat().st_ino
            assert (game / EXE).stat().st_nlink == 1
            with pinned_file(game / EXE, execute=True) as handle:
                candidates = file_process_ids(handle)
            assert process.pid in candidates  # Native file identity, no name/path filter.
            code = 'GUARD_PROCESS_UNKNOWN' if restricted else 'GUARD_PROCESS_RUNNING'
            report = status(app)
            assert not report['ready'] and code in report['blockers'], report
            snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
            for intent in ('apply', 'rollback', 'launch'):
                with rejected(code):
                    with context.deployment_guards.operation(installation_id, intent=intent) as session:
                        write_guarded(session, snapshot)
            assert file_tree(game) == before and process.poll() is None
            (tmp_path / 'deleted-image-alias-proof.json').write_text(json.dumps(
                dict(pid=process.pid, candidates=candidates, rebound=rebound,
                     restricted=restricted, report=report, zero_writes=True), indent=2), encoding='utf-8')
    finally:
        if alias.exists():
            alias.unlink()


def test_case028_unrelated_query_denied_process_is_excluded_by_file_identity(app, tmp_path, simulator):
    context, installation_id, game = app
    unrelated = tmp_path / 'unrelated.exe'
    shutil.copyfile(simulator, unrelated)
    with without_debug_privilege(), running(unrelated, tmp_path, protected=True) as process:
        with pytest.raises(pywintypes.error) as caught:
            win32api.OpenProcess(0x400, False, process.pid)
        assert caught.value.winerror == 5
        with pinned_file(game / EXE, execute=True) as handle:
            assert process.pid not in file_process_ids(handle)
        assert status(app)['ready']
        snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            write_guarded(session, snapshot)
        assert process.poll() is None
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UPDATED


def test_case028_native_file_query_failure_never_becomes_empty_list(app):
    _, _, game = app
    with pinned_file(game / EXE) as handle:
        closed = int(handle)
    with rejected('GUARD_PROCESS_UNKNOWN'):
        file_process_ids(closed)


def test_case028_nonprimary_mapping_keeps_live_identity_through_final_query(app, tmp_path, monkeypatch):
    context, installation_id, game = app
    original_query, original_open = guard_module.file_process_ids, win32api.OpenProcess
    with pinned_file(game / EXE) as handle:
        target_identity = file_identity(_information(handle, False))
    handles, observed_times = [], []
    target_queries = 0

    def open_actual_process(access, inherit, pid):
        handle = original_open(access, inherit, pid)
        if access == 0x400 and pid == os.getpid():
            handles.append(handle)
        return handle

    def query_actual_file(handle):
        nonlocal target_queries
        if file_identity(_information(handle, False)) == target_identity:
            target_queries += 1
            if target_queries == 2:
                assert handles, 'The real mapped candidate must have been inspected'
                # Real OS call fails on a closed handle. Retention prevents PID
                # reuse inside the final list comparison, even for class44=False.
                observed_times.extend(win32process.GetProcessTimes(h)['CreationTime'].isoformat() for h in handles)
        return original_query(handle)

    with (game / EXE).open('rb') as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ):
        with pinned_file(game / EXE, execute=True) as handle:
            assert os.getpid() in original_query(handle)
        # These wrappers retain the actual native API results; they do not
        # fabricate a candidate, process identity, query failure or clear state.
        with monkeypatch.context() as hooks:
            hooks.setattr(win32api, 'OpenProcess', open_actual_process)
            hooks.setattr(guard_module, 'file_process_ids', query_actual_file)
            assert status(app)['ready']  # A data mapping is not the target EXE.
        assert observed_times and all(int(h) == 0 for h in handles)
        snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            write_guarded(session, snapshot)
    (tmp_path / 'live-candidate-identity-proof.json').write_text(json.dumps(dict(
        pid=os.getpid(), target_queries=target_queries, observed_times=observed_times,
        handles_retained_at_final_query=True, handles_closed_after=True)), encoding='utf-8')
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UPDATED


@pytest.mark.parametrize('intent', ['apply', 'rollback'])
def test_case028_process_start_during_native_candidate_query_refuses_write(app, tmp_path, monkeypatch, intent):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with pinned_file(game / EXE) as handle:
        target_identity = file_identity(_information(handle, False))
    original_query = guard_module.file_process_ids
    process_scope, started_pid = None, None

    def query_then_launch(handle):
        nonlocal process_scope, started_pid
        result = original_query(handle)  # Actual pre-launch kernel result.
        if process_scope is None and file_identity(_information(handle, False)) == target_identity:
            process_scope = running(game / EXE, tmp_path)
            started_pid = process_scope.__enter__().pid
        return result

    try:
        with context.deployment_guards.operation(installation_id, intent=intent) as session:
            with monkeypatch.context() as hooks:
                hooks.setattr(guard_module, 'file_process_ids', query_then_launch)
                with rejected('GUARD_PROCESS_UNKNOWN'):
                    write_guarded(session, snapshot)
            assert process_scope is not None and not session.recovery_required
            assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED
    finally:
        if process_scope is not None:
            process_scope.__exit__(None, None, None)
    (tmp_path / 'native-query-arrival-proof.json').write_text(json.dumps(dict(
        intent=intent, real_started_pid=started_pid, guard='GUARD_PROCESS_UNKNOWN', zero_writes=True)), encoding='utf-8')


@pytest.mark.parametrize('intent', ['apply', 'rollback'])
def test_case028_real_file_symlink_replacement_refuses_write(app, tmp_path, intent):
    context, installation_id, game = app
    target = game / 'Mods/nested/sentinel.dll'
    outside = tmp_path / 'outside-file-symlink-sentinel.dll'
    outside.write_bytes(UNCHANGED)
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    target.unlink()
    try:
        os.symlink(outside, target)
    except OSError as exc:
        target.write_bytes(UNCHANGED)
        if exc.winerror == 1314:
            pytest.skip('Real file symlink requires the explicitly authorized elevated synthetic route')
        raise
    try:
        assert target.is_symlink()
        with rejected('GUARD_REPARSE_POINT'):
            context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
        with context.deployment_guards.operation(installation_id, intent=intent) as session:
            with rejected('GUARD_REPARSE_POINT'):
                write_guarded(session, snapshot)
        assert outside.read_bytes() == UNCHANGED and target.is_symlink()
        (tmp_path / 'file-symlink-proof.json').write_text(json.dumps(dict(
            intent=intent, real_symlink=True, outside_unchanged=True, zero_writes=True)), encoding='utf-8')
    finally:
        target.unlink()
        target.write_bytes(UNCHANGED)


def test_case028_same_thread_cannot_reenter_installation_mutex(app):
    context, installation_id, game = app
    with context.deployment_guards.operation(installation_id, intent='apply'):
        with rejected('GUARD_LEASE_BUSY'):
            with InstallationLease(identity(game)):
                pytest.fail('Windows recursive mutex semantics must not allow two mutations')
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


@pytest.mark.parametrize('invalidation', ['closed', 'owner_started'])
def test_case028_lost_lease_and_injected_old_owner_time_stop_before_write(app, invalidation):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        old_started = session.lease.owner_started
        if invalidation == 'closed':
            session.lease.close()
        else:
            # Start-time injection tests the PID-reuse check; it does not claim
            # that Windows actually recycled this PID during the test run.
            session.lease.owner_started = '1970-01-01T00:00:00+00:00'
        try:
            with rejected('GUARD_LEASE_LOST'):
                write_guarded(session, snapshot)
        finally:
            session.lease.owner_started = old_started
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


CHILD_OPERATION = r'''
import json, os, site, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
# Windows venv python.exe can be a launcher with another PID. Execute the base
# interpreter directly and load only the already-existing venv packages.
packages = str(Path(sys.argv[1]) / '.venv' / 'Lib' / 'site-packages')
sys.path.insert(0, packages)
site.addsitedir(packages)
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.deployments.paths import GuardRejected
context = DesktopContext(resolve_paths(sys.argv[2]))
report, release = Path(sys.argv[4]), Path(sys.argv[5])
context.initialize()
try:
    try:
        with context.deployment_guards.operation(sys.argv[3], intent='apply') as session:
            report.write_text(json.dumps({'state': 'acquired', 'pid': os.getpid(), 'owner_pid': session.lease.owner_pid}), encoding='utf-8')
            deadline = time.monotonic() + 30
            while not release.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            session.check()
    except GuardRejected as exc:
        report.write_text(json.dumps({'state': 'rejected', 'code': exc.code, 'pid': os.getpid()}), encoding='utf-8')
finally:
    context.close()
'''


@contextmanager
def child_operation(paths, installation_id, directory, label):
    report = directory / (label + '.json')
    release = directory / (label + '.release')
    command = [sys._base_executable, '-B', '-c', CHILD_OPERATION, str(ROOT), str(paths.root), installation_id,
               str(report), str(release)]
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        outcome = json.loads(await_file(report, process))
        yield process, outcome
    finally:
        release.touch()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()  # Only the exact child this context created.
            stdout, stderr = process.communicate(timeout=10)
        (directory / (label + '-process.json')).write_text(json.dumps(dict(command=command,
            exit_code=process.returncode, stdout=stdout, stderr=stderr), ensure_ascii=False, indent=2), encoding='utf-8')


def test_case028_two_real_manager_processes_different_data_roots_compete_and_crash_releases(app, tmp_path):
    context, installation_id, game = app
    other = DesktopContext(resolve_paths(str(tmp_path / 'other-private-data')))
    other.initialize()
    try:
        other_id = register(other, game)
        assert other_id != installation_id  # DB ids differ; physical installation must still share the lease.
        with child_operation(context.paths, installation_id, tmp_path, 'owner') as (owner, acquired):
            assert acquired['state'] == 'acquired' and acquired['owner_pid'] == owner.pid, acquired
            with child_operation(other.paths, other_id, tmp_path, 'contender') as (contender, blocked):
                assert blocked['state'] == 'rejected' and blocked['code'] == 'GUARD_LEASE_BUSY', blocked
                assert contender.wait(timeout=10) == 0
            report = status(app)
            assert not report['ready'] and 'GUARD_LEASE_BUSY' in report['blockers']
            owner.kill()  # Deliberate crash of our self-created manager; no normal lease close.
            assert owner.wait(timeout=10) != 0
            with other.deployment_guards.operation(other_id, intent='apply') as session:
                session.check()
        assert status(app)['ready']
        assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED
    finally:
        other.close()


def put_recovery_fixture(context, installation_id, state, last_sequence=0):
    """Persist real linked DTOs through the production compatibility-aware store.

    This seeds recovery state, not a claim that a journal/executor already ran.
    Journal parsing and recovery execution belong to the later task cards.
    """
    fixtures = json.loads((ROOT / 'tests/fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    installation = context.store.get('Installation', installation_id)
    profile = deepcopy(fixtures['Profile'])
    profile.update(profile_id=str(uuid4()), installation_id=installation_id, selections=[], display_order=[])
    context.store.put(ENTITY_MODELS['Profile'].model_validate(profile))
    baseline = deepcopy(fixtures['BaselineSnapshot'])
    baseline.update(snapshot_id=str(uuid4()), installation_id=installation_id, files=[])
    context.store.put(ENTITY_MODELS['BaselineSnapshot'].model_validate(baseline))
    plan = deepcopy(fixtures['ActionPlan'])
    plan.update(plan_id=str(uuid4()), idempotency_key=str(uuid4()), installation_id=installation_id,
        fingerprint_id=str(installation.fingerprint_id), profile_id=profile['profile_id'], profile_revision=0,
        baseline_snapshot_id=baseline['snapshot_id'], operations=[], preconditions=[], verification_checks=[])
    assert context.store.library_root is not None
    context.store.put(ENTITY_MODELS['ActionPlan'].model_validate(plan))
    transaction = deepcopy(fixtures['Transaction'])
    transaction.update(transaction_id=str(uuid4()), plan_id=plan['plan_id'], state=state,
        last_sequence=last_sequence, target_generation=str(uuid4()))
    context.store.put(ENTITY_MODELS['Transaction'].model_validate(transaction))
    return transaction['transaction_id']


@pytest.mark.parametrize('state,last_sequence', [
    ('staging', 0), ('ready', 0), ('applying', 0), ('rolling_back', 0),
    ('recovery_required', 0), ('failed', 1), ('cancelled', 1),
])
def test_case028_persisted_unfinished_transaction_blocks_all_consumers_but_reads_work(app, state, last_sequence):
    context, installation_id, game = app
    transaction_id = put_recovery_fixture(context, installation_id, state, last_sequence)
    before = file_tree(game)
    report = status(app)
    assert not report['ready'] and 'GUARD_RECOVERY_REQUIRED' in report['blockers']
    assert context.api.dispatch(request('installation.list'))['ok']
    assert str(context.store.get('Transaction', transaction_id).transaction_id) == transaction_id
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    for intent in ('apply', 'rollback', 'launch'):
        with rejected('GUARD_RECOVERY_REQUIRED'):
            with context.deployment_guards.operation(installation_id, intent=intent) as session:
                write_guarded(session, snapshot)
    assert file_tree(game) == before


@pytest.mark.parametrize('state', ['planned', 'approved', 'committed', 'rolled_back', 'failed', 'cancelled'])
def test_case028_nonwriting_or_settled_transaction_does_not_fake_recovery(app, state):
    context, installation_id, game = app
    put_recovery_fixture(context, installation_id, state)
    if state in {'failed','cancelled'}:
        assert status(app)['ready']
        with context.deployment_guards.operation(installation_id, intent='apply') as session:
            session.check()
    else:
        # A legacy DTO state alone cannot authenticate a missing journal or
        # prove its claimed committed/rolled_back files under PS-03-03.
        assert 'GUARD_RECOVERY_REQUIRED' in status(app)['blockers']
        with rejected('GUARD_RECOVERY_REQUIRED'):
            with context.deployment_guards.operation(installation_id,intent='apply'):
                raise AssertionError('Unverified journal acquired a mutation guard')
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


def test_case028_recovery_appears_after_preflight_next_step_refuses_write(app):
    context, installation_id, game = app
    snapshot = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, intent='apply') as session:
        put_recovery_fixture(context, installation_id, 'applying', 1)
        with rejected('GUARD_RECOVERY_REQUIRED'):
            write_guarded(session, snapshot)
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


def test_case028_unknown_retained_journal_is_preserved_and_blocks_all_operations(app):
    context, installation_id, game = app
    journal = context.paths.root / 'deployments/journals/unrecognized.jsonl'
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b'{"future_journal":"unknown or torn content"}\n')
    original = journal.read_bytes()
    assert 'GUARD_RECOVERY_REQUIRED' in status(app)['blockers']
    for intent in ('apply', 'rollback', 'launch'):
        with rejected('GUARD_RECOVERY_REQUIRED'):
            with context.deployment_guards.operation(installation_id, intent=intent):
                pytest.fail('An unverified journal cannot authorize a new operation')
    assert journal.read_bytes() == original
    assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED


@pytest.mark.parametrize('relative', ['deployments', 'deployments/journals'])
def test_case028_dangling_recovery_junction_is_preserved_and_fails_closed(app, tmp_path, relative):
    context, installation_id, game = app
    link = context.paths.root / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    missing = tmp_path / 'missing-journal-destination'
    junction(link, missing)
    try:
        assert not link.exists() and link.lstat().st_file_attributes & 0x400
        report = status(app)
        assert not report['ready'] and 'GUARD_RECOVERY_REQUIRED' in report['blockers']
        for intent in ('apply', 'rollback', 'launch'):
            with rejected('GUARD_RECOVERY_REQUIRED'):
                with context.deployment_guards.operation(installation_id, intent=intent):
                    pytest.fail('A dangling recovery junction is uncertain recovery state')
        assert link.lstat().st_file_attributes & 0x400
        assert not missing.exists()
        assert (game / 'Mods/nested/sentinel.dll').read_bytes() == UNCHANGED
    finally:
        os.rmdir(link)


@pytest.mark.parametrize('command,payload', [
    ('deployment.guard_status', {'path': r'C:\arbitrary'}),
    ('deployment.guard_status', {'installation_id': str(uuid4()), 'path': r'C:\arbitrary'}),
    ('deployment.apply', {'path': r'C:\arbitrary'}),
    ('deployment.rollback', {'path': r'C:\arbitrary'}),
    ('game.launch', {'path': r'C:\arbitrary'}),
    ('deployment.write', {'path': r'C:\arbitrary', 'bytes': 'unsafe'}),
])
def test_case028_bridge_does_not_expose_raw_paths_or_execution(app, command, payload):
    context, _, game = app
    before = file_tree(game)
    response = context.api.dispatch(request(command, payload))
    assert not response['ok']
    assert response['error']['code'] in ('INVALID_REQUEST', 'UNSUPPORTED_COMMAND')
    assert file_tree(game) == before
