"""CASE-035 real desktop RPC -> installation guard -> synthetic cfg file."""
import hashlib
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
import win32con
import win32file

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.integration.test_deployment_guards import app, simulator
from tests.unit.test_config_adapters import TEXT

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Existing Windows installation guard')


@pytest.fixture
def config_app(app):
    context, installation_id, game = app
    path = game / 'UserData/PSWallpaper.cfg'
    path.parent.mkdir()
    path.write_bytes(b'\xef\xbb\xbf' + TEXT.replace('\n', '\r\n').encode())
    yield context, installation_id, path


def rpc(context, command, **payload):
    return context.api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))


def read(context, installation_id):
    response = rpc(context, 'config.read', installation_id=installation_id, adapter_id='pswallpaper')
    assert response['ok'], response
    return response['data']


def save(context, installation_id, view, changes, backup=True, **extra):
    return rpc(context, 'config.save', installation_id=installation_id, adapter_id='pswallpaper',
               expected_revision=view['revision'], changes=changes, backup=backup, **extra)


def test_case035_real_rpc_save_reopen_and_optional_original_backup(config_app):
    context, installation_id, path = config_app
    before = path.read_bytes()
    view = read(context, installation_id)
    result = save(context, installation_id, view, dict(WallpaperFit='fit', WallpaperBrightness=80, LogToFile=False))
    assert result['ok'], result
    assert result['data']['saved'] and result['data']['changed'] and result['data']['backup_created']
    assert (context.paths.root / 'config-backups' / installation_id / path.name).read_bytes() == before
    expected = before.replace(b'WallpaperFit = cover', b'WallpaperFit = fit').replace(
        b'WallpaperBrightness=55', b'WallpaperBrightness=80').replace(b'LogToFile=true', b'LogToFile=false')
    assert path.read_bytes() == expected
    assert result['data']['revision'] == hashlib.sha256(expected).hexdigest()
    assert context.store.connection.execute('SELECT count(*) FROM action_plans').fetchone()[0] == 0
    paths = context.paths
    context.close()
    reopened = DesktopContext(paths)
    reopened.initialize()
    reopened.api = DesktopApi(reopened)
    try:
        assert read(reopened, installation_id)['fields'] == result['data']['fields']
    finally:
        reopened.close()


def test_case035_noop_and_backup_disabled_do_not_create_backup(config_app):
    context, installation_id, path = config_app
    before = path.read_bytes()
    modified = path.stat().st_mtime_ns
    view = read(context, installation_id)
    result = save(context, installation_id, view, {'WallpaperBrightness': 55})
    assert result['ok'] and not result['data']['changed'] and not result['data']['backup_created']
    assert path.read_bytes() == before and path.stat().st_mtime_ns == modified
    result = save(context, installation_id, view, {'WallpaperBrightness': 60}, backup=False)
    assert result['ok'] and result['data']['changed'] and not result['data']['backup_created']
    assert not (context.paths.root / 'config-backups').exists()


def test_case035_latest_backup_is_one_original_file(config_app):
    context, installation_id, path = config_app
    first = save(context, installation_id, read(context, installation_id), {'WallpaperBrightness': 60})
    assert first['ok'], first
    previous = path.read_bytes()
    second = save(context, installation_id, read(context, installation_id), {'WallpaperBrightness': 65})
    assert second['ok'], second
    assert list((context.paths.root / 'config-backups' / installation_id).iterdir()) == [
        context.paths.root / 'config-backups' / installation_id / 'PSWallpaper.cfg']
    assert (context.paths.root / 'config-backups' / installation_id / 'PSWallpaper.cfg').read_bytes() == previous


def test_case035_external_edit_is_not_overwritten(config_app):
    context, installation_id, path = config_app
    view = read(context, installation_id)
    changed = path.read_bytes() + '# 外部修改\r\n'.encode()
    path.write_bytes(changed)
    response = save(context, installation_id, view, {'LogToFile': False})
    assert not response['ok'] and response['error']['code'] == 'CONFIG_CHANGED'
    assert path.read_bytes() == changed
    assert not (context.paths.root / 'config-backups').exists()


