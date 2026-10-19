"""CASE-008/009/014: real product SQLite, isolated synthetic installations."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest

from backend.contracts import ENTITY_MODELS
from backend.storage import Store
from backend.storage.schema import CURRENT_SCHEMA
from backend.storage.inventory_repository import InventoryRepository
from backend.storage.migrations import open_database, file_sha256, restore_backup


FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / 'inventory.sqlite3')
    for kind in ('RuntimeFingerprint', 'Installation'):
        store.put(ENTITY_MODELS[kind].model_validate(FIXTURE[kind]))
    try:
        yield store, InventoryRepository(store), FIXTURE['Installation']['installation_id']
    finally:
        store.close()


def entry(path='Mods/中文 % Name.dll', **changes):
    return dict(relative_path=path, file_identity='1:2', size=3, mtime_ns=99, sha256='a' * 64,
                status='unknown', metadata=None, prediction={'status': 'unknown'}, **changes)


def complete(repo, installation, rows):
    generation = repo.begin(installation, {'rules': 'test-v1'})
    repo.finish(installation, generation, rows, 'complete', {'complete': True}, [])
    return generation


def test_restart_pagination_and_observations_are_not_components(db):
    store, repo, installation = db
    generation = complete(repo, installation, [entry(f'Mods/{i:03}.dll') for i in range(5)])
    assert repo.list(installation, limit=2, offset=2)['entries'][0]['relative_path'] == 'Mods/002.dll'
    assert repo.list(installation)['total'] == 5
    assert store.connection.execute('SELECT count(*) FROM components').fetchone()[0] == 0
    path = store.connection.execute('PRAGMA database_list').fetchone()[2]
    second = Store(path)
    try:
        assert InventoryRepository(second).list(installation)['generation'] == generation
    finally:
        second.close()


@pytest.mark.parametrize('status', ['partial', 'failed'])
def test_incomplete_never_overwrites_current_or_tags(db, status):
    _, repo, installation = db
    first = complete(repo, installation, [entry()])
    repo.set_tags(installation, 'mods/中文 % name.DLL', ['保留'])
    second = repo.begin(installation, {})
    repo.finish(installation, second, [], status, {}, ['cancelled or permission denied'])
    current = repo.list(installation)
    assert current['generation'] == first and current['latest_attempt'] == second
    assert current['last_attempt']['status'] == status
    assert current['last_attempt']['errors'] == ['cancelled or permission denied']
    assert current['entries'][0]['presence'] == 'present'
    assert current['entries'][0]['tags'] == ['保留']
    assert repo.list(installation, generation=second)['status'] == status


def test_missing_only_new_complete_snapshot_and_stable_id(db):
    _, repo, installation = db
    first = complete(repo, installation, [entry()])
    before = repo.list(installation)['entries'][0]
    repo.set_tags(installation, before['relative_path'], ['favorite'])
    second = complete(repo, installation, [])
    assert second > first
    missing = repo.list(installation)['entries'][0]
    assert missing['presence'] == 'missing' and missing['entry_id'] == before['entry_id']
    assert missing['tags'] == ['favorite']
    assert repo.list(installation, generation=first)['entries'][0]['presence'] == 'present'
    complete(repo, installation, [entry('mods/中文 % name.DLL')])
    restored = repo.list(installation)['entries'][0]
    assert restored['presence'] == 'present' and restored['entry_id'] == before['entry_id']


def test_requested_barrier_blocks_late_complete_even_when_new_attempt_fails(db):
    _, repo, installation = db
    baseline = complete(repo, installation, [entry()])
    old = repo.begin(installation, {})
    new = repo.begin(installation, {})
    repo.finish(installation, new, [], 'failed', {}, ['offline'])
    summary = repo.finish(installation, old, [entry('Mods/late.dll')], 'complete', {}, [])
    assert summary['status'] == 'superseded'
    page = repo.list(installation)
    assert page['generation'] == baseline and page['latest_attempt'] == new
    assert page['latest_requested'] == new


def test_concurrent_generation_allocation_actual_connections(db):
    store, repo, installation = db
    path = store.connection.execute('PRAGMA database_list').fetchone()[2]
    def run(_):
        other = Store(path)
        try:
            return InventoryRepository(other).begin(installation, {})
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(run, range(12)))
    assert sorted(values) == list(range(1, 13))
    assert repo.list(installation)['latest_requested'] == 12


def test_cache_keys_hash_and_parser_version_and_persists(db):
    store, repo, _ = db
    repo.cache_put('a' * 64, 'parser1:binarysha', {'status': 'managed'})
    assert repo.cache_get('a' * 64, 'parser1:binarysha') == {'status': 'managed'}
    assert repo.cache_get('b' * 64, 'parser1:binarysha') is None
    assert repo.cache_get('a' * 64, 'parser2:binarysha') is None
    assert store.connection.execute('SELECT count(*) FROM inventory_parser_cache').fetchone()[0] == 1


@pytest.mark.parametrize('path', ['../bad.dll', '/abs.dll', 'C:/bad.dll', 'Mods/a/../b.dll', 'Mods/file.dll.'])
def test_invalid_paths_rollback_without_publishing(db, path):
    _, repo, installation = db
    generation = repo.begin(installation, {})
    with pytest.raises(ValueError):
        repo.finish(installation, generation, [entry(path)], 'complete', {}, [])
    assert repo.list(installation)['status'] == 'running'


def test_duplicate_paths_and_double_finish_rejected(db):
    _, repo, installation = db
    generation = repo.begin(installation, {})
    with pytest.raises(ValueError):
        repo.finish(installation, generation, [entry('Mods/A.dll'), entry('mods/a.DLL')], 'complete', {}, [])
    repo.finish(installation, generation, [entry()], 'complete', {}, [])
    with pytest.raises(ValueError):
        repo.finish(installation, generation, [], 'failed', {}, [])


def test_supported_v2_migration_preserves_data_and_backup(tmp_path):
    path = tmp_path / 'old.sqlite3'
    old = open_database(path, target_version=2)
    old.execute('INSERT INTO mod_packages VALUES (?,?)', ('preserved', '{"opaque":"中文"}'))
    original_tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    old.close()
    before = file_sha256(path)
    current = open_database(path)
    assert current.execute('PRAGMA user_version').fetchone()[0] == CURRENT_SCHEMA
    assert current.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert current.execute('PRAGMA foreign_key_check').fetchall() == []
    assert current.execute('SELECT payload FROM mod_packages WHERE package_id=?', ('preserved',)).fetchone()[0] == '{"opaque":"中文"}'
    assert original_tables <= {r[0] for r in current.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    current.close()
    backup = next(tmp_path.glob('*.bak'))
    record = json.loads(backup.with_suffix('.bak.json').read_text())
    assert record['schema_version'] == 2 and record['source_file_sha256'] == before
    restore_backup(path, backup, expected_backup_sha256=record['sha256'], expected_current_sha256=file_sha256(path))
    with sqlite3.connect(path) as restored:
        assert restored.execute('PRAGMA user_version').fetchone()[0] == 2
        assert {r[0] for r in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")} == original_tables


def test_v3_failure_rolls_back_to_readable_v2(tmp_path, monkeypatch):
    from backend.storage import migrations
    path = tmp_path / 'old.sqlite3'
    open_database(path, target_version=2).close()
    def fail(connection):
        connection.execute('CREATE TABLE temporary_partial (value TEXT)')
        raise RuntimeError('injected migration failure')
    with monkeypatch.context() as patch:
        patch.setitem(migrations.MIGRATIONS, 3, fail)
        with pytest.raises(RuntimeError, match='injected'):
            open_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert db.execute("SELECT name FROM sqlite_master WHERE name='temporary_partial'").fetchone() is None
    open_database(path).close()


def test_summary_and_unknown_rows_keep_null_facts(db):
    _, repo, installation = db
    generation = repo.begin(installation, {})
    assert repo.summary(installation, generation)['status'] == 'running'
    row = entry()
    row.update(file_identity=None, size=None, mtime_ns=None, sha256=None, status='permission_denied')
    repo.finish(installation, generation, [row], 'partial', {}, [])
    assert repo.summary(installation, generation)['finished_at'].endswith('Z')
    assert repo.list(installation)['entries'][0]['sha256'] is None


@pytest.mark.parametrize('change', [{'size': -1}, {'mtime_ns': True}, {'mtime_ns': -(2**63)-1}, {'mtime_ns': 2**63}, {'sha256': 'fake'}, {'status': ''}])
def test_invalid_file_facts_rejected(db, change):
    _, repo, installation = db
    generation = repo.begin(installation, {})
    row = entry() | change
    with pytest.raises(ValueError):
        repo.finish(installation, generation, [row], 'complete', {}, [])


def test_negative_mtime_roundtrip(db):
    _, repo, installation = db
    complete(repo, installation, [entry() | {'mtime_ns': -123456789000000000}])
    assert repo.list(installation)['entries'][0]['mtime_ns'] == -123456789000000000
