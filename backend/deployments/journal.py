"""Handle-bound private files and a durable, authenticated transaction journal.

FlushFileBuffers is the application's durability boundary. It is not a claim
that a disk controller or sudden power loss cannot lose acknowledged writes.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import time
from uuid import UUID, uuid4

from .paths import (DirectoryChain, GuardRejected, _information, canonical_relative,
                    file_identity, flush_file, native_rename, open_handle,
                    pinned_chain, strong_hash)

# Workspace saves can change a complete multi-file Mod, while legacy direct
# actions retain their smaller operation limit. Recovery must retain every
# per-file transition as well as the complete before/after manifests.
MAX_JOURNAL_BYTES = 16 * 1024 * 1024
MAX_JOURNAL_RECORDS = 32000
MAX_COPY_BYTES = 512 * 1024 * 1024
_DOMAIN = b'PSModManager.transaction-journal.v1:'


class JournalRejected(GuardRejected):
    """A journal cannot safely authorize a filesystem recovery operation."""


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


@contextmanager
def private_dir(data_root, relative, create=False):
    """Pin all ancestors and open/create each private component by parent handle."""
    relative = canonical_relative(relative)
    data_root = Path(data_root)
    with pinned_chain(data_root) as root, ExitStack() as stack:
        identities, handles = list(root), list(root.handles)
        volume = _information(handles[-1], True)[4]
        actual = data_root
        for component in relative.split('/'):
            root.check()
            actual = actual / component
            try:
                handle = open_handle(actual, directory=True, parent=handles[-1])
            except FileNotFoundError:
                if not create:
                    raise
                handle = open_handle(actual, directory=True, create=True, parent=handles[-1])
            stack.callback(handle.Close)
            information = _information(handle, True)
            if information[4] != volume:
                raise JournalRejected('JOURNAL_VOLUME_CHANGED')
            handles.append(handle)
            identities.append((str(actual).casefold(), file_identity(information)))
        chain = DirectoryChain(identities, handles)
        chain.check()
        yield chain
        chain.check()


@contextmanager
def _parent(data_root, relative, *, create=False):
    relative = canonical_relative(relative)
    parent, _, leaf = relative.rpartition('/')
    if parent:
        with private_dir(data_root, parent, create=create) as chain:
            yield chain, leaf
    else:
        with pinned_chain(data_root) as chain:
            yield chain, leaf


def _read_handle(handle, max_bytes):
    import win32file
    before = _information(handle, False)
    size = (before[5] << 32) | before[6]
    if size > max_bytes:
        raise JournalRejected('JOURNAL_SIZE_EXCEEDED')
    win32file.SetFilePointer(handle, 0, 0)
    chunks, remaining = [], size
    while remaining:
        _, chunk = win32file.ReadFile(handle, min(256 * 1024, remaining))
        if not chunk:
            raise JournalRejected('JOURNAL_IDENTITY_CHANGED')
        chunks.append(chunk)
        remaining -= len(chunk)
    after = _information(handle, False)
    if file_identity(before) != file_identity(after) or before[5:7] != after[5:7]:
        raise JournalRejected('JOURNAL_IDENTITY_CHANGED')
    win32file.SetFilePointer(handle, 0, 0)
    return b''.join(chunks), file_identity(before)


def _read_private(data_root, relative, max_bytes):
    with _parent(data_root, relative) as (chain, leaf):
        handle = open_handle(leaf, parent=chain.handles[-1])
        try:
            result = _read_handle(handle, max_bytes)
            chain.check()
            return result
        finally:
            handle.Close()


def read_private(data_root, relative, max_bytes=MAX_JOURNAL_BYTES):
    return _read_private(data_root, relative, max_bytes)[0]


def _write_bytes(handle, data):
    import win32file
    if not isinstance(data, bytes):
        raise TypeError('private writes require bytes')
    for offset in range(0, len(data), 256 * 1024):
        chunk = data[offset:offset + 256 * 1024]
        _, written = win32file.WriteFile(handle, chunk)
        if written != len(chunk):
            raise JournalRejected('JOURNAL_WRITE_INCOMPLETE')


def write_private(data_root, relative, data, create=True):
    """CREATE_NEW by default; existing-file updates never silently truncate."""
    with _parent(data_root, relative, create=create) as (chain, leaf):
        handle = open_handle(leaf, create=create, update=not create,
                             parent=chain.handles[-1])
        try:
            if not create and ((_information(handle, False)[5] << 32) |
                               _information(handle, False)[6]):
                raise JournalRejected('JOURNAL_OVERWRITE_FORBIDDEN')
            _write_bytes(handle, data)
            flush_file(handle)
            chain.check()
            if strong_hash(handle, limit=len(data)) != hashlib.sha256(data).hexdigest():
                raise JournalRejected('JOURNAL_WRITE_INCOMPLETE')
            return file_identity(_information(handle, False))
        finally:
            handle.Close()


def copy_private(data_root, relative, source_handle, expected_hash, fault=lambda _point: None):
    """Copy actual pinned bytes to a new private file, flush, then verify both."""
    import win32file
    before = _information(source_handle, False)
    size = (before[5] << 32) | before[6]
    if size > MAX_COPY_BYTES:
        raise JournalRejected('JOURNAL_SIZE_EXCEEDED')
    if strong_hash(source_handle) != expected_hash:
        raise JournalRejected('JOURNAL_SOURCE_CHANGED')
    deadline = time.monotonic() + 120
    with _parent(data_root, relative, create=True) as (chain, leaf):
        handle = open_handle(leaf, create=True, parent=chain.handles[-1])
        try:
            remaining = size
            win32file.SetFilePointer(source_handle, 0, 0)
            while remaining:
                if time.monotonic() > deadline:
                    raise JournalRejected('JOURNAL_COPY_TIMEOUT')
                _, data = win32file.ReadFile(source_handle, min(256 * 1024, remaining))
                if not data:
                    raise JournalRejected('JOURNAL_SOURCE_CHANGED')
                _write_bytes(handle, data)
                fault('write')
                remaining -= len(data)
            # Empty files still have a meaningful write boundary for fault tests.
            if not size:
                fault('write')
            flush_file(handle)
            fault('flush')
            chain.check()
            after = _information(source_handle, False)
            if file_identity(before) != file_identity(after) or before[5:7] != after[5:7]:
                raise JournalRejected('JOURNAL_SOURCE_CHANGED')
            if strong_hash(handle) != expected_hash or strong_hash(source_handle) != expected_hash:
                raise JournalRejected('JOURNAL_SOURCE_CHANGED')
            fault('verified')
            return file_identity(_information(handle, False))
        finally:
            handle.Close()
            win32file.SetFilePointer(source_handle, 0, 0)


def _key(data_root):
    try:
        key = read_private(data_root, '.plan-approval-key', max_bytes=32)
    except (FileNotFoundError, GuardRejected):
        raise JournalRejected('JOURNAL_KEY_INVALID') from None
    if len(key) != 32:
        raise JournalRejected('JOURNAL_KEY_INVALID')
    return key


class Journal:
    def __init__(self, data_root, transaction_id):
        self.data_root = Path(data_root)
        self.transaction_id = str(UUID(str(transaction_id)))
        self.relative_path = f'deployments/journals/{self.transaction_id}.jsonl'

    def _record(self, sequence, kind, data, previous, key):
        if not isinstance(kind, str) or not kind or len(kind) > 64 or not isinstance(data, dict):
            raise JournalRejected('JOURNAL_RECORD_INVALID')
        unsigned = dict(sequence=sequence, kind=kind, data=data, previous=previous)
        # Bind records to a data-root key AND the immutable transaction filename.
        signature = hmac.digest(key, _DOMAIN + self.transaction_id.encode('ascii') + b':' +
                                canonical(unsigned), 'sha256').hex()
        return dict(unsigned, signature=signature)

    def create(self, header, *, discard_on_error=False):
        record = self._record(1, 'header', header, '0' * 64, _key(self.data_root))
        encoded = canonical(record) + b'\n'
        if len(encoded) > MAX_JOURNAL_BYTES:
            raise JournalRejected('JOURNAL_SIZE_EXCEEDED')
        with private_dir(self.data_root, 'deployments/journals', create=True) as chain:
            temporary = f'{uuid4()}.header.bin'
            handle = open_handle(temporary, create=True, delete=True, parent=chain.handles[-1])
            try:
                _write_bytes(handle, encoded)
                flush_file(handle)
                chain.check()
                if strong_hash(handle) != hashlib.sha256(encoded).hexdigest():
                    raise JournalRejected('JOURNAL_WRITE_INCOMPLETE')
                native_rename(handle, chain.handles[-1], Path(self.relative_path).name)
                flush_file(handle)
                chain.check()
            except BaseException:
                if discard_on_error:
                    # Ordinary actions have not touched any target yet. Delete
                    # only this live handle, including a partially written temp.
                    import win32file
                    win32file.SetFileInformationByHandle(handle, 4, True)
                raise
            finally:
                handle.Close()
        return record

    def _decode(self, raw, key):
        records, previous = [], '0' * 64
        full, separator, tail = raw.rpartition(b'\n')
        if not separator:
            raise JournalRejected('JOURNAL_HEADER_MISSING')
        full += separator
        remaining = full
        cached = getattr(self, '_verified_prefix', None)
        # Read and compare the complete durable prefix on every call. Only an
        # exactly unchanged byte prefix under the same private key can reuse
        # its parsed/HMAC-verified records; changed bytes get full validation.
        if cached is not None and cached[0] == key and full.startswith(cached[1]):
            records, previous = list(cached[2]), cached[3]
            remaining = full[len(cached[1]):]
            if len(records) > MAX_JOURNAL_RECORDS:
                raise JournalRejected('JOURNAL_RECORD_INVALID')
        for line in remaining.splitlines(keepends=True):
            if not line.endswith(b'\n') or len(records) >= MAX_JOURNAL_RECORDS:
                raise JournalRejected('JOURNAL_RECORD_INVALID')
            try:
                record = json.loads(line.decode('utf-8'))
                if (not isinstance(record, dict) or set(record) !=
                        {'sequence', 'kind', 'data', 'previous', 'signature'} or
                        type(record['sequence']) is not int or record['sequence'] != len(records) + 1 or
                        record['previous'] != previous):
                    raise ValueError()
                expected = self._record(record['sequence'], record['kind'], record['data'], previous, key)
                if not isinstance(record['signature'], str) or not hmac.compare_digest(
                        record['signature'], expected['signature']) or canonical(record) + b'\n' != line:
                    raise ValueError()
                if (not records and record['kind'] != 'header') or (records and record['kind'] == 'header'):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, UnicodeDecodeError, RecursionError):
                raise JournalRejected('JOURNAL_CORRUPT') from None
            records.append(record)
            previous = hashlib.sha256(canonical(record)).hexdigest()
        if not records:
            raise JournalRejected('JOURNAL_HEADER_MISSING')
        self._verified_prefix = (key, full, tuple(records), previous)
        return records, full, tail

    def load(self, repair_tail=False):
        raw, identity = _read_private(self.data_root, self.relative_path, MAX_JOURNAL_BYTES)
        records, full, tail = self._decode(raw, _key(self.data_root))
        if tail:
            if not repair_tail:
                raise JournalRejected('JOURNAL_INCOMPLETE_TAIL')
            # Preserve the exact failed append before changing its verified file.
            tail_path = f'deployments/transactions/{self.transaction_id}/tails/{uuid4()}.bin'
            write_private(self.data_root, tail_path, tail)
            with _parent(self.data_root, self.relative_path) as (chain, leaf):
                handle = open_handle(leaf, update=True, parent=chain.handles[-1])
                try:
                    current, actual = _read_handle(handle, MAX_JOURNAL_BYTES)
                    if actual != identity or current != raw:
                        raise JournalRejected('JOURNAL_IDENTITY_CHANGED')
                    import win32file
                    win32file.SetFilePointer(handle, len(full), 0)
                    win32file.SetEndOfFile(handle)
                    flush_file(handle)
                    chain.check()
                finally:
                    handle.Close()
        # Callers may annotate their returned inspection data; never expose the
        # verified cache's dictionaries for mutation.
        return deepcopy(records)

    def append(self, kind, data):
        if kind == 'header':
            raise JournalRejected('JOURNAL_RECORD_INVALID')
        raw, identity = _read_private(self.data_root, self.relative_path, MAX_JOURNAL_BYTES)
        key = _key(self.data_root)
        records, _, tail = self._decode(raw, key)
        if tail:
            raise JournalRejected('JOURNAL_INCOMPLETE_TAIL')
        if len(records) >= MAX_JOURNAL_RECORDS:
            raise JournalRejected('JOURNAL_SIZE_EXCEEDED')
        previous = hashlib.sha256(canonical(records[-1])).hexdigest()
        record = self._record(len(records) + 1, kind, data, previous, key)
        encoded = canonical(record) + b'\n'
        if len(raw) + len(encoded) > MAX_JOURNAL_BYTES:
            raise JournalRejected('JOURNAL_SIZE_EXCEEDED')
        with _parent(self.data_root, self.relative_path) as (chain, leaf):
            handle = open_handle(leaf, update=True, parent=chain.handles[-1])
            try:
                actual_raw, actual_identity = _read_handle(handle, MAX_JOURNAL_BYTES)
                if actual_identity != identity or actual_raw != raw:
                    raise JournalRejected('JOURNAL_IDENTITY_CHANGED')
                import win32file
                win32file.SetFilePointer(handle, 0, 2)
                _write_bytes(handle, encoded)
                flush_file(handle)
                chain.check()
            finally:
                handle.Close()
        return record
