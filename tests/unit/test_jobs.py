import json
import threading
import time

import pytest

from backend.runtime.jobs import JobManager, RuntimePolicyError, TERMINAL


def finished(manager, task_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = manager.snapshot(task_id)
        if state["state"] in TERMINAL:
            return state
        time.sleep(.002)
    pytest.fail("worker did not terminate")


@pytest.fixture
def manager():
    instance = JobManager()
    yield instance
    instance.close()


def test_order_correlation_unique_terminal_and_copied_data(manager):
    mutable = {"count": 1}
    def work(ctx):
        ctx.progress(1, 2, "checking", "ITEM_CHECKED", mutable)
        mutable["count"] = 99
        ctx.progress(2, 2, "done", "ITEM_CHECKED")
        return {"count": 2}
    initial = manager.start("request-1", work)
    task = initial["task_id"]
    assert initial["state"] == "queued" and initial["sequence"] == 1
    assert finished(manager, task)["state"] == "succeeded"
    response = manager.subscribe(task)
    events = response["events"]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert all(e["request_id"] == "request-1" and e["task_id"] == task for e in events)
    assert sum(e["type"] == "terminal" for e in events) == 1
    assert events[2]["data"] == {"count": 1}
    terminal = manager.snapshot(task)
    assert manager.cancel(task) == terminal
    terminal["data"]["count"] = 999
    assert manager.snapshot(task)["data"] == {"count": 2}


def test_cancel_old_task_does_not_cancel_new(manager):
    entered = threading.Event()
    def work(ctx):
        entered.set()
        while True:
            ctx.check_cancelled()
            time.sleep(.002)
    old = manager.start("old", work)["task_id"]
    assert entered.wait(1)
    manager.cancel(old)
    assert finished(manager, old)["state"] == "cancelled"
    new = manager.start("new", lambda ctx: "new-result")["task_id"]
    manager.cancel(old)
    assert finished(manager, new)["state"] == "succeeded"
    assert old != new


def test_unknown_task_rejected(manager):
    for operation in (manager.cancel, manager.snapshot, manager.subscribe):
        with pytest.raises(RuntimePolicyError, match="TASK_NOT_FOUND"):
            operation("unknown")


def test_resume_and_ring_resync():
    manager = JobManager(history_limit=3)
    try:
        def work(ctx):
            for i in range(10):
                ctx.progress(i, 10, "check", "CHECK")
        task = manager.start("r", work)["task_id"]
        terminal = finished(manager, task)
        response = manager.subscribe(task, 0)
        assert response["resync_required"] and response["events"] == []
        resumed = manager.events(response["subscription_id"], terminal["sequence"] - 2)
        assert not resumed["resync_required"]
        assert len(resumed["events"]) == 2
        assert manager.events(response["subscription_id"], terminal["sequence"])["events"] == []
        assert manager.events(response["subscription_id"], terminal["sequence"] - 2) == resumed
    finally:
        manager.close()


@pytest.mark.parametrize("cursor", [-1, True, 1.5, "0", 100])
def test_invalid_sequence_does_not_allocate(manager, cursor):
    task = manager.start("r", lambda ctx: None)["task_id"]
    finished(manager, task)
    with pytest.raises(RuntimePolicyError, match="INVALID_SEQUENCE"):
        manager.subscribe(task, cursor)
    assert not manager._subscriptions


def test_owner_isolation_and_unsubscribe(manager):
    task = manager.start("r", lambda ctx: None)["task_id"]
    sub = manager.subscribe(task, owner="a")["subscription_id"]
    for operation in (manager.events, manager.unsubscribe):
        with pytest.raises(RuntimePolicyError, match="SUBSCRIPTION_NOT_FOUND"):
            operation(sub, owner="b")
    assert manager.unsubscribe(sub, owner="a") is True
    with pytest.raises(RuntimePolicyError, match="SUBSCRIPTION_NOT_FOUND"):
        manager.events(sub, owner="a")


def test_real_subscription_ttl():
    manager = JobManager(subscription_ttl=.02, max_subscriptions=1)
    try:
        task = manager.start("r", lambda ctx: None)["task_id"]
        sub = manager.subscribe(task)["subscription_id"]
        time.sleep(.04)
        with pytest.raises(RuntimePolicyError, match="SUBSCRIPTION_EXPIRED"):
            manager.events(sub)
        assert manager.subscribe(task)["subscription_id"] != sub
    finally:
        manager.close()


def test_task_subscription_limits():
    manager = JobManager(max_tasks=1, max_subscriptions=1)
    release = threading.Event()
    try:
        task = manager.start("r", lambda ctx: release.wait(5))["task_id"]
        with pytest.raises(RuntimePolicyError, match="TASK_LIMIT"):
            manager.start("r2", lambda ctx: None)
        manager.subscribe(task, owner="a")
        with pytest.raises(RuntimePolicyError, match="SUBSCRIPTION_LIMIT"):
            manager.subscribe(task, owner="b")
        assert manager.close_owner("b") == 0
        assert manager.close_owner("a") == 1
        manager.subscribe(task, owner="b")
    finally:
        release.set()
        manager.close()


def test_worker_exception_is_sanitized(manager):
    def fail(ctx):
        raise ValueError("C:/private/password=secret-token")
    task = manager.start("r", fail)["task_id"]
    assert finished(manager, task)["state"] == "failed"
    encoded = json.dumps(manager.subscribe(task))
    assert "secret" not in encoded and "private" not in encoded and "ValueError" not in encoded


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "bad"}, {"x": object()},
                                  "x" * 32769, [None] * 1025],
                         ids=["nan", "infinity", "non-string-key", "object", "oversize", "wide-list"])
