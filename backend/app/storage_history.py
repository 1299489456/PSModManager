"""Rebind authenticated, idle deployment history in an unpublished data copy.

Plans and approvals stay immutable. Only directory-owned identities in journal
records change; game identities and game-volume transaction areas never do.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

from backend.contracts import ENTITY_MODELS
from backend.deployments.executor import DeploymentExecutor, TERMINAL
from backend.deployments.history import IssueJournal
from backend.deployments.journal import Journal, _key, canonical, private_dir, read_private, write_private
from backend.deployments.paths import _information, file_identity, open_handle, pinned_chain, strong_hash
from backend.deployments.planner import PlanService
from backend.storage.schema import ENTITY_TABLES
from .paths import AppPaths, overlaps


class MigrationHistoryRejected(ValueError):
    """History needs repair in its original location before it can migrate."""


class _ReadOnlyStore:
    def __init__(self, connection):
        self.connection = connection

    def get(self, entity, identity):
        table, key = ENTITY_TABLES[entity]
        row = self.connection.execute(f'SELECT payload FROM {table} WHERE {key}=?', (str(identity),)).fetchone()
        if row is None:
            raise MigrationHistoryRejected('STORAGE_HISTORY_SOURCE_INVALID')
        return ENTITY_MODELS[entity].model_validate_json(row['payload'])

    def all(self, entity):
        table, _ = ENTITY_TABLES[entity]
        return [ENTITY_MODELS[entity].model_validate_json(row['payload'])
                for row in self.connection.execute(f'SELECT payload FROM {table}')]


@contextmanager
def _store(root, **_kwargs):
    # Never open the startup thread's Store or run migrations against the source.
    connection = sqlite3.connect((root / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield _ReadOnlyStore(connection)
    finally:
        connection.close()


def _journals(root, relative, journal_type):
    try:
        with private_dir(root, relative) as directory:
            entries = sorted((root / relative).iterdir())
            result = []
            for path in entries:
                identity = str(UUID(path.stem))
                if path.name != identity + '.jsonl':
                    raise MigrationHistoryRejected('STORAGE_HISTORY_SOURCE_INVALID')
                journal = journal_type(root, identity)
                result.append((journal, journal.load()))
            directory.check()
            return result
    except FileNotFoundError:
        # Only an absent namespace is empty; a missing journal inside it is bad.
        if (root / relative).exists():
            raise
        return []


def _private_identity(root, relative, expected_hash, *, expected_identity=None, expected_size=None):
    with private_dir(root, Path(relative).parent.as_posix()) as directory:
        handle = open_handle(Path(relative).name, parent=directory.handles[-1])
        try:
            information = _information(handle, False)
            identity = file_identity(information)
            if (expected_identity is not None and identity != expected_identity
                    or expected_size is not None and ((information[5] << 32) | information[6]) != expected_size
                    or strong_hash(handle) != expected_hash):
                raise MigrationHistoryRejected('STORAGE_HISTORY_COPY_INVALID')
            directory.check()
            return identity
        finally:
            handle.Close()


def _backup_records(records):
    for record in records:
        if record['kind'] == 'backup':
            yield record['data']
        elif record['kind'] == 'ordinary_prepared':
            yield from record['data']['backups'].values()


def _snapshot(source):
    """Validate original authentication before deriving any target mutation."""
    source = Path(source)
    paths = AppPaths(source)
    paths.check_owned_paths()
    plans = PlanService(paths, None)
    executor = DeploymentExecutor(paths, SimpleNamespace(), plans)
    executor._store = lambda **kwargs: _store(source, **kwargs)
    with pinned_chain(source) as data, _store(source) as store:
        installations = {str(value.installation_id): value for value in store.all('Installation')}
        transactions = {str(value.transaction_id): value for value in store.all('Transaction')}
        # An external marker binds the old root identity. Do not rewrite it or
        # infer that terminal DB metadata makes unfinished cleanup safe.
        for installation in installations.values():
            if executor._marker(installation) is not None:
                raise MigrationHistoryRejected('STORAGE_HISTORY_RECOVERY_REQUIRED')
        journals = dict((journal.transaction_id, (journal, records))
                        for journal, records in _journals(source, 'deployments/journals', Journal))
        if journals.keys() - transactions.keys():
            raise MigrationHistoryRejected('STORAGE_HISTORY_SOURCE_INVALID')
        for identity, transaction in transactions.items():
            if transaction.state not in TERMINAL:
                raise MigrationHistoryRejected('STORAGE_HISTORY_RECOVERY_REQUIRED')
            if identity not in journals:
                if transaction.state in {'failed', 'cancelled'} and transaction.last_sequence == 0:
                    continue
                raise MigrationHistoryRejected('STORAGE_HISTORY_SOURCE_INVALID')
            journal, _ = journals[identity]
            plan = store.get('ActionPlan', transaction.plan_id)
            installation = installations[str(plan.installation_id)]
            records, header = executor._header(journal, transaction, plan)
            terminal = next((record['kind'] for record in reversed(records) if record['kind'] in TERMINAL), None)
            if (header['installation_identity'] != installation.file_identity
                    or terminal != transaction.state or records[-1]['kind'] not in TERMINAL | {'cleanup'}
                    or transaction.last_sequence != records[-1]['sequence']):
                raise MigrationHistoryRejected('STORAGE_HISTORY_RECOVERY_REQUIRED')
            # Verify fixed file areas, including external ancestor identities,
            # without touching or moving game-volume backup files.
            with executor._transaction_files(installation, transaction, header) as (base, area):
                operations = {str(operation.operation_id): operation for operation in plan.operations}
                for backup in _backup_records(records):
                    operation = operations[backup['operation_id']]
                    if operation.expected_before == 'absent' or backup['sha256'] != operation.expected_before:
                        raise MigrationHistoryRejected('STORAGE_HISTORY_SOURCE_INVALID')
                    _private_identity(base, area + '/backup/' + backup['operation_id'] + '.bin', backup['sha256'],
                                      expected_identity=backup['identity'])
            journals[identity] = (journal, records)
        issues = _journals(source, 'deployments/history/issues', IssueJournal)
        for journal, records in issues:
            header = records[0]['data']
            installation = installations[header['installation_id']]
            if (header['issue_id'] != journal.transaction_id or header['data_identity'] != data[-1][1]
                    or header['installation_identity'] != installation.file_identity
                    or [record['kind'] for record in records] not in (
                        ['header', 'resolved'], ['header', 'preservation', 'resolved'])
                    or header['source_transaction_id'] not in transactions
                    or header['current_transaction_id'] not in transactions):
                raise MigrationHistoryRejected('STORAGE_HISTORY_RECOVERY_REQUIRED')
            for record in records:
                if record['kind'] in {'header', 'preservation'}:
                    for conflict in record['data']['conflicts']:
                        if conflict.get('preserved'):
                            _conflict_identity(source, journal, conflict)
        data.check()
        return list(journals.values()) + issues


def _conflict_identity(root, journal, conflict):
    relative = conflict['copy_relative_path']
    # An authenticated conflict copy still cannot select arbitrary private data.
    if Path(relative).parent.as_posix() != 'deployments/history/conflicts/' + journal.transaction_id:
        raise MigrationHistoryRejected('STORAGE_HISTORY_COPY_INVALID')
    return _private_identity(root, relative, conflict['sha256'], expected_identity=conflict['copy_identity'])


def validate_migration_source(source: Path) -> None:
    _snapshot(Path(source))


def _replace_journal(root, journal, expected, records, key):
    previous, sealed = '0' * 64, []
    for record in records:
        updated = journal._record(record['sequence'], record['kind'], record['data'], previous, key)
        sealed.append(canonical(updated) + b'\n')
        previous = hashlib.sha256(canonical(updated)).hexdigest()
    relative = journal.relative_path
    parent = Path(relative).parent.as_posix()
    temporary = parent + '/' + uuid4().hex + '.migration.bin'
    write_private(root, temporary, b''.join(sealed))
    try:
        with private_dir(root, parent) as directory:
            if read_private(root, relative) != expected:
                raise MigrationHistoryRejected('STORAGE_HISTORY_COPY_CHANGED')
            # The destination is an unpublished migration copy, never an active
            # journal. A crash discards the whole copy and retains the source.
            os.replace(root / temporary, root / relative)
            directory.check()
    finally:
        (root / temporary).unlink(missing_ok=True)
    journal.load()


def rebind_migrated_history(source: Path, target: Path) -> None:
    source, target = Path(source), Path(target)
    if overlaps(source, target):
        raise MigrationHistoryRejected('STORAGE_HISTORY_COPY_INVALID')
    snapshot = _snapshot(source)
    if not snapshot:
        return
    key = _key(source)
    if _key(target) != key:
        raise MigrationHistoryRejected('STORAGE_HISTORY_COPY_INVALID')
    with pinned_chain(target) as target_chain:
        for original, records in snapshot:
            journal = type(original)(target, original.transaction_id)
            raw = read_private(source, original.relative_path)
            if read_private(target, journal.relative_path) != raw or journal.load() != records:
                raise MigrationHistoryRejected('STORAGE_HISTORY_COPY_CHANGED')
            updated = deepcopy(records)
            updated[0]['data']['data_identity'] = target_chain[-1][1]
            if type(journal) is Journal and records[0]['data'].get('file_storage') is None:
                area = 'deployments/transactions/' + journal.transaction_id
                for backup in _backup_records(updated):
                    relative = area + '/backup/' + backup['operation_id'] + '.bin'
                    backup['identity'] = _private_identity(target, relative, backup['sha256'])
            elif isinstance(journal, IssueJournal):
                for record in updated:
                    if record['kind'] in {'header', 'preservation'}:
                        for conflict in record['data']['conflicts']:
                            if conflict.get('preserved'):
                                conflict['copy_identity'] = _private_identity(
                                    target, conflict['copy_relative_path'], conflict['sha256'])
            _replace_journal(target, journal, raw, updated, key)
        target_chain.check()
