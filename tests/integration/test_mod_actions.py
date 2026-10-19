"""PS-03-07 synthetic: actual closed bridge, library and executor file effects."""
import hashlib
from datetime import datetime, timezone
import shutil
import threading
import time
import zipfile
from uuid import uuid4

import pytest
import win32con
import win32file

from backend.deployments.lease import InstallationLease
from backend.contracts import Component, Relation
from tests.integration.harness.transaction_recovery import TransactionFixture, finish, request, tree


@pytest.fixture
def app(tmp_path_factory):
    fixture = TransactionFixture(tmp_path_factory.mktemp('m'))
    fixture.context.confirm_plan = lambda _: pytest.fail('Ordinary action must not request plan approval')
    yield fixture
    fixture.close()


def action(app, release, verb, key=None):
    return app.call('mod.action', dict(installation_id=app.installation,
        release_id=release['release_id'], manifest_hash=release['manifest_hash'],
        action=verb, idempotency_key=key or str(uuid4())))


def state(app, release):
    result = app.call('mod.list', {'installation_id':app.installation})
    assert 'error_code' not in result, result
    return next(item for item in result['items'] if item['release_id'] == release['release_id'])['state']


def success(result, expected):
    assert result.get('completed') is True and result['state'] == expected, result


def test_install_disable_enable_uninstall_actual_roots_and_reopen(app):
    from tests.integration.test_content_catalog import compile_fixture
    code = compile_fixture(app.root/'code', 'DirectModCode', melon=False).read_bytes()
    plugin = compile_fixture(app.root/'plugin', 'DirectModPlugin', melon=False).read_bytes()
    release = app.publish({'Mods/demo.dll':code, 'Plugins/plugin.dll':plugin,
        'UserLibs/assets/icon.png':b'asset'})
    before = tree(app.game)
    assert state(app, release) == 'not_installed'
    for verb, expected in [('install','enabled'),('disable','disabled'),('enable','enabled'),('uninstall','not_installed')]:
        success(action(app, release, verb), expected)
        for path in ('Mods/demo.dll','Plugins/plugin.dll','UserLibs/assets/icon.png'):
            assert (app.game/path).is_file() == (expected == 'enabled')
        app.reopen()
        app.context.confirm_plan = lambda _: pytest.fail('Unexpected approval')
        assert state(app, release) == expected
        assert (app.game/'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'
        assert (app.game/'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'
    assert len(app.call('package.list')['releases']) == 1
    assert app.call('profile.list', {'installation_id':app.installation})['profiles'] == []
    for path, digest in before.items():
        assert tree(app.game)[path] == digest


def test_other_mod_and_shared_identical_file_preserved(app):
    first = app.publish({'Mods/a.png':b'a', 'UserLibs/shared.png':b'shared'}, 'First')
    second = app.publish({'Plugins/b.png':b'b', 'UserLibs/shared.png':b'shared'}, 'Second')
    success(action(app, first, 'install'), 'enabled')
    success(action(app, second, 'install'), 'enabled')
    success(action(app, first, 'disable'), 'disabled')
    assert not (app.game/'Mods/a.png').exists()
    assert (app.game/'UserLibs/shared.png').read_bytes() == b'shared'
    assert state(app, second) == 'enabled'
    success(action(app, first, 'uninstall'), 'not_installed')
    app.reopen()
    assert state(app, first) == 'not_installed' and state(app, second) == 'enabled'
    success(action(app, second, 'uninstall'), 'not_installed')
    assert not (app.game/'UserLibs/shared.png').exists()


def test_repeated_install_is_noop_and_replay_cannot_reenable_later_disabled_mod(app):
    release = app.publish({'Mods/a.png':b'a'})
    key = str(uuid4())
    success(action(app, release, 'install', key), 'enabled')
    success(action(app, release, 'install'), 'enabled')
    success(action(app, release, 'disable'), 'disabled')
    result = action(app, release, 'install', key)
    assert result['idempotent']
    assert result['state'] == 'disabled'
    assert state(app, release) == 'disabled' and not (app.game/'Mods/a.png').exists()


def test_action_identity_cannot_be_reused_for_different_mod(app):
    first = app.publish({'Mods/a.png':b'a'})
    second = app.publish({'Mods/b.png':b'b'})
    key = str(uuid4())
    success(action(app, first, 'install', key), 'enabled')
    assert action(app, second, 'install', key)['error_code'] == 'PLAN_IDEMPOTENCY_CONFLICT'
    assert not (app.game/'Mods/b.png').exists()


@pytest.mark.parametrize('verb', ['enable', 'disable'])
def test_uninstalled_mod_must_use_install(app, verb):
    release = app.publish({'Mods/a.png':b'a'})
    assert action(app, release, verb)['error_code'] == 'MOD_NOT_INSTALLED'
    assert state(app, release) == 'not_installed'


def test_unknown_occupant_preserved_error_and_retry(app):
    release = app.publish({'Mods/a.png':b'a'})
    target = app.game/'Mods/a.png'
    target.write_bytes(b'unknown user file')
    result = action(app, release, 'install')
    assert result['error_code'] == 'PLAN_UNMANAGED_CONFLICT' and result['message']
    assert target.read_bytes() == b'unknown user file' and state(app, release) == 'not_installed'
    target.rename(app.game/'Mods/user-preserved.png')
    success(action(app, release, 'install'), 'enabled')
    assert (app.game/'Mods/user-preserved.png').read_bytes() == b'unknown user file'


def test_external_edit_is_not_deleted_or_reported_uninstalled(app):
    release = app.publish({'Mods/a.png':b'a'})
    success(action(app, release, 'install'), 'enabled')
    target = app.game/'Mods/a.png'
    target.write_bytes(b'user edit')
    assert state(app, release) == 'drifted'
    assert action(app, release, 'uninstall')['error_code'] == 'PLAN_MANAGED_DRIFT'
    assert target.read_bytes() == b'user edit' and state(app, release) == 'drifted'


def test_conflicting_mod_content_cannot_replace_other_mod(app):
    first = app.publish({'Mods/a.png':b'a'})
    second = app.publish({'Mods/a.png':b'b'})
    success(action(app, first, 'install'), 'enabled')
    assert action(app, second, 'install')['error_code'] == 'PLAN_TARGET_CONFLICT'
    assert (app.game/'Mods/a.png').read_bytes() == b'a'


def test_real_installation_lease_reports_busy_and_retry(app):
    release = app.publish({'Mods/a.png':b'a'})
    identity = app.context.store.get('Installation', app.installation).file_identity
    with InstallationLease(identity):
        result = action(app, release, 'install')
    assert result['error_code'] == 'GUARD_LEASE_BUSY' and result['message']
    success(action(app, release, 'install'), 'enabled')


def test_native_readonly_file_error_retains_enabled_state_and_retry(app):
    release = app.publish({'Mods/a.png':b'a'})
    success(action(app, release, 'install'), 'enabled')
    target = app.game/'Mods/a.png'
    win32file.SetFileAttributes(str(target), win32con.FILE_ATTRIBUTE_READONLY)
    try:
        result = action(app, release, 'disable')
        assert result.get('error_code') and not result.get('completed'), result
        assert target.read_bytes() == b'a'
    finally:
        win32file.SetFileAttributes(str(target), win32con.FILE_ATTRIBUTE_NORMAL)
    app.reopen()
    assert state(app, release) == 'enabled'
    success(action(app, release, 'disable'), 'disabled')


def test_native_delete_sharing_error_retains_state(app):
    release = app.publish({'Mods/a.png':b'a'})
    success(action(app, release, 'install'), 'enabled')
    target = app.game/'Mods/a.png'
    handle = win32file.CreateFile(str(target), win32con.GENERIC_READ,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None, win32con.OPEN_EXISTING, 0, None)
    try:
        result = action(app, release, 'uninstall')
        assert result.get('error_code') and not result.get('completed'), result
        assert target.read_bytes() == b'a'
    finally:
        handle.Close()
    app.reopen()
    assert state(app, release) == 'enabled'
    success(action(app, release, 'uninstall'), 'not_installed')


def test_capacity_fault_is_explicit_and_never_marks_installed(app, monkeypatch):
    import errno
    release = app.publish({'Mods/a.png':b'a'})
    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'synthetic disk full')
    monkeypatch.setattr('backend.deployments.ordinary.copy_verified', no_space)
    result = action(app, release, 'install')
    assert result['error_code'] == 'PLAN_SPACE_INSUFFICIENT'
    assert not (app.game/'Mods/a.png').exists() and state(app, release) == 'not_installed'


@pytest.mark.parametrize('extra', ['path', 'approved', 'profile_id', 'shell'])
def test_closed_action_rejects_arbitrary_path_or_approval(app, extra):
    release = app.publish({'Mods/a.png':b'a'})
    payload = dict(installation_id=app.installation, release_id=release['release_id'],
        manifest_hash=release['manifest_hash'], action='install', idempotency_key=str(uuid4()))
    payload[extra] = 'not allowed'
    result = app.context.api.dispatch(request('mod.action', payload))
    assert not result['ok'] and result['error']['code'] == 'INVALID_REQUEST'
    assert not (app.game/'Mods/a.png').exists()


def test_manifest_mismatch_and_corrupt_library_are_actual_errors(app):
    release = app.publish({'Mods/a.png':b'a'})
    mismatched = dict(release, manifest_hash='f'*64)
    assert action(app, mismatched, 'install').get('error_code')
    digest = hashlib.sha256(b'a').hexdigest()
    (app.data/'library'/digest[:2]/digest).write_bytes(b'tampered')
    assert action(app, release, 'install').get('error_code')
    assert state(app, release) == 'unknown' and not (app.game/'Mods/a.png').exists()


def test_advanced_profile_mod_is_preserved_by_direct_action(app):
    advanced = app.publish({'Plugins/advanced.png':b'advanced'})
    saved = app.profile([advanced])
    plan, approval = app.approved(saved)
    result = app.call('plan.apply', approval)
    assert result.get('deployed'), result
    release = app.publish({'Mods/direct.png':b'direct'})
    success(action(app, release, 'install'), 'enabled')
    success(action(app, release, 'uninstall'), 'not_installed')
    assert (app.game/'Plugins/advanced.png').read_bytes() == b'advanced'
    assert state(app, advanced) == 'enabled'


@pytest.mark.parametrize('name', ['../escape.dll', '/absolute.dll', 'Mods/../../escape.dll'])
def test_actual_zip_traversal_does_not_publish_or_escape(app, name):
    archive = app.root/'malicious.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr(name, b'invalid')
    app.context.choose_import_source = lambda _: str(archive)
    grant = app.call('package.choose', {'kind':'zip'})
    preview = app.call('package.import_preview', {'source_handle':grant['source_handle']})
    assert preview.get('error_code') or preview.get('can_commit') is False, preview
    assert app.call('package.list')['releases'] == []
    assert not (app.root/'escape.dll').exists() and not (app.game/'escape.dll').exists()


def test_missing_scan_root_and_nested_directory_created_by_install(app):
    (app.game/'Plugins').rmdir()
    release = app.publish({'Plugins/new/deep/content.png':b'resource'})
    success(action(app, release, 'install'), 'enabled')
    assert (app.game/'Plugins/new/deep/content.png').read_bytes() == b'resource'
    success(action(app, release, 'uninstall'), 'not_installed')
    assert not (app.game/'Plugins/new/deep/content.png').exists()


def test_commit_recovery_publishes_membership_with_actual_files(app):
    release = app.publish({'Mods/recovery.png':b'resource'})
    fired = []
    def fault(point, details):
        if point == 'db.before_commit' and not fired:
            fired.append(point)
            raise OSError('owned commit checkpoint fault')
    app.context.deployments.fault = fault
    result = action(app, release, 'install')
    success(result, 'enabled')
    assert fired
    app.reopen()
    assert state(app, release) == 'enabled' and (app.game/'Mods/recovery.png').read_bytes() == b'resource'


def test_compatibility_dependency_does_not_add_approval_prerequisite(app):
    release = app.publish({'Mods/content.png':b'content'})
    store = app.context.store
    digest = store.connection.execute('SELECT source_hash FROM file_entries WHERE release_id=?',
        (release['release_id'],)).fetchone()[0]
    component = store.put(Component(component_id=uuid4(), release_id=release['release_id'], artifact_hash=digest,
        file_name='content.png', assembly_name=None, assembly_version=None, melon_info=None,
        roles={'content'}, content_ids=[]))
    store.put(Relation(relation_id=uuid4(), from_id=component.component_id, capability='component:'+str(uuid4()),
        kind='hard_dependency', requirement_raw=None, source={'kind':'user',
            'reference':'synthetic-dependency', 'observed_at':datetime.now(timezone.utc)}, confidence='confirmed',
        fingerprint_scope=store.get('Installation', app.installation).fingerprint_id))
    success(action(app, release, 'install'), 'enabled')
    assert (app.game/'Mods/content.png').read_bytes() == b'content'


def test_cancel_before_target_writes_does_not_publish_membership(app):
    release = app.publish({'Mods/cancel.png':b'content'})
    ready, resume = threading.Event(), threading.Event()
    def fault(point, details):
        if point == 'ordinary.prepared':
            ready.set()
            assert resume.wait(15)
    app.context.deployments.fault = fault
    payload = dict(installation_id=app.installation, release_id=release['release_id'],
        manifest_hash=release['manifest_hash'], action='install', idempotency_key=str(uuid4()))
    started = app.context.api.dispatch(request('mod.action', payload))
    assert started['ok']
    try:
        assert ready.wait(15)
        cancelled = app.context.api.dispatch(request('task.cancel', {'task_id':started['data']['task_id']}))
        assert cancelled['ok'] and cancelled['data']['state'] == 'cancelling'
    finally:
        resume.set()
    assert finish(app.context.api, started['data'])['state'] == 'cancelled'
    app.reopen()
    assert state(app, release) == 'not_installed' and not (app.game/'Mods/cancel.png').exists()


def test_large_ambient_tree_does_not_limit_listing_or_ordinary_actions(app):
    """A small Mod must not inventory/hash thousands of unrelated game assets."""
    assets = app.game/'Mods/ExistingMod/assets'
    assets.mkdir(parents=True)
    for index in range(2000):
        (assets/f'{index:04d}.png').write_bytes(f'ambient-{index}'.encode())
    # Even a busy unrelated asset is irrelevant to listing or this Mod's files.
    busy = assets/'0000.png'
    handle = win32file.CreateFile(str(busy), win32con.GENERIC_READ, 0, None,
        win32con.OPEN_EXISTING, 0, None)
    try:
        assert app.call('mod.list', {'installation_id':app.installation})['items'] == []
        release = app.publish({'Mods/DirectMod/content.png':b'direct content'})
        assert state(app, release) == 'not_installed'
        for verb, expected in [('install','enabled'),('disable','disabled'),('enable','enabled'),('uninstall','not_installed')]:
            success(action(app, release, verb), expected)
            app.reopen()
            assert state(app, release) == expected
    finally:
        handle.Close()
    assert len(list(assets.iterdir())) == 2000
    for index in range(2000):
        assert (assets/f'{index:04d}.png').read_bytes() == f'ambient-{index}'.encode()


def test_large_ambient_tree_does_not_block_executor_recovery(app):
    assets = app.game/'Mods/unrelated'
    assets.mkdir()
    for index in range(2000):
        (assets/f'{index:04d}.png').write_bytes(b'ambient')
    release = app.publish({'Mods/recovered.png':b'recovery'})
    fired = []
    def fault(point, details):
        if point == 'db.before_commit' and not fired:
            fired.append(point)
            raise OSError('scoped recovery checkpoint')
    app.context.deployments.fault = fault
    success(action(app, release, 'install'), 'enabled')
    assert fired
    app.reopen()
    assert state(app, release) == 'enabled'
    assert (app.game/'Mods/recovered.png').read_bytes() == b'recovery'
    assert len(list(assets.iterdir())) == 2000


def test_managed_file_count_is_independent_from_single_action_limit(app):
    def capacity_action(release, verb):
        started = app.context.api.dispatch(request('mod.action', dict(installation_id=app.installation,
            release_id=release['release_id'], manifest_hash=release['manifest_hash'],
            action=verb, idempotency_key=str(uuid4()))))
        assert started['ok'], started
        observed = time.monotonic()
        terminal = finish(app.context.api, started['data'], timeout=120)
        print(f'capacity {verb}: {time.monotonic()-observed:.3f}s; {terminal["state"]}', flush=True)
        assert terminal['state'] == 'succeeded', terminal
        return terminal['data']
    paths = [f'Mods/First/{index:03d}.png' for index in range(128)]
    for relative in paths:
        target = app.game/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'first')
    snapshot = app.call('package.adopt_preview', {'installation_id':app.installation})
    adopted = app.call('package.adopt', dict(snapshot_id=snapshot['snapshot_id'],
        manifest_hash=snapshot['manifest_hash'], paths=paths, display_name='Existing resource Mod', version_raw='1.0'))
    first = adopted['release']
    second = app.publish({'Mods/Second/content.png':b'second'})
    assert state(app, first) == 'enabled'
    success(capacity_action(second, 'install'), 'enabled')
    assert len(list((app.game/'Mods/First').iterdir())) == 128
    assert state(app, first) == 'enabled' and state(app, second) == 'enabled'
    success(capacity_action(second, 'disable'), 'disabled')
    assert state(app, first) == 'enabled'
    success(capacity_action(second, 'enable'), 'enabled')
    app.reopen()
    assert state(app, first) == 'enabled' and state(app, second) == 'enabled'
    assert (app.game/'Mods/Second/content.png').read_bytes() == b'second'
    for path in (app.game/'Mods/First').iterdir():
        assert path.read_bytes() == b'first'


def test_maximum_single_package_install_uses_actual_executor(app):
    release = app.publish({f'Mods/Bulk/{index:03d}.png':b'bulk' for index in range(128)})
    started = app.context.api.dispatch(request('mod.action', dict(installation_id=app.installation,
        release_id=release['release_id'], manifest_hash=release['manifest_hash'],
        action='install', idempotency_key=str(uuid4()))))
    assert started['ok'], started
    observed = time.monotonic()
    terminal = finish(app.context.api, started['data'], timeout=120)
    print(f'128-file actual install: {time.monotonic()-observed:.3f}s; {terminal["state"]}', flush=True)
    assert terminal['state'] == 'succeeded', terminal
    success(terminal['data'], 'enabled')
    assert len(list((app.game/'Mods/Bulk').iterdir())) == 128
    assert all(path.read_bytes() == b'bulk' for path in (app.game/'Mods/Bulk').iterdir())
    app.reopen()
    assert state(app, release) == 'enabled'
