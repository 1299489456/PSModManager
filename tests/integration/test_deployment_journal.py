"""Actual Windows private files, copy/flush and authenticated journal bytes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
import win32file

from backend.deployments import journal as module
from backend.deployments.journal import (Journal, JournalRejected, canonical,
    copy_private, private_dir, read_private, write_private)
from backend.deployments.paths import (GuardRejected, _information, file_identity,
    open_handle, pinned_chain, pinned_file, strong_hash)

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows native filesystem boundary')


@pytest.fixture
def private(tmp_path):
    root = tmp_path / 'isolated-data'
    root.mkdir()
    write_private(root, '.plan-approval-key', b'k' * 32)
    return root


def actual_path(root, journal):
    return root.joinpath(*journal.relative_path.split('/'))


def journal_with_header(root):
    journal = Journal(root, uuid4())
    journal.create({'installation_identity': 'd0:11', 'baseline': {'mods/a.dll': 'a' * 64}})
    return journal


def test_durable_header_and_append_reopen(private):
    journal = journal_with_header(private)
    first = journal.load()[0]
    second = journal.append('intent', {'operation_id': str(uuid4()), 'after': 'b' * 64})
    third = journal.append('done', {'published': True})
    reopened = Journal(private, journal.transaction_id)
    assert reopened.load() == [first, second, third]
    assert second['previous'] == hashlib.sha256(canonical(first)).hexdigest()
    assert third['previous'] == hashlib.sha256(canonical(second)).hexdigest()
    assert actual_path(private, journal).read_bytes().endswith(b'\n')


def test_canonical_utf8_and_nan_rejected():
    assert canonical({'z': '模组', 'a': 1}) == '{"a":1,"z":"模组"}'.encode('utf-8')
    with pytest.raises(ValueError):
        canonical({'invalid': float('nan')})


def test_create_never_replaces_existing_header(private):
    journal = journal_with_header(private)
    before = actual_path(private, journal).read_bytes()
    with pytest.raises(GuardRejected):
        journal.create({'different': True})
    assert actual_path(private, journal).read_bytes() == before
    assert journal.load()[0]['data']['installation_identity'] == 'd0:11'


@pytest.mark.parametrize('corruption', ['data', 'sequence', 'previous', 'signature', 'duplicate_key', 'newline'])
def test_complete_corruption_is_never_repaired(private, corruption):
    journal = journal_with_header(private)
    journal.append('intent', {'value': 'original'})
    lines = actual_path(private, journal).read_bytes().splitlines(keepends=True)
    data = json.loads(lines[1])
    if corruption == 'data':
        data['data']['value'] = 'changed'
    elif corruption == 'sequence':
        data['sequence'] = 9
    elif corruption == 'previous':
        data['previous'] = 'f' * 64
    elif corruption == 'signature':
        data['signature'] = '0' * 64
    elif corruption == 'duplicate_key':
        lines[1] = lines[1].replace(b'{', b'{"sequence":2,', 1)
    else:
        lines[1] = b'\n'
    if corruption not in ('duplicate_key', 'newline'):
        lines[1] = canonical(data) + b'\n'
    corrupted = b''.join(lines)
    actual_path(private, journal).write_bytes(corrupted)
    with pytest.raises(JournalRejected, match='JOURNAL_CORRUPT'):
        journal.load(repair_tail=True)
    assert actual_path(private, journal).read_bytes() == corrupted
    assert not (private / 'deployments' / 'transactions').exists()


@pytest.mark.parametrize('tail', [b'{"sequence":2,', b'garbled failed append', b'{"kind":"done"}'])
def test_only_unterminated_tail_is_preserved_then_repaired(private, tail):
    journal = journal_with_header(private)
    before = actual_path(private, journal).read_bytes()
    with actual_path(private, journal).open('ab') as stream:
        stream.write(tail)
    with pytest.raises(JournalRejected, match='JOURNAL_INCOMPLETE_TAIL'):
        journal.load()
    assert journal.load(repair_tail=True)[0]['kind'] == 'header'
    assert actual_path(private, journal).read_bytes() == before
    preserved = list((private / 'deployments' / 'transactions' / journal.transaction_id / 'tails').iterdir())
    assert len(preserved) == 1 and preserved[0].read_bytes() == tail
    journal.append('recovered', {'tail_saved': True})
    assert len(journal.load()) == 2
    assert len(journal.load(repair_tail=True)) == 2


def test_partial_header_is_not_repaired_or_deleted(private):
    journal = Journal(private, uuid4())
    write_private(private, journal.relative_path, b'{"sequence":1')
    with pytest.raises(JournalRejected, match='JOURNAL_HEADER_MISSING'):
        journal.load(repair_tail=True)
    assert read_private(private, journal.relative_path) == b'{"sequence":1'


def test_journal_is_bound_to_transaction_and_private_key(private, tmp_path):
    journal = journal_with_header(private)
    other = Journal(private, uuid4())
    write_private(private, other.relative_path, actual_path(private, journal).read_bytes())
    with pytest.raises(JournalRejected, match='JOURNAL_CORRUPT'):
        other.load()
    second_root = tmp_path / 'different-data-root'
    second_root.mkdir()
    write_private(second_root, '.plan-approval-key', b'x' * 32)
    write_private(second_root, journal.relative_path, actual_path(private, journal).read_bytes())
    with pytest.raises(JournalRejected, match='JOURNAL_CORRUPT'):
        Journal(second_root, journal.transaction_id).load()


def test_missing_and_wrong_size_key_cannot_create(tmp_path):
    root = tmp_path / 'no-key'
    root.mkdir()
    with pytest.raises(JournalRejected, match='JOURNAL_KEY_INVALID'):
        Journal(root, uuid4()).create({})
    write_private(root, '.plan-approval-key', b'not thirty two bytes')
    with pytest.raises(JournalRejected, match='JOURNAL_KEY_INVALID'):
        Journal(root, uuid4()).create({})
    assert not (root / 'deployments').exists()


def test_private_file_create_and_update_do_not_truncate(private):
    identity = write_private(private, 'deployments/test/object.bin', b'keep me')
    assert read_private(private, 'deployments/test/object.bin') == b'keep me'
    with private_dir(private, 'deployments/test') as chain:
        handle = open_handle('object.bin', parent=chain.handles[-1])
        try:
            assert file_identity(_information(handle, False)) == identity
        finally:
            handle.Close()
    with pytest.raises(GuardRejected):
        write_private(private, 'deployments/test/object.bin', b'replaced')
    with pytest.raises(JournalRejected, match='JOURNAL_OVERWRITE_FORBIDDEN'):
        write_private(private, 'deployments/test/object.bin', b'x', create=False)
    assert read_private(private, 'deployments/test/object.bin') == b'keep me'
    write_private(private, 'deployments/test/empty.bin', b'')
    write_private(private, 'deployments/test/empty.bin', b'one time only', create=False)
    assert read_private(private, 'deployments/test/empty.bin') == b'one time only'


@pytest.mark.parametrize('relative', ['../escape', '/outside', 'deployments/../escape',
                                     'deployments/x:stream', 'deployments/con', 'deployments/x.'])
def test_private_components_reject_path_escapes(private, relative):
    with pytest.raises(GuardRejected, match='GUARD_PATH_INVALID'):
        write_private(private, relative, b'no')


def test_private_hardlink_read_rejected(private):
    write_private(private, 'deployments/file.bin', b'original')
    os.link(private / 'deployments' / 'file.bin', private / 'deployments' / 'alias.bin')
    with pytest.raises(GuardRejected, match='GUARD_HARDLINK'):
        read_private(private, 'deployments/alias.bin')


def test_real_source_copy_hashes_flushes_and_retains_source(private, tmp_path):
    source = tmp_path / 'source.bin'
    content = b'actual immutable payload' * 30000
    source.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    points = []
    with pinned_file(source) as handle:
        original_identity = file_identity(_information(handle, False))
        identity = copy_private(private, 'deployments/copies/staged.bin', handle, digest, points.append)
        assert strong_hash(handle) == digest
        assert file_identity(_information(handle, False)) == original_identity
    assert source.read_bytes() == content
    assert read_private(private, 'deployments/copies/staged.bin') == content
    assert identity != original_identity
    assert points == ['write', 'write', 'write', 'flush', 'verified']


def test_copy_source_mismatch_rejects_before_private_write(private, tmp_path):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'original')
    with pinned_file(source) as handle:
        with pytest.raises(JournalRejected, match='JOURNAL_SOURCE_CHANGED'):
            copy_private(private, 'deployments/copies/rejected.bin', handle, '0' * 64)
    assert not (private / 'deployments').exists()


@pytest.mark.parametrize('point', ['write', 'flush', 'verified'])
def test_copy_fault_keeps_original_and_private_recovery_bytes(private, tmp_path, point):
    source = tmp_path / 'source.bin'
    content = b'original bytes' * 90000
    source.write_bytes(content)
    def fail(actual):
        if actual == point:
            raise OSError(28, 'synthetic ENOSPC or interrupted copy')
    with pinned_file(source) as handle:
        with pytest.raises(OSError):
            copy_private(private, 'deployments/copies/partial.bin', handle,
                         hashlib.sha256(content).hexdigest(), fail)
    assert source.read_bytes() == content
    target = read_private(private, 'deployments/copies/partial.bin')
    assert target and content.startswith(target)
    if point in ('flush', 'verified'):
        assert target == content


def test_journal_size_and_record_limits_preserve_previous_bytes(private, monkeypatch):
    journal = journal_with_header(private)
    before = actual_path(private, journal).read_bytes()
    monkeypatch.setattr(module, 'MAX_JOURNAL_BYTES', len(before) + 10)
    with pytest.raises(JournalRejected, match='JOURNAL_SIZE_EXCEEDED'):
        journal.append('intent', {'value': 'large'})
    assert actual_path(private, journal).read_bytes() == before
    monkeypatch.setattr(module, 'MAX_JOURNAL_BYTES', 2 * 1024 * 1024)
    monkeypatch.setattr(module, 'MAX_JOURNAL_RECORDS', 1)
    with pytest.raises(JournalRejected, match='JOURNAL_SIZE_EXCEEDED'):
        journal.append('intent', {})
    assert actual_path(private, journal).read_bytes() == before


def test_second_header_rejected_before_write(private):
    journal = journal_with_header(private)
    before = actual_path(private, journal).read_bytes()
    with pytest.raises(JournalRejected, match='JOURNAL_RECORD_INVALID'):
        journal.append('header', {})
    assert actual_path(private, journal).read_bytes() == before


def test_child_exit_after_flush_reopens_actual_chain(private):
    transaction_id = str(uuid4())
    script = (
        "import os,sys; from backend.deployments.journal import Journal; "
        "j=Journal(sys.argv[1],sys.argv[2]); j.create({'child':True}); "
        "j.append('intent',{'actual':'durable bytes'}); os._exit(73)"
    )
    child = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(private), transaction_id],
                           cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=30)
    assert child.returncode == 73, child.stderr.decode('utf-8')
    records = Journal(private, transaction_id).load()
    assert [r['kind'] for r in records] == ['header', 'intent']
    assert records[1]['data']['actual'] == 'durable bytes'


def test_child_exit_with_partial_append_preserves_only_verified_prefix(private):
    journal = journal_with_header(private)
    script = """
