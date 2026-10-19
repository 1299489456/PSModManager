"""Actual authenticated history survives an isolated directory/volume move."""
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import uuid4

import pytest

from backend.app.storage_history import validate_migration_source, rebind_migrated_history
from backend.deployments.journal import Journal
from backend.deployments.history import IssueJournal
from tests.integration.harness.transaction_recovery import TransactionFixture, tree
from tests.integration.test_rollback_drift import approve, rollback_preview


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows file identities required')


@pytest.fixture
def deployed(tmp_path):
    app = TransactionFixture(tmp_path / 'case')
    try:
        _, _, payload, before, after = app.mixed()
        receipt = app.call('plan.apply', payload)
        assert receipt.get('deployed'), receipt
        transaction_id = receipt['transaction']['transaction_id']
        records = Journal(app.data, transaction_id).load()
        assert 'file_storage' not in records[0]['data']  # same-volume legacy file area
        yield app, transaction_id, before, after
    finally:
        app.assert_sentinels()
        app.close()


def copy_idle(app, target):
    app.close()
    validate_migration_source(app.data)
    shutil.copytree(app.data, target)
    rebind_migrated_history(app.data, target)


def test_private_legacy_history_rebind_keeps_source_and_can_rollback(deployed):
    app, identity, before, after = deployed
    old_data = app.data
    old_bytes = (old_data / Journal(old_data, identity).relative_path).read_bytes()
    old_records = Journal(old_data, identity).load()
    copy_idle(app, app.root / 'migrated-data')
    app.data = app.root / 'migrated-data'
    assert (old_data / Journal(old_data, identity).relative_path).read_bytes() == old_bytes
    records = Journal(app.data, identity).load()
    assert records[0]['data']['data_identity'] != old_records[0]['data']['data_identity']
    assert [row['sequence'] for row in records] == [row['sequence'] for row in old_records]
    assert tree(app.game) == after
    app.reopen()
    page = app.call('deployment.history', {'installation_id': app.installation})
    assert not page['recovery_required'] and page['records'][0]['rollback_allowed'], page
    payload = approve(app, rollback_preview(app, identity))
    receipt = app.call('deployment.rollback', payload)
    assert receipt.get('deployed'), receipt
    assert tree(app.game) == before


def test_legacy_cross_volume_backup_remains_valid_and_undo_works(deployed):
    app, identity, before, after = deployed
    # Pick a second actual test volume rather than mocking the volume field.
    preview = Path(__file__).resolve().parents[2] / '.preview'
    preview.mkdir(exist_ok=True)
    scratch = Path(tempfile.gettempdir()).resolve()
    if scratch.stat().st_dev == app.data.stat().st_dev:
        scratch = preview
    with tempfile.TemporaryDirectory(prefix='storage-history-', dir=scratch) as temporary:
        target = Path(temporary) / 'migrated-data'
        if target.parent.stat().st_dev == app.data.stat().st_dev:
            pytest.skip('No second test volume available')
        copy_idle(app, target)
        app.data = target
        app.reopen()
        history = app.context.history
        with history.executor._store() as store:
            frames = history.frames(store, app.installation)
        operation = next(op for op in frames[0]['plan'].operations if op.expected_before != 'absent')
        backup = history.verify_backup(frames[0], operation)
        assert backup['identity'].split(':')[0] != frames[0]['header']['installation_identity'].split(':')[0]
        assert tree(app.game) == after
        payload = approve(app, rollback_preview(app, identity))
        receipt = app.call('deployment.rollback', payload)
        assert receipt.get('deployed'), receipt
        assert tree(app.game) == before
        app.close()


@pytest.mark.parametrize('damage', ['journal', 'pending', 'marker', 'backup', 'backup_identity'])
def test_unsafe_sources_are_rejected_without_mutation(deployed, damage):
    app, identity, _, after = deployed
    executor = app.context.deployments
    with executor._store() as store:
        transaction = store.get('Transaction', identity)
        plan = store.get('ActionPlan', transaction.plan_id)
        installation = store.get('Installation', plan.installation_id)
    journal = Journal(app.data, identity)
    records = journal.load()
    if damage == 'journal':
        path = app.data / journal.relative_path
        path.write_bytes(path.read_bytes().replace(b'"header"', b'"headeX"', 1))
    elif damage == 'pending':
        executor._save(transaction.model_copy(update={'state': 'ready'}))
    elif damage == 'marker':
        executor._mark(installation, transaction)
    else:
        identifier = next(row['data']['operation_id'] for row in records if row['kind'] == 'backup')
        backup_path = app.data / executor._area(transaction) / 'backup' / (identifier + '.bin')
        if damage == 'backup_identity':
            replacement = backup_path.with_suffix('.new')
            replacement.write_bytes(backup_path.read_bytes())
            replacement.replace(backup_path)
        else:
            backup_path.write_bytes(b'corrupt')
    app.close()
    original = (app.data / journal.relative_path).read_bytes()
    with pytest.raises((ValueError, OSError)):
        validate_migration_source(app.data)
    assert (app.data / journal.relative_path).read_bytes() == original
    assert tree(app.game) == after
    if damage == 'marker':
        # Only our test marker; product migration must leave it alone.
        marker = app.game.parent / executor._marker_path(installation)
        marker.unlink()


