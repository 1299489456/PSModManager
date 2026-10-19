"""CASE-008/009/014: actual bridge, SQLite and LPAC helper; synthetic trees only."""
import json
import os
from pathlib import Path
import shutil
import threading
from contextlib import closing

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.scanning import inventory
from backend.storage import Store
from backend.storage.inventory_repository import InventoryRepository
from tests.integration.test_metadata_worker import BUILD, dll
from tests.unit.test_installation_discovery import discover, hashes, request, wait
from tests.unit.test_installation_layout import make_installation


@pytest.fixture(scope='module')
def loader_dll(tmp_path_factory):
    root = tmp_path_factory.mktemp('synthetic-loader-version')
    source = root / 'Loader.cs'
    source.write_text('using System.Reflection; [assembly:AssemblyInformationalVersion("0.7.3")] public class SyntheticLoader {}', encoding='utf-8')
    return BUILD.compile_source(source, root / 'MelonLoader.dll', library=True)


@pytest.fixture
def selected(tmp_path, dll, loader_dll):
    game = make_installation(tmp_path / '游戏 空格 %')
    shutil.copyfile(loader_dll, game / 'MelonLoader/net6/MelonLoader.dll')
    (game / 'Mods').mkdir()
    shutil.copyfile(dll, game / 'Mods/中文 % mod.dll')
    context = DesktopContext(resolve_paths(str(tmp_path / 'isolated-data')))
    context.initialize()
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    try:
        candidate = discover(api)
        selection = api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))
        chosen = wait(api, selection['data'])
        assert chosen['state'] == 'succeeded', chosen
        yield context, api, game, chosen['data']['installation']['installation_id']
    finally:
        context.close()
    assert not (dll.parent / 'executed.txt').exists()


def start(active, force=False):
    _, api, _, identity = active
    response = api.dispatch(request('inventory.scan', {'installation_id': identity, 'force': force}))
    assert response['ok'], response
    return response['data']


def scan(active, force=False):
    result = wait(active[1], start(active, force))
    assert result['state'] == 'succeeded', result
    return result['data']


def page(active, **kwargs):
    response = active[1].dispatch(request('inventory.list', {'installation_id': active[3], **kwargs}))
    assert response['ok'], response
    return response['data']


def test_real_api_readonly_sqlite_helper_and_pagination(selected, tmp_path, monkeypatch):
    context, _, game, identity = selected
    observed = []
    original = inventory.MetadataWorker.inspect
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(result)
        return result
    monkeypatch.setattr(inventory.MetadataWorker, 'inspect', observe)
    (game / 'Mods/info.txt').write_text('unknown content, not an assembly', encoding='utf-8')
    before = hashes(game)
    complete = scan(selected)
    assert complete['status'] == 'complete', complete
    first = page(selected, limit=1)
    second = page(selected, limit=1, offset=first['next_offset'])
    assert first['total'] == 2 and len(first['entries']) == len(second['entries']) == 1
    assert first['entries'][0]['entry_id'] != second['entries'][0]['entry_id']
    rows = page(selected)['entries']
    managed = next(row for row in rows if row['relative_path'].endswith('.dll'))
    assert managed['status'] == 'managed' and managed['assembly_name'] == 'JacksonPerks'
    assert managed['prediction']['status'] == 'candidate'
    assert next(row for row in rows if row['relative_path'].endswith('.txt'))['status'] == 'unknown'
    with closing(Store(context.paths.database)) as store:
        persisted = InventoryRepository(store).list(identity)
        assert persisted['generation'] == complete['generation']
        assert next(row for row in persisted['entries'] if row['status'] == 'managed')['metadata']['assembly_name'] == 'JacksonPerks'
    assert len(observed) == 1
    process = observed[0]['process']
    assert process['appcontainer'] and process['lpac_policy_applied'] and process['capabilities'] == 0
    assert process['cleanup_remaining'] == 0 and process['profile_deleted']
    assert hashes(game) == before
    (tmp_path / 'readonly-consumer.json').write_text(json.dumps({'before': before, 'after': hashes(game), 'page': page(selected), 'worker': observed}, ensure_ascii=False, indent=2), encoding='utf-8')


def test_cache_repeat_force_and_parser_version_invalidation(selected, monkeypatch):
    before = hashes(selected[2])
    assert scan(selected)['coverage']['cache_hits'] == 0
    assert scan(selected)['coverage']['cache_hits'] == 1
    assert scan(selected, force=True)['coverage']['cache_hits'] == 0
    old = inventory.parser_version()
    monkeypatch.setattr(inventory, 'parser_version', lambda: old + '-synthetic-parser-update')
    assert scan(selected)['coverage']['cache_hits'] == 0
    assert scan(selected)['coverage']['cache_hits'] == 1
    assert hashes(selected[2]) == before


