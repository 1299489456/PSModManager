"""MelonLoader detection and installation through the real desktop bridge.

The official release is only extracted into owned synthetic installations.
Neither its bootstrap nor the game executable is loaded or executed here.
"""
import hashlib
import json
import shutil
import threading
import zipfile

import pytest
import win32con
import win32file

from backend.games.probably_stolen.layout import EXE
from tests.integration.harness.transaction_recovery import (
    ROOT, TransactionFixture, finish, request, tree,
)


OFFICIAL_ARCHIVE = (ROOT / 'docs/development/evidence/'
    '20261015T055229Z-melonloader/MelonLoader.x64.zip')


@pytest.fixture(scope='module')
def archive():
    assert OFFICIAL_ARCHIVE.is_file(), 'Prepare the documented official 0.7.3 release fixture'
    return OFFICIAL_ARCHIVE


@pytest.fixture
def app(tmp_path):
    fixture = TransactionFixture(tmp_path / 'loader')
    # The shared discovery fixture has only a PE signature. This feature also
    # reads its machine architecture; keep this fixture explicitly inert x64.
    executable = fixture.game / EXE
    pe = bytearray(executable.read_bytes())
    pe[132:134] = (0x8664).to_bytes(2, 'little')
    executable.write_bytes(pe)
    shutil.rmtree(fixture.game / 'MelonLoader')
    yield fixture
    fixture.close()


def status(app):
    value = app.call('loader.status', {'installation_id': app.installation})
    assert not value.get('error_code'), value
    assert value['recommended_version'] == '0.7.3'
    assert isinstance(value['checks'], list) and value['message']
    return value


def choose(app, archive):
    app.context.choose_loader_source = lambda: str(archive)
    result = app.call('loader.choose', {'installation_id': app.installation})
    assert result.get('source_handle'), result
    return result['source_handle']


def install_local(app, archive):
    return app.call('loader.install_local', {
        'installation_id': app.installation,
        'source_handle': choose(app, archive),
    })


def require_failure(result):
    assert result.get('error_code') and result.get('message'), result
    assert not result.get('installed'), result


def test_status_missing_loader_and_no_configuration_are_read_only(app):
    before = tree(app.game)
    value = status(app)
    assert value['status'] == 'not_installed'
    assert value['ready'] is False and value['can_install'] is True
    assert tree(app.game) == before
    assert not (app.game / 'UserData/Loader.cfg').exists()


def test_actual_bridge_local_official_release_extracts_every_file_and_reopens(app, archive):
    before = tree(app.game)
    result = install_local(app, archive)
    assert result.get('installed') is True, result
    with zipfile.ZipFile(archive) as stream:
        entries = [entry for entry in stream.infolist() if not entry.is_dir()]
        assert len(entries) == 164
        assert result['files_written'] == len(entries)
        for entry in entries:
            assert (app.game / entry.filename).read_bytes() == stream.read(entry), entry.filename
    for relative, digest in before.items():
        if digest != '<directory>':
            assert tree(app.game)[relative] == digest, relative
    app.reopen()
    value = status(app)
    assert value['status'] == 'installed' and value['version_raw'].startswith('0.7.3'), value
    assert not (app.game / 'dobby.dll').exists(), 'This official release has no dobby.dll prerequisite'
    assert not any(item['status'] == 'missing' and 'dobby' in item['id'].lower()
                   for item in value['checks'])


def test_loader_disable_configuration_is_visible_without_mod_changes(app, archive):
    assert install_local(app, archive).get('installed')
    configuration = app.game / 'UserData/Loader.cfg'
    configuration.write_text('[loader]\ndisable = true\n', encoding='utf-8')
    before = tree(app.game)
    value = status(app)
    assert value['status'] == 'installed' and value['ready'] is False, value
    assert any(item['status'] == 'disabled' for item in value['checks']), value
    assert tree(app.game) == before


