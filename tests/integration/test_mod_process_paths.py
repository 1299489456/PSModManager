"""Ordinary process-path checks without private NT mapping queries."""
from types import SimpleNamespace

import psutil
import pytest

from backend.deployments import process_guard
from tests.integration.test_deployment_guards import (app, simulator, status, write_guarded,
    running, without_debug_privilege)


def test_ordinary_guard_never_uses_legacy_nt_mapping_query(app, monkeypatch):
    context, installation_id, _ = app
    def forbidden(*args, **kwargs):
        raise AssertionError('Legacy NT mapping query must not be on the product path')
    monkeypatch.setattr(process_guard, 'file_process_ids', forbidden)
    monkeypatch.setattr(process_guard, 'same_process_image', forbidden)
    assert status(app)['ready']
    target = context.deployment_guards.prepare_target(installation_id, 'mods', 'nested/sentinel.dll')
    with context.deployment_guards.operation(installation_id, 'apply') as session:
        write_guarded(session, target)


def test_unrelated_protected_process_is_not_queried_or_a_blocker(app, monkeypatch):
    def forbidden():
        raise AssertionError('Unrelated protected executable path was queried')
    unrelated = SimpleNamespace(pid=12345, info={'name':'protected-system.exe'}, exe=forbidden)
    monkeypatch.setattr(psutil, 'process_iter', lambda attrs: iter([unrelated]))
    assert status(app)['ready']


def test_same_name_unreadable_process_is_still_unknown(app, monkeypatch):
    _, _, game = app
    def denied():
        raise psutil.AccessDenied(pid=12345)
    matching = SimpleNamespace(pid=12345, info={'name':(game/'Probably Stolen.exe').name}, exe=denied)
    monkeypatch.setattr(psutil, 'process_iter', lambda attrs: iter([matching]))
    result = status(app)
    assert not result['ready'] and result['process_status'] == 'unknown'
    assert 'GUARD_PROCESS_UNKNOWN' in result['blockers']


def test_real_protected_target_process_still_blocks_operation(app, tmp_path):
    context, installation_id, game = app
    with without_debug_privilege(), running(game/'Probably Stolen.exe', tmp_path, protected=True) as process:
        result = status(app)
        assert not result['ready'] and result['process_status'] in {'running', 'unknown'}
        with pytest.raises(process_guard.GuardRejected):
            with context.deployment_guards.operation(installation_id, 'apply'):
                pytest.fail('A live protected target process must block writes')
        assert process.poll() is None
