"""Manager auto updates are opt-in, fresh, bounded and never blindly retried."""
from copy import deepcopy
from types import SimpleNamespace
import threading

import pytest

from backend.app.workshop_update_policy import WorkshopUpdatePolicy


class Preferences:
    def __init__(self):
        self.value = {}
    def read_config(self):
        return deepcopy(self.value)
    def update(self, field, value):
        self.value[field] = deepcopy(value)


class Workshop:
    def __init__(self):
        self.calls, self.reads, self.busy = [], 0, False
        self.snapshot = dict(session=dict(state='ready', stale=False, refreshing=False),
            runtime=dict(state='stopped', writable=True),
            capabilities=dict(direct_updates=dict(available=True)), operations=[],
            items=[dict(workshop_id=str(i), subscribed=True, needs_update=True) for i in range(1, 202)])
    def is_busy(self):
        return self.busy
    def status(self, installation):
        self.reads += 1
        return deepcopy(self.snapshot)
    def action(self, installation, **args):
        self.calls.append(args)
        operation = dict(operation_id=str(len(self.calls)), state='queued')
        self.snapshot['operations'].append(operation)
        return deepcopy(operation)
    def finish(self, state='completed'):
        self.snapshot['operations'][-1]['state'] = state
        if state == 'completed':
            ids = set(self.calls[-1]['ids'])
            for row in self.snapshot['items']:
                if row['workshop_id'] in ids:
                    row['needs_update'] = False


@pytest.fixture
def policy():
    context = SimpleNamespace(preferences=Preferences(), workshop=Workshop(), storage=SimpleNamespace(blocked=False),
        installations=SimpleNamespace(list=lambda: dict(selected_installation_id='owned',
            installations=[dict(installation_id='owned', canonical_root='owned')])) )
    return WorkshopUpdatePolicy(context)


def test_disabled_does_not_even_probe_steam(policy):
    policy.tick()
    assert policy.context.workshop.reads == 0 and policy.context.workshop.calls == []
    assert policy.settings()['steam_managed_updates'] is True


def test_automatic_updates_sequential_batches_only_after_completion(policy):
    policy.set(True)
    service = policy.context.workshop
    policy.tick()
    assert len(service.calls) == 1 and len(service.calls[0]['ids']) == 100
    assert service.calls[0]['source'] == 'automatic' and service.calls[0]['action'] == 'update'
    policy.tick()
    assert len(service.calls) == 1
    service.finish()
    policy.tick()
    assert len(service.calls) == 2 and len(service.calls[1]['ids']) == 100
    service.finish()
    policy.tick()
    assert service.calls[2]['ids'] == ['201']
    service.finish()
    policy.tick()
    assert len(service.calls) == 3


@pytest.mark.parametrize('change', ['running', 'unknown', 'lease_busy', 'stale', 'refreshing', 'unavailable', 'operation_busy', 'migrating'])
def test_unsafe_or_unverified_state_never_triggers_update(policy, change):
    service, snap = policy.context.workshop, policy.context.workshop.snapshot
    policy.set(True)
    if change in ('running', 'unknown'):
        snap['runtime']['state'] = change
    elif change == 'lease_busy':
        snap['runtime']['writable'] = False
    elif change in ('stale', 'refreshing'):
        snap['session'][change] = True
    elif change == 'unavailable':
        snap['session']['state'] = change
    elif change == 'operation_busy':
        service.busy = True
    else:
        policy.context.storage.blocked = True
    policy.tick()
    assert not service.calls


@pytest.mark.parametrize('state', ['failed', 'partial', 'uncertain'])
def test_failed_batch_pauses_instead_of_replaying(policy, state):
    policy.set(True)
    service = policy.context.workshop
    policy.tick()
    service.finish(state)
    if state == 'uncertain':
        service.busy = True  # Real service retains uncertain receipts as busy.
    policy.tick()
    assert policy.settings()['paused_reason']
    policy.tick()
    assert len(service.calls) == 1


def test_off_revokes_unsubmitted_items_and_reenable_does_not_revive_old_batch(policy):
    policy.set(True)
    policy.tick()
    old_permission = policy.context.workshop.calls[0]['submission_allowed']
    assert old_permission()
    policy.set(False)
    assert not old_permission()
    policy.set(True)
    assert not old_permission() and policy._permission.is_set()
    policy.set(False)
    policy.tick()
    assert len(policy.context.workshop.calls) == 1


def test_subscriptions_without_pending_updates_are_not_downloaded(policy):
    policy.set(True)
    service = policy.context.workshop
    for i, row in enumerate(service.snapshot['items']):
        row.update(subscribed=False) if i % 3 == 0 else row.update(downloading=True) if i % 3 == 1 else row.update(download_pending=True)
    policy.tick()
    assert not service.calls


def test_migration_gate_serializes_the_last_submission_check(policy):
    policy.set(True)
    entered = threading.Event()
    def tick():
        entered.set()
        policy.tick()
    with policy.migration_gate():
        thread = threading.Thread(target=tick)
        thread.start()
        assert entered.wait(1)
        policy.context.storage.blocked = True
    thread.join(2)
    assert not thread.is_alive() and not policy.context.workshop.calls


def test_close_stops_scheduler_without_submitting(policy):
    policy.start()
    policy.close()
    policy.set(True)
    policy.tick()
    assert not policy._thread.is_alive() and not policy.context.workshop.calls