def test_partial_loader_is_repairable(app, archive):
    (app.game / 'MelonLoader/net6').mkdir(parents=True)
    (app.game / 'MelonLoader/net6/MelonLoader.dll').write_bytes(b'incomplete previous installation')
    value = status(app)
    assert value['status'] == 'incomplete' and value['can_install'] is True, value
    assert install_local(app, archive).get('installed') is True
    assert status(app)['status'] == 'installed'


def seed_core(app, archive):
    from backend.loaders.melonloader.service import CORE_FILES
    with zipfile.ZipFile(archive) as stream:
        for relative in CORE_FILES:
            target = app.game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(stream.read(relative))


def test_damaged_core_dll_is_not_reported_as_complete(app, archive):
    seed_core(app, archive)
    assert status(app)['status'] == 'installed'
    (app.game / 'MelonLoader/net6/MelonLoader.NativeHost.dll').write_bytes(b'not a PE binary')
    value = status(app)
    assert value['status'] == 'incomplete' and value['ready'] is False, value
    assert next(item for item in value['checks'] if item['id'] == 'loader_files')['status'] == 'missing'


def test_incompatible_framework_configuration_is_visible(app, archive):
    seed_core(app, archive)
    configuration = app.game / 'MelonLoader/net6/MelonLoader.runtimeconfig.json'
    data = json.loads(configuration.read_text(encoding='utf-8-sig'))
    data['runtimeOptions']['framework']['name'] = 'Microsoft.WindowsDesktop.App'
    configuration.write_text(json.dumps(data), encoding='utf-8')
    value = status(app)
    assert value['ready'] is False, value
    assert next(item for item in value['checks'] if item['id'] == 'runtime_config')['status'] == 'unknown'


def test_x86_game_is_explained_and_not_written(app, archive):
    executable = app.game / EXE
    pe = bytearray(executable.read_bytes())
    pe[132:134] = (0x14c).to_bytes(2, 'little')
    executable.write_bytes(pe)
    before = tree(app.game)
    value = status(app)
    assert value['can_install'] is False and value['ready'] is False, value
    require_failure(install_local(app, archive))
    assert tree(app.game) == before


def test_local_picker_cancellation_returns_no_handle(app):
    app.context.choose_loader_source = lambda: None
    result = app.call('loader.choose', {'installation_id': app.installation})
    assert result['source_handle'] is None
    assert status(app)['status'] == 'not_installed'


def test_wrong_checksum_rejects_local_archive_before_writing(app, tmp_path):
    source = tmp_path / 'untrusted.zip'
    with zipfile.ZipFile(source, 'w') as stream:
        stream.writestr('version.dll', b'not the official binary')
    before = tree(app.game)
    require_failure(install_local(app, source))
    assert tree(app.game) == before


