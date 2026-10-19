"""CASE-025: closed desktop RPC, native path grants and real profile consumers.

All files and Installation rows are synthetic. No WebView, real game or save
directory is opened. Plan checks exercise the existing repository contract only.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import KNOWN_GAME_ROOT, REFERENCE_ROOT, resolve_paths
from backend.contracts import ENTITY_MODELS
from backend.storage.repositories import StorageConflict
from backend.storage.package_repository import PackageRepository


@pytest.fixture
def profile_app(tmp_path):
    context = DesktopContext(resolve_paths(str(tmp_path / 'private')))
    context.initialize()
    context.api = DesktopApi(context)
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    game = tmp_path / 'synthetic-game'
    game.mkdir()
    (game / 'synthetic-save.dat').write_bytes(b'unchanged synthetic save')
    context.store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fixtures['RuntimeFingerprint']))
    fixtures['Installation']['canonical_root'] = str(game)
    context.store.put(ENTITY_MODELS['Installation'].model_validate(fixtures['Installation']))
    try:
        yield context, fixtures, game
    finally:
        context.close()


def rpc(context, command, **payload):
    return context.api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))


def ok(context, command, **payload):
    response = rpc(context, command, **payload)
    assert response['ok'], response
    return response['data']


def rejected(context, command, code=None, **payload):
    response = rpc(context, command, **payload)
    assert not response['ok'], response
    if code:
        assert response['error']['code'] == code, response
    assert response['error']['details'] == {}
    return response


def create(context, fixtures):
    return ok(context, 'profile.create', installation_id=fixtures['Installation']['installation_id'], name='本地配置')


def wait_job(context, snapshot):
    deadline = time.monotonic() + 30
    while snapshot['state'] not in {'succeeded', 'failed', 'cancelled'}:
        assert time.monotonic() < deadline, snapshot
        time.sleep(.01)
        snapshot = context.jobs.snapshot(snapshot['task_id'])
    assert snapshot['state'] == 'succeeded', snapshot
    return snapshot['data']


def manifest():
    return dict(schema_version=2, name='离线配置', groups=['资源'], selections=[dict(
        release_id=str(uuid4()), manifest_hash='a' * 64, selected=True,
        version_raw='unknown-local-19', source=dict(kind='unknown', observed_at=None), group='资源')])


def grant(context, path, value=None):
    path.write_text(json.dumps(value or manifest(), ensure_ascii=False), encoding='utf-8')
    context.choose_profile_source = lambda: str(path)
    result = ok(context, 'profile.choose')
    assert set(result) == {'source_handle', 'display_name'}
    assert str(path) not in json.dumps(result)
    return result['source_handle']


@pytest.mark.parametrize('command,payload', [
    ('profile.choose', {'path': 'C:/not-authorized.json'}),
    ('profile.import_preview', {'source_handle': str(uuid4()), 'path': 'C:/not-authorized.json'}),
    ('profile.save_export', {'profile_id': str(uuid4()), 'destination': 'C:/not-authorized.json'}),
    ('profile.get', {'profile_id': str(uuid4()), 'include_credentials': True}),
    ('profile.commit', {'profile_id': str(uuid4()), 'expected_revision': True}),
    ('profile.edit', {'profile_id': str(uuid4()), 'expected_revision': '0', 'action': {'op': 'undo'}}),
    ('profile.import_commit', {'source_handle': str(uuid4()), 'preview_hash': 'not-a-hash',
                               'installation_id': str(uuid4()), 'name': 'Example'}),
])
def test_closed_rpc_rejects_paths_and_coercion(profile_app, command, payload):
    context, _, _ = profile_app
    rejected(context, command, 'INVALID_REQUEST', **payload)
    assert not context.store.list('Profile')


def test_case027_profile_names_survive_package_pagination_and_reopen(profile_app, tmp_path):
    context, fixtures, _ = profile_app
    source = tmp_path / 'named-package'
    source.mkdir()
    (source / 'resource.png').write_bytes(b'local named package')
    handle = context.imports.grant('directory', str(source))['source_handle']
    preview = context.imports.preview(handle)
    published = context.imports.commit(preview['preview_id'], preview['preview_hash'], '重开仍可搜索的本地包', '1.0')
    draft = create(context, fixtures)
    draft = ok(context, 'profile.edit', profile_id=draft['profile_id'], expected_revision=0,
               action=dict(op='set_selection', selection=dict(release_id=published['release_id'],
                   manifest_hash=published['manifest_hash'], selected=True)))
    # The UI may currently hold a package page which does not contain this lock.
    assert wait_job(context, ok(context, 'package.list', offset=20, limit=20))['releases'] == []
    assert draft['selections'][0]['display_name'] == '重开仍可搜索的本地包'
    from backend.profiles.service import ProfileService
    reopened = ProfileService(context.paths).get(draft['profile_id'])
    assert reopened['selections'] == draft['selections']
    exported = ok(context, 'profile.export', profile_id=draft['profile_id'])['manifest']
    assert 'display_name' not in exported['selections'][0]
    source_path = tmp_path / '导出后重新导入.json'
    handle = grant(context, source_path, exported)
    preview = ok(context, 'profile.import_preview', source_handle=handle,
                 installation_id=fixtures['Installation']['installation_id'])
    assert preview['name'] == draft['name']
    assert preview['selections'][0]['display_name'] == '重开仍可搜索的本地包'
    assert preview['selections'][0]['resolved'] is True
    # Reading a portable manifest is a preview only. Explicit confirmation is
    # still required, and all original selections remain in the current draft.
    assert len(ok(context, 'profile.list', installation_id=fixtures['Installation']['installation_id'])['profiles']) == 1
    imported = ok(context, 'profile.import_commit', source_handle=handle, preview_hash=preview['preview_hash'],
                  installation_id=fixtures['Installation']['installation_id'], name='重导入副本')
    assert imported['profile_id'] != draft['profile_id']
    assert imported['selections'] == draft['selections']
    assert not context.store.list('Profile') and not context.store.list('DeploymentGeneration')


def test_create_edit_optimistic_conflict_undo_clone_and_zero_game_writes(profile_app):
    context, fixtures, game = profile_app
    before = {p.relative_to(game).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in game.rglob('*') if p.is_file()}
    draft = create(context, fixtures)
    profile_id = draft['profile_id']
    renamed = ok(context, 'profile.edit', profile_id=profile_id, expected_revision=0,
                 action={'op': 'rename', 'name': '新配置'})
    assert renamed['revision'] == 1 and renamed['name'] == '新配置'
    rejected(context, 'profile.edit', 'PROFILE_REVISION_CONFLICT', profile_id=profile_id,
             expected_revision=0, action={'op': 'rename', 'name': '过期覆盖'})
    rejected(context, 'profile.edit', profile_id=profile_id, expected_revision=1,
             action={'op': 'rename', 'name': '意外参数', 'game_path': str(game)})
    undone = ok(context, 'profile.edit', profile_id=profile_id, expected_revision=1, action={'op': 'undo'})
    assert undone['name'] == draft['name'] and undone['revision'] == 2
    clone = ok(context, 'profile.clone', profile_id=profile_id, expected_revision=2, name='副本')
    assert clone['profile_id'] != profile_id and clone['selections'] == draft['selections']
    selected = ok(context, 'profile.list', installation_id=fixtures['Installation']['installation_id'])
    assert selected['selected_profile_id'] == clone['profile_id']
    assert not context.store.list('Profile') and not context.store.list('DeploymentGeneration')
    after = {p.relative_to(game).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in game.rglob('*') if p.is_file()}
    assert before == after


def test_native_preview_binding_missing_content_and_source_change(profile_app, tmp_path):
    context, fixtures, _ = profile_app
    value = manifest()
    path = tmp_path / '选择 中文 %.json'
    handle = grant(context, path, value)
    installation_id = fixtures['Installation']['installation_id']
    preview = ok(context, 'profile.import_preview', source_handle=handle, installation_id=installation_id)
    assert preview['selections'][0]['resolved'] is False
    payload = dict(source_handle=handle, preview_hash=preview['preview_hash'],
                   installation_id=fixtures['Installation']['installation_id'], name='导入配置')
    rejected(context, 'profile.import_commit', 'PROFILE_PREVIEW_CHANGED', **(payload | {'preview_hash': 'b' * 64}))
    other = grant(context, tmp_path / 'other.json', value)
    ok(context, 'profile.import_preview', source_handle=other, installation_id=installation_id)
    rejected(context, 'profile.import_commit', 'PROFILE_PREVIEW_CHANGED', **(payload | {'source_handle': other}))
    rejected(context, 'profile.import_commit', 'PROFILE_PREVIEW_CHANGED', **(payload | {'installation_id': str(uuid4())}))
    path.write_text(json.dumps(value | {'name': 'changed'}), encoding='utf-8')
    rejected(context, 'profile.import_commit', 'PROFILE_SOURCE_CHANGED', **payload)
    assert context.store.connection.execute('SELECT count(*) FROM profile_drafts').fetchone()[0] == 0
    path2 = tmp_path / 'offline.json'
    fresh = grant(context, path2, value)
    preview = ok(context, 'profile.import_preview', source_handle=fresh, installation_id=installation_id)
    created = ok(context, 'profile.import_commit', **(payload | {'source_handle': fresh, 'preview_hash': preview['preview_hash']}))
    exported = ok(context, 'profile.export', profile_id=created['profile_id'])['manifest']
    assert exported['selections'] == value['selections']
    assert exported['name'] == '导入配置'
    result = wait_job(context, ok(context, 'profile.commit', profile_id=created['profile_id'], expected_revision=0))
    assert result['error_code'] == 'PROFILE_UNRESOLVED'
    assert not context.store.list('Profile')
    rejected(context, 'profile.import_preview', 'PROFILE_SOURCE_EXPIRED', source_handle=fresh, installation_id=installation_id)


@pytest.mark.parametrize('kind', ['game', 'data', 'known-game', 'reference', 'relative', 'network'])
def test_native_grant_rejects_protected_paths_before_source_read(profile_app, monkeypatch, kind):
    context, _, game = profile_app
    targets = {'game': game / 'private.json', 'data': context.paths.root / 'private.json',
               'known-game': KNOWN_GAME_ROOT / 'private.json', 'reference': REFERENCE_ROOT / 'private.json',
               'relative': Path('relative.json'), 'network': Path('//untrusted/share/private.json')}
    context.choose_profile_source = lambda: str(targets[kind])
    def forbidden_read(*args, **kwargs):
        pytest.fail('Protected selection reached filesystem source reader')
    monkeypatch.setattr(context.profiles, '_source_bytes', forbidden_read)
    response = rejected(context, 'profile.choose')
    assert str(targets[kind]) not in json.dumps(response)


def test_native_export_create_only_private_and_cancellation(profile_app, tmp_path):
    context, fixtures, _ = profile_app
    draft = create(context, fixtures)
    payload = {'profile_id': draft['profile_id']}
    context.choose_profile_destination = lambda: None
    assert ok(context, 'profile.save_export', **payload) == {'saved': False, 'cancelled': True}
    path = tmp_path / '导出 %.json'
    context.choose_profile_destination = lambda: str(path)
    assert ok(context, 'profile.save_export', **payload)['saved']
    original = path.read_bytes()
    data = json.loads(original)
    assert data == ok(context, 'profile.export', **payload)['manifest']
    assert str(context.paths.root).encode() not in original
    rejected(context, 'profile.save_export', 'PROFILE_EXPORT_REFUSED', **payload)
    assert path.read_bytes() == original


@pytest.mark.parametrize('kind', ['game', 'data', 'known-game', 'reference'])
def test_native_export_never_opens_protected_parent(profile_app, monkeypatch, kind):
    context, fixtures, game = profile_app
    draft = create(context, fixtures)
    root = {'game': game, 'data': context.paths.root, 'known-game': KNOWN_GAME_ROOT, 'reference': REFERENCE_ROOT}[kind]
    context.choose_profile_destination = lambda: str(root / 'must-not-exist.json')
    def forbidden_lock(*args, **kwargs):
        pytest.fail('Protected destination reached write locking')
    monkeypatch.setattr('backend.app.profile_export.locked_path', forbidden_lock)
    rejected(context, 'profile.save_export', 'PROFILE_EXPORT_REFUSED', profile_id=draft['profile_id'])


@pytest.mark.parametrize('name', ['NUL.json', 'COM1.json', 'CON.json'])
def test_native_export_rejects_reserved_device_before_any_write(profile_app, tmp_path, monkeypatch, name):
    context, fixtures, _ = profile_app
    draft = create(context, fixtures)
    context.choose_profile_destination = lambda: str(tmp_path / name)
    def forbidden_lock(*args, **kwargs):
        pytest.fail('Reserved Windows device reached destination locking or writing')
    monkeypatch.setattr('backend.app.profile_export.locked_path', forbidden_lock)
    rejected(context, 'profile.save_export', 'PROFILE_EXPORT_REFUSED', profile_id=draft['profile_id'])


def test_native_import_source_stays_locked_through_draft_transaction(profile_app, tmp_path, monkeypatch):
    context, fixtures, _ = profile_app
    path = tmp_path / 'locked.json'
    handle = grant(context, path)
    installation_id = fixtures['Installation']['installation_id']
    preview = ok(context, 'profile.import_preview', source_handle=handle, installation_id=installation_id)
    original = context.profiles._create
    observed = []
    def checked_create(*args):
        with pytest.raises(PermissionError):
            path.write_bytes(b'changed during transaction')
        observed.append(True)
        return original(*args)
    monkeypatch.setattr(context.profiles, '_create', checked_create)
    ok(context, 'profile.import_commit', source_handle=handle, preview_hash=preview['preview_hash'],
       installation_id=installation_id, name='锁定来源')
    assert observed == [True]
    assert json.loads(path.read_bytes())['schema_version'] == 2


def test_native_source_hardlink_and_oversize_rejected_without_private_output(profile_app, tmp_path):
    context, _, _ = profile_app
    source = tmp_path / 'private-manifest.json'
    source.write_text(json.dumps(manifest()), encoding='utf-8')
    alias = tmp_path / 'alias.json'
    os.link(source, alias)
    context.choose_profile_source = lambda: str(alias)
    response = rejected(context, 'profile.choose')
    assert str(alias) not in json.dumps(response)
    huge = tmp_path / 'large.json'
    huge.write_bytes(b'x' * (256 * 1024 + 1))
    context.choose_profile_source = lambda: str(huge)
    response = rejected(context, 'profile.choose', 'PROFILE_MANIFEST_INVALID')
    assert str(huge) not in json.dumps(response)
    assert not context.profiles._sources


def test_cancel_commit_rolls_back_real_profile_and_retains_draft(profile_app, tmp_path, monkeypatch):
    context, fixtures, _ = profile_app
    source = tmp_path / 'local-package'
    source.mkdir()
    (source / 'resource.txt').write_bytes(b'bounded synthetic resource')
    handle = context.imports.grant('directory', str(source))['source_handle']
    preview = context.imports.preview(handle)
    release = context.imports.commit(preview['preview_id'], preview['preview_hash'], 'Synthetic resource')
    draft = create(context, fixtures)
    draft = ok(context, 'profile.edit', profile_id=draft['profile_id'], expected_revision=0,
               action={'op': 'set_selection', 'selection': {'release_id': release['release_id'],
                       'manifest_hash': release['manifest_hash'], 'selected': True}})
    entered = threading.Event()
    original = PackageRepository.release
    def cancellable_verify(repository, *args, check=lambda: None, **kwargs):
        result = original(repository, *args, check=check, **kwargs)
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            check()
            time.sleep(.01)
        pytest.fail('Cancellation did not reach real release verification')
        return result
    monkeypatch.setattr(PackageRepository, 'release', cancellable_verify)
    initial = ok(context, 'profile.commit', profile_id=draft['profile_id'], expected_revision=draft['revision'])
    assert entered.wait(3)
    ok(context, 'task.cancel', task_id=initial['task_id'])
    deadline = time.monotonic() + 3
    while True:
        result = context.jobs.snapshot(initial['task_id'])
        if result['state'] in {'cancelled', 'failed', 'succeeded'}:
            break
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert result['state'] == 'cancelled'
    assert not context.store.list('Profile')
    assert not context.store.list('DeploymentGeneration')
    unchanged = ok(context, 'profile.get', profile_id=draft['profile_id'])
    assert unchanged == draft


def test_large_200_selection_commit_returns_bounded_success_after_actual_publication(profile_app, tmp_path):
    context, fixtures, _ = profile_app
    source = tmp_path / 'local-package'
    source.mkdir()
    (source / 'resource.txt').write_bytes(b'one real content addressed artifact reused by bounded releases')
    source_handle = context.imports.grant('directory', str(source))['source_handle']
    preview = context.imports.preview(source_handle)
    published = context.imports.commit(preview['preview_id'], preview['preview_hash'], 'Bounded shared resource', 'v' * 200)
    release = context.store.get('ModRelease', published['release_id']).model_dump(mode='json')
    package = context.store.get('ModPackage', release['package_id']).model_dump(mode='json')
    entry = context.store.list('FileEntry')[0].model_dump(mode='json')
    selections = []
    for index in range(200):
        release_id = str(uuid4())
        package_id = str(uuid4())
        context.store.put(ENTITY_MODELS['ModPackage'].model_validate(package | {'package_id': package_id}))
        context.store.put(ENTITY_MODELS['ModRelease'].model_validate(release | {'release_id': release_id, 'package_id': package_id}))
        context.store.put(ENTITY_MODELS['FileEntry'].model_validate(entry | {'release_id': release_id, 'file_entry_id': str(uuid4())}))
        selections.append(dict(release_id=release_id, manifest_hash=published['manifest_hash'], selected=True,
            version_raw='v' * 200, source=dict(kind='user', observed_at='2026-09-29T00:00:00Z'), group='组' * 80))
    document = dict(schema_version=2, name='大配置', groups=['组' * 80], selections=selections)
    handle = grant(context, tmp_path / 'large-profile.json', document)
    installation_id = fixtures['Installation']['installation_id']
    preview = ok(context, 'profile.import_preview', source_handle=handle, installation_id=installation_id)
    draft = ok(context, 'profile.import_commit', source_handle=handle, preview_hash=preview['preview_hash'],
               installation_id=installation_id, name='大配置')
    assert len(json.dumps(draft, ensure_ascii=False).encode('utf-8')) > 65536
    initial = ok(context, 'profile.commit', profile_id=draft['profile_id'], expected_revision=0)
    result = wait_job(context, initial)
    assert len(json.dumps(result, ensure_ascii=False).encode('utf-8')) < 2048
    assert 'error_code' not in result and result['profile_id'] == draft['profile_id']
    actual = context.store.get('Profile', draft['profile_id'])
    assert len(actual.selections) == 200
    reopened = ok(context, 'profile.get', profile_id=draft['profile_id'])
    assert len(reopened['selections']) == 200 and not reopened['dirty']
    assert not context.store.list('DeploymentGeneration')


def test_committed_profile_is_same_plan_repository_revision_contract(profile_app):
    context, fixtures, _ = profile_app
    draft = create(context, fixtures)
    profile_id = draft['profile_id']
    wait_job(context, ok(context, 'profile.commit', profile_id=profile_id, expected_revision=0))
    assert context.store.get('Profile', profile_id).revision == 0
    context.store.put(ENTITY_MODELS['BaselineSnapshot'].model_validate(fixtures['BaselineSnapshot']))
    plan = deepcopy(fixtures['ActionPlan'])
    plan.update(plan_id=str(uuid4()), profile_id=profile_id, profile_revision=0)
    context.store.plans.add(ENTITY_MODELS['ActionPlan'].model_validate(plan))
    ok(context, 'profile.edit', profile_id=profile_id, expected_revision=0, action={'op': 'rename', 'name': '编辑草稿'})
    assert context.store.get('Profile', profile_id).revision == 0
    wait_job(context, ok(context, 'profile.commit', profile_id=profile_id, expected_revision=1))
    assert context.store.get('Profile', profile_id).revision == 1
    stale = plan | {'plan_id': str(uuid4()), 'idempotency_key': str(uuid4())}
    with pytest.raises(StorageConflict, match='revision'):
        context.store.plans.add(ENTITY_MODELS['ActionPlan'].model_validate(stale))
    current = stale | {'profile_revision': 1}
    context.store.plans.add(ENTITY_MODELS['ActionPlan'].model_validate(current))
    assert not context.store.list('DeploymentGeneration')