def test_resolved_issue_copy_is_rebound_but_game_identity_is_unchanged(deployed):
    app, identity, _, after = deployed
    history = app.context.history
    with history.executor._store() as store:
        installation = store.get('Installation', app.installation)
        frame = history.frames(store, app.installation)[0]
    conflict = {'target_root_kind': 'mods', 'relative_path': 'replace.png'}
    issue = history.record_issue(installation, frame, frame, 'ROLLBACK_EXTERNAL_DRIFT', [conflict])
    issue_id = issue['issue_id']
    result = history.recheck({'installation_id': app.installation})
    assert not result['recovery_required'], result
    old_issue = IssueJournal(app.data, issue_id).load()
    old_conflict = old_issue[1]['data']['conflicts'][0]
    assert old_conflict['preserved']
    copy_idle(app, app.root / 'migrated-data')
    app.data = app.root / 'migrated-data'
    app.reopen()
    new_issue = IssueJournal(app.data, issue_id).load()
    new_conflict = new_issue[1]['data']['conflicts'][0]
    assert new_conflict['copy_identity'] != old_conflict['copy_identity']
    assert new_conflict['source_identity'] == old_conflict['source_identity']
    app.context.history._check_copy(new_conflict)
    page = app.call('deployment.history', {'installation_id': app.installation})
    assert not page['recovery_required'] and page['issues'][0]['resolved'], page
    assert tree(app.game) == after


def test_unresolved_issue_refuses_migration(deployed):
    app, _, _, _ = deployed
    history = app.context.history
    with history.executor._store() as store:
        installation = store.get('Installation', app.installation)
        frame = history.frames(store, app.installation)[0]
    history.record_issue(installation, frame, frame, 'ROLLBACK_EXTERNAL_DRIFT', [])
    app.close()
    with pytest.raises(ValueError):
        validate_migration_source(app.data)


def test_finished_transaction_without_marker_allows_a_pending_cleanup_receipt(deployed):
    app, identity, _, _ = deployed
    executor = app.context.deployments
    transaction = app.context.store.get('Transaction', identity)
    # Cleanup can fail after it already removed the marker. The authenticated
    # terminal receipt and actual marker absence decide whether writes remain.
    executor._record(Journal(app.data, identity), transaction, 'cleanup', {'status': 'pending'})
    app.close()
    validate_migration_source(app.data)


def test_ordinary_sibling_backups_are_not_moved_or_rebound(deployed):
    app, _, _, _ = deployed
    release = app.publish({'Mods/ordinary.png': b'ordinary-content'})
    request = dict(installation_id=app.installation, release_id=release['release_id'],
                   manifest_hash=release['manifest_hash'])
    for action in ('install', 'disable'):
        result = app.call('mod.action', dict(request, action=action, idempotency_key=str(uuid4())))
        assert result.get('completed'), result
    ordinary = []
    for path in (app.data / 'deployments/journals').glob('*.jsonl'):
        records = Journal(app.data, path.stem).load()
        header = records[0]['data']
        if header.get('execution_policy') == 'ordinary-mods-v1':
            ordinary.append((path.stem, header['file_storage']))
    assert len(ordinary) == 2
    external = app.game.parent / ('.psmm-' + app.context.deployments._slot(
        app.context.store.get('Installation', app.installation)))
    before = {p.relative_to(external): (p.read_bytes(), p.stat().st_ino)
              for p in external.rglob('*') if p.is_file()}
    copy_idle(app, app.root / 'migrated-data')
    app.data = app.root / 'migrated-data'
    assert {p.relative_to(external): (p.read_bytes(), p.stat().st_ino)
            for p in external.rglob('*') if p.is_file()} == before
    for identity, storage in ordinary:
        assert Journal(app.data, identity).load()[0]['data']['file_storage'] == storage
    app.reopen()
    result = app.call('mod.action', dict(request, action='enable', idempotency_key=str(uuid4())))
    assert result.get('completed') and (app.game / 'Mods/ordinary.png').read_bytes() == b'ordinary-content', result
