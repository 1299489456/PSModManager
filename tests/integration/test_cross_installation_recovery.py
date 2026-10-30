"""Recovery ownership is per physical installation, not the shared journal folder."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.contracts import ENTITY_MODELS
from backend.contracts.plans import plan_digest
from backend.deployments.journal import Journal
from backend.deployments.paths import pinned_chain
from backend.deployments.policy import native_approval_id
from tests.integration.test_deployment_guards import register
from tests.unit.test_installation_layout import make_installation


@pytest.fixture
def settled(tmp_path):
    # Seed an authenticated historical receipt, not a replay through retired
    # Loader APIs or a claim that a synthetic DLL was actually deployed.
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    app = SimpleNamespace(context=context, data=context.paths.root)
    try:
        app.installation = register(context, make_installation(tmp_path / 'original-game'))
        owner = context.store.get('Installation', app.installation)
        examples = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text('utf-8'))
        profile = {**examples['Profile'], 'installation_id': app.installation,
                   'selections': [], 'display_order': []}
        context.store.put(ENTITY_MODELS['Profile'].model_validate(profile))
        baseline = {**examples['BaselineSnapshot'], 'installation_id': app.installation, 'files': []}
        context.store.put(ENTITY_MODELS['BaselineSnapshot'].model_validate(baseline))
        plan = ENTITY_MODELS['ActionPlan'].model_validate({**examples['ActionPlan'],
            'installation_id': app.installation, 'fingerprint_id': owner.fingerprint_id,
            'preconditions': [], 'verification_checks': []})
        plan = plan.model_copy(update={'plan_hash': plan_digest(plan)})
        context.store.put(plan)
        approval = ENTITY_MODELS['Approval'].model_validate({**examples['Approval'],
            'approval_id': native_approval_id(app.data, plan.plan_hash, create=True),
            'plan_hash': plan.plan_hash, 'actor': 'native-user'})
        context.store.put(approval)
        transaction = {**examples['Transaction'], 'state': 'committed', 'last_sequence': 2}
        journal = Journal(app.data, transaction['transaction_id'])
        transaction['journal_path'] = journal.relative_path
        with pinned_chain(app.data) as data:
            data_identity = data[-1][1]
        journal.create(dict(transaction_id=transaction['transaction_id'], plan_id=str(plan.plan_id),
            plan_hash=plan.plan_hash, target_generation=transaction['target_generation'],
            installation_identity=owner.file_identity, data_identity=data_identity,
            approval_id=str(approval.approval_id)))
        journal.append('committed', {})
        context.store.put(ENTITY_MODELS['Transaction'].model_validate(transaction))
        other = register(app.context, make_installation(tmp_path / 'other-game'))
        yield app, other, transaction
    finally:
        context.close()


def test_settled_other_installation_does_not_block_current_game(settled):
    app, other, transaction = settled
    journal = app.data / transaction['journal_path']
    before = journal.read_bytes()
    assert app.context.deployment_guards.status(other)['ready']
    with app.context.deployment_guards.operation(other) as lease:
        lease.check()
    assert app.context.deployment_guards.status(app.installation)['ready']
    assert journal.read_bytes() == before


def test_attributable_unfinished_record_blocks_only_its_owner(settled):
    app, other, transaction = settled
    transaction['state'] = 'applying'
    app.context.store.connection.execute('UPDATE transactions SET payload=? WHERE transaction_id=?',
        (json.dumps(transaction), transaction['transaction_id']))
    assert app.context.deployment_guards.status(other)['ready']
    assert 'GUARD_RECOVERY_REQUIRED' in app.context.deployment_guards.status(app.installation)['blockers']


@pytest.mark.parametrize('damage', ['unindexed', 'corrupt', 'wrong_owner'])
def test_unattributable_journal_still_blocks_current_game(settled, damage):
    app, other, transaction = settled
    journal = app.data / transaction['journal_path']
    if damage == 'unindexed':
        journal = journal.with_name(str(uuid4()) + '.jsonl')
        journal.write_bytes(b'unknown journal')
    elif damage == 'corrupt':
        journal.write_bytes(journal.read_bytes().replace(b'"header"', b'"broken"', 1))
    else:
        plan = app.context.store.get('ActionPlan', transaction['plan_id']).model_dump(mode='json')
        plan['installation_id'] = other
        app.context.store.connection.execute('UPDATE action_plans SET installation_id=?, payload=? WHERE plan_id=?',
            (other, json.dumps(plan), plan['plan_id']))
    before = journal.read_bytes()
    assert 'GUARD_RECOVERY_REQUIRED' in app.context.deployment_guards.status(other)['blockers']
    assert journal.read_bytes() == before


@pytest.mark.parametrize('entity', ['transaction', 'plan', 'installation'])
def test_foreign_primary_key_payload_mismatch_does_not_authenticate_another_file(settled, entity):
    app, other, transaction = settled
    if entity == 'transaction':
        forged = str(uuid4())
        app.context.store.connection.execute('INSERT INTO transactions (transaction_id,plan_id,payload) VALUES (?,?,?)',
            (forged, transaction['plan_id'], json.dumps(transaction)))
        (app.data / 'deployments/journals' / (forged + '.jsonl')).write_bytes(b'not an authenticated receipt')
    else:
        model, table, key, identity = (('ActionPlan', 'action_plans', 'plan_id', transaction['plan_id'])
            if entity == 'plan' else ('Installation', 'installations', 'installation_id', app.installation))
        value = app.context.store.get(model, identity).model_dump(mode='json')
        value[key] = str(uuid4())
        app.context.store.connection.execute(f'UPDATE {table} SET payload=? WHERE {key}=?', (json.dumps(value), identity))
    assert 'GUARD_RECOVERY_REQUIRED' in app.context.deployment_guards.status(other)['blockers']


def test_disappearing_foreign_journal_is_not_treated_as_missing_directory(settled, monkeypatch):
    app, other, _ = settled
    def disappeared(self):
        raise FileNotFoundError('owned fixture race')
    monkeypatch.setattr(Journal, 'load', disappeared)
    assert 'GUARD_RECOVERY_REQUIRED' in app.context.deployment_guards.status(other)['blockers']