import os,sys
from pathlib import Path
import win32file
from backend.deployments.journal import Journal, private_dir
from backend.deployments.paths import open_handle, flush_file
j=Journal(sys.argv[1],sys.argv[2])
with private_dir(Path(sys.argv[1]),'deployments/journals') as chain:
    h=open_handle(Path(j.relative_path).name,update=True,parent=chain.handles[-1])
    win32file.SetFilePointer(h,0,2)
    win32file.WriteFile(h,b'{"sequence":2,"kind":"intent"')
    flush_file(h)
    os._exit(74)
"""
    child = subprocess.run([sys.executable, '-X', 'utf8', '-c', script,
                            str(private), journal.transaction_id],
                           cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=30)
    assert child.returncode == 74, child.stderr.decode('utf-8')
    assert len(journal.load(repair_tail=True)) == 1
    saved = list((private / 'deployments' / 'transactions' / journal.transaction_id / 'tails').iterdir())
    assert saved[0].read_bytes() == b'{"sequence":2,"kind":"intent"'


def test_private_directory_junction_never_follows_external_bytes(private, tmp_path):
    external = tmp_path / 'external-directory'
    external.mkdir()
    (external / 'secret.bin').write_bytes(b'external remains untouched')
    (private / 'deployments').mkdir()
    junction = private / 'deployments' / 'redirect'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(external)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    with pytest.raises(GuardRejected, match='GUARD_REPARSE_POINT'):
        read_private(private, 'deployments/redirect/secret.bin')
    with pytest.raises(GuardRejected, match='GUARD_REPARSE_POINT'):
        write_private(private, 'deployments/redirect/new.bin', b'never outside')
    assert (external / 'secret.bin').read_bytes() == b'external remains untouched'
    assert not (external / 'new.bin').exists()
