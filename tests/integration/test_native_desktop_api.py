"""Formal desktop bridge against isolated game/user directories and real services."""
import json
from pathlib import Path
import shutil
import subprocess
import threading
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext, page_resource_allowed
from backend.app.paths import resolve_paths
from backend.app import native_api
from backend.deployments.paths import GuardRejected
from backend.native_mods.state import ORDER_TYPE
from tests.integration.test_deployment_guards import register
from tests.integration.harness.launch_probe import build_probe
from tests.native_mods.test_native_service import package, cli_fixture


def rpc(ctx, command, **payload):
    return ctx.api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))


def ok(ctx, command, **payload):
    result = rpc(ctx, command, **payload)
    assert result['ok'], result
    return result['data']


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path / 'game'
    root.mkdir()
    (root / 'Probably Stolen.exe').write_bytes(cli_fixture())
    managed = root / 'Probably Stolen_Data/Managed'
    managed.mkdir(parents=True)
    (managed / 'Assembly-CSharp.dll').write_bytes(cli_fixture())
    (root / 'MonoBleedingEdge').mkdir()
    data = tmp_path / 'user'
    data.mkdir()
    state = data / 'SaveFile.es3'
    state.write_text(json.dumps({'modOrder': {'__type': ORDER_TYPE, 'value': []},
        'unrelated': {'__type': 'string', 'value': 'preserve gameplay preferences'}}))
    context = DesktopContext(resolve_paths(str(tmp_path / 'manager')))
    context.initialize()
    # Only explicit manual checks use this fixture parser. Ordinary saving must
    # never call it; real parser evidence is covered by the separate cycle.
    from tests.mod_security.test_service import Worker
    worker = Worker()
    context.mod_security.worker = worker
    identity = register(context, root)
    resolve = native_api.installation
    monkeypatch.setattr(native_api, 'installation', lambda ctx, ident: {
        **resolve(ctx, ident), 'persistent_root': str(data), 'workshop_root': str(tmp_path / 'workshop')})
    context.api = DesktopApi(context)
    package(root / 'Mods', 'test.core', caps=('files',))
    package(root / 'Mods', 'test.addon', dependencies=('test.core',))
    try:
        yield context, identity, root, data
    finally:
        context.close()


def test_native_bridge_save_restore_and_state_independence(app):
    ctx, identity, root, data = app
    snap = ok(ctx, 'native.snapshot', installation_id=identity)
    assert len(snap['mods']) == 2 and snap['enabled_ids'] == []
    assert snap['identity']['runtime'] == 'mono'
    saved = ok(ctx, 'native.save', installation_id=identity, expected_revision=snap['revision'],
               enabled_ids=['test.core', 'test.addon'])
    assert saved['saved'] and saved['enabled_ids'] == ['test.core', 'test.addon']
    assert saved['identity']['runtime'] == 'mono'
    assert saved['runtime']['writable'] is True
    assert all(m['approval']['state'] == 'approved' and m['run']['state'] != 'running' for m in saved['mods'])
    assert json.loads((data/'SaveFile.es3').read_text())['unrelated']['value'] == 'preserve gameplay preferences'
    assert (root/'Mods/test.core/Main.dll').is_file()
    records = ok(ctx, 'native.recovery.list', installation_id=identity)['items']
    assert records[0]['id'] == saved['recovery_id']
    restored = ok(ctx, 'native.recovery.restore', installation_id=identity, id=saved['recovery_id'],
                  expected_revision=saved['revision'])
    assert restored['enabled_ids'] == []
    assert restored['identity']['runtime'] == 'mono'
    assert restored['runtime']['writable'] is True


