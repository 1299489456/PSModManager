"""Actual synthetic log writer -> shared watcher -> SQLite evidence -> bridge."""
import base64
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.unit.test_installation_discovery import discover, hashes, request, wait
from tests.unit.test_installation_layout import make_installation


def call(active, command, **payload):
    response = active['api'].dispatch(request(command, payload))
    assert response['ok'], response
    return response['data']


def query(active, command='evidence.list', **payload):
    return call(active, command, installation_id=active['id'], **payload)


def until(condition, seconds=12):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        last = condition()
        if last:
            return last
        time.sleep(.04)
    raise AssertionError(f'Log observation deadline expired: {last!r}')


@pytest.fixture
def active(tmp_path):
    game = make_installation(tmp_path / '中文 日志 %')
    paths = resolve_paths(str(tmp_path / 'isolated-data'))
    context = DesktopContext(paths)
    context.initialize()
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    value = dict(context=context, api=api, game=game, paths=paths)
    try:
        candidate = discover(api)
        selected = wait(api, api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))['data'])
        assert selected['state'] == 'succeeded'
        value['id'] = selected['data']['installation']['installation_id']
        yield value
    finally:
        value['context'].close()


def watch(active, **kwargs):
    return query(active, 'logs.watch', **kwargs)


def stop(active, snapshot):
    call(active, 'task.cancel', task_id=snapshot['task_id'])
    assert wait(active['api'], snapshot)['state'] == 'cancelled'


def latest(active):
    return active['game'] / 'MelonLoader/Latest.log'


def count(active, minimum):
    return until(lambda: (page if (page := query(active))['total'] >= minimum else None))


def read(active, entry, **kwargs):
    return query(active, 'evidence.read', evidence_id=entry['evidence_id'], **kwargs)


def test_late_file_split_chinese_and_half_line_no_loss(active):
    task = watch(active)
    until(lambda: query(active, 'logs.sources')['sources'])
    assert query(active)['total'] == 0
    raw = '中文跨块半行\n'.encode('utf-8')
    latest(active).write_bytes(raw[:2])
    time.sleep(.25)
    assert query(active)['total'] == 0
    with latest(active).open('ab') as stream:
        stream.write(raw[2:-1])
    time.sleep(.25)
    assert query(active)['total'] == 0
    with latest(active).open('ab') as stream:
        stream.write(b'\n')
    entry = count(active, 1)['entries'][0]
    exact = read(active, entry)
    assert base64.b64decode(exact['raw_base64']) == raw
    assert exact['text'] == raw.decode('utf-8')
    assert exact['excerpt_hash'] == hashlib.sha256(raw).hexdigest()
    assert query(active)['total'] == 1
    stop(active, task)


def test_rotation_and_truncation_keep_old_id_exact(active, tmp_path):
    old = b'old evidence kept across rotation\n'
    latest(active).write_bytes(old)
    task = watch(active)
    first = count(active, 1)['entries'][0]
    latest(active).rename(active['game'] / 'MelonLoader/previous.txt')
    latest(active).write_bytes(b'new session after rotation\n')
    second = count(active, 2)['entries'][1]
    latest(active).write_bytes(b'x\n')
    third = count(active, 3)['entries'][2]
    assert len({first['epoch'], second['epoch'], third['epoch']}) == 3
    exact = read(active, first)
    assert base64.b64decode(exact['raw_base64']) == old
    assert exact['byte_start'] == 0 and exact['byte_end'] == len(old)
    assert exact['excerpt_hash'] == hashlib.sha256(old).hexdigest()
    (tmp_path / 'rotation-evidence.json').write_text(json.dumps({'entries': query(active), 'original_read': exact}, ensure_ascii=False), encoding='utf-8')
    stop(active, task)


def test_archive_full_copy_dedup_and_stable_tree_is_readonly(active):
    raw = b'MelonLoader v0.7.3\nLoading Mods\nRegistered 21 items\n'
    latest(active).write_bytes(raw)
    archives = active['game'] / 'MelonLoader/Logs'
    archives.mkdir()
    (archives / 'archive.log').write_bytes(raw)
    before = hashes(active['game'])
    task = watch(active)
    count(active, 3)
    until(lambda: len(query(active, 'logs.sources')['sources']) == 2)
    time.sleep(.4)
    page = query(active)
    assert page['total'] == 3
    assert len({entry['text'] for entry in page['entries']}) == 3
    stop(active, task)
    assert hashes(active['game']) == before
    (active['paths'].root.parent / 'readonly-copy-evidence.json').write_text(
        json.dumps({'before':before, 'after':hashes(active['game']), 'page':page}, ensure_ascii=False),
        encoding='utf-8')


