"""Cooperative jobs with bounded, owner-scoped resumable event subscriptions."""
from collections import deque
from dataclasses import dataclass, field
import threading
import time
from uuid import uuid4

from .events import RuntimePolicyError, json_copy, token

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class _Cancelled(Exception):
    pass


@dataclass
class _Job:
    task_id: str
    request_id: str
    history: deque
    cancelled: threading.Event = field(default_factory=threading.Event)
    thread: object = None
    current: dict = field(default_factory=dict)
    committed: bool = False
    on_abort: object = None


class JobContext:
    def __init__(self, manager, job):
        self._manager = manager
        self._job = job

    @property
    def cancelled(self):
        return self._job.cancelled.is_set()

    def check_cancelled(self):
        if self._job.cancelled.is_set():
            raise _Cancelled()

    def commit(self, action):
        """Linearize a short local atomic commit against cancellation.

        The action must not take service locks or perform remote work. An action
        failure remains cancellable; after success cancellation cannot undo it.
        """
        if not callable(action):
            raise RuntimePolicyError('INVALID_COMMIT')
        with self._manager._lock:
            self.check_cancelled()
            if self._job.committed or self._job.current['state'] in TERMINAL:
                raise RuntimePolicyError('JOB_FINISHED')
            result = action()
            self._job.committed = True
            return result

    def progress(self, completed, total, stage, message_code, data=None):
        if type(completed) is not int or completed < 0 or completed > 2**53 - 1:
            raise RuntimePolicyError("INVALID_JOB_PROGRESS")
        if total is not None and (type(total) is not int or total < completed or total > 2**53 - 1):
            raise RuntimePolicyError("INVALID_JOB_PROGRESS")
        stage, message_code, data = token(stage), token(message_code), json_copy(data)
        with self._manager._lock:
            self.check_cancelled()
            if self._job.current["state"] in TERMINAL:
                raise RuntimePolicyError("JOB_FINISHED")
            if completed < self._job.current["completed"]:
                raise RuntimePolicyError("INVALID_JOB_PROGRESS")
            self._manager._emit(self._job, "running", "progress", completed=completed,
                                total=total, stage=stage, message_code=message_code, data=data)