def test_hex_profile_id_roundtrip_export_and_import(app, tmp_path):
    ctx, identity, _, _ = app
    result = ok(ctx, 'native.profile.create', installation_id=identity, name='测试方案', enabled_ids=['test.core'])
    profile = result['profile']
    assert len(profile['id']) == 32
    preview = ok(ctx, 'native.profile.preview', installation_id=identity, profile_id=profile['id'])
    assert preview['enabled_ids'] == ['test.core']
    updated = ok(ctx, 'native.profile.update', installation_id=identity, profile_id=profile['id'],
                 name='更新方案', enabled_ids=['test.core', 'test.addon'])
    assert updated['profile']['name'] == '更新方案'
    exported = ok(ctx, 'native.profile.export', installation_id=identity, profile_id=profile['id'], kind='text')
    assert exported['manifest']['format'] == 'psmm.native-profile.v1'
    assert 'modTrust' not in exported['text'] and 'canonical_root' not in exported['text']
    path = tmp_path/'profile.json'
    path.write_text(exported['text'], encoding='utf-8')
    ctx.choose_profile_source = lambda: str(path)
    imported = ok(ctx, 'native.profile.import.choose', installation_id=identity)
    assert not imported['legacy']
    result = ok(ctx, 'native.profile.import.commit', installation_id=identity,
        source_handle=imported['source_handle'], name='导入方案', enabled_ids=imported['enabled_ids'])
    assert result['profile']['enabled_ids'] == ['test.core', 'test.addon']
    ok(ctx, 'native.profile.delete', installation_id=identity, profile_id=profile['id'])


def test_native_config_preserves_unknown_values_and_uses_recovery(app):
    ctx, identity, _, data = app
    config = data/'ModConfigs/test.core.json'
    config.parent.mkdir()
    config.write_text(json.dumps(dict(custom='retain', entries=[
        dict(key='odd', value='00.20', hidden=True), dict(key='secretish', value='keep')]), ensure_ascii=False))
    read = ok(ctx, 'native.config.read', installation_id=identity, id='test.core')
    assert read['entries'][0]['type'] == 'unknown'
    saved = ok(ctx, 'native.config.save', installation_id=identity, id='test.core',
        expected_revision=read['revision'], entries=[dict(key='odd', value='0.25')])
    assert saved['recovery_id']
    after = json.loads(config.read_text())
    assert after['custom'] == 'retain' and after['entries'][0]['hidden'] is True
    assert after['entries'][1]['value'] == 'keep'


def test_profile_import_keeps_sender_sources_and_rules(app, tmp_path):
    ctx, identity, _, _ = app
    document = dict(format='psmm.native-profile.v1', name='跨机器方案',
        enabled_ids=['test.core', 'not.installed'],
        rules=[dict(before='test.core', after='not.installed')],
        sources=[dict(id='test.core', source='workshop', workshop_id='123456', version='old.version'),
                 dict(id='not.installed', source='workshop', workshop_id='987654', version='1.0')])
    path = tmp_path/'portable.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    ctx.choose_profile_source = lambda: str(path)
    preview = ok(ctx, 'native.profile.import.choose', installation_id=identity)
    result = ok(ctx, 'native.profile.import.commit', installation_id=identity,
        source_handle=preview['source_handle'], name=document['name'], enabled_ids=document['enabled_ids'])
    profile = result['profile']
    assert profile['rules'] == document['rules']
    assert profile['sources'] == document['sources']
    exported = ok(ctx, 'native.profile.export', installation_id=identity, profile_id=profile['id'], kind='text')
    assert exported['manifest']['rules'] == document['rules']
    assert exported['manifest']['sources'] == document['sources']
    # Import creates a private plan only, never changes the global rule file.
    assert ok(ctx, 'native.rules.read', installation_id=identity)['rules'] == []


def test_validate_uses_saved_rules_when_omitted(app):
    ctx, identity, _, _ = app
    rules = ok(ctx, 'native.rules.read', installation_id=identity)
    ok(ctx, 'native.rules.save', installation_id=identity, expected_revision=rules['revision'],
       rules=[dict(before='test.addon', after='test.core')])
    checked = ok(ctx, 'native.validate', installation_id=identity, enabled_ids=['test.core', 'test.addon'])
    assert checked['blocking']  # conflicts with the addon's real fixture dependency
    explicit = ok(ctx, 'native.validate', installation_id=identity,
                  enabled_ids=['test.core', 'test.addon'], rules=[])
    assert not explicit['blocking']


