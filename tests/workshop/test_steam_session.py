from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from backend.workshop import WorkshopError, WorkshopService
from backend.workshop.errors import MESSAGES
from backend.workshop.steam_session import EngineProvider, SessionEngine, SteamHelper

ONE, TWO, THREE = '3828868413', '3828902092', '3829106910'


class FakeSDK:
    def __init__(self, ids=(ONE, TWO), *, auto=False):
        self.rows = {identity: self.blank(identity, subscribed=True, steam_installed=True, needs_update=True) for identity in ids}
        self.calls, self.events, self.waiting = [], [], []
        self.auto, self.serial, self.closed = auto, 100, False

    @staticmethod
    def blank(identity, **extra):
        return dict(workshop_id=identity, subscribed=extra.get('subscribed', False),
                    steam_installed=extra.get('steam_installed', False), needs_update=extra.get('needs_update', False),
                    downloading=False, download_pending=False, disabled_locally=False,
                    bytes_downloaded=None, bytes_total=None, progress=None)

    def ensure_online(self):
        pass

    def subscription_ids(self):
        return [key for key, value in self.rows.items() if value['subscribed']]

    def item(self, identity):
        return deepcopy(self.rows.get(identity, self.blank(identity)))

    def begin(self, action, identity):
        self.serial += 1
        self.calls.append((action, identity))
        if action == 'update':
            self.rows[identity]['download_pending'] = True
        self.waiting.append((action, identity, self.serial))
        return True if action == 'update' else self.serial

    def complete(self, action, identity, call, result=1):
        row = self.rows.setdefault(identity, self.blank(identity))
        if result == 1:
            if action == 'update':
                row.update(steam_installed=True, needs_update=False, downloading=False, download_pending=False,
                           bytes_downloaded=100, bytes_total=100, progress=1.0)
            else:
                row['subscribed'] = action == 'subscribe'
        self.events.append(dict(kind='download', workshop_id=identity, result=result) if action == 'update' else
                           dict(kind='call', workshop_id=identity, call=call, callback=1313 if action == 'subscribe' else 1315,
                                result=result, io_failure=False))

    def callbacks(self):
        if self.auto:
            for value in self.waiting:
                self.complete(*value)
            self.waiting = []
        events, self.events = self.events, []
        return events

    def close(self):
        self.closed = True


def test_update_distinguishes_acceptance_progress_callback_and_install_state():
    api = FakeSDK()
    engine = SessionEngine(api)
    first = engine.begin('operation', 'update', [ONE])
    assert first['items'][0]['state'] == 'download_pending'
    assert not first['items'][0]['download_completed']
    api.rows[ONE].update(downloading=True, bytes_downloaded=25, bytes_total=100, progress=.25)
    assert engine.operation('operation')['items'][0]['progress'] == .25
    assert engine.operation('operation')['items'][0]['state'] == 'downloading'
    # A success callback alone cannot override still-downloading state.
    api.events.append(dict(kind='download', workshop_id=ONE, result=1))
    assert engine.operation('operation')['state'] == 'running'
    api.rows[ONE].update(downloading=False, download_pending=False, needs_update=False)
    result = engine.operation('operation')
    assert result['state'] == 'completed' and result['items'][0]['callback_confirmed']


def test_subscribe_callback_is_not_download_completion():
    api = FakeSDK(())
    engine = SessionEngine(api)
    engine.begin('sub', 'subscribe', [ONE])
    api.complete(*api.waiting[0])
    row = engine.operation('sub')['items'][0]
    assert row['state'] == 'completed' and row['subscription_confirmed']
    assert not row['download_completed'] and not api.rows[ONE]['steam_installed']


def test_callback_id_handle_and_item_must_match():
    api = FakeSDK()
    engine = SessionEngine(api)
    engine.begin('unsub', 'unsubscribe', [ONE])
    api.events.append(dict(kind='call', call=999, callback=1315, result=1, workshop_id=ONE))
    assert engine.operation('unsub')['state'] == 'running'
    api.events.append(dict(kind='call', call=101, callback=1313, result=1, workshop_id=ONE))
    assert engine.operation('unsub')['state'] == 'running'
    api.events.append(dict(kind='call', call=101, callback=1315, result=1, workshop_id=TWO))
    assert engine.operation('unsub')['state'] == 'uncertain'


