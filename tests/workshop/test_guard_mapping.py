from contextlib import contextmanager
from copy import deepcopy
import time

import pytest

from backend.deployments.paths import GuardRejected
from backend.workshop import WorkshopError
from backend.workshop.steam_session import EngineProvider, SessionEngine
from test_steam_session import setup, ONE, wait_status, wait_operation


@pytest.mark.parametrize('state,blockers,expected', [
    ('stopped', ['GUARD_RECOVERY_REQUIRED'], 'WORKSHOP_RECOVERY_REQUIRED'),
    ('stopped', ['GUARD_RECOVERY_REQUIRED', 'GUARD_LEASE_BUSY'], 'WORKSHOP_RECOVERY_REQUIRED'),
    ('unknown', ['GUARD_INSTALLATION_CHANGED'], 'WORKSHOP_INSTALLATION_CHANGED'),
    ('unknown', ['GUARD_IDENTITY_CHANGED'], 'WORKSHOP_INSTALLATION_CHANGED'),
    ('unknown', ['GUARD_PATH_UNAVAILABLE'], 'WORKSHOP_INSTALLATION_UNAVAILABLE'),
    ('stopped', ['GUARD_REPARSE_POINT'], 'WORKSHOP_INSTALLATION_UNAVAILABLE'),
    ('stopped', ['GUARD_LEASE_BUSY'], 'WORKSHOP_INSTALLATION_BUSY'),
    ('stopped', ['GUARD_LEASE_LOST'], 'WORKSHOP_OPERATION_INTERRUPTED'),
    ('running', ['GUARD_PROCESS_RUNNING'], 'WORKSHOP_PROCESS_RUNNING'),
    ('unknown', ['GUARD_PROCESS_UNKNOWN'], 'WORKSHOP_PROCESS_UNKNOWN'),
    ('stopped', ['GUARD_UNRECOGNIZED'], 'WORKSHOP_GUARD_UNAVAILABLE'),
    ('stopped', [], 'WORKSHOP_GUARD_UNAVAILABLE'),
])
def test_guard_blockers_have_consistent_safe_status_capability_and_action_codes(setup, state, blockers, expected):
    service, installation, api, _ = setup
    service.process_status = lambda _: dict(state=state, writable=False, blockers=deepcopy(blockers))
    service.steam_factory = lambda _: pytest.fail('blocked status must not initialize Steam')
    result = service.status(installation, refresh=True)
    assert result['session']['reason_code'] == expected
    assert result['runtime']['blockers'] == blockers
    for name in ('direct_updates', 'direct_subscription'):
        capability = result['capabilities'][name]
        assert not capability['available'] and capability['reason_code'] == expected
        assert capability['blockers'] == blockers
    with pytest.raises(WorkshopError) as caught:
        service.action(installation, action='update', ids=[ONE])
    assert caught.value.code == expected and api.calls == []


@pytest.mark.parametrize('guard_code,expected', [
    ('GUARD_RECOVERY_REQUIRED', 'WORKSHOP_RECOVERY_REQUIRED'),
    ('GUARD_INSTALLATION_CHANGED', 'WORKSHOP_INSTALLATION_CHANGED'),
    ('GUARD_LEASE_BUSY', 'WORKSHOP_INSTALLATION_BUSY'),
    ('GUARD_PROCESS_RUNNING', 'WORKSHOP_PROCESS_RUNNING'),
    ('GUARD_UNRECOGNIZED', 'WORKSHOP_GUARD_UNAVAILABLE'),
])
def test_probe_guard_race_uses_exception_code_without_exposing_native_details(setup, guard_code, expected):
    service, installation, api, _ = setup
    @contextmanager
    def rejected(_):
        raise GuardRejected(guard_code, native_error='PRIVATE_PATH_OR_ACCOUNT', attempts=37)
        yield
    service.write_guard = rejected
    service.steam_factory = lambda _: pytest.fail('guard denial must precede Steam')
    result = wait_status(service, installation)
    assert result['session']['reason_code'] == expected
    assert result['session']['last_error']['code'] == expected
    assert result['capabilities']['direct_updates']['reason_code'] == expected
    assert 'PRIVATE_PATH_OR_ACCOUNT' not in str(result) and api.calls == []