def test_same_size_restored_mtime_external_change_uses_fresh_hash(selected):
    assert scan(selected)['status'] == 'complete'
    old = page(selected)['entries'][0]
    path = selected[2] / old['relative_path']
    info = path.stat()
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1  # external writer changes padding; manager must notice regardless of parser result
    path.write_bytes(raw)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    changed = hashes(selected[2])
    result = scan(selected)
    current = page(selected, generation=result['generation'])['entries'][0]
    assert current['size'] == old['size'] and current['mtime_ns'] == old['mtime_ns']
    assert current['sha256'] != old['sha256'] and not current['cache_hit']
    assert hashes(selected[2]) == changed


def test_rename_marks_missing_only_on_complete_and_preserves_tags(selected):
    context, _, game, identity = selected
    initial = scan(selected)
    old = page(selected)['entries'][0]
    with closing(Store(context.paths.database)) as store:
        InventoryRepository(store).set_tags(identity, old['relative_path'], ['用户标签'])
    (game / old['relative_path']).rename(game / 'Mods/renamed.dll')
    changed = hashes(game)
    result = scan(selected)
    assert result['status'] == 'complete' and result['generation'] > initial['generation']
    rows = page(selected)['entries']
    assert len(rows) == 2
    missing = next(row for row in rows if row['presence'] == 'missing')
    assert missing['entry_id'] == old['entry_id'] and missing['tags'] == ['用户标签']
    assert next(row for row in rows if row['presence'] == 'present')['relative_path'] == 'Mods/renamed.dll'
    assert hashes(game) == changed


def test_partial_permission_error_preserves_authoritative_history(selected, monkeypatch):
    initial = scan(selected)
    original = inventory.read_file
    def denied(root, relative, *args, **kwargs):
        if relative.startswith('Mods/'):
            raise PermissionError('synthetic denied file read')
        return original(root, relative, *args, **kwargs)
    monkeypatch.setattr(inventory, 'read_file', denied)
    result = scan(selected)
    assert result['status'] == 'partial'
    authoritative = page(selected)
    assert authoritative['generation'] == initial['generation']
    assert authoritative['last_attempt']['status'] == 'partial'
    assert authoritative['entries'][0]['presence'] == 'present'


def test_actual_windows_exclusive_file_lock_retains_previous_complete(selected):
    import win32con
    import win32file
    initial = scan(selected)
    assert initial['status'] == 'complete'
    row = page(selected)['entries'][0]
    path = selected[2] / row['relative_path']
    before = hashes(selected[2])
    handle = win32file.CreateFile(str(path), win32con.GENERIC_READ, 0, None,
                                 win32con.OPEN_EXISTING, win32con.FILE_ATTRIBUTE_NORMAL, None)
    try:
        result = scan(selected)
        assert result['status'] == 'partial', result
        failed_page = page(selected, generation=result['generation'])
        assert any(item['code'] == 'FILE_UNREADABLE' for item in failed_page['errors'])
        assert failed_page['entries'][0]['status'] == 'unknown'
        assert failed_page['entries'][0]['sha256'] is None
        authoritative = page(selected)
        assert authoritative['generation'] == initial['generation']
        assert authoritative['entries'][0]['presence'] == 'present'
    finally:
        win32file.CloseHandle(handle)
    assert hashes(selected[2]) == before


def test_cancelled_generation_never_marks_previous_entries_missing(selected, monkeypatch):
    initial = scan(selected)
    entered, release = threading.Event(), threading.Event()
    original = inventory._walk
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10), 'test release timed out'
        return original(*args, **kwargs)
    monkeypatch.setattr(inventory, '_walk', paused)
    started = start(selected)
    try:
        assert entered.wait(10)
        cancelled = selected[1].dispatch(request('task.cancel', {'task_id': started['task_id']}))
        assert cancelled['ok'], cancelled
    finally:
        release.set()
    result = wait(selected[1], started)
    assert result['state'] == 'cancelled'
    authoritative = page(selected)
    assert authoritative['generation'] == initial['generation']
    assert authoritative['last_attempt']['status'] == 'partial'
    assert authoritative['entries'][0]['presence'] == 'present'


def test_external_drift_after_worker_is_partial_not_new_authority(selected, monkeypatch):
    initial = scan(selected)
    original = inventory.MetadataWorker.inspect
    def changing(worker, root, relative, **kwargs):
        result = original(worker, root, relative, **kwargs)
        with (root / relative).open('ab') as stream:
            stream.write(b'external mutation after parse')
        return result
    monkeypatch.setattr(inventory.MetadataWorker, 'inspect', changing)
    result = scan(selected, force=True)
    assert result['status'] == 'partial' and result['error_count']
    assert page(selected)['generation'] == initial['generation']
    assert page(selected, generation=result['generation'])['errors']