def test_timeout_blocks_duplicates_and_late_callback_resolves_without_replay():
    tick = [0]
    api = FakeSDK()
    engine = SessionEngine(api, clock=lambda: tick[0], call_timeout=5)
    engine.begin('unsub', 'unsubscribe', [ONE])
    tick[0] = 6
    assert engine.operation('unsub')['state'] == 'uncertain'
    with pytest.raises(WorkshopError) as exc:
        engine.begin('duplicate', 'unsubscribe', [ONE])
    assert exc.value.code == 'WORKSHOP_OPERATION_BUSY'
    api.complete(*api.waiting[0])
    assert engine.operation('unsub')['state'] == 'completed'
    assert len(api.calls) == 1


def test_batch_partial_failure_keeps_success():
    api = FakeSDK()
    engine = SessionEngine(api)
    engine.begin('update', 'update', [ONE, TWO])
    api.complete(*api.waiting[0])
    api.complete(*api.waiting[1], result=15)
    result = engine.operation('update')
    assert result['state'] == 'partial'
    assert [row['state'] for row in result['items']] == ['completed', 'failed']
    assert result['items'][1]['steam_result'] == 15


def test_existing_download_is_observed_not_requeued():
    api = FakeSDK()
    api.rows[ONE]['downloading'] = True
    engine = SessionEngine(api)
    engine.begin('update', 'update', [ONE])
    assert api.calls == []
    api.complete('update', ONE, 0)
    assert engine.operation('update')['state'] == 'completed'


def test_current_app_membership_prevents_foreign_item_update_or_unsubscribe():
    api = FakeSDK()
    api.rows[THREE] = api.blank(THREE, subscribed=True, steam_installed=True)
    api.subscription_ids = lambda: [ONE, TWO]
    engine = SessionEngine(api)
    assert engine.begin('other-update', 'update', [THREE])['items'][0]['error']['code'] == 'WORKSHOP_NOT_SUBSCRIBED'
    assert engine.begin('other-unsub', 'unsubscribe', [THREE])['items'][0]['state'] == 'already_current'
    assert api.calls == []


def test_session_snapshot_uses_actual_subscription_and_state_not_acf():
    api = FakeSDK()
    snapshot = SessionEngine(api).snapshot([THREE])
    assert snapshot['session']['state'] == 'ready' and snapshot['subscribed_ids'] == [ONE, TWO]
    assert snapshot['items'][2]['subscribed'] is False


class Guard:
    def __init__(self):
        self.lock = threading.Lock()
        self.held = False
        self.state = 'stopped'
        self.checks = 0
        self.fail_at = None

    def status(self, _):
        return dict(state=self.state, writable=self.state == 'stopped' and not self.held)

    @contextmanager
    def lease(self, _):
        if not self.lock.acquire(False):
            raise RuntimeError('fixture lease busy')
        self.held = True
        try:
            yield self
        finally:
            self.held = False
            self.lock.release()

    def check(self):
        self.checks += 1
        if self.state != 'stopped' or self.fail_at == self.checks:
            raise RuntimeError('fixture game started')


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'steamapps/common/Probably Stolen'
    root.mkdir(parents=True)
    installation = dict(canonical_root=str(root), app_id='4348910')
    api, guard = FakeSDK(auto=True), Guard()
    provider = lambda _: EngineProvider(SessionEngine(api))
    service = WorkshopService(SimpleNamespace(root=tmp_path / 'owned'), process_status=guard.status,
                              write_guard=guard.lease, steam_factory=provider)
    service.client.query = lambda ids: [dict(workshop_id=i, app_id='4348910', available=True) for i in ids]
    yield service, installation, api, guard
    service.close()


def wait_operation(service, identity):
    until = time.monotonic() + 4
    while time.monotonic() < until:
        with service._lock:
            value = deepcopy(service._operations[identity][1])
        if value['state'] in ('completed', 'partial', 'failed', 'uncertain'):
            return value
        time.sleep(.02)
    pytest.fail('operation did not finish')


def wait_status(service, installation):
    service.status(installation)
    until = time.monotonic() + 3
    while time.monotonic() < until:
        if not service._probes:
            return service.status(installation, _probe=False)
        time.sleep(.01)
    pytest.fail('background probe did not finish')


