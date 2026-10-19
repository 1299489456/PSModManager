import hashlib
import json
import os

import pytest

from backend.diagnostics.tailer import Tailer


def test_late_file_utf8_half_line_and_resume(tmp_path):
    tail = Tailer(tmp_path, '迟到 %.log', chunk_bytes=1)
    assert tail.poll()['status'] == 'missing'
    path = tmp_path / '迟到 %.log'
    payload = '中文一行\n尾巴'.encode()
    path.write_bytes(payload[:2])
    assert tail.poll()['records'] == []
    tail = Tailer(tmp_path, path.name, state=json.loads(json.dumps(tail.state())), chunk_bytes=1)
    with path.open('ab') as stream: stream.write(payload[2:])
    result = tail.poll()
    assert [r['text'] for r in result['records']] == ['中文一行\n']
    with path.open('ab') as stream: stream.write(b'\n')
    result2 = tail.poll()
    assert result2['records'][0]['text'] == '尾巴\n'
    assert b''.join(r['raw'] for r in result['records'] + result2['records']) == payload + b'\n'
    assert tail.poll()['records'] == []


def test_long_line_decoder_ansi_and_invalid_bytes(tmp_path):
    payload = b'\x1b[31m' + '中文'.encode() * 8 + b'\x1b[0m\xff\n'
    (tmp_path / 'a.log').write_bytes(payload)
    tail = Tailer(tmp_path, 'a.log', chunk_bytes=1, max_line_bytes=4, max_read_bytes=7)
    records = []
    while True:
        result = tail.poll()
        records += result['records']
        if result['caught_up']: break
    assert b''.join(r['raw'] for r in records) == payload
    assert ''.join(r['text'] for r in records) == '中文' * 8 + '\ufffd\n'
    assert sum(r['decode_errors'] for r in records) == 1
    assert all(r['fragment'] for r in records)
    assert records[-1]['complete_line']
    assert [(r['byte_start'], r['byte_end']) for r in records] == [(i, min(i + 4, len(payload))) for i in range(0, len(payload), 4)]


@pytest.mark.parametrize('mode', ['replace', 'truncate', 'same_size', 'larger'])
def test_epoch_reset_retains_old_pending(tmp_path, mode):
    path = tmp_path / 'a.log'
    path.write_bytes(b'old\nremaining')
    tail = Tailer(tmp_path, 'a.log')
    old = tail.poll()
    if mode == 'replace':
        path.rename(tmp_path / 'archive.log')
        path.write_bytes(b'new\n')
    elif mode == 'truncate': path.write_bytes(b'n\n')
    elif mode == 'same_size': path.write_bytes(b'new\nchangedxx')
    else: path.write_bytes(b'new\nchangedxxlong\n')
    new = tail.poll()
    assert old['epoch'] != new['epoch']
    assert new['records'][0]['epoch'] == old['epoch']
    assert new['records'][0]['raw'] == b'remaining'
    assert new['records'][0]['abandoned']
    assert new['records'][1]['byte_start'] == 0