@pytest.mark.parametrize('raw', [b'bad input', b'[PSWallpaper]\nWallpaperFit=cover\nWallpaperFit=fit', b'\xffbad'])
def test_case035_unsupported_config_is_readonly_and_never_rewritten(config_app, raw):
    context, installation_id, path = config_app
    path.write_bytes(raw)
    view = read(context, installation_id)
    assert not view['editable'] and view['fields'] == [] and view['message']
    response = save(context, installation_id, view, {'LogToFile': False})
    assert not response['ok'] and response['error']['code'] == 'CONFIG_UNSUPPORTED'
    assert path.read_bytes() == raw


def test_case035_missing_config_is_not_created(config_app):
    context, installation_id, path = config_app
    path.unlink()
    response = rpc(context, 'config.read', installation_id=installation_id, adapter_id='pswallpaper')
    assert not response['ok'] and response['error']['code'] == 'CONFIG_MISSING'
    assert not path.exists()


@pytest.mark.parametrize('extra', [{'path': 'C:/private/file'}, {'relative_path': '../save.dat'}, {'approved': True}])
def test_case035_rpc_does_not_accept_paths_or_approval_fields(config_app, extra):
    context, installation_id, path = config_app
    before = path.read_bytes()
    response = save(context, installation_id, read(context, installation_id), {'LogToFile': False}, **extra)
    assert not response['ok'] and response['error']['code'] == 'INVALID_REQUEST'
    assert path.read_bytes() == before


@pytest.mark.parametrize('changes', [{'WallpaperFile': '../../save.dat'}, {'WallpaperBrightness': True},
                                  {'WallpaperBrightness': 101}, {'LogToFile': 'false'}])
def test_case035_unknown_fields_and_wrong_types_are_rejected(config_app, changes):
    context, installation_id, path = config_app
    before = path.read_bytes()
    response = save(context, installation_id, read(context, installation_id), changes)
    assert not response['ok'] and response['error']['code'] == 'CONFIG_VALUE_INVALID'
    assert path.read_bytes() == before


def test_case035_actual_exclusive_file_lock_returns_error_without_write(config_app):
    context, installation_id, path = config_app
    view, before = read(context, installation_id), path.read_bytes()
    lock = win32file.CreateFile(str(path), win32con.GENERIC_READ, 0, None, win32con.OPEN_EXISTING, 0, None)
    try:
        response = save(context, installation_id, view, {'LogToFile': False})
        assert not response['ok'] and response['error']['code'] == 'CONFIG_IO'
    finally:
        lock.Close()
    assert path.read_bytes() == before


def test_case035_actual_readonly_attribute_returns_error_without_write(config_app):
    context, installation_id, path = config_app
    view, before = read(context, installation_id), path.read_bytes()
    win32file.SetFileAttributes(str(path), win32con.FILE_ATTRIBUTE_READONLY)
    try:
        response = save(context, installation_id, view, {'LogToFile': False})
        assert not response['ok'] and response['error']['code'] == 'CONFIG_IO'
    finally:
        win32file.SetFileAttributes(str(path), win32con.FILE_ATTRIBUTE_NORMAL)
    assert path.read_bytes() == before


def test_case035_hardlinked_config_cannot_modify_another_file(config_app, tmp_path):
    context, installation_id, path = config_app
    before = path.read_bytes()
    link = tmp_path / 'outside-config.cfg'
    link.hardlink_to(path)
    response = rpc(context, 'config.read', installation_id=installation_id, adapter_id='pswallpaper')
    assert not response['ok'] and response['error']['code'] == 'GUARD_HARDLINK'
    assert link.read_bytes() == before


def test_case035_real_synthetic_game_process_blocks_save_but_read_is_available(config_app):
    context, installation_id, path = config_app
    before = path.read_bytes()
    marker = path.parent / 'synthetic-process-ready'
    process = subprocess.Popen([str(path.parent.parent / 'Probably Stolen.exe'), str(marker), 'normal'],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        view = read(context, installation_id)
        response = save(context, installation_id, view, {'LogToFile': False})
        assert not response['ok'] and response['error']['code'] == 'GUARD_PROCESS_RUNNING', response
        assert path.read_bytes() == before
    finally:
        process.terminate()
        process.wait(timeout=10)
