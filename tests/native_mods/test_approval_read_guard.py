"""Read-loop throttling never throttles package or write boundaries."""
import pytest

from backend.deployments.paths import GuardRejected
from backend.native_mods import NativeModError
from backend.native_mods.service import _ReadGuard
from tests.native_mods.test_native_service import env, package


def test_read_guard_polls_long_reads_but_finish_is_immediate(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('backend.native_mods.service.time.monotonic', lambda: clock[0])
    calls = []
    guard = _ReadGuard(lambda: calls.append(clock[0]))
    for _ in range(1000):
        guard()
    assert calls == [10.0]
    clock[0] = 10.5
    guard()
    assert calls == [10.0, 10.5]
    clock[0] = 10.6
    guard.finish()
    assert calls == [10.0, 10.5, 10.6]


def test_read_guard_propagates_process_failure_within_poll_bound(monkeypatch):
    clock = [10.0]; running = [False]
    monkeypatch.setattr('backend.native_mods.service.time.monotonic', lambda: clock[0])
    def check():
        if running[0]:
            raise GuardRejected('GUARD_PROCESS_RUNNING')
    guard = _ReadGuard(check)
    running[0] = True; clock[0] += guard.interval
    with pytest.raises(GuardRejected, match='GUARD_PROCESS_RUNNING'):
        guard()


def test_large_declared_dll_has_bounded_full_checks(env):
    svc, inst, game, _, _, _, lease = env
    folder = package(game / 'Mods', 'a')
    dll = folder / 'Main.dll'
    dll.write_bytes(dll.read_bytes() + b'\0' * (16 * 1024 * 1024))
    snapshot = svc.snapshot(inst)
    result = svc.apply(inst, snapshot['revision'], ['a'])
    assert result['mods'][0]['approval']['state'] == 'approved'
    # Both required DLL fingerprint passes still run. Full guard queries do not
    # scale with the 64 chunks in each pass; write boundaries remain immediate.
    assert lease.calls < 32


def test_final_write_boundary_is_not_throttled(env, monkeypatch):
    svc, inst, game, _, _, path, lease = env
    package(game / 'Mods', 'a'); before = path.read_bytes()
    finish = svc._finish
    def game_starts(folder, record, status='committed', **values):
        finish(folder, record, status, **values)
        if status == 'prepared':
            lease.fail_at = lease.calls + 1
    monkeypatch.setattr(svc, '_finish', game_starts)
    with pytest.raises(NativeModError, match='NATIVE_PROCESS_UNKNOWN'):
        svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    assert path.read_bytes() == before