def test_archive_partial_line_is_not_a_proven_complete_copy(active):
    latest(active).write_bytes(b'old\npartial')
    archives = active['game'] / 'MelonLoader/Logs'
    archives.mkdir()
    (archives / 'archive.log').write_bytes(b'old\npartial')
    task = watch(active)
    assert count(active, 2)['total'] == 2
    with latest(active).open('ab') as stream:
        stream.write(b'-new\n')
    assert count(active, 3)['total'] == 3
    stop(active, task)


def test_large_log_many_short_lines_polls_without_record_overflow(active):
    latest(active).write_bytes(b'\n' * 6007)
    task = watch(active)
    assert count(active, 6007)['total'] == 6007
    assert call(active, 'task.snapshot', task_id=task['task_id'])['state'] == 'running'
    stop(active, task)


def test_large_log_capacity_failure_retains_evidence_and_can_resume(active, monkeypatch):
    import backend.diagnostics.evidence as storage
    monkeypatch.setattr(storage, 'MAX_RECORDS', 1)
    latest(active).write_bytes(b'first\n')
    task = watch(active)
    first = count(active, 1)['entries'][0]
    subscription = call(active, 'task.subscribe', task_id=task['task_id'], after_sequence=0)
    with latest(active).open('ab') as stream:
        stream.write(b'second\n')
    assert wait(active['api'], task)['state'] == 'failed'
    events = call(active, 'task.events', subscription_id=subscription['subscription_id'], after_sequence=0)
    assert 'LOG_CAPACITY_EXCEEDED' in json.dumps(events)
    assert base64.b64decode(read(active, first)['raw_base64']) == b'first\n'
    monkeypatch.setattr(storage, 'MAX_RECORDS', 20000)
    resumed = watch(active)
    assert resumed['task_id'] != task['task_id']
    assert count(active, 2)['total'] == 2
    stop(active, resumed)


def test_user_selected_logs_and_tutorial_words_do_not_imply_failure(active):
    root = active['game'] / 'UserData'
    root.mkdir()
    (root / 'custom.log').write_text('教程：如果失败请检查配置。\nno time or session marker\n', encoding='utf-8')
    (root / 'unselected.log').write_text('must not read\n', encoding='utf-8')
    task = watch(active, user_logs=['UserData/custom.log'])
    page = count(active, 2)
    assert len(page['entries']) == 2
    assert all(entry['stage'] == 'unknown' for entry in page['entries'])
    assert all(entry.get('severity') not in ('error', 'fatal') and entry.get('status') != 'failed' for entry in page['entries'])
    assert {source['relative_path'] for source in query(active, 'logs.sources')['sources']} == {'MelonLoader/Latest.log', 'UserData/custom.log'}
    sessions = query(active, 'session.list')['entries']
    assert sessions and all(row['pid'] is None and row['generation_id'] is None and row['association_confidence'] == 'unknown' for row in sessions)
    stop(active, task)


def test_log_declared_pid_is_not_bound_to_deployment(active):
    latest(active).write_text('MelonLoader v0.7.3\nPID: 12345\nLoading Mods\n', encoding='utf-8')
    task = watch(active)
    count(active, 3)
    sessions = query(active, 'session.list')['entries']
    assert any(row['pid'] == 12345 for row in sessions)
    assert all(row['generation_id'] is None for row in sessions)
    assert all(row['association_confidence'] != 'confirmed' for row in sessions)
    stop(active, task)


def test_multiple_consumers_duplicate_watch_and_page_reopen(active):
    latest(active).write_bytes(b'one\ntwo\nthree\n')
    first = watch(active)
    count(active, 3)
    second = watch(active)
    assert first['task_id'] == second['task_id'] and second['reused']
    a = query(active, limit=1)
    b = query(active, limit=1)
    assert a == b
    a_next = query(active, after=a['next_cursor'], limit=1)
    assert a_next['entries'][0]['evidence_id'] != b['entries'][0]['evidence_id']
    active['api']._page_loading()
    third = watch(active)
    assert third['task_id'] == first['task_id']
    assert query(active, limit=1) == b
    assert query(active)['total'] == 3
    stop(active, first)