def test_invalid_worker_results_fail_without_leak(manager, value):
    task = manager.start("r", lambda ctx: value)["task_id"]
    assert finished(manager, task)["state"] == "failed"


@pytest.mark.parametrize("args", [(True, 1, "x", "X"), (-1, 1, "x", "X"),
                                  (2, 1, "x", "X"), (0, 1, "C:/private", "X"),
                                  (0, 1, "x", "password=secret")])
def test_invalid_progress_fails(manager, args):
    task = manager.start("r", lambda ctx: ctx.progress(*args))["task_id"]
    assert finished(manager, task)["state"] == "failed"


def test_close_cancels_and_joins_threads_and_clears_subscriptions():
    manager = JobManager()
    def worker(ctx):
        while True:
            ctx.check_cancelled()
            time.sleep(.002)
    tasks = [manager.start(str(i), worker)["task_id"] for i in range(4)]
    for task in tasks:
        manager.subscribe(task)
    manager.close()
    assert not manager._subscriptions
    assert all(manager.snapshot(task)["state"] == "cancelled" for task in tasks)
    assert all(not job.thread.is_alive() for job in manager._jobs.values())
    with pytest.raises(RuntimePolicyError, match="RUNTIME_CLOSED"):
        manager.start("r", worker)
    manager.close()


def test_uncooperative_worker_close_is_bounded_and_reported():
    release = threading.Event()
    manager = JobManager(close_timeout=.02)
    manager.start("r", lambda ctx: release.wait(1))
    time.sleep(.01)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimePolicyError, match="JOB_SHUTDOWN_TIMEOUT"):
            manager.close()
        assert time.monotonic() - started < .5
    finally:
        release.set()
        for job in manager._jobs.values():
            job.thread.join(1)
        manager.close()


def test_concurrent_cancel_has_one_terminal(manager):
    task = manager.start("r", lambda ctx: time.sleep(.04))["task_id"]
    callers = [threading.Thread(target=manager.cancel, args=(task,)) for _ in range(12)]
    for thread in callers:
        thread.start()
    for thread in callers:
        thread.join()
    assert finished(manager, task)["state"] == "cancelled"
    events = manager.subscribe(task)["events"]
    assert sum(e["state"] == "cancelling" for e in events) == 1
    assert sum(e["type"] == "terminal" for e in events) == 1


def test_queued_cancel_finalizes_abort_before_terminal(manager):
    called = []
    worker_entered = threading.Event()
    # Holding the existing runtime lock deterministically keeps the real worker
    # queued until cancel has been delivered; no sleeps or fake executor.
    with manager._lock:
        task = manager.start('queued-abort', lambda ctx: worker_entered.set(),
                             on_abort=lambda reason: called.append((reason, manager.snapshot(task)['state'])))['task_id']
        assert manager.snapshot(task)['state'] == 'queued'
        manager.cancel(task)
    assert finished(manager, task)['state'] == 'cancelled'
    assert called == [('cancelled', 'cancelling')]
    assert not worker_entered.is_set()
    manager.cancel(task)
    assert len(called) == 1
    assert sum(event['type'] == 'terminal' for event in manager.subscribe(task)['events']) == 1


def test_worker_failure_waits_for_abort_finalization(manager):
    entered, release = threading.Event(), threading.Event()
    called = []
    def work(ctx):
        raise ValueError('private-worker-secret')
    def abort(reason):
        called.append(reason)
        entered.set()
        assert release.wait(2)
    task = manager.start('failed-abort', work, on_abort=abort)['task_id']
    try:
        assert entered.wait(1)
        assert manager.snapshot(task)['state'] not in TERMINAL
    finally:
        release.set()
    assert finished(manager, task)['state'] == 'failed'
    assert called == ['failed']
    assert 'private-worker-secret' not in json.dumps(manager.subscribe(task))


@pytest.mark.parametrize('cancel', [False, True], ids=['worker-failure', 'queued-cancel'])
def test_abort_callback_exception_is_failed_terminal_without_leak(manager, cancel):
    called = []
    def work(ctx):
        raise ValueError('private-worker-secret')
    def abort(reason):
        called.append(reason)
        raise OSError('C:/private/abort-secret')
    with manager._lock:
        task = manager.start('abort-exception', work, on_abort=abort)['task_id']
        if cancel:
            manager.cancel(task)
    assert finished(manager, task)['state'] == 'failed'
    assert called == ['cancelled' if cancel else 'failed']
    response = manager.subscribe(task)
    assert sum(event['type'] == 'terminal' for event in response['events']) == 1
    assert 'secret' not in json.dumps(response) and 'private' not in json.dumps(response)
