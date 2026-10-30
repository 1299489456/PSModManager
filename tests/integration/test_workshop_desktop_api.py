"""Workshop UI endpoints use host authority; never mutate a real Steam account."""
from uuid import uuid4
import shutil
import time
import threading

import pytest

from backend.app.native_api import _preview_image
from tests.integration.test_native_desktop_api import app, ok, rpc


def test_action_uses_registered_installation_and_explicit_ids(app, monkeypatch):
    ctx, identity, root, _ = app
    calls = []
    def action(installation, **arguments):
        calls.append((installation, arguments))
        return dict(operation_id='test-operation', state='queued', items=[])
    monkeypatch.setattr(ctx.workshop, 'action', action)
    result = ok(ctx, 'workshop.action', installation_id=identity, action='update', ids=['3828868413'])
    assert result['state'] == 'queued'
    assert calls[0][0]['canonical_root'] == str(root)
    assert calls[0][1] == dict(action='update', ids=['3828868413'])
    for changes in ({'action': 'delete'}, {'ids': []}, {'ids': ['1'] * 101},
                    {'ids': [3828868413]}, {'ids': ['../private']}, {'path': str(root)}):
        payload = dict(installation_id=identity, action='update', ids=['3828868413'])
        payload.update(changes)
        result = rpc(ctx, 'workshop.action', **payload)
        assert not result['ok'] and result['error']['code'] == 'INVALID_REQUEST'
    assert len(calls) == 1


def test_capabilities_uses_selected_installation(app, monkeypatch):
    ctx, identity, _, _ = app
    calls = []
    def capabilities(value=None):
        calls.append(value)
        return dict(direct_subscription=dict(available=bool(value)))
    monkeypatch.setattr(ctx.workshop, 'capabilities', capabilities)
    assert ok(ctx, 'workshop.capabilities', installation_id=identity)['direct_subscription']['available']
    assert calls[-1]['installation_id'] == identity
    assert not ok(ctx, 'workshop.capabilities')['direct_subscription']['available']


@pytest.mark.parametrize('real_release', [False, True], ids=['hook-order', 'manual-without-steam'])
def test_launch_releases_steam_session_before_owned_process_starts(app, monkeypatch, tmp_path, real_release):
    from tests.integration.harness.launch_probe import build_probe
    ctx, identity, root, _ = app
    shutil.copyfile(build_probe(tmp_path/'probe'), root/'Probably Stolen.exe')
    calls = []
    def release(value):
        assert not (root/'launch-probe.txt').exists()
        calls.append(value['installation_id'])
    if not real_release:
        monkeypatch.setattr(ctx.workshop, 'release', release)
    try:
        result = ok(ctx, 'game.launch', installation_id=identity)
        assert result['state'] == 'running'
        if not real_release:
            assert calls == [identity]
    finally:
        (root/'exit-request.txt').write_text('0')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and ctx.launcher.status(identity)['state'] == 'running':
            time.sleep(.05)
        assert ctx.launcher.status(identity)['state'] != 'running'


def test_data_migration_cannot_interrupt_steam_mutation(app, monkeypatch, tmp_path):
    ctx, _, _, _ = app
    monkeypatch.setattr(ctx.workshop, 'is_busy', lambda: True)
    destination = tmp_path/'migration-target'
    result = rpc(ctx, 'app.storage.start', target=str(destination))
    assert not result['ok'] and result['error']['code'] == 'STORAGE_OPERATIONS_RUNNING'
    assert not ctx.storage.blocked and not destination.exists()


def test_translation_uses_host_metadata_not_page_text(app, monkeypatch):
    ctx, identity, _, _ = app
    calls = []
    task_id = str(uuid4())

    def detail(workshop_id, installation, *, check):
        check()
        calls.append((workshop_id, installation['installation_id']))
        return dict(workshop_id=workshop_id, available=True, title='Item Details',
                    description='Show more item details.', description_truncated=False)

    def translate(request_id, load, errors, **options):
        assert options == dict(cache_id='3828868413', force_refresh=False)
        source = load(lambda: None)
        assert source['parts'] == [('title', 'Item Details'), ('description', 'Show more item details.')]
        assert not source['truncated']
        assert 'WORKSHOP_NETWORK_ERROR' in errors
        return dict(task_id=task_id, state='queued')

    monkeypatch.setattr(ctx.workshop, 'translation_detail', detail)
    monkeypatch.setattr(ctx.ai, 'translate', translate)
    result = rpc(ctx, 'workshop.translate', installation_id=identity, workshop_id='3828868413')
    assert result['ok'] and result['task_id'] == task_id
    assert calls == [('3828868413', identity)]
    for extra in ({'text': 'page-controlled instructions'}, {'url': 'file:///C:/private'}, {'refresh': True}):
        refused = rpc(ctx, 'workshop.translate', workshop_id='3828868413', **extra)
        assert not refused['ok'] and refused['error']['code'] == 'INVALID_REQUEST'


def test_translation_rejects_unknown_installation_before_starting_job(app, monkeypatch):
    ctx, _, _, _ = app
    monkeypatch.setattr(ctx.ai, 'translate', lambda *args: pytest.fail('must resolve installation before starting'))
    result = rpc(ctx, 'workshop.translate', installation_id=str(uuid4()), workshop_id='3828868413')
    assert not result['ok'] and result['error']['code'] == 'INSTALLATION_NOT_FOUND'


@pytest.mark.parametrize('header,mime', [(b'\x89PNG\r\n\x1a\n', 'png'), (b'\xff\xd8\xff', 'jpeg'),
                                         (b'RIFF0000WEBP', 'webp')])
