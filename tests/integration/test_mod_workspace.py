"""Synthetic Mod-sized workspace through the actual deployment/recovery stack."""
from uuid import uuid4
from functools import wraps
import time
import subprocess
import sys

import pytest

from backend.deployments.mod_workspace import ModWorkspaceService
from backend.deployments.policy import PlanRejected
from tests.integration.harness.transaction_recovery import TransactionFixture


@pytest.fixture
def app(tmp_path):
    fixture = TransactionFixture(tmp_path)
    fixture.context.confirm_plan = lambda _: pytest.fail('Workspace must not ask for a plan approval')
    yield fixture
    fixture.close()


def workspace(app):
    return ModWorkspaceService(app.context.mods)


def view(app):
    return workspace(app).list(app.installation)


def save(app, ids, *, snapshot=None, key=None):
    return workspace(app).save(dict(installation_id=app.installation,
        snapshot_id=snapshot or view(app)['snapshot_id'], enabled_ids=ids,
        idempotency_key=key or str(uuid4())))


def write(app, name, content=b'synthetic content'):
    target = app.game / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def test_browse_groups_mods_and_does_not_read_bytes_or_assemblies(app, monkeypatch):
    release = app.publish({'Mods/Imported/content.png': b'imported'}, 'Imported')
    write(app, 'Mods/BrewingExpansion.dll')
    write(app, 'Mods/BrewingExpansion/assets/deep/bottle.png')
    write(app, 'Mods/BrewingExpansion/settings.json', b'{"owned":true}')
    write(app, 'Mods/FolderMod/nested/FolderMod.dll')
    write(app, 'Mods/FolderMod/assets/model.bundle')
    write(app, 'Plugins/Plugin.dll')
    write(app, 'Mods/item_catalog.json')
    write(app, 'Mods/item_mod_config.json')
    for index in range(140):
        write(app, f'Mods/UnrelatedAssets/{index}.png')
    from backend.storage.package_repository import PackageRepository
    monkeypatch.setattr(PackageRepository, 'verify', lambda *args, **kwargs: pytest.fail('Browsing must not verify library bytes'))
    monkeypatch.setattr(app.context.plans, '_scan', lambda *args, **kwargs: pytest.fail('Browsing must not hash game files'))
    result = view(app)
    assert {item['display_name'] for item in result['items']} == {'Imported', 'BrewingExpansion', 'FolderMod', 'Plugin'}
    assert len(result['enabled_ids']) == 3
    brewing = next(item for item in result['items'] if item['display_name'] == 'BrewingExpansion')
    assert brewing['file_count'] == 3 and brewing['source'] == 'external'
    detail = workspace(app).detail(app.installation, brewing['id'])
    assert {file['relative_path'] for file in detail['files']} == {
        'Mods/BrewingExpansion.dll', 'Mods/BrewingExpansion/assets/deep/bottle.png', 'Mods/BrewingExpansion/settings.json'}
    assert next(item for item in result['items'] if item['release_id'] == release['release_id'])['state'] == 'not_installed'
    assert result['order_policy'] == 'display_only'


