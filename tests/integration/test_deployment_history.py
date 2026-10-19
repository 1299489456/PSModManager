"""Real committed receipts, native backup reads and durable conflict history."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.deployments.history import HistoryService, IssueJournal
from backend.deployments.paths import GuardRejected
from backend.deployments.policy import PlanRejected
from tests.integration.harness.transaction_recovery import TransactionFixture, child_at, durable_json, tree


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Actual Windows handle guard required')


def service(app):
    history = getattr(app.context, 'history', None)
    if history is None:
        history = HistoryService(app.context.paths, app.context.deployment_guards,
                                 app.context.deployments, app.context.plans)
        def check(installation, permit=None):
            history.executor.probe(installation, permit)
            history.probe(installation, permit)
        history.guards.recovery_check = check
    return history


@pytest.fixture
def deployed(tmp_path_factory, request):
    app = TransactionFixture(tmp_path_factory.mktemp('h'))
    durable_json(app.root / 'case.json', {'nodeid': request.node.nodeid})
    try:
        state, preview, payload, before, after = app.mixed()
        receipt = app.call('plan.apply', payload)
        durable_json(app.root / 'apply-receipt.json', receipt)
        assert receipt.get('deployed'), receipt
        history = service(app)
        with history.executor._store() as store:
            installation = store.get('Installation', app.installation)
            frames = history.frames(store, app.installation)
        assert len(frames) == 1
        yield app, history, installation, frames[0], before, after
    finally:
        app.assert_sentinels()
        app.close()


def changed_operation(frame):
    return next(op for op in frame['plan'].operations if op.expected_before != 'absent')


def drift_issue(fixture):
    app, history, installation, frame, *_ = fixture
    target = app.game / 'Mods/replace.png'
    target.write_bytes(b'player-edited-pixels')
    conflict = dict(target_root_kind='mods', relative_path='replace.png',
                    expected=hashlib.sha256(b'new-replace').hexdigest(),
                    actual=hashlib.sha256(b'player-edited-pixels').hexdigest())
    issue = history.record_issue(installation, frame, frame, 'ROLLBACK_EXTERNAL_DRIFT', [conflict])
    return target, issue


def test_history_authenticates_real_generation_and_protects_all_references(deployed):
    app, history, _, frame, _, after = deployed
    page = app.call('deployment.history', {'installation_id': app.installation, 'limit': 1})
    assert page['total'] == 1 and page['next_offset'] is None
    row = page['records'][0]
    assert row['generation_id'] == str(frame['generation'].generation_id)
    assert row['state'] == 'committed' and row['kind'] == 'deployment'
    assert row['profile_name'] == 'Real transaction fixture'
    assert row['retention']['protected'] and not row['retention']['cleanup_available']
    assert {'signed_history', 'plan_baseline', 'owner_references', 'rollback_source'} <= set(row['retention']['reasons'])
    assert page['verification_status'] == 'not_run' and not page['cleanup_available']
    assert tree(app.game) == after


@pytest.mark.parametrize('extra', [{'limit': 11}, {'offset': -1}, {'path': 'Mods/replace.png'}])
def test_history_payload_is_closed_and_bounded(deployed, extra):
    app, history, *_ = deployed
    with pytest.raises(ValidationError):
        history.list({'installation_id': app.installation, **extra})


def test_database_commit_flag_without_valid_journal_is_unknown(deployed):
    app, history, _, frame, *_ = deployed
    path = app.data / frame['transaction'].journal_path
    path.write_bytes(path.read_bytes().replace(b'"header"', b'"headeX"', 1))
    page = history.list({'installation_id': app.installation})
    assert page['recovery_required']
    assert page['records'][0]['kind'] == 'unknown'
    assert page['records'][0]['rollback_allowed'] is False
    assert page['records'][0]['retention']['protected']


@pytest.mark.parametrize('damage,code', [('missing', 'ROLLBACK_BACKUP_MISSING'), ('corrupt', 'ROLLBACK_BACKUP_CORRUPT')])
def test_backup_requires_original_hash_and_actual_private_file(deployed, damage, code):
    app, history, _, frame, *_ = deployed
    operation = changed_operation(frame)
    descriptor = history.verify_backup(frame, operation)
    path = app.data / descriptor['relative_path']
    before = path.read_bytes()
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'corrupt-backup')
    with pytest.raises(PlanRejected) as failure:
        history.verify_backup(frame, operation)
    assert failure.value.code == code
    if damage == 'corrupt':
        assert path.read_bytes() == b'corrupt-backup'
    durable_json(app.root / 'backup-damage.json', {'damage': damage, 'code': code,
        'original_sha256': hashlib.sha256(before).hexdigest()})


def test_same_bytes_repaired_backup_binds_fresh_physical_identity(deployed):
    app, history, _, frame, *_ = deployed
    operation = changed_operation(frame)
    first = history.verify_backup(frame, operation)
    path = app.data / first['relative_path']
    temporary = path.with_name('repair-' + uuid4().hex + '.bin')
    temporary.write_bytes(path.read_bytes())
    os.replace(temporary, path)
    repaired = history.verify_backup(frame, operation)
    assert repaired['sha256'] == first['sha256']
    assert repaired['identity'] != first['identity']


def test_player_edit_is_copied_without_game_mutation_and_survives_restart(deployed):
    app, history, installation, frame, *_ = deployed
    target, issue = drift_issue(deployed)
    copy = issue['conflicts'][0]
    assert copy['preserved']
    assert copy['expected'] == hashlib.sha256(b'new-replace').hexdigest()
    assert copy['actual'] == copy['sha256'] == hashlib.sha256(b'player-edited-pixels').hexdigest()
    assert (app.data / copy['copy_relative_path']).read_bytes() == target.read_bytes() == b'player-edited-pixels'
    with pytest.raises(GuardRejected) as blocked:
        history.guards._check_recovery(installation)
    assert blocked.value.code == 'GUARD_RECOVERY_REQUIRED'
    page = history.list({'installation_id': app.installation})
    assert page['recovery_required'] and page['records'][0]['state'] == 'recovery_required'
    assert page['records'][0]['generation_id'] == str(frame['generation'].generation_id)
    app.reopen()
    history = service(app)
    page = history.list({'installation_id': app.installation})
    assert page['recovery_required'] and page['issues'][0]['issue_id'] == issue['issue_id']
    assert target.read_bytes() == b'player-edited-pixels'


def test_recheck_never_restores_game_and_only_resolves_verified_repair(deployed):
    app, history, _, frame, *_ = deployed
    target, issue = drift_issue(deployed)
    failed = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert failed['recovery_required'] and failed['resolved_count'] == 0
    assert target.read_bytes() == b'player-edited-pixels'
    # A human/external tool repairs the owned synthetic target. Recheck itself
    # only verifies; it has no automatic-overwrite permission.
    target.write_bytes(b'new-replace')
    repaired = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert not repaired['recovery_required'] and repaired['resolved_count'] == 1
    assert target.read_bytes() == b'new-replace'
    copy = issue['conflicts'][0]
    assert (app.data / copy['copy_relative_path']).read_bytes() == b'player-edited-pixels'
    assert history.list({'installation_id': app.installation})['records'][0]['state'] == 'committed'
    repeated = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert repeated['resolved_count'] == 0 and not repeated['recovery_required']
    assert len(IssueJournal(app.data, issue['issue_id']).load()) == 3


def test_damaged_conflict_copy_prevents_resolution_and_is_retained(deployed):
    app, history, *_ = deployed
    target, issue = drift_issue(deployed)
    path = app.data / issue['conflicts'][0]['copy_relative_path']
    path.write_bytes(b'corrupt-preserved-copy')
    target.write_bytes(b'new-replace')
    result = history.recheck({'installation_id': app.installation})
    assert result['recovery_required'] and result['resolved_count'] == 0
    assert path.read_bytes() == b'corrupt-preserved-copy'


def test_modified_issue_mac_blocks_guard_and_does_not_clear_recovery(deployed):
    app, history, installation, *_ = deployed
    _, issue = drift_issue(deployed)
    journal = IssueJournal(app.data, issue['issue_id'])
    path = app.data / journal.relative_path
    path.write_bytes(path.read_bytes().replace(b'ROLLBACK_EXTERNAL_DRIFT', b'ROLLBACK_EXTERNAL_DRIfT', 1))
    with pytest.raises(GuardRejected):
        history.probe(installation)
    result = history.recheck({'installation_id': app.installation})
    assert result['recovery_required'] and result['resolved_count'] == 0


def test_directory_occupant_is_never_overwritten_or_followed(deployed):
    app, history, installation, frame, *_ = deployed
    target = app.game / 'Mods/replace.png'
    target.unlink()
    target.mkdir()
    (target / 'unknown-player-file.bin').write_bytes(b'keep-directory-occupant')
    issue = history.record_issue(installation, frame, frame, 'ROLLBACK_TARGET_OCCUPIED',
                                 [dict(target_root_kind='mods', relative_path='replace.png')])
    assert issue['conflicts'][0]['preserved'] is False
    assert (target / 'unknown-player-file.bin').read_bytes() == b'keep-directory-occupant'
    assert history.recheck({'installation_id': app.installation})['recovery_required']


def test_partial_manual_history_deletion_cannot_grant_cleanup_or_rollback(deployed):
    app, history, _, frame, *_ = deployed
    path = app.data / frame['transaction'].journal_path
    path.unlink()
    result = history.list({'installation_id': app.installation})
    assert result['recovery_required'] and not result['cleanup_available']
    assert result['records'][0]['rollback_allowed'] is False
    assert result['records'][0]['retention']['protected']


def test_recheck_with_no_issues_keeps_actual_interrupted_transaction_blocked(deployed):
    app, *_ = deployed
    state = app.profile([app.publish({'Mods/replace.png': b'next-generation'})])
    _, payload = app.approved(state)
    attempts = []
    def recovery_fault(point, details):
        if point == 'recovery.before_restore':
            attempts.append(details)
            raise OSError('Owned recovery interruption')
    with child_at(app, payload, 'operation.published', restart_fault=recovery_fault):
        pass
    assert attempts and app.status()['recovery_required']
    before = tree(app.game)
    result = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert result['issues'] == []
    assert result['recovery_required'] and result['remaining_count'] >= 1
    assert result['resolved_count'] == 0 and tree(app.game) == before


def test_initial_mac_issue_without_copies_remains_blocked_after_cold_start(deployed):
    app, history, installation, frame, *_ = deployed
    operation = changed_operation(frame)
    descriptor = history.verify_backup(frame, operation)
    backup = app.data / descriptor['relative_path']
    backup.write_bytes(b'owned-broken-backup')
    before = tree(app.game)
    def abort_before_copy():
        raise RuntimeError('Owned interruption after authenticated issue header')
    with pytest.raises(RuntimeError):
        history.record_issue(installation, frame, frame, 'ROLLBACK_BACKUP_CORRUPT',
            [dict(target_root_kind=operation.target_root_kind, relative_path=operation.relative_path)],
            check=abort_before_copy)
    journals = list((app.data / 'deployments/history/issues').glob('*.jsonl'))
    assert len(journals) == 1
    issue_id = journals[0].stem
    records = IssueJournal(app.data, issue_id).load()
    assert len(records) == 1 and records[0]['kind'] == 'header'
    assert records[0]['data']['conflicts'][0]['preserved'] is False
    app.reopen()
    assert app.status()['recovery_required'] and tree(app.game) == before
    listed = app.call('deployment.history', {'installation_id': app.installation})
    assert listed['recovery_required'] and listed['issues'][0]['files'][0]['preserved'] is False
    recheck = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert recheck['recovery_required'] and recheck['resolved_count'] == 0
    assert backup.read_bytes() == b'owned-broken-backup' and tree(app.game) == before


def test_no_issue_header_cannot_clear_unknown_occupant_at_removed_owned_path(deployed):
    app, history, installation, frame, *_ = deployed
    operation = next(op for op in frame['plan'].operations if op.relative_path == 'remove.png')
    descriptor = history.verify_backup(frame, operation)
    backup = app.data / descriptor['relative_path']
    original = backup.read_bytes()
    backup.write_bytes(b'owned-broken-backup')
    parent = app.data / 'deployments/history'
    parent.mkdir(exist_ok=True)
    blocked_parent = parent / 'issues'
    blocked_parent.write_bytes(b'owned-private-file-blocks-directory-creation')
    with pytest.raises((GuardRejected, OSError)):
        history.record_issue(installation, frame, frame, 'ROLLBACK_BACKUP_CORRUPT',
            [dict(target_root_kind='mods', relative_path='remove.png')])
    # Exact fixture-owned occupants/backup are repaired externally. The product
    # must still preserve a new unknown game occupant at the old removed path.
    blocked_parent.unlink()
    backup.write_bytes(original)
    target = app.game / 'Mods/remove.png'
    target.write_bytes(b'unknown-player-occupant')
    app.reopen()
    assert app.status()['recovery_required']
    before = tree(app.game)
    result = app.call('deployment.conflicts.recheck', {'installation_id': app.installation})
    assert result['issues'] == [] and result['recovery_required']
    assert target.read_bytes() == b'unknown-player-occupant' and tree(app.game) == before


def test_recheck_waiting_for_executor_does_not_hold_history_lock(deployed):
    app, history, *_ = deployed
    target, _ = drift_issue(deployed)
    target.write_bytes(b'new-replace')
    started, done = threading.Event(), threading.Event()
    results, errors = [], []
    def consumer():
        started.set()
        try:
            results.append(history.recheck({'installation_id': app.installation}))
        except BaseException as exc:
            errors.append(repr(exc))
        finally:
            done.set()
    with history.executor._lock:
        thread = threading.Thread(target=consumer, name='owned-history-recheck', daemon=True)
        thread.start()
        assert started.wait(5)
        assert not done.wait(.2)
        acquired = history._lock.acquire(timeout=1)
        assert acquired, 'A waiting history consumer reversed the executor/history lock order'
        if acquired:
            history._lock.release()
    thread.join(45)
    assert done.is_set() and not errors, errors
    assert results and not results[0]['recovery_required'], results
    assert target.read_bytes() == b'new-replace'