def test_stop_restart_context_reopen_persists_cursor_and_releases_handles(active):
    latest(active).write_bytes(b'persisted\n')
    first = watch(active)
    evidence = count(active, 1)['entries'][0]
    stop(active, first)
    second = watch(active)
    time.sleep(.3)
    assert query(active)['total'] == 1
    stop(active, second)
    active['context'].close()
    renamed = latest(active).with_name('temporary.txt')
    latest(active).rename(renamed)  # must succeed immediately after close, no pinned handle
    renamed.rename(latest(active))
    context = DesktopContext(active['paths'])
    context.initialize()
    api = DesktopApi(context)
    context.api = api
    active.update(context=context, api=api)
    third = watch(active)
    time.sleep(.3)
    assert query(active)['total'] == 1
    assert read(active, evidence)['text'] == 'persisted\n'
    with latest(active).open('ab') as stream:
        stream.write(b'after restart\n')
    assert count(active, 2)['total'] == 2
    stop(active, third)


def test_real_exclusive_file_lock_recovers(active):
    import win32con
    import win32file
    latest(active).write_bytes(b'locked then readable\n')
    handle = win32file.CreateFile(str(latest(active)), win32con.GENERIC_READ, 0, None,
                                 win32con.OPEN_EXISTING, win32con.FILE_ATTRIBUTE_NORMAL, None)
    task = watch(active)
    try:
        time.sleep(.35)
        assert query(active)['total'] == 0
        assert call(active, 'task.snapshot', task_id=task['task_id'])['state'] == 'running'
    finally:
        win32file.CloseHandle(handle)
    assert count(active, 1)['entries'][0]['text'] == 'locked then readable\n'
    stop(active, task)


def test_large_log_invalid_encoding_long_line_pagination_and_responsive_api(active):
    raw = b'\xff invalid\n' + b'a' * 40000 + b'\n'
    latest(active).write_bytes(raw)
    task = watch(active)
    latencies = []
    errors = []
    def writer():
        try:
            for start in range(0, 300, 10):
                with latest(active).open('ab') as stream:
                    stream.write(b''.join(('record-%04d 中文\n' % i).encode('utf-8') for i in range(start, start + 10)))
                time.sleep(.01)
        except BaseException as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while query(active)['total'] < 303:
            begin = time.monotonic()
            call(active, 'app.info')
            latencies.append(time.monotonic() - begin)
            assert len(latencies) < 250
            time.sleep(.02)
    finally:
        thread.join(5)
    assert not thread.is_alive() and not errors
    page = query(active, limit=20)
    assert len(page['entries']) == 20 and len(json.dumps(page)) < 32768
    assert all(len(entry['text']) <= 512 for entry in page['entries'])
    assert max(latencies, default=0) < 2
    first = page['entries'][0]
    assert base64.b64decode(read(active, first)['raw_base64']) == b'\xff invalid\n'
    # Window pagination is byte exact even for a deliberately overlong line.
    fragment = next(entry for entry in page['entries'] if entry.get('fragment'))
    window = read(active, fragment, limit=128)
    assert len(base64.b64decode(window['raw_base64'])) == 128 and window['next_offset'] == 128
    stop(active, task)


def test_multiline_exception_group_has_byte_exact_individual_evidence(active):
    lines = ['System.Exception: synthetic\n', '   at Synthetic.Entry()\n', '   at Synthetic.Main()\n', 'ordinary followup\n']
    latest(active).write_text(''.join(lines), encoding='utf-8', newline='')
    task = watch(active)
    rows = count(active, 4)['entries']
    assert len({row['event_group_id'] for row in rows[:3]}) == 1
    assert rows[3]['event_group_id'] != rows[0]['event_group_id']
    grouped = query(active, group_id=rows[0]['event_group_id'])
    assert grouped['total'] == 3
    assert b''.join(base64.b64decode(read(active, row)['raw_base64']) for row in grouped['entries']) == ''.join(lines[:3]).encode('utf-8')
    stop(active, task)


@pytest.mark.parametrize('path', ['../secret.log', 'C:/secret.log', 'UserData/../secret.log', 'Mods/a.log', 'UserData/a.txt'])
def test_bridge_rejects_unapproved_user_log_paths(active, path):
    response = active['api'].dispatch(request('logs.watch', {'installation_id': active['id'], 'user_logs': [path]}))
    assert not response['ok']