def test_external_whole_mod_disable_enable_and_reopen_preserves_unrelated_files(app):
    payloads = {'Mods/BrewingExpansion.dll': b'dll',
        'Mods/BrewingExpansion/assets/deep/bottle.png': b'bottle',
        'Mods/BrewingExpansion/settings.json': b'{"enabled":true}'}
    for name, value in payloads.items():
        write(app, name, value)
    other = write(app, 'Mods/Other.dll', b'other')
    configuration = write(app, 'Mods/item_catalog.json', b'global configuration')
    before = view(app)
    target = next(item['id'] for item in before['items'] if item['display_name'] == 'BrewingExpansion')
    result = save(app, [identity for identity in before['enabled_ids'] if identity != target])
    assert result.get('completed'), result
    assert all(not (app.game/name).exists() for name in payloads)
    assert other.read_bytes() == b'other' and configuration.read_bytes() == b'global configuration'
    app.reopen()
    current = view(app)
    item = next(item for item in current['items'] if item['id'] == target)
    assert item['state'] == 'disabled' and item['source'] == 'local' and item['file_count'] == 3
    result = save(app, [target, *current['enabled_ids']])
    assert result.get('completed'), result
    for name, value in payloads.items():
        assert (app.game/name).read_bytes() == value
    app.reopen()
    assert view(app)['enabled_ids'][0] == target
    assert (app.game/'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'


def test_reorder_only_is_persisted_without_adopting_or_writing_external_mods(app):
    first = write(app, 'Mods/First.dll', b'first')
    second = write(app, 'Mods/Second.dll', b'second')
    initial = view(app)
    order = list(reversed(initial['enabled_ids']))
    result = save(app, order)
    assert result['completed'] and result['no_op']
    assert app.call('package.list')['releases'] == []
    app.reopen()
    assert view(app)['enabled_ids'] == order
    assert first.read_bytes() == b'first' and second.read_bytes() == b'second'


def test_stale_draft_does_not_capture_changed_external_file(app):
    target = write(app, 'Mods/External.dll', b'original')
    initial = view(app)
    target.write_bytes(b'modified')
    with pytest.raises(PlanRejected, match='MOD_WORKSPACE_STALE'):
        save(app, [], snapshot=initial['snapshot_id'])
    assert target.read_bytes() == b'modified'
    assert app.call('package.list')['releases'] == []


def test_managed_same_size_external_edit_is_preserved_on_save(app):
    release = app.publish({'Mods/Managed/content.png': b'original'})
    result = save(app, [release['release_id']])
    assert result['completed'], result
    target = app.game/'Mods/Managed/content.png'
    target.write_bytes(b'modified')
    with pytest.raises(PlanRejected, match='PLAN_MANAGED_DRIFT'):
        save(app, [])
    assert target.read_bytes() == b'modified'


def test_shared_files_retained_and_library_mods_saved_together(app):
    first = app.publish({'Mods/First/content.png': b'first', 'UserLibs/shared.png': b'shared'}, 'First')
    second = app.publish({'Mods/Second/content.png': b'second', 'UserLibs/shared.png': b'shared'}, 'Second')
    assert save(app, [second['release_id'], first['release_id']])['completed']
    result = save(app, [second['release_id']])
    assert result['completed'], result
    assert not (app.game/'Mods/First/content.png').exists()
    assert (app.game/'Mods/Second/content.png').read_bytes() == b'second'
    assert (app.game/'UserLibs/shared.png').read_bytes() == b'shared'
    app.reopen()
    assert view(app)['enabled_ids'] == [second['release_id']]


def test_multi_level_mod_above_legacy_128_file_limit_is_complete(app, monkeypatch):
    from backend.deployments.executor import DeploymentExecutor
    from backend.deployments.journal import Journal
    from backend.deployments.process_guard import GuardSession, ProcessGuard
    from backend.storage.package_repository import PackageRepository
    totals = {}
    def measured(cls, name):
        original = getattr(cls, name)
        label = cls.__name__+'.'+name
        @wraps(original)
        def timed(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                value = totals.setdefault(label, [0, 0.0])
                value[0] += 1
                value[1] += time.perf_counter()-started
        monkeypatch.setattr(cls, name, timed)
    for cls, names in ((DeploymentExecutor, ('_stage', '_execute', '_save', 'probe')),
                       (GuardSession, ('check',)), (ProcessGuard, ('inspect',)),
                       (Journal, ('append', 'load')), (PackageRepository, ('release',))):
        for name in names:
            measured(cls, name)
    write(app, 'Mods/LargeMod.dll', b'large mod')
    for index in range(129):
        write(app, f'Mods/LargeMod/assets/deep/{index:03d}.png', b'resource')
    item = view(app)['items'][0]
    assert item['file_count'] == 130
    first = workspace(app).detail(app.installation, item['id'], 0, 100)
    second = workspace(app).detail(app.installation, item['id'], first['next_offset'], 100)
    assert len(first['files']) + len(second['files']) == 130 and second['next_offset'] is None
    result = save(app, [])
    assert result['completed'], result
    assert not (app.game/'Mods/LargeMod.dll').exists()
    assert list((app.game/'Mods/LargeMod').rglob('*.png')) == []
    app.reopen()
    result = save(app, [item['id']])
    assert result['completed'], result
    assert (app.game/'Mods/LargeMod.dll').read_bytes() == b'large mod'
    assert len(list((app.game/'Mods/LargeMod').rglob('*.png'))) == 129
    print('workspace 130-file stages (nested times):',
        {name: dict(calls=value[0], seconds=round(value[1], 3)) for name, value in totals.items()}, flush=True)
    # Keep the ordinary uninstall entry point usable for a large Mod managed
    # by the new workspace; its transaction must not require a plan dialog.
    from tests.integration.harness.transaction_recovery import finish, request
    current = next(value for value in view(app)['items'] if value['id'] == item['id'])
    started = app.context.api.dispatch(request('mod.action', dict(installation_id=app.installation,
        release_id=current['release_id'], manifest_hash=current['manifest_hash'],
        action='uninstall', idempotency_key=str(uuid4()))))
    assert started['ok'], started
    observed = time.perf_counter()
    terminal = finish(app.context.api, started['data'], timeout=240)
    assert terminal['state'] == 'succeeded' and terminal['data'].get('completed'), terminal
    print('workspace 130-file ordinary uninstall:', round(time.perf_counter()-observed, 3), 'seconds', flush=True)
    assert not (app.game/'Mods/LargeMod.dll').exists()
    assert not list((app.game/'Mods/LargeMod').rglob('*.png'))
    app.reopen()
    assert next(value for value in view(app)['items'] if value['id'] == item['id'])['state'] == 'not_installed'


def test_unknown_target_collision_is_not_overwritten(app):
    release = app.publish({'Mods/Collision/content.png': b'library'})
    target = write(app, 'Mods/Collision/content.png', b'external')
    initial = view(app)
    with pytest.raises(PlanRejected, match='PLAN_UNMANAGED_CONFLICT'):
        save(app, [*initial['enabled_ids'], release['release_id']])
    assert target.read_bytes() == b'external'


def test_save_replay_never_reverts_newer_loading_list(app):
    target = write(app, 'Mods/Replay.dll', b'replay')
    initial = view(app)
    key = str(uuid4())
    assert save(app, [], snapshot=initial['snapshot_id'], key=key)['completed']
    assert save(app, initial['enabled_ids'])['completed']
    replay = save(app, [], snapshot=initial['snapshot_id'], key=key)
    assert replay['idempotent'] and replay['workspace']['enabled_ids'] == initial['enabled_ids']
    assert target.read_bytes() == b'replay'


def test_workspace_commit_recovery_keeps_loading_state_and_order(app):
    release = app.publish({'Mods/Recovery/content.png': b'recover'})
    fired = []
    def fault(point, details):
        if point == 'db.before_commit' and not fired:
            fired.append(point)
            raise OSError('synthetic workspace commit fault')
    app.context.deployments.fault = fault
    result = save(app, [release['release_id']])
    assert result['completed'] and fired, result
    app.reopen()
    assert view(app)['enabled_ids'] == [release['release_id']]
    assert (app.game/'Mods/Recovery/content.png').read_bytes() == b'recover'


def test_journal_cache_never_trusts_mutated_prefix_or_caller_data(tmp_path, monkeypatch):
    from backend.deployments.journal import Journal, JournalRejected, write_private
    root = tmp_path/'private'
    root.mkdir()
    write_private(root, '.plan-approval-key', b'k'*32)
    journal = Journal(root, uuid4())
    journal.create({'scope': 'original'})
    journal.append('intent', {'value': 'original'})
    journal.load()
    actual = journal._record
    verified = []
    def count(*args):
        verified.append(args)
        return actual(*args)
    monkeypatch.setattr(journal, '_record', count)
    returned = journal.load()
    assert verified == []  # Unchanged durable prefix is not re-encoded/HMACed.
    returned[0]['data']['scope'] = 'caller annotation'
    assert journal.load()[0]['data']['scope'] == 'original'
    path = root/journal.relative_path
    path.write_bytes(path.read_bytes().replace(b'original', b'tampered'))
    with pytest.raises(JournalRejected, match='JOURNAL_CORRUPT'):
        journal.load()


def test_external_file_changed_during_backup_is_not_adopted(app, monkeypatch):
    from backend.storage.package_repository import PackageRepository
    target = write(app, 'Mods/Race.dll', b'original')
    initial = view(app)
    capture = PackageRepository.capture_ordinary
    fired = []
    def change_then_capture(*args, **kwargs):
        if not fired:
            target.write_bytes(b'modified')
            fired.append(True)
        return capture(*args, **kwargs)
    monkeypatch.setattr(PackageRepository, 'capture_ordinary', change_then_capture)
    with pytest.raises(PlanRejected, match='MOD_WORKSPACE_STALE'):
        save(app, [], snapshot=initial['snapshot_id'])
    assert target.read_bytes() == b'modified'
    assert app.call('package.list')['releases'] == []


def test_unsupported_library_file_remains_visible_without_breaking_workspace(app):
    release = app.publish({'unknown.json': b'package data', 'Mods/content.png': b'content'})
    write(app, 'Mods/Valid.dll', b'external')
    result = view(app)
    assert len(result['items']) == 2
    item = next(item for item in result['items'] if item['release_id'] == release['release_id'])
    assert item['manageable'] is False and item['file_count'] == 2 and item['message']
    detail = workspace(app).detail(app.installation, item['id'])
    assert {file['relative_path'] for file in detail['files']} == {'unknown.json', 'Mods/content.png'}
    with pytest.raises(PlanRejected, match='MOD_WORKSPACE_UNSAFE'):
        save(app, [*result['enabled_ids'], item['id']])
    assert not (app.game/'Mods/content.png').exists()


def test_library_only_readme_is_not_a_deployable_mod(app):
    release = app.publish({'README.txt': b'notes only'})
    item = next(item for item in view(app)['items'] if item['release_id'] == release['release_id'])
    assert item['manageable'] is False and item['file_count'] == 1 and item['message']
    with pytest.raises(PlanRejected, match='MOD_WORKSPACE_UNSAFE'):
        save(app, [item['id']])
    assert not (app.game/'README.txt').exists()


def test_workspace_bridge_dispatch_and_strict_payloads(app):
    from tests.integration.harness.transaction_recovery import request
    write(app, 'Mods/Bridge.dll', b'external')
    write(app, 'Mods/Bridge/assets/item.png', b'asset')
    current = app.call('mod.workspace', {'installation_id': app.installation})
    assert current['items'][0]['display_name'] == 'Bridge'
    detail = app.call('mod.workspace.detail', {'installation_id': app.installation, 'id': current['items'][0]['id']})
    assert detail['files'][0]['relative_path'] == 'Mods/Bridge.dll'
    saved = app.call('mod.workspace.save', dict(installation_id=app.installation,
        snapshot_id=current['snapshot_id'], enabled_ids=[], idempotency_key=str(uuid4())))
    assert saved['completed'] and saved['workspace']['enabled_ids'] == []
    assert not (app.game/'Mods/Bridge.dll').exists()
    invalid = app.context.api.dispatch(request('mod.workspace.save', dict(installation_id=app.installation,
        snapshot_id=current['snapshot_id'], enabled_ids=[], idempotency_key=str(uuid4()), path='Mods/Bridge.dll')))
    assert not invalid['ok'] and invalid['error']['code'] == 'INVALID_REQUEST'


@pytest.mark.parametrize('point', ['ordinary.header', 'ordinary.prepared', 'ordinary.file_applied',
                                  'ordinary.commit_intent', 'ordinary.db_committed'])
def test_real_process_crash_recovers_workspace_without_per_file_db_checkpoints(app, point):
    payloads = {'Mods/Crash.dll': b'crash mod', 'Mods/Crash/assets/item.png': b'asset'}
    for name, value in payloads.items():
        write(app, name, value)
    identity = view(app)['items'][0]['id']
    app.close()
    code = '''
import os,sys
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
context=DesktopContext(resolve_paths(sys.argv[1]))
context.initialize()
def fault(point, details):
    if point == sys.argv[3]:
        os._exit(73)
context.deployments.fault=fault
from uuid import uuid4
workspace=context.mod_workspace
current=workspace.list(sys.argv[2])
workspace.save(dict(installation_id=sys.argv[2],snapshot_id=current['snapshot_id'],enabled_ids=[],idempotency_key=str(uuid4())))
os._exit(74)
'''
    child = subprocess.run([sys.executable, '-c', code, str(app.data), app.installation, point],
        capture_output=True, timeout=120)
    assert child.returncode == 73, (point, child.returncode, child.stderr.decode(errors='replace'))
    app.reopen()
    state = next(item for item in view(app)['items'] if item['id'] == identity)['state']
    assert state in {'enabled', 'disabled'}
    for name, content in payloads.items():
        assert (app.game/name).exists() == (state == 'enabled')
        if state == 'enabled':
            assert (app.game/name).read_bytes() == content
    assert not app.status()['recovery_required']
    assert (app.game/'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'


def test_multi_resource_import_above_legacy_limit_preserves_complete_manifest(app):
    files = {f'Mods/Imported/assets/{index:03d}.png': b'asset' for index in range(129)}
    release = app.publish(files, 'Large imported Mod')
    item = next(item for item in view(app)['items'] if item['release_id'] == release['release_id'])
    assert item['file_count'] == 129 and item['manageable']
    detail = workspace(app).detail(app.installation, item['id'])
    assert {file['relative_path'] for file in detail['files']} == set(files)
