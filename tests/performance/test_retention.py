"""Meaningful regressions for the two measured continuous-use limits."""
import threading
import time

import pytest

from backend.runtime.events import RuntimePolicyError
from backend.runtime.jobs import JobManager
from tests.integration.harness.transaction_recovery import TransactionFixture


def completed(manager, request_id):
    snapshot = manager.start(request_id, lambda _: request_id)
    manager._jobs[snapshot['task_id']].thread.join(2)
    assert manager.snapshot(snapshot['task_id'])['state'] == 'succeeded'
    return snapshot['task_id']


def test_completed_tasks_reclaim_capacity_but_live_subscriptions_pin_receipts():
    manager = JobManager(max_tasks=2)
    try:
        first = completed(manager, 'first')
        sub = manager.subscribe(first)['subscription_id']
        second = completed(manager, 'second')
        third = completed(manager, 'third')
        assert manager.events(sub)['snapshot']['data'] == 'first'
        with pytest.raises(RuntimePolicyError, match='TASK_NOT_FOUND'):
            manager.snapshot(second)
        assert len(manager._jobs) == 2
        manager.unsubscribe(sub)
        fourth = completed(manager, 'fourth')
        with pytest.raises(RuntimePolicyError, match='TASK_NOT_FOUND'):
            manager.snapshot(first)
        assert manager.snapshot(third)['data'] == 'third'
        assert manager.snapshot(fourth)['data'] == 'fourth'
    finally:
        manager.close()


def test_active_tasks_stay_bounded_and_expired_subscription_releases_capacity():
    manager = JobManager(max_tasks=1, subscription_ttl=.02)
    release = threading.Event()
    try:
        first = manager.start('live', lambda _: release.wait(2))['task_id']
        with pytest.raises(RuntimePolicyError, match='TASK_LIMIT'):
            manager.start('extra', lambda _: None)
        sub = manager.subscribe(first)['subscription_id']
        release.set()
        manager._jobs[first].thread.join(2)
        time.sleep(.03)
        completed(manager, 'next')
        with pytest.raises(RuntimePolicyError, match='SUBSCRIPTION_EXPIRED'):
            manager.events(sub)
    finally:
        release.set()
        manager.close()


def test_pending_imports_stay_valid_while_completed_import_frees_capacity(tmp_path_factory):
    app = TransactionFixture(tmp_path_factory.mktemp('r'))
    try:
        source = app.root/'source'
        (source/'Mods').mkdir(parents=True)
        (source/'Mods/resource.png').write_bytes(b'owned-resource')
        app.context.choose_import_source = lambda _: str(source)
        previews = []
        for _ in range(8):
            grant = app.call('package.choose', {'kind':'directory'})
            previews.append(app.call('package.import_preview', {'source_handle':grant['source_handle']}))
        assert app.call('package.choose', {'kind':'directory'})['error_code'] == 'IMPORT_CAPACITY'
        first = previews[0]
        payload = dict(preview_id=first['preview_id'], preview_hash=first['preview_hash'], display_name='First')
        published = app.call('package.import_commit', payload)
        assert app.call('package.import_commit', payload) == published
        grant = app.call('package.choose', {'kind':'directory'})
        newest = app.call('package.import_preview', {'source_handle':grant['source_handle']})
        assert newest['can_commit']
        for pending in previews[1:]:
            assert app.call('package.import_files', {'preview_id':pending['preview_id']})['can_commit']
        assert app.call('package.import_commit', payload)['error_code'] == 'IMPORT_PREVIEW_EXPIRED'
        assert len(app.context.imports._previews) == 8
        assert (source/'Mods/resource.png').read_bytes() == b'owned-resource'
    finally:
        app.close()