def test_local_cover_is_safe_embedded_image(tmp_path, header, mime):
    path = tmp_path / 'preview.png'
    path.write_bytes(header + b'test-only-image-signature')
    mod = dict(folder_path=str(tmp_path), preview_path=str(path))
    _preview_image(mod)
    assert mod['preview_url'].startswith(f'data:image/{mime};base64,')


def test_missing_or_unsafe_cover_leaves_remote_fallback_available(tmp_path):
    root = tmp_path / 'mod'
    root.mkdir()
    other = tmp_path / 'not-a-cover.png'
    other.write_bytes(b'\x89PNG\r\n\x1a\nprivate')
    for path in (None, root / 'missing.png', other):
        mod = dict(workshop_id='3828902092', folder_path=str(root), preview_path=str(path) if path else None)
        _preview_image(mod)
        assert 'preview_url' not in mod and mod['workshop_id'] == '3828902092'
    svg = root / 'preview.png'
    svg.write_text('<svg onload="alert(1)"></svg>')
    mod = dict(folder_path=str(root), preview_path=str(svg))
    _preview_image(mod)
    assert 'preview_url' not in mod


def test_slow_workshop_read_does_not_block_desktop_and_blocks_migration(app, monkeypatch, tmp_path):
    ctx, _, _, _ = app
    entered, release = threading.Event(), threading.Event()
    responses = []
    def detail(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {'title': 'Owned slow public response'}
    monkeypatch.setattr(ctx.workshop, 'detail', detail)
    worker = threading.Thread(target=lambda: responses.append(rpc(ctx, 'workshop.detail', workshop_id='123')))
    worker.start()
    try:
        assert entered.wait(2)
        immediate = threading.Event()
        reader = threading.Thread(target=lambda: (ok(ctx, 'app.info'), immediate.set()))
        reader.start()
        assert immediate.wait(1), 'A public network read held the desktop command mutex'
        reader.join()
        refused = rpc(ctx, 'app.storage.start', target=str(tmp_path/'cannot-move-active-cache'))
        assert refused['error']['code'] == 'STORAGE_OPERATIONS_RUNNING'
        assert not ctx.storage.blocked
    finally:
        release.set()
        worker.join(3)
    assert responses[0]['ok'] and ctx.api._active_reads == 0


def test_close_drains_admitted_public_reads_and_rejects_new_calls(app, monkeypatch):
    ctx, _, _, _ = app
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    def detail(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {}
    monkeypatch.setattr(ctx.workshop, 'detail', detail)
    worker = threading.Thread(target=lambda: rpc(ctx, 'workshop.detail', workshop_id='123'))
    worker.start()
    assert entered.wait(2)
    closer = threading.Thread(target=lambda: (ctx.api._close(), closed.set()))
    closer.start()
    try:
        deadline = time.monotonic() + 2
        while not ctx.api._closed and time.monotonic() < deadline:
            time.sleep(.01)
        assert ctx.api._closed and not closed.is_set()
        assert rpc(ctx, 'app.info')['error']['code'] == 'RUNTIME_CLOSED'
    finally:
        release.set()
        worker.join(3)
        closer.join(3)
    assert closed.is_set() and ctx.api._active_reads == 0


def test_workshop_page_size_image_and_refresh_are_closed_host_inputs(app, monkeypatch):
    ctx, identity, _, _ = app
    monkeypatch.setattr(ctx.workshop, 'browse', lambda value, **args: args)
    for size in (12, 24, 48):
        assert ok(ctx, 'workshop.browse', page_size=size)['page_size'] == size
    for size in (0, 13, 100, '24', 24.0, True):
        assert rpc(ctx, 'workshop.browse', page_size=size)['error']['code'] == 'INVALID_REQUEST'
    monkeypatch.setattr(ctx.workshop, 'image', lambda value, **args: dict(workshop_id=value, **args))
    assert ok(ctx, 'workshop.image', workshop_id='123', refresh=True) == dict(workshop_id='123', refresh=True)
    assert rpc(ctx, 'workshop.image', workshop_id='123', url='https://example.invalid/private')['error']['code'] == 'INVALID_REQUEST'
    monkeypatch.setattr(ctx.workshop, 'status', lambda value, **args: args)
    assert ok(ctx, 'workshop.status', installation_id=identity, refresh=True)['refresh']


def test_manager_update_setting_is_persistent_explicit_opt_in(app):
    ctx, _, _, _ = app
    assert ok(ctx, 'app.workshop.settings.get')['auto_update'] is False
    assert rpc(ctx, 'app.workshop.settings.set', auto_update='yes')['error']['code'] == 'INVALID_REQUEST'
    assert ok(ctx, 'app.workshop.settings.set', auto_update=True)['auto_update'] is True
    assert ctx.preferences.read_config()['workshop'] == {'auto_update': True}
    assert ok(ctx, 'app.workshop.settings.set', auto_update=False)['auto_update'] is False


def test_failed_service_quiesce_is_retried_instead_of_marked_closed(app, monkeypatch):
    ctx, _, _, _ = app
    attempts, closed = [], []
    original_policy, original_workshop = ctx.workshop_updates.close, ctx.workshop.close
    def policy_close():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError('owned delayed automatic check')
        original_policy()
    def workshop_close():
        closed.append(True)
        original_workshop()
    monkeypatch.setattr(ctx.workshop_updates, 'close', policy_close)
    monkeypatch.setattr(ctx.workshop, 'close', workshop_close)
    with pytest.raises(RuntimeError, match='owned delayed'):
        ctx.close(keep_store=True)
    assert not ctx._services_closed and not closed
    ctx.close(keep_store=True)
    assert ctx._services_closed and len(attempts) == 2 and len(closed) == 1