class JobManager:
    def __init__(self, *, max_tasks=128, max_subscriptions=128, history_limit=128,
                 subscription_ttl=300.0, close_timeout=2.0):
        if any(type(v) is not int or v < 1 for v in (max_tasks, max_subscriptions, history_limit)):
            raise ValueError("Limits must be positive integers")
        if not 0 < subscription_ttl <= 86400 or not 0 < close_timeout <= 30:
            raise ValueError("Invalid time limit")
        self._max_tasks, self._max_subscriptions = max_tasks, max_subscriptions
        self._history_limit, self._ttl, self._close_timeout = history_limit, subscription_ttl, close_timeout
        self._jobs, self._subscriptions = {}, {}
        self._expired = deque(maxlen=max_subscriptions)
        self._lock = threading.RLock()
        self._closed = False

    def _emit(self, job, state, event_type, **fields):
        previous = job.current
        if previous.get("state") in TERMINAL:
            return
        job.current = {"task_id": job.task_id, "request_id": job.request_id,
                       "sequence": previous.get("sequence", 0) + 1, "type": event_type,
                       "state": state, "completed": previous.get("completed", 0),
                       "total": previous.get("total"), "stage": previous.get("stage", "queued"),
                       "message_code": "JOB_" + state.upper(), "data": None, **fields}
        job.history.append(json_copy(job.current, limit=65536))

    def _job(self, task_id):
        if type(task_id) is not str or task_id not in self._jobs:
            raise RuntimePolicyError("TASK_NOT_FOUND")
        return self._jobs[task_id]

    def start(self, request_id, worker, *, on_abort=None):
        if type(request_id) is not str or not request_id.strip() or len(request_id) > 128 or not callable(worker) or (on_abort is not None and not callable(on_abort)):
            raise RuntimePolicyError("INVALID_REQUEST")
        with self._lock:
            if self._closed:
                raise RuntimePolicyError("RUNTIME_CLOSED")
            if len(self._jobs) >= self._max_tasks:
                self._expire()
                subscribed = {item[0] for item in self._subscriptions.values()}
                retired = next((key for key, old in self._jobs.items()
                                if old.current['state'] in TERMINAL
                                and not old.thread.is_alive() and key not in subscribed), None)
                if retired is None:
                    raise RuntimePolicyError("TASK_LIMIT")
                # Capacity bounds retained receipts, not lifetime operations.
                # A live subscription pins its task until unsubscribe/expiry.
                del self._jobs[retired]
            job = _Job(str(uuid4()), request_id, deque(maxlen=self._history_limit))
            job.on_abort = on_abort
            self._jobs[job.task_id] = job
            self._emit(job, "queued", "state")
            initial = json_copy(job.current)
            job.thread = threading.Thread(target=self._run, args=(job, worker),
                                          name="PSModManager-job-" + job.task_id, daemon=True)
            job.thread.start()
            return initial

    def _run(self, job, worker):
        try:
            with self._lock:
                JobContext(self, job).check_cancelled()
                self._emit(job, "running", "state", stage="running")
            result = json_copy(worker(JobContext(self, job)))
            with self._lock:
                JobContext(self, job).check_cancelled()
                self._emit(job, "succeeded", "terminal", data=result)
        except _Cancelled:
            if job.on_abort is not None:
                try:
                    job.on_abort('cancelled')
                except Exception:
                    with self._lock:
                        self._emit(job, 'failed', 'terminal')
                    return
            with self._lock:
                self._emit(job, "cancelled", "terminal")
        except BaseException:
            if job.on_abort is not None:
                try:
                    job.on_abort('failed')
                except Exception:
                    with self._lock:
                        self._emit(job, 'failed', 'terminal')
                    return
            # Never forward exception text, filenames, arguments or traceback to JS.
            with self._lock:
                self._emit(job, "cancelled" if job.cancelled.is_set() else "failed", "terminal")

    def snapshot(self, task_id):
        with self._lock:
            return json_copy(self._job(task_id).current, limit=65536)

    def cancel(self, task_id):
        with self._lock:
            job = self._job(task_id)
            if job.current["state"] not in TERMINAL and not job.cancelled.is_set() and not job.committed:
                job.cancelled.set()
                self._emit(job, "cancelling", "state")
            return json_copy(job.current, limit=65536)

    def _expire(self):
        now = time.monotonic()
        for key, item in tuple(self._subscriptions.items()):
            if item[2] <= now:
                del self._subscriptions[key]
                self._expired.append(key)

    def _response(self, subscription_id, job, after_sequence):
        if type(after_sequence) is not int or not 0 <= after_sequence <= job.current["sequence"]:
            raise RuntimePolicyError("INVALID_SEQUENCE")
        resync = after_sequence < job.history[0]["sequence"] - 1
        return {"subscription_id": subscription_id, "snapshot": json_copy(job.current, limit=65536),
                "events": [] if resync else [json_copy(event, limit=65536) for event in job.history
                                               if event["sequence"] > after_sequence],
                "resync_required": resync}

    def subscribe(self, task_id, after_sequence=0, owner="desktop"):
        if type(owner) is not str or not owner or len(owner) > 128:
            raise RuntimePolicyError("INVALID_OWNER")
        with self._lock:
            if self._closed:
                raise RuntimePolicyError("RUNTIME_CLOSED")
            self._expire()
            job = self._job(task_id)
            if len(self._subscriptions) >= self._max_subscriptions:
                raise RuntimePolicyError("SUBSCRIPTION_LIMIT")
            key = str(uuid4())
            response = self._response(key, job, after_sequence)
            self._subscriptions[key] = (task_id, owner, time.monotonic() + self._ttl)
            return response

    def _subscription(self, subscription_id, owner):
        self._expire()
        if type(subscription_id) is not str or subscription_id not in self._subscriptions:
            code = "SUBSCRIPTION_EXPIRED" if subscription_id in self._expired else "SUBSCRIPTION_NOT_FOUND"
            raise RuntimePolicyError(code)
        item = self._subscriptions[subscription_id]
        if item[1] != owner:
            raise RuntimePolicyError("SUBSCRIPTION_NOT_FOUND")
        return item

    def events(self, subscription_id, after_sequence=0, owner="desktop"):
        with self._lock:
            task_id, _, _ = self._subscription(subscription_id, owner)
            result = self._response(subscription_id, self._job(task_id), after_sequence)
            self._subscriptions[subscription_id] = (task_id, owner, time.monotonic() + self._ttl)
            return result

    def unsubscribe(self, subscription_id, owner="desktop"):
        with self._lock:
            self._subscription(subscription_id, owner)
            del self._subscriptions[subscription_id]
            return True

    def close_owner(self, owner):
        with self._lock:
            keys = [key for key, item in self._subscriptions.items() if item[1] == owner]
            for key in keys:
                del self._subscriptions[key]
            return len(keys)

    def is_idle(self):
        with self._lock:
            return all(not job.thread.is_alive() for job in self._jobs.values())

    def close(self):
        with self._lock:
            self._closed = True
            self._subscriptions.clear()
            jobs = tuple(self._jobs.values())
            for job in jobs:
                self.cancel(job.task_id)
        deadline = time.monotonic() + self._close_timeout
        for job in jobs:
            if job.thread is not threading.current_thread():
                job.thread.join(max(0, deadline - time.monotonic()))
        if any(job.thread.is_alive() for job in jobs):
            raise RuntimePolicyError("JOB_SHUTDOWN_TIMEOUT")
