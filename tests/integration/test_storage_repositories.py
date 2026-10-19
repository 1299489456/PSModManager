"""Actual composition -> typed repositories -> persistent SQLite, synthetic only."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.composition import DesktopApi, DesktopContext
from backend.app.paths import resolve_paths
from backend.contracts import ENTITY_MODELS, Profile
from backend.storage import Store, StorageConflict
from backend.storage.schema import ENTITY_TABLES

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/contracts/valid.json'


def model(name, **changes):
    payload = deepcopy(json.loads(FIXTURES.read_text(encoding='utf-8'))[name])
    payload.update(changes)
    return ENTITY_MODELS[name].model_validate(payload)


ORDER = ['RuntimeFingerprint', 'Installation', 'ModPackage', 'ModRelease', 'Artifact',
         'Component', 'FileEntry', 'Relation', 'Profile', 'BaselineSnapshot',
         'RuntimeSession', 'DiagnosticEvidence', 'Observation', 'ActionPlan', 'Approval', 'Transaction', 'DeploymentGeneration']


def populate(store):
    entities = {}
    for name in ORDER:
        item = model(name, **({'state': 'committed'} if name == 'Transaction' else {}))
        if name == 'Profile':
            result = store.profiles.create(item)
        elif name in store.plans.ENTITIES:
            result = store.plans.add(item)
        else:
            result = store.inventory.add(item)
        assert result == item
        entities[name] = item
    return entities


@pytest.fixture
def seeded(tmp_path):
    store = Store(tmp_path / 'db.sqlite3')
    try:
        yield store, populate(store)
    finally:
        store.close()


def test_actual_desktop_composition_reopens_all_entities(tmp_path):
    paths = resolve_paths(str(tmp_path / 'PSModManager'))
    paths.root.mkdir()
    # Schema fixtures intentionally use placeholder hashes, not published bytes.
    # Seed the low-level DTO store; production Profile consumers must reject it.
    seed = Store(paths.database)
    try:
        entities = populate(seed)
    finally:
        seed.close()
    context = DesktopContext(paths)
    try:
        context.initialize()
        with pytest.raises((ValueError, OSError)):
            context.store.profiles.export(entities['Profile'].profile_id)
        assert DesktopApi(context).dispatch({'schema_version': 1, 'request_id': 'read', 'command': 'app.info', 'payload': {}})['ok']
    finally:
        context.close()
    second = DesktopContext(paths)
    try:
        second.initialize()
        for name, item in entities.items():
            if name == 'Transaction':
                # A schema DTO claiming committed has no authenticated journal
                # or physical deployment. Startup must preserve it as blocked.
                assert second.store.list(name) == [item.model_copy(update={'state': 'recovery_required'})]
            else:
                assert second.store.list(name) == [item]
        assert second.deployments.startup_recovery['status'] == 'blocked'
        assert second.deployments.startup_recovery['pending_count'] == 1
        assert second.store.connection.execute('SELECT COUNT(*) FROM deployment_current').fetchone()[0] == 0
        assert second.store.connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert second.store.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert second.store.connection.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises((ValueError, OSError)):
            second.store.profiles.export(entities['Profile'].profile_id)
    finally:
        second.close()


def test_same_names_versions_different_bytes_and_namespaced_content(seeded):
    store, entities = seeded
    first = entities['ModRelease']
    second = first.model_copy(update={'release_id': uuid4(), 'manifest_hash': 'b' * 64})
    store.inventory.add(second)
    package = entities['ModPackage'].model_copy(update={'package_id': uuid4()})
    store.inventory.add(package)
    assert len(store.inventory.list('ModPackage')) == 2
    assert {r.manifest_hash for r in store.inventory.list('ModRelease')} == {'a' * 64, 'b' * 64}
    assert {r.version_raw for r in store.inventory.list('ModRelease')} == {first.version_raw}
    component = entities['Component'].model_dump(mode='json')
    component['component_id'] = str(uuid4())
    component['content_ids'] = [{'framework': f, 'type': 'BundleId', 'value': 'same'} for f in ['framework-one', 'framework-two']]
    created = store.inventory.add(ENTITY_MODELS['Component'].model_validate(component))
    assert len({c.namespace_key for c in created.content_ids}) == 2
    assert {'melon_mod', 'itemforge_content'} <= created.roles


def test_immutable_ids_do_not_overwrite(seeded):
    store, entities = seeded
    assert store.inventory.add(entities['ModRelease']) == entities['ModRelease']
    with pytest.raises(StorageConflict):
        store.inventory.add(entities['ModRelease'].model_copy(update={'manifest_hash': 'b' * 64}))
    assert store.list('ModRelease') == [entities['ModRelease']]


def test_profile_update_is_atomic_revisioned_and_records_history(seeded):
    store, entities = seeded
    profile = entities['Profile']
    changed = profile.model_copy(deep=True, update={'revision': 1})
    changed.selections[0].selected = False
    store.profiles.update(changed, expected_revision=0)
    with pytest.raises(StorageConflict):
        store.profiles.update(changed, expected_revision=0)
    invalid = changed.model_copy(deep=True, update={'revision': 2})
    invalid.selections[0].manifest_hash = 'b' * 64
    with pytest.raises(sqlite3.IntegrityError):
        store.profiles.update(invalid, expected_revision=1)
    assert store.get('Profile', profile.profile_id) == changed
    rows = store.connection.execute('SELECT revision,payload FROM profile_history ORDER BY revision').fetchall()
    assert [r['revision'] for r in rows] == [0, 1]
    assert Profile.model_validate_json(rows[0]['payload']) == profile
    assert store.connection.execute('SELECT selected FROM profile_selections').fetchone()[0] == 0


def test_real_two_connection_stale_revision_rejected(seeded, tmp_path):
    first, entities = seeded
    second = Store(tmp_path / 'db.sqlite3')
    try:
        profile = entities['Profile'].model_copy(update={'revision': 1})
        first.profiles.update(profile, expected_revision=0)
        with pytest.raises(StorageConflict):
            second.profiles.update(profile, expected_revision=0)
        assert second.get('Profile', profile.profile_id).revision == 1
    finally:
        second.close()


def test_three_states_and_new_session_unknown_are_independent(seeded):
    store, e = seeded
    component, profile, session = e['Component'], e['Profile'], e['RuntimeSession']
    state = store.inventory.state(component.component_id, profile.profile_id, session.session_id, deployed='absent')
    assert (state.desired, state.deployed, state.observed) == ('selected', 'absent', 'registered')
    failure = e['Observation'].model_copy(update={'observation_id': uuid4(), 'event_kind': 'failed'})
    store.inventory.add(failure)
    store.inventory.add(e['Observation'].model_copy(update={'observation_id': uuid4(), 'event_kind': 'initialized'}))
    assert store.inventory.state(component.component_id, profile.profile_id, session.session_id, deployed='matching').observed == 'failed'
    new_session = session.model_copy(update={'session_id': uuid4()})
    store.inventory.add(new_session)
    fresh = store.inventory.state(component.component_id, profile.profile_id, new_session.session_id, deployed='matching')
    assert fresh.observed == 'unknown'
    changed = profile.model_copy(update={'revision': 1, 'selections': []})
    store.profiles.update(changed, expected_revision=0)
    assert store.inventory.state(component.component_id, profile.profile_id, session.session_id, deployed='matching').desired == 'unselected'


@pytest.mark.parametrize('version', [None, '1.0-local', 'C:/private/user/key', '/home/private', 'api_key synthetic-secret', 'sk-proj-SyntheticCredential', 'ghp_SyntheticCredential'])
def test_profile_export_has_only_portable_whitelisted_metadata(tmp_path, version):
    store = Store(tmp_path / 'export.sqlite3')
    try:
        for name in ['RuntimeFingerprint', 'Installation', 'ModPackage', 'ModRelease', 'Artifact', 'Component', 'Profile']:
            item = model(name)
            if name == 'ModPackage':
                item.source.reference = 'C:/private/password=synthetic-secret'
            if name == 'ModRelease':
                item.version_raw = version
            if name == 'Profile':
                item.config_overrides = {'api_key': 'synthetic-secret', 'absolute_path': 'C:/private/data', 'nested': {'token': 'secret'}}
            store.put(item)
        result = store.profiles.export(model('Profile').profile_id)
        text = json.dumps(result)
        assert not any(word in text for word in ['synthetic-secret', 'C:/', '/home/', 'api_key', 'absolute_path', 'config_overrides', 'installation_id'])
        assert set(result) == {'schema_version', 'profile_id', 'revision', 'selections'}
        selection = result['selections'][0]
        assert selection['version_raw'] == (version if version in (None, '1.0-local') else None)
        assert selection['manifest_hash'] == 'a' * 64
        assert set(selection['source']) == {'kind', 'observed_at'}
    finally:
        store.close()


def test_sql_foreign_unique_constraints_are_enforced(seeded):
    store, e = seeded
    with pytest.raises(sqlite3.IntegrityError):
        store.inventory.add(e['ModRelease'].model_copy(update={'release_id': uuid4(), 'package_id': uuid4()}))
    with pytest.raises(sqlite3.IntegrityError):
        store.inventory.add(e['Installation'].model_copy(update={'installation_id': uuid4(), 'canonical_root': 'C:/synthetic/alias'}))
    with pytest.raises(sqlite3.IntegrityError):
        store.plans.add(e['Approval'].model_copy(update={'approval_id': uuid4(), 'plan_hash': 'b' * 64}))
    assert store.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_plan_bound_to_complete_baseline_and_profile_revision(seeded):
    store, e = seeded
    partial = e['BaselineSnapshot'].model_copy(update={'snapshot_id': uuid4(), 'coverage': 'partial'})
    store.inventory.add(partial)
    with pytest.raises(StorageConflict):
        store.plans.add(e['ActionPlan'].model_copy(update={'plan_id': uuid4(), 'idempotency_key': uuid4(), 'baseline_snapshot_id': partial.snapshot_id}))
    store.profiles.update(e['Profile'].model_copy(update={'revision': 1}), expected_revision=0)
    with pytest.raises(StorageConflict):
        store.plans.add(e['ActionPlan'].model_copy(update={'plan_id': uuid4(), 'idempotency_key': uuid4()}))
    assert len(store.list('ActionPlan')) == 1


def test_deployment_generation_never_inferred_from_planned_transaction(seeded):
    store, e = seeded
    transaction = e['Transaction'].model_copy(update={'transaction_id': uuid4(), 'state': 'planned', 'target_generation': uuid4()})
    store.plans.add(transaction)
    generation = e['DeploymentGeneration'].model_copy(update={'generation_id': transaction.target_generation, 'transaction_id': transaction.transaction_id, 'sequence': 2})
    with pytest.raises(StorageConflict):
        store.plans.add(generation)
    assert len(store.list('DeploymentGeneration')) == 1


def test_nested_dto_mutation_cannot_bypass_write_validation(seeded):
    store, e = seeded
    payload = e['Profile'].model_dump(mode='json')
    payload['profile_id'] = str(uuid4())
    payload['selections'][0]['arbitrary_path'] = 'C:/private'
    forged = Profile.model_construct(**payload)
    with pytest.warns(UserWarning, match='Pydantic serializer warnings'):
        with pytest.raises(ValidationError):
            store.profiles.create(forged)
    assert len(store.list('Profile')) == 1


def test_batch_failure_rolls_back_all_repositories(seeded):
    store, e = seeded
    package = e['ModPackage'].model_copy(update={'package_id': uuid4()})
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction():
            store.inventory.add(package)
            store.inventory.add(e['ModRelease'].model_copy(update={'release_id': uuid4(), 'package_id': uuid4()}))
    assert store.list('ModPackage') == [e['ModPackage']]


def test_json_only_references_are_checked_on_create_and_update(seeded):
    store, e = seeded
    with pytest.raises(KeyError):
        store.profiles.create(e['Profile'].model_copy(update={'profile_id': uuid4(), 'display_order': [uuid4()]}))
    with pytest.raises(KeyError):
        store.profiles.update(e['Profile'].model_copy(update={'revision': 1, 'display_order': [uuid4()]}), expected_revision=0)
    with pytest.raises(KeyError):
        store.plans.add(e['ActionPlan'].model_copy(update={'plan_id': uuid4(), 'idempotency_key': uuid4(), 'evidence_ids': [uuid4()]}))
    assert len(store.list('Profile')) == len(store.list('ActionPlan')) == 1
    assert store.get('Profile', e['Profile'].profile_id).revision == 0
