"""Ordinary durable executor regressions using owned synthetic files only.

Every plan, approval, transaction and generation comes from the real product
API. Fault callbacks alter only fixture-owned file objects or stop a bounded
transaction; they never execute payloads or substitute execution/recovery.
"""
import errno
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.deployments.journal import Journal, canonical
from backend.deployments.paths import GuardRejected, ROOTS, _information, file_identity, open_handle
from tests.integration.harness.transaction_recovery import (
    TransactionFixture, database_snapshot, durable_json, rpc, tree,
)


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Requires actual Windows file identities')


@pytest.fixture
def app(tmp_path_factory, request):
    fixture = TransactionFixture(tmp_path_factory.mktemp('c'))
    durable_json(fixture.root / 'case.json', {'nodeid': request.node.nodeid})
    try:
        yield fixture
    finally:
        try:
            fixture.evidence('final')
            fixture.assert_sentinels()
        finally:
            fixture.close()


def identity(path):
    # Fixture private leaves are opened directly for observation: applying the
    # external-input component budget to a deeply nested evidence path would
    # stop this callback before the intended owned-object replacement.
    handle = open_handle(path)
    try:
        return file_identity(_information(handle, False))
    finally:
        handle.Close()


def file_facts(root):
    return {p.relative_to(root).as_posix(): (identity(p), p.stat().st_mtime_ns)
            for p in Path(root).rglob('*') if p.is_file()}


def replace_owned_object(app, path):
    """Keep bytes, but replace an exact existing object within this fixture."""
    path = Path(path).resolve(strict=True)
    assert path.is_relative_to(app.root.resolve(strict=True)) and path.is_file()
    contents, old_identity = path.read_bytes(), identity(path)
    temporary = path.with_name('swap-' + uuid4().hex[:8] + '.bin')
    with temporary.open('xb') as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    new_identity = identity(path)
    assert path.read_bytes() == contents and new_identity != old_identity
    return {'path': str(path), 'old_identity': old_identity, 'new_identity': new_identity}


def entities(app):
    snapshot = database_snapshot(app.data / 'psmodmanager.sqlite3')
    assert snapshot['quick_check'] == [('ok',)] and snapshot['foreign_key_check'] == []
    return snapshot['entities']


def test_header_rechecks_target_changed_after_approval_before_any_game_write(app, monkeypatch):
    _, _, payload, _, _ = app.mixed()
    original = app.context.deployments._new_header
    observed = {}

    def changed_input_then_original(*args, **kwargs):
        target = app.game / 'Mods/replace.png'
        stamp = target.stat()
        target.write_bytes(b'bad-replace')
        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert target.stat().st_size == stamp.st_size
        observed.update(game=tree(app.game), files=file_facts(app.game))
        # The original product header builder remains the authority.
        return original(*args, **kwargs)

    boundaries = []
    monkeypatch.setattr(app.context.deployments, '_new_header', changed_input_then_original)
    app.context.deployments.fault = lambda point, details: boundaries.append(point)
    result = app.call('plan.apply', payload)
    durable_json(app.root / 'header-check.json', {'result': result, 'boundaries': boundaries, 'observed': observed})
    assert observed, 'Apply did not reach the intended original header boundary'
    assert result.get('error_code') == 'PLAN_DISK_CHANGED', result
    assert tree(app.game) == observed['game'] and file_facts(app.game) == observed['files']
    assert boundaries == [] and entities(app)['deployment_generations'] == []


def test_changed_stage_identity_with_same_bytes_cannot_be_published(app):
    _, _, payload, before, _ = app.mixed()
    plan = app.context.store.get('ActionPlan', payload['plan_id'])
    operation = next(op for op in plan.operations if op.desired_after != 'absent')
    observed = {}

    def replace_stage(point, details):
        if point == 'transaction.ready':
            stage = app.data / 'deployments/transactions' / details['transaction_id'] / 'stage' / (str(operation.operation_id) + '.bin')
            observed.update(replace_owned_object(app, stage))

    app.context.deployments.fault = replace_stage
    result = app.call('plan.apply', payload)
    app.evidence('stage-result')
    durable_json(app.root / 'stage-check.json', {'result': result, 'replacement': observed})
    assert observed, ('Apply did not replace the staged object at ready', result)
    assert result.get('error_code') == 'EXEC_STAGE_INVALID', result
    assert tree(app.game) == before
    assert entities(app)['deployment_generations'] == []
    status = app.status(payload['plan_id'])
    assert status['transaction']['state'] == 'rolled_back' and not status['recovery_required'], status


