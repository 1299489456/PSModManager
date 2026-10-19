"""Scheme deletion removes editor entries, not library or deployment evidence."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.contracts import ENTITY_MODELS
from backend.profiles.service import ProfileService
from backend.profiles.serialization import ProfileError


@pytest.fixture
def app(tmp_path):
    context = DesktopContext(resolve_paths(str(tmp_path / 'private')))
    context.initialize()
    context.api = DesktopApi(context)
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    game = tmp_path / 'synthetic-game'
    game.mkdir()
    (game / 'synthetic-save.dat').write_bytes(b'unchanged save')
    context.store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fixtures['RuntimeFingerprint']))
    fixtures['Installation']['canonical_root'] = str(game)
    context.store.put(ENTITY_MODELS['Installation'].model_validate(fixtures['Installation']))
    try:
        yield context, fixtures, game
    finally:
        context.close()


def rpc(context, command, **payload):
    return context.api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def test_delete_current_falls_back_and_last_delete_survives_reopen(app):
    context, fixtures, game = app
    service = context.profiles
    installation_id = fixtures['Installation']['installation_id']
    first = service.create(installation_id, '第一个方案')
    second = service.create(installation_id, '第二个方案')
    before = snapshot(game)
    result = rpc(context, 'profile.delete', profile_id=second['profile_id'], expected_revision=0)
    assert result['ok'], result
    assert result['data'] == {'deleted_profile_id': second['profile_id'], 'selected_profile_id': first['profile_id']}
    with pytest.raises(ProfileError, match='PROFILE_NOT_FOUND'):
        service.get(second['profile_id'])
    assert service.list(installation_id)['selected_profile_id'] == first['profile_id']
    service.delete(first['profile_id'], 0)
    reopened = ProfileService(context.paths)
    try:
        assert reopened.list(installation_id) == {'profiles': [], 'selected_profile_id': None}
        assert reopened.create(installation_id, '重新创建')['profile_id'] not in (first['profile_id'], second['profile_id'])
    finally:
        reopened.close()
    assert before == snapshot(game)


def test_delete_nonselected_preserves_other_installation_and_selection(app):
    context, fixtures, _ = app
    service = context.profiles
    installation_id = fixtures['Installation']['installation_id']
    first = service.create(installation_id, '待删除')
    second = service.create(installation_id, '当前方案')
    other_id = str(uuid4())
    other_installation = fixtures['Installation'] | {'installation_id': other_id,
        'canonical_root': str(Path(fixtures['Installation']['canonical_root']).parent / 'other-game'), 'file_identity': 'other:identity'}
    context.store.put(ENTITY_MODELS['Installation'].model_validate(other_installation))
    other = service.create(other_id, '另一游戏方案')
    result = service.delete(first['profile_id'], 0)
    assert result['selected_profile_id'] == second['profile_id']
    assert [item['profile_id'] for item in service.list(installation_id)['profiles']] == [second['profile_id']]
    assert service.list(other_id)['selected_profile_id'] == other['profile_id']


@pytest.mark.parametrize('revision', [0, True, '1', -1])
def test_delete_requires_current_strict_revision_and_keeps_selection_on_failure(app, revision):
    context, fixtures, _ = app
    installation_id = fixtures['Installation']['installation_id']
    original = context.profiles.create(installation_id, '原方案')
    renamed = context.profiles.edit(original['profile_id'], 0, {'op': 'rename', 'name': '已修改'})
    before = context.profiles.list(installation_id)
    result = rpc(context, 'profile.delete', profile_id=original['profile_id'], expected_revision=revision)
    assert not result['ok'], result
    assert result['error']['code'] == ('PROFILE_REVISION_CONFLICT' if type(revision) is int and revision == 0 else 'INVALID_REQUEST')
    assert context.profiles.list(installation_id) == before
    assert context.profiles.get(original['profile_id']) == renamed


def test_delete_retains_committed_profile_history_plan_library_and_game(app):
    context, fixtures, game = app
    draft = context.profiles.create(fixtures['Installation']['installation_id'], '部署过的方案')
    saved = context.profiles.commit(draft['profile_id'], 0)
    context.store.put(ENTITY_MODELS['BaselineSnapshot'].model_validate(fixtures['BaselineSnapshot']))
    plan = deepcopy(fixtures['ActionPlan']) | {'plan_id': str(uuid4()), 'profile_id': draft['profile_id'], 'profile_revision': 0}
    context.store.plans.add(ENTITY_MODELS['ActionPlan'].model_validate(plan))
    library_file = context.paths.root / 'library' / 'preserved.txt'
    library_file.parent.mkdir(exist_ok=True)
    library_file.write_text('shared library bytes', encoding='utf-8')
    library_before, game_before = snapshot(library_file.parent), snapshot(game)
    historical_before = [tuple(row) for row in context.store.connection.execute('SELECT * FROM profile_history')]
    committed_before = context.store.get('Profile', draft['profile_id'])
    plan_before = context.store.get('ActionPlan', plan['plan_id'])
    context.profiles.delete(saved['profile_id'], saved['revision'])
    assert context.store.get('Profile', draft['profile_id']) == committed_before
    assert context.store.get('ActionPlan', plan['plan_id']) == plan_before
    assert [tuple(row) for row in context.store.connection.execute('SELECT * FROM profile_history')] == historical_before
    assert context.store.connection.execute('PRAGMA foreign_key_check').fetchall() == []
    assert snapshot(library_file.parent) == library_before
    assert snapshot(game) == game_before


def test_delete_rpc_has_closed_payload_and_missing_profile_error(app):
    context, fixtures, _ = app
    draft = context.profiles.create(fixtures['Installation']['installation_id'], '保留')
    payload = {'profile_id': draft['profile_id'], 'expected_revision': 0}
    invalid = rpc(context, 'profile.delete', **payload, delete_mods=True)
    assert not invalid['ok'] and invalid['error']['code'] == 'INVALID_REQUEST'
    missing = rpc(context, 'profile.delete', profile_id=str(uuid4()), expected_revision=0)
    assert not missing['ok'] and missing['error']['code'] == 'PROFILE_NOT_FOUND'
    assert context.profiles.get(draft['profile_id']) == draft
