"""CASE-003: actual SQLite files and interrupted subprocess, never an in-memory substitute."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.app.identity import DATABASE_APPLICATION_ID
from backend.storage import migrations
from backend.storage.migrations import StorageError, file_sha256, open_database, restore_backup
from backend.storage.schema import ENTITY_TABLES, CURRENT_SCHEMA
from backend.storage.schema import ALL_COLUMNS
from backend.contracts import ENTITY_MODELS

FIXTURES = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))


def seeded_old(path):
    db = open_database(path, target_version=1)
    for name in ['RuntimeFingerprint', 'Installation', 'Profile']:
        data = dict(FIXTURES[name])
        if name == 'Profile':
            data.update(selections=[], display_order=[], config_overrides={'retained': 19})
        data = ENTITY_MODELS[name].model_validate(data).model_dump(mode='json')
        payload = json.dumps(data)
        table, _ = ENTITY_TABLES[name]
        columns = (*ALL_COLUMNS[name], 'payload')
        db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                   (*[data[c] for c in ALL_COLUMNS[name]], payload))
    db.close()
    return payload


def seed_deployment_rows(db):
    """Persist the existing DTO fixtures in FK order, without fabricating a current pointer."""
    for name in ('BaselineSnapshot', 'ActionPlan', 'Transaction', 'DeploymentGeneration'):
        data = FIXTURES[name]
        table, _ = ENTITY_TABLES[name]
        columns = (*ALL_COLUMNS[name], 'payload')
        db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                   (*[data[c] for c in ALL_COLUMNS[name]], json.dumps(data)))


def test_new_database_constraints_and_repeat(tmp_path):
    path = tmp_path / 'new.sqlite3'
    db = open_database(path)
    assert db.isolation_level is None
    assert db.execute('PRAGMA application_id').fetchone()[0] == DATABASE_APPLICATION_ID
    assert db.execute('PRAGMA user_version').fetchone()[0] == CURRENT_SCHEMA
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {value[0] for value in ENTITY_TABLES.values()} | {
        'profile_selections', 'profile_history', 'scan_generations', 'scan_entries',
        'inventory_current', 'inventory_tags', 'inventory_parser_cache',
        'log_sources', 'log_sessions', 'log_evidence', 'log_snapshots', 'sqlite_sequence', 'package_adoptions',
        'profile_drafts', 'profile_editor_selection', 'deployment_current'}
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO mod_releases VALUES ('r', '{}', 'missing', 'hash')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO mod_packages VALUES ('p', 'not json')")
    db.close()
    before = file_sha256(path)
    open_database(path).close()
    assert file_sha256(path) == before
    assert not list(tmp_path.glob('*.bak'))


def test_runtime_connection_skips_repeat_full_scan_but_keeps_foreign_keys(tmp_path, monkeypatch):
    from backend.storage import Store
    path = tmp_path/'runtime.sqlite3'
    open_database(path).close()
    statements = []
    connect = sqlite3.connect
    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', traced)
    store = Store(path, validate=False)
    try:
        assert store.connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            store.connection.execute("INSERT INTO mod_releases VALUES ('r', '{}', 'missing', 'hash')")
    finally:
        store.close()
    assert not any('integrity_check' in sql.casefold() or 'foreign_key_check' in sql.casefold() for sql in statements)
    statements.clear()
    Store(path).close()
    assert any('integrity_check' in sql.casefold() for sql in statements)
    assert any('foreign_key_check' in sql.casefold() for sql in statements)


def test_runtime_flag_never_skips_migration_validation(tmp_path, monkeypatch):
    path = tmp_path/'upgrade.sqlite3'
    seeded_old(path)
    statements = []
    connect = sqlite3.connect
    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', traced)
    connection = open_database(path, validate=False)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == CURRENT_SCHEMA
    connection.close()
    assert sum('integrity_check' in sql.casefold() for sql in statements) >= 2
    assert sum('foreign_key_check' in sql.casefold() for sql in statements) >= 2
    assert len(list(tmp_path.glob('*.bak'))) == 1


@pytest.mark.parametrize('pragma,value', [('application_id', 1234), ('user_version', CURRENT_SCHEMA+1)])
def test_runtime_flag_still_rejects_foreign_or_future_database(tmp_path, pragma, value):
    path = tmp_path/'identity.sqlite3'
    open_database(path).close()
    with sqlite3.connect(path) as connection:
        connection.execute(f'PRAGMA {pragma}={value}')
    before = file_sha256(path)
    with pytest.raises(StorageError):
        open_database(path, validate=False)
    assert file_sha256(path) == before


def test_ps0002_identity_only_database_migrates(tmp_path):
    path = tmp_path / 'desktop.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute(f'PRAGMA application_id={DATABASE_APPLICATION_ID}')
    original = file_sha256(path)
    open_database(path).close()
    backups = list(tmp_path.glob('*.bak'))
    assert len(backups) == 1
    record = json.loads(backups[0].with_suffix('.bak.json').read_text())
    assert record['source_file_sha256'] == original
    with sqlite3.connect(backups[0]) as restored:
        assert restored.execute('PRAGMA user_version').fetchone()[0] == 0
        assert restored.execute('PRAGMA application_id').fetchone()[0] == DATABASE_APPLICATION_ID
        assert not restored.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()


def test_schema5_profiles_upgrade_to_current_without_payload_change(tmp_path):
    path = tmp_path / 'schema5.sqlite3'
    payload = seeded_old(path)
    open_database(path, target_version=5).close()
    before = file_sha256(path)
    db = open_database(path)
    assert db.execute('PRAGMA user_version').fetchone()[0] == CURRENT_SCHEMA
    assert db.execute('SELECT payload FROM profiles').fetchone()[0] == payload
    assert db.execute('SELECT count(*) FROM profile_drafts').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM profile_editor_selection').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM deployment_current').fetchone()[0] == 0
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    db.close()
    backup = next(tmp_path.glob('*.before-v5.*.bak'))
    assert json.loads(backup.with_suffix('.bak.json').read_text())['source_file_sha256'] == before
    digest = file_sha256(path)
    open_database(path).close()
    assert file_sha256(path) == digest


def test_schema6_upgrade_preserves_history_without_inventing_current(tmp_path):
    path = tmp_path / 'schema6.sqlite3'
    payload = seeded_old(path)
    db = open_database(path, target_version=6)
    seed_deployment_rows(db)
    historical = db.execute('SELECT payload FROM deployment_generations').fetchone()[0]
    db.close()
    before = file_sha256(path)
    db = open_database(path)
    assert db.execute('PRAGMA user_version').fetchone()[0] == CURRENT_SCHEMA
    assert db.execute('SELECT payload FROM profiles').fetchone()[0] == payload
    assert db.execute('SELECT payload FROM deployment_generations').fetchone()[0] == historical
    assert db.execute('SELECT count(*) FROM deployment_current').fetchone()[0] == 0
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    db.close()
    backup = next(tmp_path.glob('*.before-v6.*.bak'))
    receipt = json.loads(backup.with_suffix('.bak.json').read_text())
    assert receipt['source_file_sha256'] == before
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='deployment_current'").fetchone()
        assert db.execute('SELECT payload FROM deployment_generations').fetchone()[0] == historical
    digest = file_sha256(path)
    open_database(path).close()
    assert file_sha256(path) == digest


@pytest.mark.parametrize('column,invalid', [
    ('installation_id', 'missing-installation'),
    ('generation_id', 'missing-generation'),
    ('generation_id', None),
    ('manifest_hash', None),
    ('manifest_file_hash', None),
    ('manifest_hash', 'a' * 63),
    ('manifest_hash', 'A' * 64),
    ('manifest_file_hash', 'g' * 64),
])
def test_current_pointer_rejects_unknown_identity_null_and_invalid_hash(tmp_path, column, invalid):
    path = tmp_path / 'current.sqlite3'
    seeded_old(path)
    db = open_database(path)
    seed_deployment_rows(db)
    values = {
        'installation_id': FIXTURES['Installation']['installation_id'],
        'generation_id': FIXTURES['DeploymentGeneration']['generation_id'],
        'manifest_hash': 'a' * 64,
        'manifest_file_hash': 'b' * 64,
    }
    values[column] = invalid
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('INSERT INTO deployment_current VALUES (?,?,?,?)', tuple(values.values()))
    assert db.execute('SELECT count(*) FROM deployment_current').fetchone()[0] == 0
    db.close()


def test_current_pointer_uniqueness_and_transaction_rollback(tmp_path):
    path = tmp_path / 'atomic.sqlite3'
    seeded_old(path)
    db = open_database(path)
    db.execute('BEGIN IMMEDIATE')
    seed_deployment_rows(db)
    pointer = (FIXTURES['Installation']['installation_id'], FIXTURES['DeploymentGeneration']['generation_id'], 'a' * 64, 'b' * 64)
    db.execute('INSERT INTO deployment_current VALUES (?,?,?,?)', pointer)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('INSERT INTO deployment_current VALUES (?,?,?,?)', pointer)
    db.rollback()
    assert db.execute('SELECT count(*) FROM deployment_current').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM deployment_generations').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM transactions').fetchone()[0] == 0
    db.execute('BEGIN IMMEDIATE')
    seed_deployment_rows(db)
    db.execute('INSERT INTO deployment_current VALUES (?,?,?,?)', pointer)
    db.commit()
    db.close()
    db = open_database(path)
    assert tuple(db.execute('SELECT * FROM deployment_current').fetchone()) == pointer
    assert db.execute('SELECT count(*) FROM deployment_generations').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM transactions').fetchone()[0] == 1
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    db.close()


def test_schema7_sql_failure_retains_version6_and_profile(tmp_path, monkeypatch):
    path = tmp_path / 'migration-failed.sqlite3'
    payload = seeded_old(path)
    open_database(path, target_version=6).close()
    before = file_sha256(path)
    migration = migrations.MIGRATIONS[7]
    def fail(db):
        migration(db)
        db.execute("UPDATE profiles SET revision=1,payload='{}'")
        raise RuntimeError('synthetic pointer migration interruption')
    monkeypatch.setitem(migrations.MIGRATIONS, 7, fail)
    with pytest.raises(RuntimeError, match='pointer migration'):
        open_database(path)
    assert file_sha256(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
        assert db.execute('SELECT payload FROM profiles').fetchone()[0] == payload
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='deployment_current'").fetchone()
    backup = next(tmp_path.glob('*.before-v6.*.bak'))
    assert json.loads(backup.with_suffix('.bak.json').read_text())['source_file_sha256'] == before


def test_supported_old_profile_backfill_backup_and_explicit_rollback(tmp_path):
    path = tmp_path / 'old.sqlite3'
    payload = seeded_old(path)
    original = file_sha256(path)
    db = open_database(path)
    assert db.execute('SELECT payload FROM profiles').fetchone()[0] == payload
    assert tuple(db.execute('SELECT profile_id,revision,payload FROM profile_history').fetchone()) == (FIXTURES['Profile']['profile_id'], 0, payload)
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    db.close()
    backup = next(tmp_path.glob('*.bak'))
    record = json.loads(backup.with_suffix('.bak.json').read_text())
    assert record['sha256'] == file_sha256(backup)
    assert record['source_file_sha256'] == original
    upgraded = file_sha256(path)
    retained = restore_backup(path, backup, expected_backup_sha256=record['sha256'], expected_current_sha256=upgraded)
    assert file_sha256(retained) == upgraded
    assert file_sha256(path) == record['sha256']
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT payload FROM profiles').fetchone()[0] == payload
    open_database(path).close()


def test_backup_filesystem_failure_prevents_any_migration(tmp_path, monkeypatch):
    path = tmp_path / 'old.sqlite3'
    seeded_old(path)
    original = file_sha256(path)
    monkeypatch.setattr(migrations.uuid, 'uuid4', lambda: SimpleNamespace(hex='occupied'))
    (tmp_path / 'old.sqlite3.before-v1.occupied.bak').mkdir()
    with pytest.raises(StorageError, match='regular'):
        open_database(path)
    assert file_sha256(path) == original


def test_upgrade_sql_failure_rolls_back_all_ddl_and_data(tmp_path, monkeypatch):
    path = tmp_path / 'old.sqlite3'
    payload = seeded_old(path)
    original = file_sha256(path)
    def fail(connection):
        connection.execute('CREATE TABLE should_rollback(value)')
        connection.execute("UPDATE profiles SET payload='{}'")
        connection.execute('INSERT INTO nonexistent_table VALUES (1)')
    monkeypatch.setitem(migrations.MIGRATIONS, 2, fail)
    with pytest.raises(sqlite3.OperationalError):
        open_database(path)
    assert file_sha256(path) == original
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert db.execute('SELECT payload FROM profiles').fetchone()[0] == payload
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchall()


def test_real_process_interrupted_during_migration_recovers(tmp_path):
    path = tmp_path / 'interrupted.sqlite3'
    payload = seeded_old(path)
    source = '''import os,sys
from backend.storage import migrations
def die(connection):
    connection.execute('PRAGMA cache_size=1')
    connection.execute('CREATE TABLE interrupted_work(value BLOB)')
    for _ in range(24): connection.execute('INSERT INTO interrupted_work VALUES (zeroblob(65536))')
    connection.execute("UPDATE profiles SET payload='{}'")
    os._exit(73)
migrations.MIGRATIONS[2]=die
migrations.open_database(sys.argv[1])
'''
    result = subprocess.run([sys.executable, '-B', '-c', source, str(path)], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, (result.stdout, result.stderr)
    assert Path(str(path) + '-journal').exists()
    db = open_database(path)
    assert db.execute('SELECT payload FROM profiles').fetchone()[0] == payload
    assert db.execute('SELECT payload FROM profile_history').fetchone()[0] == payload
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='interrupted_work'").fetchall()
    db.close()


@pytest.mark.parametrize('mode', ['garbage', 'future', 'foreign', 'damaged'])
def test_unrecognized_or_corrupt_database_unchanged(tmp_path, mode):
    path = tmp_path / 'rejected.sqlite3'
    if mode == 'garbage':
        path.write_bytes(b'not a sqlite database; keep me')
    else:
        seeded_old(path)
        if mode == 'damaged':
            raw = bytearray(path.read_bytes())
            raw[100:108] = bytes(8)
            path.write_bytes(raw)
        else:
            with sqlite3.connect(path) as db:
                db.execute('PRAGMA user_version=999' if mode == 'future' else 'PRAGMA application_id=77')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises((StorageError, sqlite3.DatabaseError)):
        open_database(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob('*.bak'))


def test_wrong_rollback_hash_refuses_to_replace(tmp_path):
    path = tmp_path / 'old.sqlite3'
    seeded_old(path)
    open_database(path).close()
    before = file_sha256(path)
    backup = next(tmp_path.glob('*.bak'))
    with pytest.raises(StorageError, match='hash'):
        restore_backup(path, backup, expected_backup_sha256='0' * 64, expected_current_sha256=before)
    assert file_sha256(path) == before


@pytest.mark.parametrize('suffix', ['', '-journal', '-wal', '-shm'])
def test_hardlinked_database_or_sidecar_rejected(tmp_path, suffix):
    original = tmp_path / 'source.sqlite3'
    seeded_old(original)
    path = tmp_path / 'target.sqlite3'
    if suffix:
        seeded_old(path)
    os.link(original, Path(str(path) + suffix))
    before = file_sha256(path) if suffix else hashlib.sha256(original.read_bytes()).hexdigest()
    with pytest.raises(StorageError, match='unlinked'):
        open_database(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_junction_database_directory_rejected(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows junction acceptance requires Windows')
    owned = tmp_path / 'owned'
    owned.mkdir()
    path = owned / 'db.sqlite3'
    seeded_old(path)
    alias = tmp_path / 'alias'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(owned)], capture_output=True)
    assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError, match='联接'):
        open_database(alias / path.name)


def test_profile_history_preserves_versions_and_rejects_revision_reuse(tmp_path):
    path = tmp_path / 'profile.sqlite3'
    original = seeded_old(path)
    db = open_database(path)
    db.execute("UPDATE profiles SET revision=1,payload='{\"revision\":1}'")
    assert [tuple(r) for r in db.execute('SELECT revision,payload FROM profile_history ORDER BY revision')] == [(0, original), (1, '{"revision":1}')]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE profiles SET payload='{}'")
    assert db.execute('SELECT payload FROM profiles').fetchone()[0] == '{"revision":1}'
    db.close()


def test_backup_runs_while_real_second_writer_is_locked_out(tmp_path, monkeypatch):
    path = tmp_path / 'old.sqlite3'
    seeded_old(path)
    backup = migrations._backup
    attempts = []
    def checked_backup(connection, target, version):
        other = sqlite3.connect(target, timeout=0, isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute("UPDATE profiles SET payload='{}'")
            attempts.append('writer rejected')
        finally:
            other.close()
        return backup(connection, target, version)
    monkeypatch.setattr(migrations, '_backup', checked_backup)
    open_database(path).close()
    assert attempts == ['writer rejected']


def test_future_version_in_wal_is_rejected_without_writes(tmp_path):
    path = tmp_path / 'future.sqlite3'
    seeded_old(path)
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA user_version=999')
        assert int.from_bytes(path.read_bytes()[60:64], 'big') == 1
        watched = [path, Path(str(path) + '-wal')]
        before = [p.read_bytes() for p in watched]
        with pytest.raises(StorageError, match='Future'):
            open_database(path)
        assert [p.read_bytes() for p in watched] == before
        assert not list(tmp_path.glob('*.bak'))
    finally:
        writer.close()


@pytest.mark.parametrize('statement', [
    'DROP TABLE observations',
    'ALTER TABLE runtime_fingerprints RENAME COLUMN fingerprint_id TO broken',
    'DROP TRIGGER profile_history_update',
    'DROP TABLE deployment_current',
    'ALTER TABLE deployment_current RENAME COLUMN manifest_file_hash TO broken',
])
def test_current_version_missing_schema_is_rejected(tmp_path, statement):
    path = tmp_path / 'current.sqlite3'
    open_database(path).close()
    with sqlite3.connect(path) as db:
        db.execute(statement)
    before = file_sha256(path)
    with pytest.raises(StorageError, match='missing'):
        open_database(path)
    assert file_sha256(path) == before


@pytest.mark.parametrize('definition,reason', [
    ('installation_id TEXT PRIMARY KEY NOT NULL, generation_id TEXT NOT NULL REFERENCES deployment_generations(generation_id), manifest_hash TEXT NOT NULL, manifest_file_hash TEXT NOT NULL', 'foreign key'),
    ('installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(installation_id), generation_id TEXT NOT NULL, manifest_hash TEXT NOT NULL, manifest_file_hash TEXT NOT NULL', 'foreign key'),
    ('installation_id TEXT NOT NULL REFERENCES installations(installation_id), generation_id TEXT NOT NULL REFERENCES deployment_generations(generation_id), manifest_hash TEXT NOT NULL, manifest_file_hash TEXT NOT NULL', 'uniqueness'),
    ('installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(installation_id), generation_id TEXT REFERENCES deployment_generations(generation_id), manifest_hash TEXT NOT NULL, manifest_file_hash TEXT NOT NULL', 'non-null'),
    ('installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(installation_id), generation_id TEXT NOT NULL REFERENCES deployment_generations(generation_id), manifest_hash TEXT, manifest_file_hash TEXT NOT NULL', 'non-null'),
])
def test_current_pointer_missing_constraints_rejected_without_writes(tmp_path, definition, reason):
    path = tmp_path / 'malformed-current.sqlite3'
    open_database(path).close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE deployment_current')
        db.execute(f'CREATE TABLE deployment_current ({definition})')
    before = file_sha256(path)
    with pytest.raises(StorageError, match=reason):
        open_database(path)
    assert file_sha256(path) == before
    assert not list(tmp_path.glob('*.bak'))


def test_future_current_database_cannot_be_downgraded_via_restore(tmp_path):
    path = tmp_path / 'current.sqlite3'
    seeded_old(path)
    open_database(path).close()
    backup = next(tmp_path.glob('*.bak'))
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=999')
    before = file_sha256(path)
    with pytest.raises(StorageError, match='Future'):
        restore_backup(path, backup, expected_backup_sha256=file_sha256(backup), expected_current_sha256=before)
    assert file_sha256(path) == before


def test_failed_empty_database_migration_leaves_retryable_identity(tmp_path, monkeypatch):
    path = tmp_path / 'empty.sqlite3'
    original = migrations.MIGRATIONS[1]
    def fail(db):
        db.execute('CREATE TABLE interrupted(value)')
        raise RuntimeError('synthetic first migration failure')
    monkeypatch.setitem(migrations.MIGRATIONS, 1, fail)
    with pytest.raises(RuntimeError):
        open_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA application_id').fetchone()[0] == DATABASE_APPLICATION_ID
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    monkeypatch.setitem(migrations.MIGRATIONS, 1, original)
    open_database(path).close()


def test_structurally_invalid_backup_is_not_restored(tmp_path):
    path = tmp_path / 'current.sqlite3'
    seeded_old(path)
    open_database(path).close()
    backup = next(tmp_path.glob('*.bak'))
    with sqlite3.connect(backup) as db:
        db.execute('DROP TABLE observations')
    before = file_sha256(path)
    with pytest.raises(StorageError, match='missing'):
        restore_backup(path, backup, expected_backup_sha256=file_sha256(backup), expected_current_sha256=before)
    assert file_sha256(path) == before