def test_late_scan_cannot_overwrite_newer_generation(selected, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    guard = threading.Lock()
    calls = [0]
    original = inventory._walk
    def first_paused(*args, **kwargs):
        with guard:
            calls[0] += 1
            first = calls[0] == 1
        if first:
            entered.set()
            assert release.wait(15)
        return original(*args, **kwargs)
    monkeypatch.setattr(inventory, '_walk', first_paused)
    first = start(selected)
    try:
        assert entered.wait(10)
        second = scan(selected)
        assert second['status'] == 'complete'
    finally:
        release.set()
    old = wait(selected[1], first)
    assert old['state'] == 'succeeded' and old['data']['status'] == 'superseded'
    assert page(selected)['generation'] == second['generation']
    pinned = page(selected, generation=old['data']['generation'])
    assert pinned['status'] == 'superseded' and pinned['generation'] < second['generation']


def test_cancel_queued_scan_persists_partial_without_running_worker(selected):
    initial = scan(selected)
    context = selected[0]
    with context.jobs._lock:
        queued = start(selected)
        response = selected[1].dispatch(request('task.cancel', {'task_id': queued['task_id']}))
        assert response['ok']
    result = wait(selected[1], queued)
    assert result['state'] == 'cancelled'
    current = page(selected)
    assert current['generation'] == initial['generation']
    assert current['last_attempt']['status'] == 'partial'
    assert current['last_attempt']['coverage']['cancelled'] is True


def test_real_assembly_versions_choose_highest_with_same_identity(selected, tmp_path):
    game = selected[2]
    library = game / 'UserLibs'
    library.mkdir()
    for version, name in [('1.0.0.0', 'low.dll'), ('2.0.0.0', 'high.dll')]:
        build = tmp_path / name
        build.mkdir()
        source = build / 'Versioned.cs'
        source.write_text('using System.Reflection; [assembly:AssemblyVersion("' + version + '")] public class SyntheticLibrary {}', encoding='utf-8')
        artifact = BUILD.compile_source(source, build / 'SameLibrary.dll', library=True)
        shutil.copyfile(artifact, library / name)
    before = hashes(game)
    result = scan(selected)
    assert result['status'] == 'complete'
    rows = {row['relative_path']: row for row in page(selected)['entries']}
    low, high = rows['UserLibs/low.dll'], rows['UserLibs/high.dll']
    assert low['assembly_name'] == high['assembly_name'] == 'SameLibrary'
    assert low['assembly_version'] == '1.0.0.0' and high['assembly_version'] == '2.0.0.0'
    assert low['prediction']['status'] == 'excluded'
    assert 'assembly_version_shadowed' in low['prediction']['reasons']
    assert high['prediction']['status'] == 'candidate'
    assert hashes(game) == before


def test_physical_discovery_keeps_excluded_and_manifest_subdirectories(selected, dll):
    _, _, game, _ = selected
    for folder in ('Mods/Disabled', 'Plugins/disabled', 'Mods/NoManifest', 'Mods/Bundle/deep', 'Mods/Bundle/UserLibs'):
        path = game / folder
        path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dll, path / 'sample.dll')
    (game / 'Plugins/disabled/MANIFEST.JSON').write_bytes(b'bad json but file exists')
    (game / 'Mods/Bundle/manifest.json').write_bytes(b'{}')
    before = hashes(game)
    result = scan(selected)
    assert result['status'] == 'complete', result
    rows = {row['relative_path']: row for row in page(selected)['entries']}
    assert len(rows) == 8
    assert rows['Mods/Disabled/sample.dll']['prediction']['status'] == 'excluded'
    assert rows['Mods/NoManifest/sample.dll']['prediction']['status'] == 'excluded'
    assert rows['Plugins/disabled/sample.dll']['prediction']['status'] == 'candidate'
    assert rows['Mods/Bundle/deep/sample.dll']['prediction']['status'] == 'unknown'
    assert rows['Mods/Bundle/UserLibs/sample.dll']['prediction']['stage'] == 'UserLibs'
    assert rows['Mods/Bundle/UserLibs/sample.dll']['prediction']['status'] == 'candidate'
    assert hashes(game) == before


@pytest.mark.parametrize('payload', [{'limit': 0}, {'limit': 51}, {'offset': -1}, {'generation': 0}, {'root': 'C:/secret'}])
def test_paging_rejects_invalid_or_unapproved_payload(selected, payload):
    response = selected[1].dispatch(request('inventory.list', {'installation_id': selected[3], **payload}))
    assert not response['ok']
