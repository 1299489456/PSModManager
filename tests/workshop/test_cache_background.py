from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
import json
import threading
import time

import httpx
import pytest

from backend.workshop import WorkshopError, WorkshopService
from backend.workshop.cache import PublicCache
from backend.workshop.remote import normalize, PublicWorkshopClient
from backend.workshop.steam_session import EngineProvider, SessionEngine
from test_workshop import ID, Session, Response, remote_item, response_for, installed
from test_steam_session import setup, ONE, TWO, THREE, wait_status, wait_operation


def wait_until(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.01)
    pytest.fail('background work did not settle')


def test_persistent_public_metadata_survives_offline_cold_start(tmp_path, installed):
    paths = SimpleNamespace(root=tmp_path / 'owned')
    first = WorkshopService(paths, session=Session(response_for()))
    first.query([ID])
    first.close()
    second = WorkshopService(paths, session=Session(httpx.ConnectError('offline')),
        process_status=lambda _: dict(state='stopped', writable=True),
        steam_factory=lambda _: pytest.fail('public details cannot initialize Steam'))
    try:
        value = second.detail(ID, installed[0])
        assert value['cache_hit'] and value['title'] == '物品详情'
        assert value['description_scope'] == 'detail' and not value['source_pending']
        assert value['source_updated_at'] and second.cached_detail(ID)['description']
        raw = (paths.root / 'cache/workshop/metadata.json').read_text(encoding='utf-8')
        assert '76561190000000000' not in raw and 'PRIVATE_ACCOUNT_VALUE' not in raw
        assert str(installed[0]['canonical_root']) not in raw
    finally:
        second.close()