def test_per_poll_bound_snapshot_and_handles_closed(tmp_path):
    path = tmp_path / 'a.log'
    payload = b'hello\n' * 500
    path.write_bytes(payload)
    tail = Tailer(tmp_path, 'a.log', max_read_bytes=100)
    result = tail.poll()
    assert result['state']['offset'] == 100
    assert not result['caught_up'] and 'snapshot' not in result
    while not result['caught_up']: result = tail.poll()
    assert result['snapshot'] == {'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}
    # Windows rename is rejected by a normal open Python read handle.
    path.rename(tmp_path / 'closed.log')


def test_large_log_still_reads_bounded_without_snapshot(tmp_path):
    path = tmp_path / 'a.log'
    with path.open('wb') as stream:
        stream.seek(8 * 1024 * 1024)
        stream.write(b'\n')
    tail = Tailer(tmp_path, 'a.log', max_read_bytes=1048576)
    result = tail.poll()
    while not result['caught_up']: result = tail.poll()
    assert 'snapshot' not in result


@pytest.mark.parametrize('path', ['../a.log', '/a.log', 'C:/a.log', 'a:stream', 'a\\b.log', '//server/a', 'a//b'])
def test_rejects_paths(tmp_path, path):
    with pytest.raises(ValueError): Tailer(tmp_path, path)


def test_hardlink_refused_and_recovery(tmp_path):
    source = tmp_path / 'source.log'
    source.write_bytes(b'data\n')
    path = tmp_path / 'a.log'
    os.link(source, path)
    tail = Tailer(tmp_path, 'a.log')
    assert tail.poll()['status'] == 'error'
    path.unlink()
    path.write_bytes(b'fresh\n')
    assert tail.poll()['records'][0]['raw'] == b'fresh\n'


def test_cursor_validation_and_literal_replacement_character(tmp_path):
    path = tmp_path / 'a.log'
    path.write_bytes('\ufffd\n'.encode())
    tail = Tailer(tmp_path, path.name)
    assert not tail.poll()['records'][0]['decode_errors']
    state = tail.state()
    state['offset'] = -1
    with pytest.raises(ValueError): Tailer(tmp_path, path.name, state=state)


def test_cancel_stops_reads_and_can_resume(tmp_path):
    (tmp_path / 'a.log').write_bytes(b'ok\n')
    stopped = True
    tail = Tailer(tmp_path, 'a.log', cancelled=lambda: stopped)
    assert tail.poll()['state']['offset'] == 0
    stopped = False
    assert tail.poll()['records'][0]['raw'] == b'ok\n'


def test_resume_decoder_and_ansi_across_fragment_boundary(tmp_path):
    payload = b'\x1b[31m' + '中文'.encode() + b'\x1b[0m\n'
    path = tmp_path / 'a.log'
    path.write_bytes(payload)
    tail = Tailer(tmp_path, path.name, max_line_bytes=2, max_read_bytes=6)
    first = tail.poll()
    tail = Tailer(tmp_path, path.name, state=json.loads(json.dumps(first['state'])),
                  max_line_bytes=2, max_read_bytes=6)
    records = list(first['records'])
    while True:
        result = tail.poll()
        records.extend(result['records'])
        if result['caught_up']: break
    assert ''.join(r['text'] for r in records) == '中文\n'
    assert b''.join(r['raw'] for r in records) == payload
    assert not any(r['decode_errors'] for r in records)


def test_concurrent_append_retains_epoch_without_duplicate_bytes(tmp_path):
    path = tmp_path / 'a.log'
    path.write_bytes(b'old\n')
    changed = False

    def mutate_once():
        nonlocal changed
        if not changed:
            changed = True
            with path.open('ab') as stream: stream.write(b'new\n')
        return False

    tail = Tailer(tmp_path, path.name, cancelled=mutate_once)
    first = tail.poll()
    assert first['status'] == 'ok' and not first['caught_up']
    assert 'snapshot' not in first
    second = tail.poll()
    assert second['status'] == 'ok' and second['epoch'] == first['epoch']
    assert b''.join(r['raw'] for r in first['records'] + second['records']) == b'old\nnew\n'
    assert [(r['byte_start'], r['byte_end']) for r in first['records'] + second['records']] == [(0, 4), (4, 8)]
    assert tail.poll()['records'] == []


def test_concurrent_overwrite_still_invalidates(tmp_path):
    path = tmp_path / 'a.log'
    path.write_bytes(b'old\n')
    tail = Tailer(tmp_path, path.name)
    old = tail.poll()
    with path.open('ab') as stream: stream.write(b'more\n')
    changed = False

    def overwrite_once():
        nonlocal changed
        if not changed:
            changed = True
            path.write_bytes(b'NEW\nmore\nextra\n')
        return False

    tail.cancelled = overwrite_once
    drift = tail.poll()
    assert drift['status'] == 'error' and drift['error_code'] == 'log_read_drift'
    new = tail.poll()
    assert new['epoch'] != old['epoch']
    assert b''.join(r['raw'] for r in new['records']) == b'NEW\nmore\nextra\n'


def test_snapshot_hash_is_cached_only_in_process(tmp_path, monkeypatch):
    import backend.diagnostics.tailer as module

    path = tmp_path / 'a.log'
    path.write_bytes(b'line\n')
    original = module.hashlib.sha256
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module.hashlib, 'sha256', counted)
    tail = Tailer(tmp_path, path.name)
    first = tail.poll()
    for _ in range(5): assert tail.poll()['snapshot'] == first['snapshot']
    assert calls == 1
    tail = Tailer(tmp_path, path.name, state=tail.state())
    assert tail.poll()['snapshot'] == first['snapshot']
    assert calls == 2
    with path.open('ab') as stream: stream.write(b'next\n')
    assert tail.poll()['snapshot'] != first['snapshot']
    assert calls == 3


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing semantics')
def test_real_share_denial_recovers(tmp_path):
    import win32con
    import win32file

    path = tmp_path / 'a.log'
    path.write_bytes(b'recovered\n')
    handle = win32file.CreateFile(str(path), win32con.GENERIC_READ, 0, None,
                                  win32con.OPEN_EXISTING, 0, None)
    tail = Tailer(tmp_path, path.name)
    try:
        denied = tail.poll()
        assert denied['status'] == 'error' and denied['records'] == []
    finally:
        handle.Close()
    assert tail.poll()['records'][0]['raw'] == b'recovered\n'


def test_many_short_lines_bounded_resume_preserves_every_byte(tmp_path):
    payload = b'\n' * 6007 + b'x' * 20000 + b'\nlast\n'
    path = tmp_path / 'a.log'
    path.write_bytes(payload)
    tail = Tailer(tmp_path, path.name, max_records=1000)
    records = []
    polls = 0
    epoch = tail.epoch
    while True:
        result = tail.poll()
        polls += 1
        assert len(result['records']) <= 1000
        assert result['epoch'] == epoch
        records.extend(result['records'])
        if result['caught_up']: break
        tail = Tailer(tmp_path, path.name, max_records=1000,
                      state=json.loads(json.dumps(result['state'])))
    assert polls >= 7
    assert b''.join(r['raw'] for r in records) == payload
    assert records[0]['byte_start'] == 0 and records[-1]['byte_end'] == len(payload)
    assert all(a['byte_end'] == b['byte_start'] for a, b in zip(records, records[1:]))
    assert any(r['fragment'] for r in records)


def test_record_limit_counts_rotation_abandoned_pending(tmp_path):
    path = tmp_path / 'a.log'
    path.write_bytes(b'pending')
    tail = Tailer(tmp_path, path.name, max_records=1)
    assert tail.poll()['records'] == []
    path.rename(tmp_path / 'old.log')
    path.write_bytes(b'new\n')
    rotated = tail.poll()
    assert len(rotated['records']) == 1 and rotated['records'][0]['abandoned']
    assert rotated['state']['offset'] == 0 and not rotated['caught_up']
    assert tail.poll()['records'][0]['raw'] == b'new\n'


@pytest.mark.parametrize('value', [0, -1, 4097, True, 1.5])
def test_invalid_record_limit(tmp_path, value):
    with pytest.raises(ValueError, match='invalid_log_record_limit'):
        Tailer(tmp_path, 'a.log', max_records=value)