@pytest.mark.parametrize('entry', [
    '../escape.dll', '/absolute.dll', 'MelonLoader/../../escape.dll',
    'Mods/foreign.json', 'UserData/Loader.cfg',
])
def test_even_checksum_trusted_archives_cannot_write_outside_loader(app, tmp_path, entry):
    from backend.loaders.melonloader.service import MelonLoaderService
    source = tmp_path / 'bad-layout.zip'
    with zipfile.ZipFile(source, 'w') as stream:
        stream.writestr('version.dll', b'test bootstrap')
        stream.writestr(entry, b'forbidden content')
    app.context.loader = MelonLoaderService(app.context.paths, app.context.deployment_guards,
        release_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    before = tree(app.game)
    require_failure(install_local(app, source))
    assert tree(app.game) == before
    assert not (app.root / 'escape.dll').exists()
    assert not (app.game / 'escape.dll').exists()


def test_corrupt_archive_is_explained_before_writing(app, tmp_path):
    from backend.loaders.melonloader.service import MelonLoaderService
    source = tmp_path / 'corrupt.zip'
    source.write_bytes(b'not a zip archive')
    app.context.loader = MelonLoaderService(app.context.paths, app.context.deployment_guards,
        release_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    before = tree(app.game)
    require_failure(install_local(app, source))
    assert tree(app.game) == before


def test_native_sharing_error_preserves_prior_loader_and_retry_succeeds(app, archive):
    (app.game / 'MelonLoader/net6').mkdir(parents=True)
    (app.game / 'MelonLoader/net6/MelonLoader.dll').write_bytes(b'old-loader')
    target = app.game / 'version.dll'
    target.write_bytes(b'old-bootstrap')
    before = tree(app.game)
    handle = win32file.CreateFile(str(target), win32con.GENERIC_READ,
        win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING, 0, None)
    try:
        require_failure(install_local(app, archive))
        assert tree(app.game) == before
    finally:
        handle.Close()
    assert install_local(app, archive).get('installed') is True
    assert status(app)['status'] == 'installed'


def test_install_download_failure_is_retryable_without_partial_files(app, archive, monkeypatch):
    before = tree(app.game)
    def unavailable(destination, check, progress):
        raise OSError('owned fixture has no network')
    monkeypatch.setattr(app.context.loader, '_download', unavailable)
    require_failure(app.call('loader.install', {'installation_id': app.installation}))
    assert tree(app.game) == before
    def fixture_download(destination, check, progress):
        check()
        shutil.copyfile(archive, destination)
    monkeypatch.setattr(app.context.loader, '_download', fixture_download)
    result = app.call('loader.install', {'installation_id': app.installation})
    assert result.get('installed') is True, result
    assert status(app)['status'] == 'installed'


def test_cancel_real_install_job_during_download_leaves_game_unchanged(app, monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    def wait_for_cancel(destination, check, progress):
        entered.set()
        assert resume.wait(5), 'The test must release its owned download'
        check()
    monkeypatch.setattr(app.context.loader, '_download', wait_for_cancel)
    before = tree(app.game)
    response = app.context.api.dispatch(request('loader.install', {'installation_id': app.installation}))
    assert response['ok'] and 'task_id' in response['data'], response
    snapshot = response['data']
    try:
        assert entered.wait(5)
        cancelled = app.context.api.dispatch(request('task.cancel', {'task_id': snapshot['task_id']}))
        assert cancelled['ok'], cancelled
    finally:
        resume.set()
    terminal = finish(app.context.api, snapshot)
    assert terminal['state'] == 'cancelled', terminal
    assert tree(app.game) == before
    assert status(app)['status'] == 'not_installed'


def test_cancel_at_final_publish_rolls_back_before_commit(app, archive, monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    original = app.context.loader._publish
    def publish_with_final_boundary(root, stage, files, check, progress, *args, **kwargs):
        def at_boundary(done, total, *fields):
            progress(done, total, *fields)
            if done == total:
                entered.set()
                assert resume.wait(10), 'The test must release its owned publication boundary'
        return original(root, stage, files, check, at_boundary, *args, **kwargs)
    monkeypatch.setattr(app.context.loader, '_publish', publish_with_final_boundary)
    handle = choose(app, archive)
    before = tree(app.game)
    response = app.context.api.dispatch(request('loader.install_local', {
        'installation_id': app.installation, 'source_handle': handle}))
    assert response['ok'] and 'task_id' in response['data'], response
    snapshot = response['data']
    try:
        assert entered.wait(60), 'Official archive did not reach its final publication'
        cancelled = app.context.api.dispatch(request('task.cancel', {'task_id': snapshot['task_id']}))
        assert cancelled['ok'], cancelled
    finally:
        resume.set()
        terminal = finish(app.context.api, snapshot, timeout=60)
    assert terminal['state'] == 'cancelled', terminal
    assert tree(app.game) == before


@pytest.mark.parametrize('extra', ['path', 'url', 'release_sha256', 'shell'])
def test_bridge_rejects_arbitrary_install_source_and_commands(app, extra):
    payload = {'installation_id': app.installation, extra: 'not accepted'}
    response = app.context.api.dispatch(request('loader.install', payload))
    assert response['ok'] is False and response['error']['code'] == 'INVALID_REQUEST', response
    assert status(app)['status'] == 'not_installed'