def test_stale_revalidate_returns_immediately_and_deduplicates(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    cached = normalize(remote_item(), source='steam_public_api', description_limit=8000)
    service.cache.put(('item', ID), cached, updated=time.time() - 600)
    gate, entered = threading.Event(), threading.Event()
    calls = []
    def fetch(ids):
        calls.append(ids)
        entered.set()
        gate.wait(3)
        return [dict(cached, title='updated')]
    service.client.query = fetch
    try:
        start = time.monotonic()
        value = service.query([ID])
        assert time.monotonic() - start < .3 and value['stale'] and value['refreshing']
        assert entered.wait(1)
        for _ in range(5):
            assert service.query([ID])['items'][0]['title'] == cached['title']
        assert len(calls) == 1
        gate.set()
        service._fetches[('query', ID)].result(timeout=2)
        assert service.query([ID])['items'][0]['title'] == 'updated'
    finally:
        gate.set()
        service.close()


def test_cold_concurrent_query_uses_one_request(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    calls = []
    gate = threading.Event()
    def fetch(ids):
        calls.append(ids)
        gate.wait(2)
        return [normalize(remote_item(), source='steam_public_api', description_limit=8000)]
    service.client.query = fetch
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.query, [ID])
            wait_until(lambda: calls)
            second = pool.submit(service.query, [ID])
            gate.set()
            assert first.result(timeout=2)['items'] == second.result(timeout=2)['items']
        assert len(calls) == 1
    finally:
        service.close()


def test_cached_card_is_instant_but_translation_waits_for_full_source(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    card = normalize(remote_item(), source='steam_community')
    service.cache.put(('item', ID), card)
    gate = threading.Event()
    def fetch(ids):
        gate.wait(3)
        return [dict(card, description_scope='detail', description='full description')]
    service.client.query = fetch
    service.client.detail_extras = lambda _: {}
    try:
        start = time.monotonic()
        value = service.detail(ID)
        assert time.monotonic() - start < .3 and value['source_pending']
        assert service.cached_detail(ID) is None
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(service.translation_detail, ID)
            assert not result.done()
            gate.set()
            assert result.result(timeout=2)['description'] == 'full description'
        assert not service.detail(ID)['source_pending']
    finally:
        gate.set()
        service.close()


def test_image_is_bounded_verified_and_persistent(tmp_path):
    # Minimal byte fixture is sufficient for signature/transport checks, not a render test.
    png = b'\x89PNG\r\n\x1a\nfixture'
    service = WorkshopService(SimpleNamespace(root=tmp_path), session=Session(Response(png)))
    item = normalize(remote_item(), source='steam_public_api', description_limit=8000)
    service.cache.put(('item', ID), item)
    try:
        service.image(ID)
        service._fetches[('image', ID)].result(timeout=2)
        value = service.image(ID)
        assert value['state'] == 'ready' and value['data_url'].startswith('data:image/png;base64,')
    finally:
        service.close()
    offline = WorkshopService(SimpleNamespace(root=tmp_path), session=Session())
    try:
        assert offline.image(ID)['data_url'] == value['data_url']
        assert offline.query([ID])['items'][0]['preview_cached']
        assert offline.client.session.calls == []
    finally:
        offline.close()
    with pytest.raises(WorkshopError):
        PublicWorkshopClient(Session()).image('https://evil.test/file.png')
    with pytest.raises(WorkshopError):
        PublicWorkshopClient(Session(Response('<svg onload="bad()"/>'))).image(item['preview_url'])
    with pytest.raises(WorkshopError):
        PublicWorkshopClient(Session(Response(png + b'x' * 1024 * 1024))).image(item['preview_url'])


def test_image_before_metadata_resolves_public_id_and_caches_without_ui_retry(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    entered, release = threading.Event(), threading.Event()
    calls = []
    item = normalize(remote_item(), source='steam_public_api', description_limit=8000)
    def query(ids):
        entered.set()
        release.wait(3)
        calls.append(('metadata', ids))
        return [item]
    def image(url):
        calls.append(('image', url))
        return b'\xff\xd8\xfffixture'
    service.client.query, service.client.image = query, image
    try:
        first = service.image(ID)
        assert first['state'] == 'loading' and entered.wait(1)
        assert service.image(ID)['state'] == 'loading'
        release.set()
        service._fetches[('image', ID)].result(timeout=2)
        assert service.image(ID)['state'] == 'ready'
        assert calls == [('metadata', [ID]), ('image', item['preview_url'])]
    finally:
        release.set()
        service.close()


def test_later_metadata_releases_failed_image_dependency_backoff(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    def unavailable(ids):
        raise WorkshopError('WORKSHOP_NETWORK_ERROR', 'offline', retryable=True)
    service.client.query = unavailable
    service.client.image = lambda _: b'\xff\xd8\xfffixture'
    try:
        service.image(ID)
        assert service._fetches[('image', ID)].exception(timeout=2)
        service.cache.put(('item', ID), normalize(remote_item(), source='steam_public_api'))
        # A successful independent batch supplied the previously missing input;
        # its cover can now run immediately, rather than waiting out a stale error.
        service.image(ID)
        service._fetches[('image', ID)].result(timeout=2)
        assert service.image(ID)['state'] == 'ready'
    finally:
        service.close()


@pytest.mark.parametrize('size', [12, 24, 48])
def test_ui_pagination_converts_official_30_without_gaps(tmp_path, size):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    rows = [normalize(dict(remote_item(), publishedfileid=str(1000 + i)), source='steam_community') for i in range(100)]
    calls = []
    def browse(*, query, page, sort):
        calls.append(page)
        return dict(items=deepcopy(rows[(page - 1) * 30:page * 30]), total=100,
                    has_more=page * 30 < 100, page=page, page_size=30, source='steam_community')
    service.client.browse = browse
    try:
        one = service.browse(page_size=size)
        two = service.browse(page=2, page_size=size)
        assert [x['workshop_id'] for x in one['items'] + two['items']] == [str(1000 + i) for i in range(size * 2)]
        assert one['page_size'] == size and two['total'] == 100
        assert len(calls) == len(set(calls))
    finally:
        service.close()


def test_slow_steam_probe_is_background_deduplicated_and_public_independent(setup):
    service, installation, api, guard = setup
    gate, entered = threading.Event(), threading.Event()
    probes = []
    class Provider(EngineProvider):
        def snapshot(self, ids=()):
            probes.append(True)
            entered.set()
            gate.wait(3)
            return super().snapshot(ids)
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    item = normalize(remote_item(), source='steam_public_api', description_limit=8000)
    service.cache.put(('item', ID), item)
    service.client.detail_extras = lambda _: {}
    try:
        start = time.monotonic()
        result = service.status(installation)
        assert time.monotonic() - start < .3 and result['session']['refreshing']
        assert entered.wait(1)
        for _ in range(3):
            service.status(installation)
            assert service.detail(ID, installation)['title'] == item['title']
        assert len(probes) == 1 and api.calls == []
        gate.set()
        result = wait_status(service, installation)
        assert result['session']['state'] == 'ready'
        assert not service._providers and not guard.held
    finally:
        gate.set()


def test_failed_probe_preserves_last_observed_items_and_marks_stale(setup):
    service, installation, api, _ = setup
    original = wait_status(service, installation)
    observed = original['session']['observed_at']
    class Offline(EngineProvider):
        def snapshot(self, ids=()):
            raise WorkshopError('WORKSHOP_STEAM_OFFLINE', 'offline', retryable=True)
    service.steam_factory = lambda _: Offline(SessionEngine(api))
    service.status(installation, refresh=True)
    wait_until(lambda: not service._probes)
    result = service.status(installation, _probe=False)
    assert result['items'] and result['session']['stale']
    assert result['session']['observed_at'] == observed
    assert result['session']['last_error']['code'] == 'WORKSHOP_STEAM_OFFLINE'
    assert not result['capabilities']['direct_updates']['available']


def test_callback_adapter_failure_keeps_observations_and_is_not_a_game_guard_error(setup):
    service, installation, api, guard = setup
    wait_status(service, installation)
    callbacks = api.callbacks
    def broken_callbacks():
        if api.calls:
            raise KeyError('private adapter details')
        return callbacks()
    api.callbacks = broken_callbacks
    op = service.action(installation, action='subscribe', ids=[THREE])
    result = wait_operation(service, op['operation_id'])
    assert result['state'] == 'uncertain' and not guard.held
    assert result['items'][0]['error']['code'] == 'WORKSHOP_SESSION_LOST'
    cached = service.status(installation, _probe=False)
    observed = cached['session']['observed_at']
    assert observed and cached['session']['stale']
    assert cached['session']['reason_code'] == 'WORKSHOP_SESSION_LOST'
    assert {row['workshop_id'] for row in cached['items'] if row['subscribed']} == {ONE, TWO}
    # The still-failing callback is also encountered by a new probe; no prior
    # installed/subscribed observation may turn into an invented unknown row.
    refreshed = wait_status(service, installation)
    assert refreshed['session']['stale'] and refreshed['session']['observed_at'] == observed
    assert refreshed['session']['last_error']['code'] == 'WORKSHOP_SESSION_LOST'
    assert all(row['evidence']['steam_stale'] for row in refreshed['items'])
    assert {row['workshop_id'] for row in refreshed['items'] if row['subscribed']} == {ONE, TWO}
    assert not refreshed['capabilities']['direct_subscription']['available']


def test_completed_action_keeps_latest_observation_until_fresh_probe(setup):
    service, installation, api, _ = setup
    op = service.action(installation, action='update', ids=[ONE])
    assert wait_operation(service, op['operation_id'])['state'] == 'completed'
    cached = service.status(installation, _probe=False)
    assert cached['session']['stale']
    assert next(row for row in cached['items'] if row['workshop_id'] == ONE)['needs_update'] is False
    class Offline(EngineProvider):
        def snapshot(self, ids=()):
            raise WorkshopError('WORKSHOP_STEAM_OFFLINE', 'offline', retryable=True)
    service.steam_factory = lambda _: Offline(SessionEngine(api))
    refreshed = wait_status(service, installation)
    assert refreshed['session']['last_error']['code'] == 'WORKSHOP_STEAM_OFFLINE'
    assert refreshed['session']['observed_at'] == cached['session']['observed_at']
    assert next(row for row in refreshed['items'] if row['workshop_id'] == ONE)['needs_update'] is False


def test_launch_release_keeps_last_observation_while_game_runs(setup):
    service, installation, api, guard = setup
    wait_status(service, installation)
    service.release(installation)
    guard.state = 'running'
    result = service.status(installation)
    assert result['session']['stale'] and not result['session']['refreshing']
    assert result['session']['reason_code'] == 'WORKSHOP_PROCESS_RUNNING'
    assert {row['workshop_id'] for row in result['items'] if row['subscribed']} == {ONE, TWO}
    assert api.calls == [] and not service._providers


def test_action_source_is_host_owned_receipt(setup):
    service, installation, _, _ = setup
    op = service.action(installation, action='update', ids=[ONE], source='automatic')
    assert wait_operation(service, op['operation_id'])['source'] == 'automatic'


def test_disabling_automatic_submission_keeps_first_receipt_and_skips_later_items(setup):
    service, installation, api, _ = setup
    entered, release, permission = threading.Event(), threading.Event(), threading.Event()
    permission.set()
    class Provider(EngineProvider):
        def begin(self, *args):
            entered.set()
            release.wait(3)
            return super().begin(*args)
    service.steam_factory = lambda _: Provider(SessionEngine(api))
    op = service.action(installation, action='update', ids=[ONE, TWO, THREE],
                        source='automatic', submission_allowed=permission.is_set)
    assert entered.wait(1)
    permission.clear()
    release.set()
    result = wait_operation(service, op['operation_id'])
    assert api.calls == [('update', ONE)]
    assert result['state'] == 'partial' and result['items'][0]['state'] == 'completed'
    assert result['items'][0]['callback_confirmed']
    assert all(row['error']['code'] == 'WORKSHOP_AUTO_UPDATE_DISABLED' for row in result['items'][1:])


def test_close_drains_swr_writes_and_busy_blocks_migration(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path))
    value = normalize(remote_item(), source='steam_public_api', description_limit=8000)
    service.cache.put(('item', ID), value)
    gate, entered, closed = threading.Event(), threading.Event(), threading.Event()
    def fetch(ids):
        entered.set()
        gate.wait(3)
        return [dict(value, title='final-before-copy')]
    service.client.query = fetch
    service.query([ID], refresh=True)
    assert entered.wait(1) and service.is_busy()
    closer = threading.Thread(target=lambda: (service.close(), closed.set()))
    closer.start()
    assert not closed.wait(.1)
    gate.set()
    closer.join(3)
    assert closed.is_set() and not service.is_busy()
    assert PublicCache(tmp_path).get(('item', ID))[1]['title'] == 'final-before-copy'


def test_warm_status_does_not_reprobe_or_flicker_writable(setup):
    service, installation, api, _ = setup
    wait_status(service, installation)
    service.steam_factory = lambda _: pytest.fail('warm status must not spawn')
    for _ in range(8):
        value = service.status(installation)
        assert value['runtime']['writable'] and value['capabilities']['direct_updates']['available']
        assert not value['session']['refreshing']
    assert api.calls == []


def test_corrupt_or_hardlinked_cache_cannot_overwrite_other_files(tmp_path):
    root = tmp_path / 'cache/workshop'
    root.mkdir(parents=True)
    outside = tmp_path / 'outside.json'
    outside.write_text('private')
    (root / 'metadata.json').hardlink_to(outside)
    cache = PublicCache(tmp_path)
    cache.put(('item', ID), normalize(remote_item(), source='steam_public_api'))
    assert outside.read_text() == 'private'
    assert cache.get(('item', ID)) is not None  # safe in-memory cache still works