def test_changed_published_identity_with_same_bytes_is_preserved_pending_recovery(app):
    _, _, payload, _, _ = app.mixed()
    plan = app.context.store.get('ActionPlan', payload['plan_id'])
    operations = {str(op.operation_id): op for op in plan.operations}
    observed = {}

    def replace_published(point, details):
        if point == 'operation.published' and not observed:
            operation = operations[details['operation_id']]
            target = app.game / ROOTS[operation.target_root_kind] / operation.relative_path
            observed.update(replace_owned_object(app, target))
            observed['game'] = tree(app.game)

    app.context.deployments.fault = replace_published
    result = app.call('plan.apply', payload)
    app.evidence('published-result')
    durable_json(app.root / 'published-check.json', {'result': result, 'replacement': observed})
    assert observed, 'Apply did not replace the published object'
    assert result.get('error_code') == 'EXEC_TARGET_MISMATCH', result
    assert result['transaction']['state'] == 'recovery_required'
    assert tree(app.game) == observed['game'] and identity(observed['path']) == observed['new_identity']
    assert entities(app)['deployment_generations'] == []
    assert app.status()['recovery_required']
    retry = app.call('deployment.recover', {'installation_id': app.installation})
    assert retry['pending_count'] == 1 and retry['recovery_required'], retry
    app.reopen()
    assert tree(app.game) == observed['game'] and identity(observed['path']) == observed['new_identity']
    assert app.status()['recovery_required'] and entities(app)['deployment_generations'] == []


@pytest.mark.parametrize('false_state', ['rolled_back', 'cancelled'])
def test_startup_restores_committed_receipt_from_mac_journal_after_only_sql_state_drift(app, false_state):
    _, _, payload, _, after = app.mixed()
    result = app.call('plan.apply', payload)
    assert not result.get('error_code') and result['transaction']['state'] == 'committed', result
    original = entities(app)
    transaction = next(t for t in original['transactions'] if t['plan_id'] == payload['plan_id'])
    assert len(original['deployment_generations']) == 1
    generation = original['deployment_generations'][0]
    assert generation['generation_id'] == transaction['target_generation']
    journal_path = app.data / transaction['journal_path']
    journal_bytes = journal_path.read_bytes()
    records = Journal(app.data, transaction['transaction_id']).load()
    assert any(r['kind'] == 'committed' for r in records)
    current_files = {p.relative_to(app.data).as_posix(): p.read_bytes()
                     for p in (app.data / 'deployments/installations').rglob('current.json')}
    app.evidence('before-sql-drift')
    app.close()
    with sqlite3.connect(app.data / 'psmodmanager.sqlite3') as db:
        original_payload = db.execute('SELECT payload FROM transactions WHERE transaction_id=?',
                                      (transaction['transaction_id'],)).fetchone()[0]
        changed = json.loads(original_payload)
        changed['state'] = false_state
        assert {k: v for k, v in changed.items() if k != 'state'} == {k: v for k, v in transaction.items() if k != 'state'}
        db.execute('UPDATE transactions SET payload=? WHERE transaction_id=?',
                   (json.dumps(changed), transaction['transaction_id']))
    durable_json(app.root / 'sql-drift.json', {'state': false_state, 'transaction_id': transaction['transaction_id']})
    app.reopen()
    status = app.status(payload['plan_id'])
    assert status['transaction']['state'] == 'committed' and not status['recovery_required'], status
    assert status['current_generation'] == generation['generation_id']
    assert tree(app.game) == after and entities(app) == original
    assert journal_path.read_bytes() == journal_bytes
    assert {p.relative_to(app.data).as_posix(): p.read_bytes()
            for p in (app.data / 'deployments/installations').rglob('current.json')} == current_files


def test_foreign_data_root_blocks_on_active_marker_and_original_root_recovers(app, monkeypatch):
    _, _, payload, before, _ = app.mixed()
    boundaries = []

    def interrupted(point, details):
        if point in {'operation.done', 'recovery.before_restore'}:
            boundaries.append(point)
            raise OSError(errno.EIO, 'Owned consistency fault at ' + point)

    app.context.deployments.fault = interrupted
    result = app.call('plan.apply', payload)
    assert result.get('error_code') and set(boundaries) == {'operation.done', 'recovery.before_restore'}, result
    assert result['transaction']['state'] == 'recovery_required' and app.status()['recovery_required']
    pending_game, pending_data = tree(app.game), tree(app.data)
    assert pending_game != before and entities(app)['deployment_generations'] == []
    installation = app.context.store.get('Installation', app.installation)
    marker = app.game.parent / app.context.deployments._marker_path(installation)
    marker_bytes, marker_identity = marker.read_bytes(), identity(marker)
    foreign = DesktopContext(resolve_paths(str(app.root / 'b')))
    foreign_reads = []
    original_load = Journal.load

    def observe_journal_load(journal, *args, **kwargs):
        foreign_reads.append(str(journal.data_root))
        assert journal.data_root.resolve() != app.data.resolve(), 'Foreign context read the original authenticated journal'
        return original_load(journal, *args, **kwargs)

    try:
        with monkeypatch.context() as observation:
            observation.setattr(Journal, 'load', observe_journal_load)
            foreign.initialize()
            foreign.api = DesktopApi(foreign)
            foreign.choose_installation_folder = lambda _: str(app.game)
            grant = rpc(foreign.api, 'installation.choose', {'kind': 'manual'})
            found = rpc(foreign.api, 'installation.discover', {'source_handles': [grant['source_handle']]})
            assert found['candidates'][0]['valid'], found
            registered = rpc(foreign.api, 'installation.select', {'candidate_id': found['candidates'][0]['candidate_id']})
            foreign_installation = registered['installation']['installation_id']
            assert registered['installation']['file_identity'] == installation.file_identity
            guard = rpc(foreign.api, 'deployment.guard_status', {'installation_id': foreign_installation})
            status = rpc(foreign.api, 'deployment.status', {'installation_id': foreign_installation})
            assert 'GUARD_RECOVERY_SOURCE_UNKNOWN' in guard['blockers'] and not guard['ready'], guard
            assert status['recovery_required'] and not status['execution_available'] and status['transaction'] is None, status
            for intent in ('apply', 'rollback', 'launch'):
                with pytest.raises(GuardRejected, match='GUARD_RECOVERY_SOURCE_UNKNOWN'):
                    with foreign.deployment_guards.operation(foreign_installation, intent):
                        pytest.fail('Foreign data root obtained a deployment/launch guard session')
            foreign.recovery.startup()
            assert tree(app.game) == pending_game and tree(app.data) == pending_data
            assert marker.read_bytes() == marker_bytes and identity(marker) == marker_identity
            assert foreign.store.list('Transaction') == [] and foreign.store.list('DeploymentGeneration') == []
            durable_json(app.root / 'foreign-root-check.json', {'result': result, 'guard': guard, 'status': status,
                'journal_roots_read': foreign_reads, 'marker_identity': marker_identity,
                'foreign_database': database_snapshot(foreign.paths.database)})
    finally:
        foreign.close()
    app.evidence('before-original-recovery')
    app.reopen()
    status = app.status(payload['plan_id'])
    assert tree(app.game) == before and not marker.exists()
    assert not status['recovery_required'] and status['transaction']['state'] == 'rolled_back', status
    assert entities(app)['deployment_generations'] == []


