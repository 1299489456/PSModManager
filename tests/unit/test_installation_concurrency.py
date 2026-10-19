"""Deterministic commit/cancel and publication races, using real threads/SQLite."""
import json
import sqlite3
import threading

import pytest

from backend.app.installations import InstallationService
from backend.runtime.events import RuntimePolicyError
from backend.runtime.jobs import JobManager


def finish(manager, task_id):
    manager._jobs[task_id].thread.join(5)
    assert not manager._jobs[task_id].thread.is_alive()
    return manager.snapshot(task_id)


def database(path):
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE effects(value TEXT)')


def rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute('SELECT value FROM effects').fetchall()


def test_cancel_before_commit_never_performs_sqlite_action(tmp_path):
    path = tmp_path / 'facts.db'
    database(path)
    ready, resume = threading.Event(), threading.Event()
    manager = JobManager()
    def worker(task):
        ready.set()
        assert resume.wait(5)
        def action():
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO effects VALUES('committed')")
        task.commit(action)
        return {'committed': True}
    task_id = manager.start('before', worker)['task_id']
    assert ready.wait(5)
    assert manager.cancel(task_id)['state'] == 'cancelling'
    resume.set()
    assert finish(manager, task_id)['state'] == 'cancelled'
    assert rows(path) == []
    manager.close()


def test_cancel_after_commit_keeps_real_effect_and_success(tmp_path):
    path = tmp_path / 'facts.db'
    database(path)
    committed, resume = threading.Event(), threading.Event()
    manager = JobManager()
    def worker(task):
        def action():
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO effects VALUES('committed')")
            return {'committed': True}
        result = task.commit(action)
        committed.set()
        assert resume.wait(5)
        task.progress(1, 1, 'complete', 'COMMITTED')
        return result
    task_id = manager.start('after', worker)['task_id']
    assert committed.wait(5)
    assert rows(path) == [('committed',)]
    assert manager.cancel(task_id)['state'] == 'running'
    assert not manager._jobs[task_id].cancelled.is_set()
    resume.set()
    final = finish(manager, task_id)
    assert final['state'] == 'succeeded' and final['data'] == {'committed': True}
    manager.close()


def test_failed_atomic_action_rolls_back_and_remains_cancellable(tmp_path):
    path = tmp_path / 'facts.db'
    database(path)
    failed, resume = threading.Event(), threading.Event()
    manager = JobManager()
    def worker(task):
        def action():
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO effects VALUES('rolled back')")
                raise ValueError('controlled transaction failure')
        with pytest.raises(ValueError):
            task.commit(action)
        failed.set()
        assert resume.wait(5)
        task.check_cancelled()
    task_id = manager.start('failed', worker)['task_id']
    assert failed.wait(5)
    assert rows(path) == [] and not manager._jobs[task_id].committed
    assert manager.cancel(task_id)['state'] == 'cancelling'
    resume.set()
    assert finish(manager, task_id)['state'] == 'cancelled'
    manager.close()


def test_close_does_not_cancel_already_committed_job():
    committed, resume, close_cancel = threading.Event(), threading.Event(), threading.Event()
    class ObservedManager(JobManager):
        def cancel(self, task_id):
            result = super().cancel(task_id)
            close_cancel.set()
            return result
    manager = ObservedManager(close_timeout=5)
    def worker(task):
        task.commit(lambda: None)
        committed.set()
        assert resume.wait(5)
        return {'committed': True}
    task_id = manager.start('closing', worker)['task_id']
    assert committed.wait(5)
    closer = threading.Thread(target=manager.close)
    closer.start()
    assert close_cancel.wait(5)
    assert not manager._jobs[task_id].cancelled.is_set()
    resume.set()
    closer.join(5)
    assert not closer.is_alive()
    assert manager.snapshot(task_id)['state'] == 'succeeded'


def test_fast_worker_cannot_release_ownership_before_start_publication():
    ran, held, publish = threading.Event(), threading.Event(), threading.Event()
    class PausedStart(JobManager):
        def start(self, request_id, worker):
            result = super().start(request_id, worker)
            assert ran.wait(5)
            held.set()
            assert publish.wait(5)
            return result
    manager = PausedStart()
    service = InstallationService(None)
    results = []
    def operation(task):
        ran.set()
        return {'done': True}
    starter = threading.Thread(target=lambda: results.append(service._start(manager, 'fast', operation)))
    starter.start()
    assert held.wait(5)
    acquired = service._lock.acquire(blocking=False)
    if acquired:
        service._lock.release()
    publish.set()
    starter.join(5)
    assert not acquired, 'service ownership must remain locked until jobs.start is published'
    assert not starter.is_alive()
    assert service._active_job[1] == results[0]['task_id']
    assert finish(manager, results[0]['task_id'])['state'] == 'succeeded'
    manager.close()


def test_active_operation_rejects_second_worker():
    ready, resume = threading.Event(), threading.Event()
    manager = JobManager()
    service = InstallationService(None)
    def worker(task):
        ready.set()
        assert resume.wait(5)
        return {'done': True}
    first = service._start(manager, 'one', worker)
    assert ready.wait(5)
    with pytest.raises(RuntimePolicyError, match='INSTALLATION_BUSY'):
        service._start(manager, 'two', lambda task: None)
    resume.set()
    assert finish(manager, first['task_id'])['state'] == 'succeeded'
    assert len(manager._jobs) == 1
    manager.close()


@pytest.mark.parametrize('sources', [[], [{'kind': 'unknown', 'root': 'C:/game'}], [{'kind': 'manual', 'root': 'relative'}], [{'kind': 'manual', 'root': 'C:/game', 'expiry': 1}]])
def test_saved_provenance_strictly_rejected(sources):
    reference = json.dumps({'format': 'psmm.installation-source.v1', 'discovery_sources': sources, 'file_facts': 'SHA256'})
    with pytest.raises(RuntimePolicyError, match='INVALID_INSTALLATION_SOURCE'):
        InstallationService._saved_sources(reference, 'C:/fallback')


def test_legacy_provenance_keeps_manual_compatibility():
    assert InstallationService._saved_sources('original file facts', 'C:/game') == [{'kind': 'manual', 'root': 'C:/game'}]
