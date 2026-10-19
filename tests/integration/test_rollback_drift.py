"""CASE-022/034: real committed sources, rollback consumers and guarded drift.

Every source generation below is produced by the product API. The only user
input substituted here is native confirmation; desktop confirmation is covered
by the separate UI route. Owned process kills are not power-loss experiments.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest
import win32api
import win32con
import win32security

from backend.deployments.journal import Journal
from backend.deployments.policy import check_value
from tests.integration.harness.transaction_recovery import (
    ROOTS, TransactionFixture, child_at, database_snapshot, durable_json, finish,
    request, tree,
)
from tests.unit.test_installation_layout import make_installation

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Actual Windows deployment primitives required')


@pytest.fixture
def app(tmp_path_factory, request):
    fixture = TransactionFixture(tmp_path_factory.mktemp('r'))
    durable_json(fixture.root / 'case.json', {'nodeid': request.node.nodeid, 'responsibility': 'PS-03-04'})
    try:
        yield fixture
    finally:
        try:
            fixture.assert_sentinels()
        finally:
            fixture.close()


def history(app):
    value = app.call('deployment.history', {'installation_id': app.installation, 'offset': 0, 'limit': 10})
    assert value['verification_status'] == 'not_run', value
    assert value['current_generation'] == app.status()['current_generation'], value
    for record in value['records']:
        assert record['retention']['protected'] and not record['retention']['cleanup_available'], record
        assert isinstance(record['retention']['refs'], list), record
    durable_json(app.root / 'history-latest.json', value)
    return value


def approve(app, preview):
    assert 'plan' in preview and not preview.get('error_code'), preview
    identity = {key: preview['plan'][key] for key in ('plan_id', 'plan_hash')}
    app.context.confirm_plan = lambda _: True
    try:
        result = app.call('plan.approve', identity)
    finally:
        app.context.confirm_plan = lambda _: False
    assert not result.get('error_code'), result
    return dict(identity, approval_id=(result.get('approval') or {}).get('approval_id'))


def committed(app, payload, *, command='plan.apply'):
    result = app.call(command, payload)
    assert not result.get('error_code') and result['transaction']['state'] == 'committed', result
    status = app.status(payload['plan_id'])
    assert not status['recovery_required'] and status['verification_status'] == 'not_run', status
    transaction = app.context.store.get('Transaction', result['transaction']['transaction_id'])
    generation = app.context.store.get('DeploymentGeneration', transaction.target_generation)
    assert status['current_generation'] == str(generation.generation_id)
    assert generation.transaction_id == transaction.transaction_id
    records = Journal(app.data, transaction.transaction_id).load()
    assert any(record['kind'] == 'committed' for record in records)
    assert records[-1]['sequence'] == transaction.last_sequence
    db = database_snapshot(app.data / 'psmodmanager.sqlite3')
    assert db['quick_check'] == [('ok',)] and db['foreign_key_check'] == []
    return str(transaction.transaction_id), generation


def rollback_preview(app, source, *, generation=None, key=None):
    return app.call('deployment.rollback.create', {'installation_id': app.installation,
        'transaction_id': source, 'expected_generation': generation or app.status()['current_generation'],
        'idempotency_key': key or str(uuid4())})


def rollback(app, source, *, command='deployment.rollback'):
    preview = rollback_preview(app, source)
    assert preview['intent'] == 'rollback' and preview['source_transaction_id'] == source, preview
    payload = approve(app, preview)
    transaction, generation = committed(app, payload, command=command)
    records = history(app)['records']
    record = next(item for item in records if item['transaction_id'] == transaction)
    assert record['kind'] == 'rollback' and record['reverts_transaction'] == source, record
    return preview, payload, transaction, generation


def first_deployment(app, files=None):
    release = app.publish(files or {'Mods/owned.png': b'owned-new'})
    state = app.profile([release])
    preview, payload = app.approved(state)
    before = tree(app.game)
    source, generation = committed(app, payload)
    return state, release, source, generation, before


def adopt(app, paths, name):
    before = tree(app.game)
    app.call('inventory.scan', {'installation_id': app.installation, 'force': True})
    preview = app.call('package.adopt_preview', {'installation_id': app.installation})
    result = app.call('package.adopt', {'snapshot_id': preview['snapshot_id'],
        'manifest_hash': preview['manifest_hash'], 'paths': paths, 'display_name': name})
    assert not result.get('error_code'), result
    assert tree(app.game) == before and result['deployment_generation'] is None
    assert result['release']['release_id'], result
    return result['release']


def original_backups(app, transaction):
    plan = app.context.store.get('ActionPlan', app.context.store.get('Transaction', transaction).plan_id)
    result = []
    for operation in plan.operations:
        if operation.expected_before != 'absent':
            path = app.data / 'deployments/transactions' / transaction / 'backup' / (str(operation.operation_id) + '.bin')
            assert path.is_file(), path
            assert hashlib.sha256(path.read_bytes()).hexdigest() == operation.expected_before
            result.append((path, path.read_bytes()))
    assert result, 'Fixture must exercise a real retained original backup'
    return result


def current_plan(app):
    generation = app.context.store.get('DeploymentGeneration', app.status()['current_generation'])
    transaction = app.context.store.get('Transaction', generation.transaction_id)
    return app.context.store.get('ActionPlan', transaction.plan_id)


def persistent_issue(app, expected_code):
    value = history(app)
    assert value['recovery_required'] and app.status()['recovery_required'], value
    assert expected_code in json.dumps(value['issues']), value
    app.reopen()
    again = history(app)
    assert again['recovery_required'] and expected_code in json.dumps(again['issues']), again
    return again


def conflict_copies(app, content):
    copies = [path for path in (app.data / 'deployments').rglob('*.bin')
              if 'conflicts' in path.parts and path.read_bytes() == content]
    assert copies, 'Product must preserve the actual external bytes outside the scan trees'
    assert all(path.resolve().is_relative_to(app.data.resolve()) for path in copies)
    assert not any(path.resolve().is_relative_to(app.game.resolve()) for path in copies)
    return copies


def nofollow_tree(root):
    """Record the link object, never enumerate its external target."""
    result = {}
    def visit(directory):
        for entry in os.scandir(directory):
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            information = entry.stat(follow_symlinks=False)
            if getattr(information, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                result[relative] = {'kind': 'reparse', 'identity': information.st_ino}
            elif entry.is_dir(follow_symlinks=False):
                result[relative] = '<directory>'; visit(path)
            else:
                result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    visit(Path(root))
    return result


def test_case022_first_adopted_multi_file_rollback_restores_original_without_baseline_generation(app):
    _, preview, payload, before, after = app.mixed()
    assert preview['plan']['base_generation'] is None
    assert not app.context.store.list('DeploymentGeneration')
    source, _ = committed(app, payload)
    assert tree(app.game) == after
    backups = original_backups(app, source)
    rollback(app, source)
    assert tree(app.game) == before
    generations = sorted(app.context.store.list('DeploymentGeneration'), key=lambda item: item.sequence)
    assert [item.sequence for item in generations] == [1, 2]
    first_transaction = app.context.store.get('Transaction', generations[0].transaction_id)
    assert app.context.store.get('ActionPlan', first_transaction.plan_id).base_generation is None, 'No fabricated baseline generation'
    assert len(app.context.store.list('Transaction')) == 2
    assert all(path.read_bytes() == content for path, content in backups)
    latest = current_plan(app)
    owners = json.loads(check_value(latest, 'owned_after'))
    assert {item['relative_path'] for item in owners} == {'replace.png', 'remove.png', 'shared.png'}
    app.evidence('case022-adopted-first-rollback')


def test_case022_shared_owners_exit_across_real_metadata_generation_and_last_owner_removal(app):
    a = app.publish({'Mods/shared.png': b'shared', 'Mods/a.png': b'a'}, 'Owner A')
    b = app.publish({'Mods/shared.png': b'shared', 'Mods/b.png': b'b'}, 'Owner B')
    state = app.profile([a]); _, a_payload = app.approved(state)
    source_a, _ = committed(app, a_payload)
    state = app.save(app.select(state, b))
    preview_b, b_payload = app.approved(state)
    assert not preview_b['no_op'], 'Owner changes require durable source metadata'
    source_b, _ = committed(app, b_payload)
    rollback(app, source_a)
    assert not (app.game / 'Mods/a.png').exists()
    assert (app.game / 'Mods/shared.png').read_bytes() == b'shared'
    assert (app.game / 'Mods/b.png').read_bytes() == b'b'
    owned = json.loads(check_value(current_plan(app), 'owned_after'))
    shared = next(item for item in owned if item['relative_path'] == 'shared.png')
    assert shared['owner_ids'] == [b['release_id']]
    rollback(app, source_b)
    assert not (app.game / 'Mods/shared.png').exists()
    assert not (app.game / 'Mods/b.png').exists()
    assert [item.sequence for item in sorted(app.context.store.list('DeploymentGeneration'), key=lambda item: item.sequence)] == [1, 2, 3, 4]
    app.evidence('case022-shared-exit-and-last-owner')


def test_case022_same_bytes_owner_only_addition_is_a_generation_and_rollback_keeps_other_owner(app):
    a = app.publish({'Mods/shared.png': b'shared'}, 'A')
    b = app.publish({'Mods/shared.png': b'shared'}, 'B')
    state = app.profile([a]); _, payload = app.approved(state); committed(app, payload)
    state = app.save(app.select(state, b)); preview, payload = app.approved(state)
    assert not preview['no_op'], preview
    source, _ = committed(app, payload)
    rollback(app, source)
    assert (app.game / 'Mods/shared.png').read_bytes() == b'shared'
    owned = json.loads(check_value(current_plan(app), 'owned_after'))
    assert owned[0]['owner_ids'] == [a['release_id']]
    assert len(app.context.store.list('DeploymentGeneration')) == 3
    app.evidence('case022-owner-only-real-generation')


def test_case022_unknown_config_and_mutable_data_survive_multi_file_rollback(app):
    (app.game / 'Mods/player.cfg').write_bytes(b'player-config')
    state, release, source, _, before = first_deployment(app, {'Mods/a.png': b'a', 'UserLibs/b.png': b'b'})
    (app.game / 'Plugins/unknown.png').write_bytes(b'external-new')
    unknown = tree(app.game)
    rollback(app, source)
    assert (app.game / 'Mods/player.cfg').read_bytes() == b'player-config'
    assert (app.game / 'Plugins/unknown.png').read_bytes() == b'external-new'
    assert not (app.game / 'Mods/a.png').exists() and not (app.game / 'UserLibs/b.png').exists()
    assert tree(app.game) == dict(before, **{'Plugins/unknown.png': unknown['Plugins/unknown.png']})
    app.evidence('case022-unknown-config-mutable-preserved')


def test_case022_actual_profile_disable_retains_shared_other_owner_and_removes_only_last_owner(app):
    a = app.publish({'Mods/shared.png': b'shared', 'Mods/a.png': b'a'}, 'A')
    b = app.publish({'Mods/shared.png': b'shared', 'Mods/b.png': b'b'}, 'B')
    (app.game / 'Mods/player.cfg').write_bytes(b'config-kept')
    state = app.profile([a, b]); _, payload = app.approved(state); committed(app, payload)
    state = app.save(app.select(state, a, False)); preview, payload = app.approved(state)
    assert not preview['no_op'], preview
    committed(app, payload)
    assert not (app.game / 'Mods/a.png').exists()
    assert (app.game / 'Mods/shared.png').read_bytes() == b'shared'
    assert (app.game / 'Mods/b.png').read_bytes() == b'b'
    owned = json.loads(check_value(current_plan(app), 'owned_after'))
    assert next(item for item in owned if item['relative_path'] == 'shared.png')['owner_ids'] == [b['release_id']]
    state = app.save(app.select(state, b, False)); _, payload = app.approved(state); committed(app, payload)
    assert not (app.game / 'Mods/shared.png').exists() and not (app.game / 'Mods/b.png').exists()
    assert (app.game / 'Mods/player.cfg').read_bytes() == b'config-kept'
    assert len(app.context.store.list('DeploymentGeneration')) == 3
    app.evidence('case022-actual-profile-disable')


def test_case022_new_explicit_adoption_after_source_generation_survives_rollback_of_unrelated_source(app):
    _, _, source, _, _ = first_deployment(app)
    (app.game / 'Mods/later.png').write_bytes(b'later-user-owned')
    late = adopt(app, ['Mods/later.png'], 'Later explicitly adopted file')
    assert len(app.context.store.list('DeploymentGeneration')) == 1
    rollback(app, source)
    assert not (app.game / 'Mods/owned.png').exists()
    assert (app.game / 'Mods/later.png').read_bytes() == b'later-user-owned'
    owned = json.loads(check_value(current_plan(app), 'owned_after'))
    assert len(owned) == 1 and owned[0]['relative_path'] == 'later.png'
    assert owned[0]['owner_ids'] == [late['release_id']]
    app.evidence('case022-later-independent-adoption')


def test_case022_new_same_hash_explicit_owner_after_source_generation_keeps_shared_target_on_rollback(app):
    _, old, source, _, _ = first_deployment(app)
    late = adopt(app, ['Mods/owned.png'], 'Later explicit shared owner')
    assert late['release_id'] != old['release_id']
    assert len(app.context.store.list('DeploymentGeneration')) == 1
    rollback(app, source)
    assert (app.game / 'Mods/owned.png').read_bytes() == b'owned-new'
    owned = json.loads(check_value(current_plan(app), 'owned_after'))
    assert len(owned) == 1 and owned[0]['relative_path'] == 'owned.png'
    assert owned[0]['owner_ids'] == [late['release_id']]
    assert len(app.context.store.list('DeploymentGeneration')) == 2
    app.evidence('case022-later-shared-adoption')


def test_case034_normal_replay_idempotent_creation_and_apply_has_once_only_disk_effect(app):
    _, _, source, _, before = first_deployment(app)
    key = str(uuid4()); current = app.status()['current_generation']
    preview = rollback_preview(app, source, generation=current, key=key)
    again = rollback_preview(app, source, generation=current, key=key)
    assert again['plan'] == preview['plan'], (again, preview)
    payload = approve(app, preview)
    reverted, generation = committed(app, payload, command='deployment.rollback')
    assert tree(app.game) == before
    disk = {p.relative_to(app.game).as_posix(): (p.stat().st_ino, p.stat().st_mtime_ns)
            for p in app.game.rglob('*') if p.is_file()}
    for command in ('deployment.rollback', 'plan.apply', 'assistant.apply_plan'):
        result = app.call(command, payload)
        assert result.get('idempotent') and result['transaction']['transaction_id'] == reverted, result
        assert tree(app.game) == before and len(app.context.store.list('DeploymentGeneration')) == 2
        assert {p.relative_to(app.game).as_posix(): (p.stat().st_ino, p.stat().st_mtime_ns)
                for p in app.game.rglob('*') if p.is_file()} == disk
    assert history(app)['current_generation'] == str(generation.generation_id)
    app.reopen(); assert tree(app.game) == before and not app.status()['recovery_required']
    app.evidence('case034-once-only-replay')


def test_case034_rollback_itself_can_be_rolled_back_through_the_same_executor(app):
    _, _, source, _, before = first_deployment(app)
    deployed = tree(app.game)
    _, _, reverted, _ = rollback(app, source)
    assert tree(app.game) == before
    _, _, reintroduced, _ = rollback(app, reverted, command='assistant.apply_plan')
    assert tree(app.game) == deployed
    assert len(app.context.store.list('DeploymentGeneration')) == 3
    record = next(item for item in history(app)['records'] if item['transaction_id'] == reintroduced)
    assert record['reverts_transaction'] == reverted
    app.evidence('case034-rollback-of-rollback')


def test_case034_later_formal_hash_is_superseded_not_player_drift_and_latest_rollback_restores_previous(app):
    state, a, source_a, _, _ = first_deployment(app, {'Mods/owned.png': b'v1'})
    b = app.publish({'Mods/owned.png': b'v2'}, 'Formal v2')
    state = app.select(state, a, False); state = app.save(app.select(state, b))
    _, payload = app.approved(state); source_b, _ = committed(app, payload)
    after = tree(app.game)
    refused = rollback_preview(app, source_a)
    assert refused.get('error_code') == 'ROLLBACK_SUPERSEDED', refused
    assert tree(app.game) == after and not app.status()['recovery_required']
    record = next(item for item in history(app)['records'] if item['transaction_id'] == source_a)
    assert not record['rollback_allowed'] and 'ROLLBACK_SUPERSEDED' in json.dumps(record['rollback_blockers'])
    rollback(app, source_b)
    assert (app.game / 'Mods/owned.png').read_bytes() == b'v1'
    app.evidence('case034-formal-later-generation')


def test_case034_player_edit_is_copied_preserved_persistent_and_recheck_only_reads_game(app):
    _, _, source, _, _ = first_deployment(app)
    target = app.game / 'Mods/owned.png'
    target.write_bytes(b'player-edit')
    changed = tree(app.game); count = len(app.context.store.list('DeploymentGeneration'))
    refused = rollback_preview(app, source)
    assert refused.get('error_code') == 'ROLLBACK_DRIFT', refused
    copies = conflict_copies(app, b'player-edit')
    persistent_issue(app, 'ROLLBACK_DRIFT')
    unresolved = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert unresolved['remaining_count'] > 0 and unresolved['recovery_required'], unresolved
    assert tree(app.game) == changed and len(app.context.store.list('DeploymentGeneration')) == count
    target.write_bytes(b'owned-new')  # Explicit controlled user restoration, never product overwrite.
    restored = tree(app.game)
    app.reopen()
    assert app.status()['recovery_required'], 'Cold startup must retain an unresolved authenticated history issue'
    recovery = app.call('deployment.recover', {'installation_id': app.installation})
    assert recovery['recovery_required'], 'Generic recovery must not replace the explicit conflict recheck'
    assert tree(app.game) == restored and len(app.context.store.list('DeploymentGeneration')) == count
    resolved = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert resolved['resolved_count'] > 0 and resolved['remaining_count'] == 0 and not resolved['recovery_required'], resolved
    assert tree(app.game) == restored and all(path.read_bytes() == b'player-edit' for path in copies)
    assert len(app.context.store.list('DeploymentGeneration')) == count
    rollback(app, source)
    assert not target.exists()
    app.evidence('case034-player-drift-preservation-recheck')


def test_case034_player_drift_of_other_current_owned_file_is_preserved_before_rollback_of_earlier_source(app):
    state, _, source, _, _ = first_deployment(app, {'Mods/a.png': b'a'})
    b = app.publish({'Mods/b.png': b'b'}, 'Later B')
    state = app.save(app.select(state, b)); _, payload = app.approved(state); committed(app, payload)
    (app.game / 'Mods/b.png').write_bytes(b'player-edited-later-b')
    changed = tree(app.game)
    refused = rollback_preview(app, source)
    assert refused.get('error_code') == 'ROLLBACK_DRIFT', refused
    copies = conflict_copies(app, b'player-edited-later-b')
    persistent_issue(app, 'ROLLBACK_DRIFT')
    assert tree(app.game) == changed and (app.game / 'Mods/a.png').read_bytes() == b'a'
    assert all(path.read_bytes() == b'player-edited-later-b' for path in copies)
    assert len(app.context.store.list('DeploymentGeneration')) == 2
    app.evidence('case034-drift-other-current-owner')


def test_case034_recheck_cannot_resolve_when_actual_preserved_conflict_copy_is_corrupt(app):
    _, _, source, _, _ = first_deployment(app)
    target = app.game / 'Mods/owned.png'
    target.write_bytes(b'player-edit')
    assert rollback_preview(app, source).get('error_code') == 'ROLLBACK_DRIFT'
    copies = conflict_copies(app, b'player-edit')
    target.write_bytes(b'owned-new')
    copies[0].write_bytes(b'damaged-preservation')
    before = tree(app.game)
    unresolved = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert unresolved['remaining_count'] > 0 and unresolved['recovery_required'], unresolved
    assert tree(app.game) == before and len(app.context.store.list('DeploymentGeneration')) == 1
    copies[0].write_bytes(b'player-edit')
    resolved = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert not resolved['recovery_required'] and resolved['remaining_count'] == 0, resolved
    assert tree(app.game) == before
    app.evidence('case034-conflict-copy-recheck-integrity')


@pytest.mark.parametrize('occupant', ['foreign_file', 'directory'])
def test_case034_removed_original_path_occupied_by_foreign_object_is_never_overwritten(app, occupant):
    _, _, payload, _, _ = app.mixed(); source, _ = committed(app, payload)
    target = app.game / 'Mods/remove.png'
    if occupant == 'foreign_file':
        target.write_bytes(b'foreign-occupant')
    else:
        target.mkdir(); (target / 'keep.txt').write_bytes(b'foreign-child')
    changed = tree(app.game)
    refused = rollback_preview(app, source)
    assert refused.get('error_code') == 'ROLLBACK_DRIFT', refused
    if occupant == 'foreign_file':
        conflict_copies(app, b'foreign-occupant')
    else:
        assert (target / 'keep.txt').read_bytes() == b'foreign-child'
    persistent_issue(app, 'ROLLBACK_DRIFT')
    assert tree(app.game) == changed
    assert len(app.context.store.list('DeploymentGeneration')) == 1
    app.evidence('case034-occupied-' + occupant)


def test_case034_actual_junction_occupant_is_not_followed_and_external_tree_is_untouched(app):
    _, _, payload, _, _ = app.mixed(); source, _ = committed(app, payload)
    target = app.game / 'Mods/remove.png'
    outside = app.root / 'outside'
    outside.mkdir(); (outside / 'keep.bin').write_bytes(b'outside-junction-never-touched')
    control = subprocess.run(['cmd', '/c', 'mklink', '/J', str(target), str(outside)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    durable_json(app.root / 'junction-control.json', {'exit_code': control.returncode,
        'stdout': control.stdout, 'stderr': control.stderr, 'scope': 'owned fixture only; no symlink privilege'})
    assert control.returncode == 0, control.stderr
    assert target.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    before, external_before = nofollow_tree(app.game), tree(outside)
    refused = rollback_preview(app, source)
    assert refused.get('error_code') in {'GUARD_PATH_LINK', 'ROLLBACK_DRIFT'}, refused
    assert nofollow_tree(app.game) == before and tree(outside) == external_before
    values = history(app)
    record = next(item for item in values['records'] if item['transaction_id'] == source)
    assert not record['rollback_allowed'] and record['rollback_blockers'], record
    rechecked = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert rechecked.get('error_code') == 'GUARD_PATH_LINK' or rechecked['remaining_count'] > 0, rechecked
    assert nofollow_tree(app.game) == before and tree(outside) == external_before
    durable_json(app.root / 'case034-junction-nofollow.json', {'before': before,
        'after': nofollow_tree(app.game), 'external_before': external_before, 'external_after': tree(outside),
        'preview': refused, 'history': values, 'recheck': rechecked})


def test_case034_actual_junction_in_other_current_owner_is_not_followed_and_keeps_persistent_issue(app):
    state, _, source, _, _ = first_deployment(app, {'Mods/a.png': b'a'})
    b = app.publish({'Mods/b.png': b'b'}, 'Later B')
    state = app.save(app.select(state, b)); _, payload = app.approved(state); committed(app, payload)
    target = app.game / 'Mods/b.png'
    target.unlink()
    outside = app.root / 'outside'
    outside.mkdir(); (outside / 'keep.bin').write_bytes(b'non-source-junction-never-touched')
    control = subprocess.run(['cmd', '/c', 'mklink', '/J', str(target), str(outside)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    durable_json(app.root / 'junction-control.json', {'exit_code': control.returncode,
        'stdout': control.stdout, 'stderr': control.stderr, 'scope': 'owned non-source fixture only'})
    assert control.returncode == 0, control.stderr
    before, external_before = nofollow_tree(app.game), tree(outside)
    refused = rollback_preview(app, source)
    assert refused.get('error_code') in {'ROLLBACK_DRIFT', 'GUARD_PATH_LINK'}, refused
    assert app.status()['recovery_required'], app.status()
    app.reopen()
    assert app.status()['recovery_required'], 'Non-source reparse drift must survive cold startup'
    rechecked = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert rechecked['recovery_required'] and rechecked['remaining_count'] > 0, rechecked
    assert nofollow_tree(app.game) == before and tree(outside) == external_before
    assert (app.game / 'Mods/a.png').read_bytes() == b'a'
    assert len(app.context.store.list('DeploymentGeneration')) == 2
    durable_json(app.root / 'case034-non-source-junction.json', {'game_before': before,
        'game_after': nofollow_tree(app.game), 'outside_before': external_before,
        'outside_after': tree(outside), 'result': refused, 'recheck': rechecked})


def test_case034_actual_private_directory_dacl_denies_first_issue_header_but_persistent_block_survives_restart(app):
    _, _, payload, _, _ = app.mixed(); source, _ = committed(app, payload)
    backup, original = original_backups(app, source)[0]
    backup.write_bytes(b'corrupt-original-before-header')
    before = tree(app.game)
    directory = app.data / 'deployments/history'
    directory.mkdir(parents=True, exist_ok=True)
    assert not (directory / 'issues').exists(), 'Exercise the first durable issue header creation'
    original_security = win32security.GetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()
    # Deny FILE_ADD_SUBDIRECTORY while allowing read/list and WRITE_DAC. This
    # affects only this owned directory and does not enable system privileges.
    denied = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        'D:(D;;0x00000004;;;' + sid + ')(A;;FA;;;' + sid + ')', win32security.SDDL_REVISION_1)
    try:
        win32security.SetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION, denied)
        listed = list(os.scandir(directory))  # Read/list still succeeds under the actual DACL.
        with pytest.raises(PermissionError) as control:
            (directory / 'denied-control').mkdir()
        assert control.value.winerror == 5
        refused = rollback_preview(app, source)
        assert refused.get('error_code'), refused
        assert app.status()['recovery_required'], app.status()
        assert app.context.store.get('Transaction', source).state == 'recovery_required'
        durable_json(app.root / 'issue-first-header-real-dacl.json', {'control_native_error': 5,
            'layer': 'actual owned-directory FILE_ADD_SUBDIRECTORY DACL', 'preview': refused,
            'readable_entries': [entry.name for entry in listed],
            'issues_created': (directory / 'issues').exists(), 'game_unchanged': tree(app.game) == before})
    finally:
        win32security.SetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION, original_security)
    app.reopen()
    assert app.status()['recovery_required'], 'Failed first MAC header cannot silently clear on startup'
    generic = app.call('deployment.recover', {'installation_id': app.installation})
    assert generic['recovery_required'], generic
    assert tree(app.game) == before and len(app.context.store.list('DeploymentGeneration')) == 1
    backup.write_bytes(original)
    explicit = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert not explicit['recovery_required'] and explicit['remaining_count'] == 0, explicit
    assert tree(app.game) == before and not app.status()['recovery_required']
    app.evidence('case034-first-header-write-real-dacl')


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_case034_lost_or_corrupt_retained_backup_never_reports_success_and_recheck_requires_repair(app, damage):
    _, _, payload, _, _ = app.mixed(); source, _ = committed(app, payload)
    path, original = original_backups(app, source)[0]
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'corrupt-original-backup')
    changed = tree(app.game)
    code = 'ROLLBACK_BACKUP_MISSING' if damage == 'missing' else 'ROLLBACK_BACKUP_CORRUPT'
    refused = rollback_preview(app, source)
    assert refused.get('error_code') == code, refused
    persistent_issue(app, code)
    unresolved = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert unresolved['remaining_count'] > 0 and unresolved['recovery_required'], unresolved
    assert tree(app.game) == changed and len(app.context.store.list('DeploymentGeneration')) == 1
    path.write_bytes(original)
    repaired = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert repaired['remaining_count'] == 0 and not repaired['recovery_required'], repaired
    assert tree(app.game) == changed
    rollback(app, source)
    app.evidence('case034-backup-' + damage)


@pytest.mark.parametrize('drift', ['disk_bytes', 'backup_bytes', 'backup_identity'])
def test_case034_approved_rollback_rechecks_disk_and_backup_bytes_and_physical_identity(app, drift):
    _, _, payload, _, _ = app.mixed(); source, _ = committed(app, payload)
    preview = rollback_preview(app, source); approved = approve(app, preview)
    if drift == 'disk_bytes':
        (app.game / 'Mods/replace.png').write_bytes(b'late-player-edit')
    else:
        path, original = original_backups(app, source)[0]
        if drift == 'backup_identity':
            temporary = path.with_suffix('.foreign')
            temporary.write_bytes(original); path.unlink(); temporary.rename(path)
        else:
            path.write_bytes(b'late-backup-corruption')
    changed = tree(app.game)
    refused = app.call('deployment.rollback', approved)
    assert refused.get('error_code'), refused
    assert tree(app.game) == changed and len(app.context.store.list('DeploymentGeneration')) == 1
    if drift == 'disk_bytes':
        conflict_copies(app, b'late-player-edit')
    assert app.status()['recovery_required'], app.status()
    app.evidence('case034-approved-revalidation-' + drift)


def test_case034_stale_generation_wrong_plan_hash_and_unknown_source_have_zero_game_effect(app):
    _, _, source, generation, _ = first_deployment(app)
    changed = tree(app.game)
    missing = rollback_preview(app, str(uuid4()))
    assert missing.get('error_code'), missing
    stale = rollback_preview(app, source, generation=str(uuid4()))
    assert stale.get('error_code'), stale
    preview = rollback_preview(app, source); payload = approve(app, preview)
    wrong = dict(payload, plan_hash='0' * 64)
    refused = app.call('deployment.rollback', wrong)
    assert refused.get('error_code') == 'PLAN_HASH_MISMATCH', refused
    assert tree(app.game) == changed and len(app.context.store.list('DeploymentGeneration')) == 1
    assert app.status()['current_generation'] == str(generation.generation_id)
    assert not app.status()['recovery_required']
    app.evidence('case034-source-generation-hash-binding')


def test_case034_source_from_another_registered_installation_cannot_be_replayed(app):
    _, _, source, _, _ = first_deployment(app)
    other = make_installation(app.root / 'other-game')
    for name in (*ROOTS.values(), 'UserData'):
        (other / name).mkdir()
    (other / 'UserData/keep.bin').write_bytes(b'other-installation-userdata')
    app.context.choose_installation_folder = lambda _: str(other)
    grant = app.call('installation.choose', {'kind': 'manual'})
    found = app.call('installation.discover', {'source_handles': [grant['source_handle']]})
    registered = app.call('installation.select', {'candidate_id': found['candidates'][0]['candidate_id']})
    other_id = registered['installation']['installation_id']
    assert other_id != app.installation
    before, other_before = tree(app.game), tree(other)
    refused = app.call('deployment.rollback.create', {'installation_id': other_id,
        'transaction_id': source, 'expected_generation': app.status()['current_generation'],
        'idempotency_key': str(uuid4())})
    assert refused.get('error_code'), refused
    assert tree(app.game) == before and tree(other) == other_before
    assert len(app.context.store.list('DeploymentGeneration')) == 1
    app.evidence('case034-other-installation-source')


def test_case034_pending_actual_transaction_history_is_visible_but_cannot_be_a_rollback_source(app):
    _, _, payload, before, _ = app.mixed()
    reached, release = threading.Event(), threading.Event()
    def pause(actual, details):
        if actual == 'operation.intent' and not reached.is_set():
            reached.set(); assert release.wait(20), 'Owned pending-history pause timed out'
    app.context.deployments.fault = pause
    response = app.context.api.dispatch(request('plan.apply', payload))
    assert response['ok'], response
    try:
        assert reached.wait(20)
        values = history(app)
        pending = next(item for item in values['records'] if item['state'] == 'applying')
        assert pending['generation_id'] is None and pending['sequence'] is None
        assert not pending['rollback_allowed'] and pending['rollback_blockers'], pending
        cancelled = app.context.api.dispatch(request('task.cancel', {'task_id': response['data']['task_id']}))
        assert cancelled['ok'], cancelled
    finally:
        release.set()
    terminal = finish(app.context.api, response['data'])
    app.context.deployments.fault = None
    assert terminal['state'] in {'cancelled', 'succeeded'}, terminal
    assert tree(app.game) == before and not app.context.store.list('DeploymentGeneration')
    assert not app.status()['recovery_required']
    rejected = rollback_preview(app, pending['transaction_id'], generation=str(uuid4()))
    assert rejected.get('error_code'), rejected
    app.evidence('case034-pending-history-no-rollback')


@pytest.mark.parametrize('point', ['operation.published', 'commit.intent'])
def test_case034_real_job_cancel_of_rollback_restores_current_deployment_without_new_generation(app, point):
    _, _, payload, _, _ = app.mixed(); source, _ = committed(app, payload)
    current = tree(app.game)
    preview = rollback_preview(app, source); rollback_payload = approve(app, preview)
    reached, release = threading.Event(), threading.Event()
    control = dict(point=point, boundary_wait_seconds=60, release_wait_seconds=90,
        task_snapshots=[], before_game=current,
        guard_before=app.context.api.dispatch(request('deployment.guard_status', {'installation_id': app.installation})))
    started = time.monotonic()
    def pause(actual, details):
        if actual == point and not reached.is_set():
            control['boundary'] = dict(point=actual, details=details,
                                       elapsed_seconds=time.monotonic()-started)
            reached.set()
            assert release.wait(90), 'Owned rollback cancellation control timed out'
    previous_fault = app.context.deployments.fault
    app.context.deployments.fault = pause
    response = terminal = None
    try:
        response = app.context.api.dispatch(request('deployment.rollback', rollback_payload))
        control['apply_response'] = response
        assert response['ok'], response
        deadline = started + 60
        previous_sequence = None
        while not reached.is_set() and time.monotonic() < deadline:
            observed = app.context.api.dispatch(request('task.snapshot', {'task_id': response['data']['task_id']}))
            assert observed['ok'], observed
            snapshot = observed['data']
            if snapshot['sequence'] != previous_sequence:
                control['task_snapshots'].append(dict(elapsed_seconds=time.monotonic()-started, snapshot=snapshot))
                previous_sequence = snapshot['sequence']
            if snapshot['state'] in {'succeeded', 'failed', 'cancelled'}:
                control['early_terminal'] = snapshot
                raise AssertionError('Rollback ended before '+point+': '+json.dumps(snapshot, ensure_ascii=False))
            reached.wait(min(.05, max(0, deadline-time.monotonic())))
        assert reached.is_set(), dict(message='Product did not reach rollback cancellation boundary',
            point=point, elapsed_seconds=time.monotonic()-started,
            latest_actual_snapshot=control['task_snapshots'][-1] if control['task_snapshots'] else None)
        control['cancel_response'] = app.context.api.dispatch(request('task.cancel', {'task_id': response['data']['task_id']}))
        assert control['cancel_response']['ok'], control['cancel_response']
    except BaseException as exc:
        control['failure'] = dict(type=type(exc).__name__, message=str(exc), elapsed_seconds=time.monotonic()-started)
        raise
    finally:
        release.set()
        try:
            if response and response.get('ok') and isinstance(response.get('data'), dict) and 'task_id' in response['data']:
                if control.get('failure') and not control.get('early_terminal'):
                    control['cleanup_cancel_response'] = app.context.api.dispatch(request('task.cancel',
                        {'task_id': response['data']['task_id']}))
                # Drain this exact job even when the control assertion fails;
                # context.close must not race its rollback/recovery worker.
                terminal = finish(app.context.api, response['data'], timeout=120)
                control['terminal'] = terminal
                control['finished_elapsed_seconds'] = time.monotonic()-started
        except BaseException as exc:
            control['finish_failure'] = dict(type=type(exc).__name__, message=str(exc))
            raise
        finally:
            app.context.deployments.fault = previous_fault
            control['guard_after'] = app.context.api.dispatch(request('deployment.guard_status', {'installation_id': app.installation}))
            control['status_after'] = app.context.api.dispatch(request('deployment.status',
                {'installation_id': app.installation, 'plan_id': rollback_payload['plan_id']}))
            control['after_game'] = tree(app.game)
            durable_json(app.root / ('cancel-control-'+point.replace('.', '-')+'.json'), control)
    assert terminal['state'] in {'cancelled', 'succeeded'}, terminal
    status = app.status(rollback_payload['plan_id'])
    assert status['transaction']['state'] == 'rolled_back' and not status['recovery_required'], status
    assert tree(app.game) == current and len(app.context.store.list('DeploymentGeneration')) == 1
    app.reopen(); assert tree(app.game) == current and not app.status()['recovery_required']
    app.evidence('case034-cancel-rollback-' + point.replace('.', '-'))


@pytest.mark.parametrize('point', ['operation.intent', 'operation.published', 'manifest.published', 'db.committed'])
def test_case034_actual_owned_process_kill_during_rollback_reopens_to_a_whole_verified_generation(app, point):
    _, _, payload, original, _ = app.mixed(); source, _ = committed(app, payload)
    current = tree(app.game)
    preview = rollback_preview(app, source); rollback_payload = approve(app, preview)
    with child_at(app, rollback_payload, point) as (process, boundary):
        assert boundary['details']['transaction_id'] != source
        process.kill(); process.wait(timeout=10)
    status = app.status()
    assert not status['recovery_required'] and status['verification_status'] == 'not_run', status
    expected = original if point in {'manifest.published', 'db.committed'} else current
    assert tree(app.game) == expected
    generations = app.context.store.list('DeploymentGeneration')
    assert len(generations) == (2 if expected == original else 1)
    values = history(app)
    for record in values['records']:
        if record['state'] != 'committed':
            assert not record['rollback_allowed']
    app.evidence('case034-owned-process-kill-' + point.replace('.', '-'))


def test_case034_history_retention_references_protect_sources_and_no_fake_cleanup_endpoint(app):
    _, _, payload, _, _ = app.mixed(); source, _ = committed(app, payload)
    backups = original_backups(app, source)
    rollback(app, source)
    values = history(app)
    original = next(item for item in values['records'] if item['transaction_id'] == source)
    assert original['retention']['refs'], original
    assert all(path.read_bytes() == content for path, content in backups)
    response = app.context.api.dispatch(request('deployment.history.cleanup', {'installation_id': app.installation}))
    assert not response['ok'], response
    assert all(path.read_bytes() == content for path, content in backups)
    app.evidence('case034-protected-history-no-cleanup')
