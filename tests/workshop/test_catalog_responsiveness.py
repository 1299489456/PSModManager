"""No real Steam/account actions: slow transports exercise actual service concurrency."""
from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace
import threading
import time

import pytest

from backend.workshop import WorkshopError, WorkshopService
from backend.workshop.remote import normalize
from backend.workshop.steam_session import EngineProvider, SessionEngine, SteamHelper
from test_cache_background import wait_until
from test_steam_session import setup, ONE
from test_workshop import installed, remote_item


def page(number=1):
    return dict(items=[normalize(dict(remote_item(), publishedfileid=str(1000+i)), source='steam_community')
                       for i in range((number-1)*30, number*30)], total=100, has_more=True)


def test_cold_catalog_is_immediate_deduplicated_and_two_source_pages_run_together(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    gate, calls = threading.Event(), []
    def fetch(**args):
        calls.append(args['page'])
        gate.wait(3)
        return page(args['page'])
    service.client.browse = fetch
    try:
        start = time.monotonic()
        result = service.browse(page_size=48)
        assert time.monotonic()-start < .3
        assert result['items'] == [] and result['loading'] and result['refreshing']
        wait_until(lambda: len(calls) == 2)
        for _ in range(4):
            service.browse(page_size=48)
        assert sorted(calls) == [1, 2]
        gate.set()
        result = wait_until(lambda: (value if not value['refreshing'] else None)
                            if (value := service.browse(page_size=48)) else None)
        assert [r['workshop_id'] for r in result['items']] == [str(1000+i) for i in range(48)]
        assert not result['loading'] and result['network_error'] is None
    finally:
        gate.set()
        service.close()


def test_partly_cached_page_keeps_absolute_offsets_and_refresh_failure_is_catalog_only(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    service.cache.put(('browse', '', 2, 'hot'), page(2))
    entered, gate = threading.Event(), threading.Event()
    def fetch(**args):
        entered.set()
        gate.wait(3)
        raise WorkshopError('WORKSHOP_NETWORK_ERROR', '公开请求失败', retryable=True)
    service.client.browse = fetch
    try:
        result = service.browse(page=2, page_size=24)
        assert [r['workshop_id'] for r in result['items']] == [str(1000+i) for i in range(30, 48)]
        assert result['partial'] and result['loading'] and entered.wait(1)
        gate.set()
        wait_until(lambda: all(f.done() for f in service._fetches.values()))
        result = service.browse(page=2, page_size=24)
        assert result['network_error']['code'] == 'WORKSHOP_NETWORK_ERROR'
        assert len(result['items']) == 18 and not result['loading']
        assert not service._steam_cache and not service._providers
    finally:
        gate.set()
        service.close()


def test_catalog_has_capacity_when_general_public_workers_are_blocked(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    gate, started, calls = threading.Event(), [], []
    def blocked(i):
        started.append(i)
        gate.wait(3)
    try:
        for i in range(3):
            service._fetch(('held', i), lambda i=i: blocked(i))
        wait_until(lambda: len(started) == 3)
        service.client.browse = lambda **args: (calls.append(args), page())[1]
        service.browse()
        wait_until(lambda: calls)
    finally:
        gate.set()
        service.close()


def test_mine_renders_local_records_before_remote_metadata(installed, tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    gate, entered = threading.Event(), threading.Event()
    def query(ids):
        entered.set()
        gate.wait(3)
        return [normalize(remote_item(), source='steam_public_api')]
    service.client.query = query
    try:
        start = time.monotonic()
        result = service.browse(installed[0], sort='mine')
        assert time.monotonic()-start < .3
        assert result['items'] and result['items'][0]['metadata_source'] == 'local_manifest'
        assert not result['loading'] and result['refreshing'] and entered.wait(1)
    finally:
        gate.set()
        service.close()


def cancelable_probe(service, api):
    entered, cancelled, closed = threading.Event(), threading.Event(), threading.Event()
    class Provider(EngineProvider):
        def snapshot(self, ids=()):
            entered.set()
            assert cancelled.wait(3), 'foreground must cancel our read-only wait'
            raise WorkshopError('WORKSHOP_SESSION_LOST', 'cancelled')
        def cancel_probe(self):
            cancelled.set()
        def close(self):
            closed.set()
            super().close()
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    return entered, closed


def test_foreground_save_cancels_probe_once_and_holds_off_reconnection(setup):
    service, installation, api, guard = setup
    entered, closed = cancelable_probe(service, api)
    service.status(installation)
    assert entered.wait(1) and guard.held
    start = time.monotonic()
    with service.foreground_operation(installation):
        assert time.monotonic()-start < .3 and closed.is_set() and not guard.held
        # The host's first save/launch lease now succeeds, without user retries.
        with guard.lease(installation):
            guard.check()
            for _ in range(3):
                service.status(installation, refresh=True)
            assert not service._probes and not service._providers
        with service.foreground_operation(installation):
            service.status(installation, refresh=True)
        assert service._foreground and not service._probes
    assert not service._foreground and not service._steam_cache and api.calls == []


def test_probe_ownership_proves_actual_lease_not_queued_thread_or_other_owner(setup):
    service, installation, api, guard = setup
    queued, allow_lease = threading.Event(), threading.Event()
    @contextmanager
    def delayed_lease(value):
        queued.set()
        assert allow_lease.wait(3)
        with guard.lease(value) as lease:
            yield lease
    service.write_guard = delayed_lease
    entered, _ = cancelable_probe(service, api)
    assert not service.probe_owns_lease(installation)
    service.status(installation)
    assert queued.wait(1) and service._probes and not guard.held
    assert not service.probe_owns_lease(installation)
    allow_lease.set()
    assert entered.wait(1) and guard.held
    assert service.probe_owns_lease(installation)
    key = service._key(installation)
    with service._lock:
        service._active[key] = 'actual-operation'
    assert not service.probe_owns_lease(installation)
    with service._lock:
        service._active.pop(key)
    with service.foreground_operation(installation):
        assert not service.probe_owns_lease(installation) and not guard.held
        with guard.lease(installation):
            assert not service.probe_owns_lease(installation)
    assert not service.probe_owns_lease(installation)


def test_foreground_does_not_discard_real_active_or_uncertain_actions(setup):
    service, installation, api, _ = setup
    key = service._key(installation)
    service._active[key] = 'active'
    with pytest.raises(WorkshopError) as failure:
        with service.foreground_operation(installation):
            pytest.fail('active operation cannot be cancelled as a probe')
    assert failure.value.code == 'WORKSHOP_OPERATION_BUSY'
    service._active.clear()
    service._operations['uncertain'] = (key, dict(items=[dict(state='uncertain', workshop_id=ONE)]))
    with pytest.raises(WorkshopError):
        with service.foreground_operation(installation):
            pytest.fail('uncertain evidence cannot be erased')
    assert service._operations['uncertain'][1]['items'][0]['state'] == 'uncertain'


def test_close_cancels_read_probe_and_releases_installation_lease(setup):
    service, installation, api, guard = setup
    entered, closed = cancelable_probe(service, api)
    service.status(installation)
    assert entered.wait(1)
    start = time.monotonic()
    service.close()
    assert time.monotonic()-start < .3
    assert closed.is_set() and not service._probes and not guard.held


def test_foreground_preserves_legacy_demo_contract_without_discarding_existing_state(setup):
    service, installation, _, _ = setup
    demo = dict(installation, app_id='3513980')
    service.steam_factory = lambda _: pytest.fail('legacy launch must not initialize Steam')
    with service.foreground_operation(demo):
        assert not service._foreground
    key = installation['canonical_root'].casefold()
    service._active[key] = 'existing'
    with pytest.raises(WorkshopError) as failure:
        with service.foreground_operation(demo):
            pytest.fail('changing app metadata cannot hide tracked activity')
    assert failure.value.code == 'WORKSHOP_OPERATION_BUSY'
    service._active.clear()


def test_owned_helper_wait_is_interruptible_without_waiting_sdk_timeout():
    helper = SteamHelper(dict(canonical_root='unused'))
    entered = threading.Event()
    class Pipe:
        def poll(self, delay):
            entered.set()
            time.sleep(delay)
            return False
    helper._connection = Pipe()
    errors = []
    def wait():
        try:
            helper._receive(20)
        except WorkshopError as error:
            errors.append(error.code)
    thread = threading.Thread(target=wait)
    thread.start()
    assert entered.wait(1)
    start = time.monotonic()
    helper.cancel_probe()
    thread.join(1)
    assert not thread.is_alive() and time.monotonic()-start < .3
    assert errors == ['WORKSHOP_SESSION_LOST']
    with pytest.raises(WorkshopError):
        helper._start()  # Cancellation cannot restart a helper after yielding.