def test_service_real_engine_roundtrip_and_terminal_releases_lease(setup):
    service, installation, api, guard = setup
    assert wait_status(service, installation)['items'][0]['needs_update'] is True
    op = service.action(installation, action='update', ids=[ONE, TWO])
    final = wait_operation(service, op['operation_id'])
    assert final['state'] == 'completed' and not guard.held
    status = wait_status(service, installation)
    assert status['runtime']['writable'] and status['capabilities']['direct_updates']['available']
    assert all(not row['needs_update'] for row in status['items'])
    op = service.action(installation, action='unsubscribe', ids=[ONE])
    assert wait_operation(service, op['operation_id'])['state'] == 'completed'
    op = service.action(installation, action='subscribe', ids=[ONE])
    assert wait_operation(service, op['operation_id'])['state'] == 'completed'
    assert api.calls == [('update', ONE), ('update', TWO), ('unsubscribe', ONE), ('subscribe', ONE)]


@pytest.mark.parametrize('state', ['running', 'unknown'])
def test_running_unknown_never_creates_provider_or_modifies(setup, state):
    service, installation, api, guard = setup
    guard.state = state
    service.steam_factory = lambda _: pytest.fail('must not start helper')
    assert service.status(installation)['session']['state'] == 'unavailable'
    with pytest.raises(WorkshopError):
        service.action(installation, action='update', ids=[ONE])
    assert api.calls == []


def test_passive_probe_owns_shared_launch_lease(setup):
    service, installation, api, guard = setup
    observed = []
    class Provider(EngineProvider):
        def snapshot(self, ids=()):
            observed.append(guard.held)
            return super().snapshot(ids)
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    wait_status(service, installation)
    assert observed == [True] and not guard.held
    with guard.lease(installation):
        service._steam_cache.clear()
        assert service.status(installation)['session']['state'] == 'unavailable'
    assert observed == [True]


def test_duplicate_active_batch_does_not_submit_twice(setup):
    service, installation, api, guard = setup
    api.auto = False
    first = service.action(installation, action='update', ids=[ONE])
    # The same batch is idempotently observed, not submitted a second time.
    again = service.action(installation, action='update', ids=[ONE])
    assert again['operation_id'] == first['operation_id']
    until = time.monotonic() + 1
    while not api.calls and time.monotonic() < until:
        time.sleep(.01)
    assert api.calls == [('update', ONE)]
    api.complete(*api.waiting[0])
    assert wait_operation(service, first['operation_id'])['state'] == 'completed'


def test_second_submission_error_preserves_prior_and_unattempted_states(setup):
    service, installation, api, guard = setup
    class Provider(EngineProvider):
        def begin(self, operation_id, action, ids):
            if ids == [TWO]:
                raise WorkshopError('WORKSHOP_SESSION_TIMEOUT', MESSAGES['WORKSHOP_SESSION_TIMEOUT'])
            result = super().begin(operation_id, action, ids)
            return self.operation(operation_id)  # First one confirms immediately.
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    result = service.action(installation, action='update', ids=[ONE, TWO, THREE])
    final = wait_operation(service, result['operation_id'])
    assert [row['state'] for row in final['items']] == ['completed', 'uncertain', 'failed']
    assert api.calls == [('update', ONE)] and not guard.held


def test_second_poll_error_preserves_just_confirmed_first_result(setup):
    service, installation, api, guard = setup
    api.auto = False
    class Provider(EngineProvider):
        def operation(self, operation_id):
            if operation_id.endswith(':1'):
                raise WorkshopError('WORKSHOP_SESSION_TIMEOUT', MESSAGES['WORKSHOP_SESSION_TIMEOUT'])
            api.complete(*api.waiting[0])
            return super().operation(operation_id)
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    result = service.action(installation, action='update', ids=[ONE, TWO])
    final = wait_operation(service, result['operation_id'])
    assert [row['state'] for row in final['items']] == ['completed', 'uncertain']
    assert final['items'][0]['callback_confirmed'] and not guard.held