def test_unknown_probe_guard_failure_is_not_an_unconfirmed_operation(setup):
    service, installation, _, _ = setup
    @contextmanager
    def rejected(_):
        raise RuntimeError('PRIVATE native failure')
        yield
    service.write_guard = rejected
    result = wait_status(service, installation)
    assert result['session']['reason_code'] == 'WORKSHOP_GUARD_UNAVAILABLE'
    assert result['operations'] == [] and 'PRIVATE' not in str(result)


def test_manual_refresh_retries_failed_probe_inside_ttl_without_extra_mutations(setup):
    service, installation, api, _ = setup
    calls = []
    class Provider(EngineProvider):
        def snapshot(self, ids=()):
            calls.append(True)
            if len(calls) == 1:
                raise WorkshopError('WORKSHOP_STEAM_OFFLINE', 'offline', retryable=True)
            return super().snapshot(ids)
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    assert wait_status(service, installation)['session']['reason_code'] == 'WORKSHOP_STEAM_OFFLINE'
    assert not service.status(installation)['session']['refreshing'] and len(calls) == 1
    service.status(installation, refresh=True)
    deadline = time.monotonic() + 2
    while service._probes and time.monotonic() < deadline:
        time.sleep(.01)
    result = service.status(installation, _probe=False)
    assert result['session']['state'] == 'ready' and len(calls) == 2
    assert result['capabilities']['direct_updates']['available'] and api.calls == []


def test_action_guard_race_reports_recovery_not_process_or_pending(setup):
    service, installation, api, _ = setup
    @contextmanager
    def rejected(_):
        raise GuardRejected('GUARD_RECOVERY_REQUIRED')
        yield
    service.write_guard = rejected
    result = service.action(installation, action='update', ids=[ONE])
    operation = wait_operation(service, result['operation_id'])
    assert operation['state'] == 'failed'
    assert operation['items'][0]['error']['code'] == 'WORKSHOP_RECOVERY_REQUIRED'
    assert api.calls == []


def test_refresh_after_recovery_is_cleared_does_not_wait_for_failure_ttl(setup):
    service, installation, api, guard = setup
    @contextmanager
    def rejected(_):
        raise GuardRejected('GUARD_RECOVERY_REQUIRED')
        yield
    service.write_guard = rejected
    assert wait_status(service, installation)['session']['reason_code'] == 'WORKSHOP_RECOVERY_REQUIRED'
    service.write_guard = guard.lease
    assert not service.status(installation)['session']['refreshing']
    service.status(installation, refresh=True)
    deadline = time.monotonic() + 2
    while service._probes and time.monotonic() < deadline:
        time.sleep(.01)
    result = service.status(installation, _probe=False)
    assert result['session']['state'] == 'ready'
    assert result['capabilities']['direct_updates']['available'] and api.calls == []


def test_recovery_blocker_takes_precedence_over_owned_probe_label(setup):
    service, _, _, _ = setup
    runtime = dict(state='stopped', writable=False, blockers=['GUARD_RECOVERY_REQUIRED', 'GUARD_LEASE_BUSY'],
                   busy_owner='workshop_probe')
    capability = service._capabilities(runtime, dict(state='ready'))['direct_updates']
    assert capability['reason_code'] == 'WORKSHOP_RECOVERY_REQUIRED'
    assert capability['blockers'] == runtime['blockers']


def test_uncertain_receipt_remains_blocked_and_is_not_reclassified_as_guard_busy(setup):
    service, installation, _, _ = setup
    key = service._key(installation)
    service._operations['old'] = (key, dict(operation_id='old', action='update', state='uncertain',
        items=[dict(workshop_id=ONE, state='uncertain', error=None)]))
    with pytest.raises(WorkshopError) as caught:
        service.action(installation, action='update', ids=[ONE])
    assert caught.value.code == 'WORKSHOP_OPERATION_BUSY'
    assert service.is_busy()
