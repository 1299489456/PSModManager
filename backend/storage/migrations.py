"""Offline SQLite migration and explicit, hash-bound recovery. No import side effects."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from backend.app.identity import DATABASE_APPLICATION_ID
from backend.app.paths import reject_reparse
from .schema import ALL_COLUMNS, CURRENT_SCHEMA, ENTITY_TABLES, MIGRATIONS


class StorageError(ValueError):
    """Database rejected without rebuilding or replacing its content."""


def _safe(path):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise StorageError('Database must use a local absolute path')
    reject_reparse(path)
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise StorageError('Database and recovery artifacts must be regular, unlinked files')
    return path


def _safe_database(path):
    path = _safe(path)
    for suffix in ('-journal', '-wal', '-shm'):
        _safe(Path(str(path) + suffix))
    return path


def file_sha256(path):
    return hashlib.sha256(_safe(path).read_bytes()).hexdigest()


def _header(path):
    """Reject foreign/future DB before writable open, including hot-journal recovery."""
    with path.open('rb') as stream:
        header = stream.read(100)
    if len(header) < 100 or header[:16] != b'SQLite format 3\x00':
        raise StorageError('Invalid SQLite database; original retained')
    app_id = int.from_bytes(header[68:72], 'big')
    version = int.from_bytes(header[60:64], 'big')
    if app_id != DATABASE_APPLICATION_ID:
        raise StorageError('Foreign database application_id')
    if version > CURRENT_SCHEMA:
        raise StorageError('Future schema is not writable')
    return version


def _validate(connection):
    if [row[0] for row in connection.execute('PRAGMA integrity_check')] != ['ok']:
        raise StorageError('Database integrity check failed')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise StorageError('Database foreign keys are invalid')


def _validate_schema(connection, version):
    if version == 0:
        if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
            raise StorageError('Unversioned database with tables is not a supported baseline')
        return
    required = {table: set(ALL_COLUMNS[name]) | {'payload'} for name, (table, _) in ENTITY_TABLES.items()}
    required['profile_selections'] = {'profile_id', 'release_id', 'manifest_hash', 'selected'}
    if version >= 2:
        required['profile_history'] = {'profile_id', 'revision', 'payload'}
    if version >= 3:
        required.update({
            'scan_generations': {'installation_id', 'generation', 'status', 'policy', 'coverage', 'errors', 'started_at', 'finished_at'},
            'inventory_current': {'installation_id', 'latest_requested', 'latest_attempt', 'latest_complete'},
            'scan_entries': {'installation_id', 'generation', 'path_key', 'entry_id', 'payload'},
            'inventory_tags': {'installation_id', 'path_key', 'tags'},
            'inventory_parser_cache': {'sha256', 'parser_version', 'metadata'},
        })
    if version >= 4:
        required.update({
            'log_sources': {'source_id', 'installation_id', 'path_key', 'relative_path', 'kind', 'state'},
            'log_sessions': {'session_id', 'installation_id', 'payload'},
            'log_evidence': {'seq', 'evidence_id', 'source_id', 'epoch', 'byte_start', 'byte_end', 'excerpt_hash', 'raw', 'session_id', 'event_group_id', 'payload'},
            'log_snapshots': {'source_id', 'epoch', 'sha256', 'size'},
        })
    if version >= 5:
        required['package_adoptions'] = {'snapshot_id', 'release_id', 'adopted_at'}
    if version >= 6:
        required['profile_drafts'] = {'profile_id', 'installation_id', 'revision', 'committed_revision', 'payload'}
        required['profile_editor_selection'] = {'installation_id', 'profile_id'}
    if version >= 7:
        required['deployment_current'] = {'installation_id', 'generation_id', 'manifest_hash', 'manifest_file_hash'}
    for table, fields in required.items():
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            raise StorageError('Required schema table is missing')
        actual = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        if not fields <= actual:
            raise StorageError('Required schema columns are missing')
    if version >= 7:
        pointer_columns = {row[1]: row for row in connection.execute('PRAGMA table_info(deployment_current)')}
        if any(not pointer_columns[name][3] for name in required['deployment_current']):
            raise StorageError('Required current pointer non-null constraints are missing')
    expected_foreign = {
        'installations': {('fingerprint_id', 'runtime_fingerprints', 'fingerprint_id')},
        'mod_releases': {('package_id', 'mod_packages', 'package_id')},
        'components': {('release_id', 'mod_releases', 'release_id'), ('artifact_hash', 'artifacts', 'sha256')},
        'profiles': {('installation_id', 'installations', 'installation_id')},
        'profile_selections': {('profile_id', 'profiles', 'profile_id'), ('release_id', 'mod_releases', 'release_id'), ('manifest_hash', 'mod_releases', 'manifest_hash')},
        'approvals': {('plan_id', 'action_plans', 'plan_id'), ('plan_hash', 'action_plans', 'plan_hash')},
    }
    if version >= 3:
        expected_foreign.update({
            'scan_generations': {('installation_id', 'installations', 'installation_id')},
            'scan_entries': {('installation_id', 'scan_generations', 'installation_id'), ('generation', 'scan_generations', 'generation')},
            'inventory_current': {('installation_id', 'installations', 'installation_id'),
                                  ('latest_requested', 'scan_generations', 'generation'),
                                  ('latest_attempt', 'scan_generations', 'generation'),
                                  ('latest_complete', 'scan_generations', 'generation')},
            'inventory_tags': {('installation_id', 'installations', 'installation_id')},
        })
    if version >= 4:
        expected_foreign.update({
            'log_sources': {('installation_id', 'installations', 'installation_id')},
            'log_sessions': {('installation_id', 'installations', 'installation_id')},
            'log_evidence': {('source_id', 'log_sources', 'source_id'), ('session_id', 'log_sessions', 'session_id')},
            'log_snapshots': {('source_id', 'log_sources', 'source_id')},
        })
    if version >= 5:
        expected_foreign['package_adoptions'] = {('snapshot_id', 'baseline_snapshots', 'snapshot_id'), ('release_id', 'mod_releases', 'release_id')}
    if version >= 6:
        expected_foreign['profile_drafts'] = {('installation_id', 'installations', 'installation_id')}
        expected_foreign['profile_editor_selection'] = {('installation_id', 'installations', 'installation_id'), ('profile_id', 'profile_drafts', 'profile_id')}
    if version >= 7:
        expected_foreign['deployment_current'] = {('installation_id', 'installations', 'installation_id'), ('generation_id', 'deployment_generations', 'generation_id')}
    for table, expected in expected_foreign.items():
        actual = {(row[3], row[2], row[4]) for row in connection.execute(f'PRAGMA foreign_key_list({table})')}
        if not expected <= actual:
            raise StorageError('Required foreign key constraints are missing')
    expected_unique = {
        'installations': {('canonical_root',), ('file_identity',)},
        'mod_releases': {('release_id', 'manifest_hash'), ('package_id', 'manifest_hash')},
        'action_plans': {('idempotency_key',), ('plan_id', 'plan_hash')},
        'profile_selections': {('profile_id', 'release_id')},
    }
    if version >= 3:
        expected_unique.update({
            'scan_generations': {('installation_id', 'generation')},
            'scan_entries': {('installation_id', 'generation', 'path_key')},
            'inventory_current': {('installation_id',)},
            'inventory_tags': {('installation_id', 'path_key')},
            'inventory_parser_cache': {('sha256', 'parser_version')},
        })
    if version >= 4:
        expected_unique.update({
            'log_sources': {('source_id',), ('installation_id', 'path_key')},
            'log_sessions': {('session_id',)},
            'log_evidence': {('evidence_id',), ('source_id', 'epoch', 'byte_start', 'byte_end', 'excerpt_hash')},
            'log_snapshots': {('source_id', 'epoch', 'size', 'sha256')},
        })
    if version >= 5:
        expected_unique['package_adoptions'] = {('snapshot_id', 'release_id')}
    if version >= 6:
        expected_unique['profile_drafts'] = {('profile_id',)}
        expected_unique['profile_editor_selection'] = {('installation_id',)}
    if version >= 7:
        expected_unique['deployment_current'] = {('installation_id',)}
    for table, expected in expected_unique.items():
        actual = set()
        for index in connection.execute(f'PRAGMA index_list({table})'):
            if index[2] and not index[4]:
                actual.add(tuple(row[2] for row in connection.execute('SELECT * FROM pragma_index_info(?)', (index[1],))))
        if not expected <= actual:
            raise StorageError('Required uniqueness constraints are missing')
    if version >= 2:
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        if not {'profile_history_insert', 'profile_history_update'} <= triggers:
            raise StorageError('Profile history triggers are missing')


def _backup(connection, path, version):
    backup = _safe(path.with_name(f'{path.name}.before-v{version}.{uuid.uuid4().hex}.bak'))
    # Exclusive file creation means an existing artifact is never overwritten.
    with backup.open('xb'):
        pass
    target = sqlite3.connect(backup, isolation_level=None)
    try:
        # A separate reader can copy while our BEGIN IMMEDIATE holds the writer
        # reservation. Backing up the transaction-owning connection can deadlock.
        source = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            source_digest = hashlib.sha256('\n'.join(source.iterdump()).encode()).hexdigest()
            source.backup(target)
        finally:
            source.close()
        _validate(target)
        if target.execute('PRAGMA user_version').fetchone()[0] != version:
            raise StorageError('Backup version differs from source')
        if target.execute('PRAGMA application_id').fetchone()[0] != DATABASE_APPLICATION_ID:
            raise StorageError('Backup application identity differs from source')
        if hashlib.sha256('\n'.join(target.iterdump()).encode()).hexdigest() != source_digest:
            raise StorageError('Backup logical contents differ from source')
    finally:
        target.close()
    digest = file_sha256(backup)
    record = backup.with_suffix(backup.suffix + '.json')
    with record.open('x', encoding='utf-8') as stream:
        json.dump({'schema_version': version, 'sha256': digest, 'logical_sha256': source_digest,
                   'source_file_sha256': file_sha256(path), 'database_name': path.name}, stream)
    return backup


def open_database(path, *, target_version=CURRENT_SCHEMA, validate=True):
    path = _safe_database(path)
    if target_version not in MIGRATIONS:
        raise StorageError('Unsupported target schema')
    exists = path.exists()
    version = _header(path) if exists else 0
    if version > target_version:
        raise StorageError('Downgrade requires explicit verified backup restore')
    if not exists:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        actual_version = connection.execute('PRAGMA user_version').fetchone()[0]
        actual_app = connection.execute('PRAGMA application_id').fetchone()[0]
        if actual_version > CURRENT_SCHEMA or actual_version > target_version:
            raise StorageError('Future schema is not writable')
        if actual_app != (DATABASE_APPLICATION_ID if exists else 0):
            raise StorageError('Foreign database application_id')
        version = actual_version
        # Startup/default callers validate the whole database. Short-lived
        # operation connections retain identity/version checks without repeating
        # full-table integrity/schema scans for every file checkpoint.
        # A migration always validates both sides, including runtime callers.
        if validate or version < target_version:
            _validate(connection)
            _validate_schema(connection, version)
        if version < target_version:
            if not exists:
                # A failed first migration must leave a recognizable, retryable
                # identity-only baseline, not an anonymous zero-byte database.
                connection.execute(f'PRAGMA application_id={DATABASE_APPLICATION_ID}')
            connection.execute('BEGIN IMMEDIATE')
            try:
                if connection.execute('PRAGMA user_version').fetchone()[0] != version:
                    raise StorageError('Schema changed concurrently; retry migration')
                if exists:
                    _backup(connection, path, version)
                for number in range(version + 1, target_version + 1):
                    MIGRATIONS[number](connection)
                    connection.execute(f'PRAGMA user_version={number}')
                connection.execute(f'PRAGMA application_id={DATABASE_APPLICATION_ID}')
                _validate(connection)
                _validate_schema(connection, target_version)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return connection
    except BaseException:
        connection.close()
        raise


def restore_backup(path, backup, *, expected_backup_sha256, expected_current_sha256):
    """Caller must close all DB handles. No implicit rollback or data deletion."""
    path, backup = _safe_database(path), _safe_database(backup)
    _header(path)
    if path == backup:
        raise StorageError('Backup must be distinct')
    if any(Path(str(path) + suffix).exists() for suffix in ('-journal', '-wal', '-shm')):
        raise StorageError('Database has active recovery sidecars; close/recover it first')
    if any(Path(str(backup) + suffix).exists() for suffix in ('-journal', '-wal', '-shm')):
        raise StorageError('Backup must be a self-contained, closed database')
    if file_sha256(backup) != expected_backup_sha256 or file_sha256(path) != expected_current_sha256:
        raise StorageError('Recovery hash mismatch')
    _header(backup)
    readonly = sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)
    try:
        _validate(readonly)
        _validate_schema(readonly, readonly.execute('PRAGMA user_version').fetchone()[0])
    finally:
        readonly.close()
    # Preserve the displaced generation as well as the supplied backup.
    retained = _safe(path.with_name(f'{path.name}.before-restore.{uuid.uuid4().hex}.bak'))
    with retained.open('xb') as stream:
        stream.write(path.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    replacement = _safe(path.with_name(f'{path.name}.restore.{uuid.uuid4().hex}.tmp'))
    with replacement.open('xb') as stream:
        stream.write(backup.read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    if file_sha256(replacement) != expected_backup_sha256:
        raise StorageError('Recovery copy verification failed')
    _safe_database(path)
    if file_sha256(path) != expected_current_sha256:
        raise StorageError('Database changed during recovery')
    os.replace(replacement, path)
    return retained