def test_portable_export_cannot_write_game_persistent_data(app, tmp_path, monkeypatch):
    ctx, identity, _, _ = app
    profile = ok(ctx, 'native.profile.create', installation_id=identity,
                 name='export boundary', enabled_ids=['test.core'])['profile']
    monkeypatch.setenv('USERPROFILE', str(tmp_path/'profile-home'))
    target = tmp_path/'profile-home/AppData/LocalLow/Questing Goose Studio/Probably Stolen/ModConfigs/new.json'
    target.parent.mkdir(parents=True)
    ctx.choose_profile_destination = lambda: str(target)
    result = rpc(ctx, 'native.profile.export', installation_id=identity, profile_id=profile['id'], kind='file')
    assert not result['ok'] and result['error']['code'] == 'PROFILE_EXPORT_REFUSED'
    assert not target.exists()


def test_import_only_picker_authorized_and_hex_preview_survives(app, tmp_path):
    ctx, identity, root, _ = app
    source = package(tmp_path/'selected', 'test.imported')
    ctx.choose_import_source = lambda kind: str(source)
    grant = ok(ctx, 'native.import.choose', installation_id=identity, kind='directory')
    preview = ok(ctx, 'native.import.preview', installation_id=identity, source_handle=grant['source_handle'])
    assert not preview['blocking'] and len(preview['preview_id']) == 32
    result = ok(ctx, 'native.import.commit', installation_id=identity, preview_id=preview['preview_id'],
                expected_revision=preview['revision'])
    assert (root/'Mods/test.imported/manifest.xml').is_file() and result['recovery_id']
    assert 'test.imported' not in result['enabled_ids']
    refused = rpc(ctx, 'native.import.preview', installation_id=identity, path=str(source))
    assert not refused['ok'] and refused['error']['code'] == 'INVALID_REQUEST'


def test_legacy_commands_are_retired_not_aliased_to_file_deletion(app):
    ctx, _, _, _ = app
    for command in ('loader.install', 'loader.repair', 'nexus.account', 'nexus.protocol.register',
                    'mod.action', 'mod.workspace.save', 'plan.apply', 'profile.commit'):
        result = rpc(ctx, command)
        assert not result['ok'] and result['error']['code'] == 'UNSUPPORTED_COMMAND', result


def test_unknown_or_running_game_fails_closed(app, monkeypatch):
    ctx, identity, _, data = app
    snap = ok(ctx, 'native.snapshot', installation_id=identity)
    before = (data/'SaveFile.es3').read_bytes()
    from backend.deployments.process_guard import ProcessGuard
    for status in ('running', 'unknown'):
        monkeypatch.setattr(ProcessGuard, 'inspect', lambda self: dict(status=status, pids=[], unknown_pids=[]))
        fresh = ok(ctx, 'native.snapshot', installation_id=identity)
        assert not fresh['runtime']['writable']
        result = rpc(ctx, 'native.save', installation_id=identity,
                     expected_revision=snap['revision'], enabled_ids=['test.core'])
        assert not result['ok'] and result['error']['code'] == 'GUARD_PROCESS_' + status.upper()
        assert (data/'SaveFile.es3').read_bytes() == before