@pytest.mark.parametrize('mutation', ['missing_manifest', 'delete_pointer', 'bad_hash', 'foreign_manifest'])
def test_committed_current_metadata_recovery_uses_seal_and_preserves_foreign_manifest(app, mutation):
    _, _, payload, _, after = app.mixed()
    result = app.call('plan.apply', payload)
    assert not result.get('error_code') and result['transaction']['state'] == 'committed', result
    original = entities(app)
    assert len(original['deployment_generations']) == 1
    transaction = next(t for t in original['transactions'] if t['plan_id'] == payload['plan_id'])
    installation = app.context.store.get('Installation', app.installation)
    current = app.data / app.context.deployments._current_path(installation)
    assert current.resolve(strict=True).is_relative_to(app.data.resolve(strict=True))
    manifest_bytes = current.read_bytes()
    original_pointer = dict(app.context.store.connection.execute(
        'SELECT * FROM deployment_current WHERE installation_id=?', (app.installation,)).fetchone())
    journal_path = app.data / transaction['journal_path']
    journal_bytes = journal_path.read_bytes()
    records = Journal(app.data, transaction['transaction_id']).load()
    seals = [record['data']['manifest'] for record in records if record['kind'] == 'commit_intent']
    assert len(seals) == 1 and canonical(seals[0]) == manifest_bytes
    assert tree(app.game) == after
    game_objects = file_facts(app.game)
    app.evidence('before-current-drift')
    app.close()
    preserved = current.with_name('saved-manifest.bin')
    foreign_bytes = b'{"foreign":"owned consistency fixture"}'
    if mutation == 'missing_manifest':
        os.replace(current, preserved)
        assert not current.exists() and preserved.read_bytes() == manifest_bytes
    elif mutation == 'foreign_manifest':
        current.write_bytes(foreign_bytes)
    else:
        with sqlite3.connect(app.data / 'psmodmanager.sqlite3') as db:
            if mutation == 'delete_pointer':
                db.execute('DELETE FROM deployment_current WHERE installation_id=?', (app.installation,))
            else:
                db.execute('UPDATE deployment_current SET manifest_file_hash=? WHERE installation_id=?',
                           ('0' * 64, app.installation))
    durable_json(app.root / 'current-drift.json', {'mutation': mutation, 'original_pointer': original_pointer,
        'transaction_id': transaction['transaction_id'], 'generation': original['deployment_generations'][0]})
    app.reopen()
    status = app.status(payload['plan_id'])
    restored_pointer = dict(app.context.store.connection.execute(
        'SELECT * FROM deployment_current WHERE installation_id=?', (app.installation,)).fetchone())
    app.evidence('current-recovery-result')
    assert tree(app.game) == after and file_facts(app.game) == game_objects
    assert entities(app)['deployment_generations'] == original['deployment_generations']
    assert journal_path.read_bytes() == journal_bytes
    assert restored_pointer == original_pointer
    if mutation == 'foreign_manifest':
        assert current.read_bytes() == foreign_bytes
        assert status['recovery_required'] and not status['execution_available'], status
        assert status['transaction']['state'] == 'recovery_required', status
    else:
        assert current.read_bytes() == manifest_bytes
        assert status['transaction']['state'] == 'committed' and not status['recovery_required'], status
        assert status['current_generation'] == original['deployment_generations'][0]['generation_id']
        assert entities(app) == original
        if mutation == 'missing_manifest':
            assert preserved.read_bytes() == manifest_bytes