def test_manual_installation_release_keeps_native_launch_available(setup, tmp_path):
    service, _, _, _ = setup
    installation = dict(canonical_root=str(tmp_path / 'manual-game'))
    service.steam_factory = lambda _: pytest.fail('manual installation must not start Steam helper')
    service.release(installation)
    assert service.status(installation)['session']['reason_code'] == 'STEAM_LIBRARY_NOT_DETECTED'
    with pytest.raises(WorkshopError) as exc:
        service.action(installation, action='update', ids=[ONE])
    assert exc.value.code == 'STEAM_LIBRARY_NOT_DETECTED'
    service._operations['old'] = (service._key(installation), dict(action='update', state='uncertain',
        items=[dict(workshop_id=ONE, state='uncertain')]))
    with pytest.raises(WorkshopError) as exc:
        service.release(installation)
    assert exc.value.code == 'WORKSHOP_OPERATION_BUSY'


def test_game_start_mid_batch_stops_later_submission(setup):
    service, installation, api, guard = setup
    class Provider(EngineProvider):
        def begin(self, *args):
            result = super().begin(*args)
            guard.state = 'running'
            return result
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    result = service.action(installation, action='update', ids=[ONE, TWO])
    final = wait_operation(service, result['operation_id'])
    assert api.calls == [('update', ONE)]
    assert final['items'][0]['state'] == 'uncertain' and final['items'][1]['state'] == 'failed'
    assert final['items'][0]['error']['code'] == 'WORKSHOP_OPERATION_INTERRUPTED'


def test_release_blocks_external_or_uncertain_download(setup):
    service, installation, api, guard = setup
    api.rows[ONE]['downloading'] = True
    with pytest.raises(WorkshopError) as exc:
        service.release(installation)
    assert exc.value.code == 'WORKSHOP_OPERATION_BUSY'
    assert service._providers == {}
    api.rows[ONE]['downloading'] = False
    service.release(installation)


def test_fresh_state_reconciliation_never_forges_callback(setup):
    service, installation, api, _ = setup
    key = service._key(installation)
    service._operations['old'] = (key, dict(operation_id='old', action='update', state='uncertain',
        created_at='earlier', updated_at='earlier', items=[dict(workshop_id=ONE, state='uncertain', error={}, callback_confirmed=False)]))
    api.rows[ONE]['needs_update'] = False
    wait_status(service, installation)
    row = service._operations['old'][1]['items'][0]
    assert row['state'] == 'completed' and row['completion_source'] == 'steam_state_readback'
    assert row['callback_confirmed'] is False


def test_uncertain_without_active_flags_still_blocks_launch_and_migration(setup):
    service, installation, api, _ = setup
    key = service._key(installation)
    service._operations['old'] = (key, dict(operation_id='old', action='update', state='uncertain',
        created_at='earlier', updated_at='earlier', items=[dict(workshop_id=ONE, state='uncertain', error={}, callback_confirmed=False)]))
    assert service.is_busy()
    assert not api.rows[ONE]['downloading'] and api.rows[ONE]['needs_update']
    with pytest.raises(WorkshopError) as exc:
        service.release(installation)
    assert exc.value.code == 'WORKSHOP_OPERATION_BUSY'
    api.rows[ONE]['needs_update'] = False
    service.release(installation)
    assert not service.is_busy()


def test_timeout_discards_pipe_before_any_next_request(monkeypatch, tmp_path):
    helper = SteamHelper(dict(canonical_root=str(tmp_path)))
    closed = []
    class Pipe:
        def send_bytes(self, _):
            pass
    helper._connection = Pipe()
    monkeypatch.setattr(helper, '_start', lambda: None)
    monkeypatch.setattr(helper, 'close', lambda: closed.append(True))
    def timeout(_):
        raise WorkshopError('WORKSHOP_SESSION_TIMEOUT', MESSAGES['WORKSHOP_SESSION_TIMEOUT'])
    monkeypatch.setattr(helper, '_receive', timeout)
    with pytest.raises(WorkshopError):
        helper.begin('op', 'update', [ONE])
    assert closed == [True]


def test_service_never_hides_public_app_validation_failure(setup):
    service, installation, api, _ = setup
    def wrong_app(_):
        raise WorkshopError('WORKSHOP_WRONG_APP', MESSAGES['WORKSHOP_WRONG_APP'])
    service.client.query = wrong_app
    op = service.action(installation, action='subscribe', ids=[THREE])
    result = wait_operation(service, op['operation_id'])
    assert result['state'] == 'failed' and result['items'][0]['error']['code'] == 'WORKSHOP_WRONG_APP'
    assert api.calls == []
