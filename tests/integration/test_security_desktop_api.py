"""Closed safety bridge and real launch-policy integration on isolated roots."""
import threading
from uuid import uuid4

import pytest

from tests.integration.test_native_desktop_api import app, ok, rpc


def test_security_scan_uses_host_installation_and_explicit_options(app, monkeypatch):
    ctx, identity, root, data = app
    calls = []
    def scan(installation, **arguments):
        calls.append((installation, arguments))
        return dict(scanning=True, items=[])
    monkeypatch.setattr(ctx.mod_security, 'scan', scan)
    assert ok(ctx, 'security.scan', installation_id=identity, ids=['test.core'])['scanning']
    assert calls[0][0]['canonical_root'] == str(root)
    assert calls[0][0]['persistent_root'] == str(data)
    assert calls[0][1] == dict(ids=['test.core'], antivirus=False)
    for changes in ({'ids': []}, {'ids': [123]}, {'antivirus': 'true'}, {'antivirus': 1},
                    {'root': str(root)}, {'canonical_root': str(root)}, {'ids': ['x'] * 1001}):
        payload = dict(installation_id=identity, ids=['test.core'])
        payload.update(changes)
        result = rpc(ctx, 'security.scan', **payload)
        assert not result['ok'] and result['error']['code'] == 'INVALID_REQUEST'
    assert len(calls) == 1
    result = rpc(ctx, 'security.scan', installation_id=str(uuid4()))
    assert result['error']['code'] == 'INSTALLATION_NOT_FOUND'
    assert len(calls) == 1


@pytest.mark.parametrize('command,payload', [
    ('security.dispose.preview', dict(id='test.core', mode='delete')),
    ('security.dispose', dict(preview_id='../state')),
    ('security.dispose', dict(preview_id='0' * 32, confirmed=True)),
    ('security.restore', dict(record_id='../state')),
    ('security.restore', dict(record_id='0' * 32, enable=True)),
])
def test_security_write_surface_is_tokens_not_paths_or_approval(app, command, payload):
    ctx, identity, _, _ = app
    result = rpc(ctx, command, installation_id=identity, **payload)
    assert result['error']['code'] == 'INVALID_REQUEST'


def test_unscanned_enabled_mod_does_not_require_analysis_at_launch(app, monkeypatch):
    from types import SimpleNamespace
    ctx, identity, _, data = app
    snap = ok(ctx, 'native.snapshot', installation_id=identity)
    from backend.app import native_api
    ctx.native_mods.save(native_api.installation(ctx, identity), snap['revision'], ['test.core'])
    calls = []
    monkeypatch.setattr(ctx.workshop, 'release', lambda value: calls.append(('steam_released', value['persistent_root'])))
    monkeypatch.setattr(ctx.mod_security.worker, 'inspect_security', lambda *a, **k: pytest.fail('ordinary launch scanned'))
    session = SimpleNamespace(installation=SimpleNamespace(installation_id=identity), check=lambda: None)
    ctx._prepare_launch(session)
    assert calls == [('steam_released', str(data))]
    status = ok(ctx, 'security.status', installation_id=identity)
    assert status['blocking_reasons'] == []
    assert status['items'][0]['state'] == 'unscanned'


def test_security_work_blocks_migration_without_switching_storage(app, monkeypatch, tmp_path):
    ctx, _, _, _ = app
    monkeypatch.setattr(ctx.mod_security, 'is_busy', lambda: True)
    destination = tmp_path / 'destination'
    result = rpc(ctx, 'app.storage.start', target=str(destination))
    assert result['error']['code'] == 'STORAGE_OPERATIONS_RUNNING'
    assert not ctx.storage.blocked and not destination.exists()


def test_security_read_does_not_hold_desktop_mutex_and_migration_drains(app, monkeypatch, tmp_path):
    ctx, identity, _, _ = app
    entered, release = threading.Event(), threading.Event()
    def detail(*args):
        entered.set()
        assert release.wait(5)
        return {'report': None}
    monkeypatch.setattr(ctx.mod_security, 'detail', detail)
    results = []
    thread = threading.Thread(target=lambda: results.append(rpc(ctx, 'security.detail', installation_id=identity, id='test.core')))
    thread.start()
    try:
        assert entered.wait(5)
        assert ok(ctx, 'app.storage.status')['phase'] == 'idle'
        denied = rpc(ctx, 'app.storage.start', target=str(tmp_path / 'destination'))
        assert denied['error']['code'] == 'STORAGE_OPERATIONS_RUNNING'
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and results[0]['ok']


def test_explicit_quarantine_still_blocks_save_until_restored(app):
    ctx, identity, _, _ = app
    preview = ok(ctx, 'security.dispose.preview', installation_id=identity, id='test.core', mode='quarantine')
    disposed = ok(ctx, 'security.dispose', installation_id=identity, preview_id=preview['preview_id'])
    snap = ok(ctx, 'native.snapshot', installation_id=identity)
    rejected = rpc(ctx, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=['test.core'])
    assert rejected['error']['code'] == 'SECURITY_QUARANTINED'
    ok(ctx, 'security.restore', installation_id=identity, record_id=disposed['record']['id'])
    snap = ok(ctx, 'native.snapshot', installation_id=identity)
    assert ok(ctx, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=['test.core'])['saved']