@pytest.mark.parametrize('state,blockers,owns,ready', [
    ('clear', ['GUARD_LEASE_BUSY'], True, True),
    ('clear', ['GUARD_LEASE_BUSY'], False, False),
    ('clear', ['GUARD_LEASE_BUSY', 'GUARD_RECOVERY_REQUIRED'], True, False),
    ('running', ['GUARD_LEASE_BUSY'], True, False),
    ('unknown', ['GUARD_LEASE_BUSY'], True, False),
])
def test_foreground_readiness_only_preempts_proven_read_only_probe(app, monkeypatch, state, blockers, owns, ready):
    ctx, identity, _, _ = app
    monkeypatch.setattr(ctx.deployment_guards, 'status', lambda _: dict(
        process_status=state, blockers=list(blockers), ready=False))
    monkeypatch.setattr(ctx.workshop, 'probe_owns_lease', lambda _: owns)
    selected = native_api.installation(ctx, identity)
    assert native_api.process_status(ctx, selected)['writable'] is False  # SDK never gets the exemption
    snap = ok(ctx, 'native.snapshot', installation_id=identity)
    assert snap['runtime']['writable'] is ready
    if ready:
        assert snap['runtime']['busy_owner'] == 'workshop_probe' and not snap['runtime']['blockers']
    else:
        assert snap['runtime']['blockers'] == blockers


def test_manual_scan_does_not_block_normal_save_or_read_progress(app):
    ctx, identity, _, _ = app
    snap = ok(ctx, 'native.snapshot', installation_id=identity)
    worker = ctx.mod_security.worker
    worker.release = threading.Event()
    ok(ctx, 'security.scan', installation_id=identity, ids=['test.core'])
    try:
        assert worker.entered.wait(5)
        progress = ok(ctx, 'security.activity', installation_id=identity)
        assert progress['scanning'] and progress['operation']['current_id'] == 'test.core'
        saved = ok(ctx, 'native.save', installation_id=identity,
            expected_revision=snap['revision'], enabled_ids=['test.core'])
        assert saved['saved'] and saved['mods'][0]['approval']['detected'] is None
        assert worker.calls == ['Main.dll']  # Only the explicitly started scan.
        assert rpc(ctx, 'app.storage.start', target='unused')['error']['code'] == 'STORAGE_OPERATIONS_RUNNING'
    finally:
        worker.release.set()


def test_real_external_process_prevents_native_state_write(app, tmp_path):
    ctx, identity, root, data = app
    probe = build_probe(tmp_path/'probe')
    shutil.copyfile(probe, root/'Probably Stolen.exe')
    process = subprocess.Popen([str(root/'Probably Stolen.exe')], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        from tests.integration.test_launch_verification import wait_for
        wait_for(lambda: (root/'launch-probe.txt').is_file())
        snap = ok(ctx, 'native.snapshot', installation_id=identity)
        assert snap['runtime']['state'] == 'running' and not snap['runtime']['writable']
        before = (data/'SaveFile.es3').read_bytes()
        result = rpc(ctx, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=['test.core'])
        assert not result['ok'] and result['error']['code'] == 'GUARD_PROCESS_RUNNING'
        assert before == (data/'SaveFile.es3').read_bytes()
    finally:
        (root/'exit-request.txt').write_text('0')
        process.wait(timeout=10)


def test_workshop_failure_is_actionable_and_does_not_echo_service_text(app, monkeypatch):
    from backend.workshop import WorkshopError
    ctx, identity, _, _ = app
    def fail(*args, **kwargs):
        raise WorkshopError('WORKSHOP_NETWORK_ERROR', 'never echo arbitrary HTTP body', retryable=True)
    monkeypatch.setattr(ctx.workshop, 'browse', fail)
    result = rpc(ctx, 'workshop.browse', installation_id=identity)
    assert not result['ok'] and result['error']['code'] == 'WORKSHOP_NETWORK_ERROR'
    assert result['error']['retryable'] and 'HTTP body' not in result['error']['message']


def test_external_images_are_valve_only():
    assert page_resource_allowed('https://images.steamusercontent.com/a.jpg', True, 'GET')
    for url in ('https://images.nexusmods.com/a.jpg', 'file:///C:/private.png',
                'https://images.steamusercontent.com.evil.invalid/a.jpg',
                'https://user:pass@images.steamusercontent.com/a.jpg'):
        assert not page_resource_allowed(url, True, 'GET')
