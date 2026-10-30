"""Sanitized native startup statements, not private Player.log fixtures."""
import json
import os
import time

import pytest

from backend.diagnostics import native_log
from backend.diagnostics.native_log import parse_runtime_log, read_runtime_summary
from backend.diagnostics.sessions import SessionTracker
from tests.unit.test_log_session_rules import records


HEADER = 'Initialize engine version: 2021.3.45f2 (fixture)\n[ModLoader] audit: game 0.46D, trust list: fixture\n'


def audit(mod_id, running=True, *, error=False):
    return (f'[ModLoader] audit: {mod_id} v1.0 source=Workshop workshop=100 owner=fixture-owner '
            f'running={running} trust=Match caps=none hash=fixture-fingerprint'
            + (' error="fixture exception"' if error else '') + '\n')


def test_seven_enabled_only_one_reported_running_without_approval_inference():
    ids = ['one', 'two', 'three', 'four', 'five', 'six', 'seven']
    text = HEADER + ''.join(audit(i, i == 'one') for i in ids)
    text += "[ModLoader] 'two' enabled=True (restart required)\n"
    text += "[ModLoader] 'two' approved: hash fixture-private-hash, capabilities files\n"
    value = parse_runtime_log(text, ids, ids)
    assert value['configured_count'] == 7
    assert value['running_count'] == 1
    assert value['pending_restart_count'] == 6
    assert value['by_id']['two']['runtime_state'] == 'not_running'
    assert value['by_id']['two']['audit_lines'] == [4]
    assert value['by_id']['two']['events'][-1] == {'kind': 'approved_in_game', 'line': 11}
    assert value['process_verified'] is False
    serialized = json.dumps(value)
    assert all(secret not in serialized for secret in ('fixture-owner','fixture-fingerprint','fixture-private-hash'))


def test_missing_record_unknown_and_saved_disable_does_not_erase_running_evidence():
    value = parse_runtime_log(HEADER + audit('one'), ['missing'], ['one','missing'])
    assert value['by_id']['missing']['running'] is None
    assert value['by_id']['missing']['runtime_state'] == 'unknown'
    assert value['by_id']['missing']['restart_pending'] is None
    assert value['by_id']['one']['running'] is True
    assert value['by_id']['one']['restart_pending'] is True


def test_multiple_entries_mixed_failed_and_all_success_remain_distinct():
    value = parse_runtime_log(HEADER + audit('mixed') + audit('mixed',False,error=True)
                              + audit('failed',False,error=True) + audit('good') + audit('good'))
    assert value['by_id']['mixed']['runtime_state'] == 'partial'
    assert value['by_id']['mixed']['running'] is None
    assert value['by_id']['mixed']['observed_entry_count'] == 2
    assert value['by_id']['failed']['runtime_state'] == 'failed'
    assert value['by_id']['good']['runtime_state'] == 'running'
    assert value['partial_count'] == value['failed_count'] == value['running_count'] == 1
    assert 'fixture exception' not in json.dumps(value)


def test_latest_startup_and_latest_audit_replace_not_merge():
    value = parse_runtime_log(HEADER + audit('old') + HEADER + audit('new',False), ['old','new'])
    assert value['by_id']['old']['runtime_state'] == 'unknown'
    assert value['by_id']['new']['audit_line'] == 6
    value = parse_runtime_log(HEADER + audit('old') + 'Initialize engine version: 2021.3.45f2\n', ['old'])
    assert value['by_id']['old']['runtime_state'] == 'unknown'
    assert value['status'] == 'partial'


@pytest.mark.parametrize('broken', [audit('same',False).rstrip('\n'),
    '[ModLoader] audit: same v1.0 source=unrecognized\n',
    '[ModLoader] audit: same v1.0 ' + 'x' * 17000 + '\n'])
def test_incomplete_or_unrecognized_entry_does_not_promote_package_success(broken):
    value = parse_runtime_log(HEADER + audit('same') + broken, ['same'])
    assert value['status'] == 'partial'
    assert value['by_id']['same']['runtime_state'] == 'partial'
    assert value['running_count'] == 0


def test_tutorial_and_record_without_startup_audit_do_not_prove_running():
    value = parse_runtime_log('Tutorial: ' + HEADER + 'quoted: ' + audit('one'), ['one'])
    assert value['by_id']['one']['runtime_state'] == 'unknown'
    value = parse_runtime_log(audit('one'), ['one'])
    assert value['by_id']['one']['runtime_state'] == 'unknown'


def test_current_file_only_missing_and_stale_are_unknown(tmp_path):
    (tmp_path / 'Player-prev.log').write_text(HEADER + audit('one'))
    assert read_runtime_summary(tmp_path,['one'])['status'] == 'missing'
    current = tmp_path / 'Player.log'
    current.write_text(HEADER + audit('one'))
    os.utime(current, (1000,1000))
    value = read_runtime_summary(tmp_path,['one'],not_before=time.time())
    assert value['status'] == 'stale'
    assert value['by_id']['one']['runtime_state'] == 'unknown'


@pytest.mark.parametrize('encoding', ['utf-8-sig','utf-16'])
def test_unicode_encodings_and_exception_counts(tmp_path,encoding):
    (tmp_path / 'Player.log').write_text(HEADER + audit('one') + 'System.InvalidOperationException: fixture\n',encoding=encoding)
    value = read_runtime_summary(tmp_path,['one'])
    assert value['status'] == 'available'
    assert value['coverage']['error_line_count'] == 1
    assert value['source_modified_at']


def test_limits_encoding_invalid_and_hardlink_denied(tmp_path,monkeypatch):
    current=tmp_path/'Player.log'
    current.write_bytes(b'\xff\xff\xff')
    assert read_runtime_summary(tmp_path,['one'])['coverage']['reason']=='log_encoding_invalid'
    current.write_text(HEADER+audit('one'))
    monkeypatch.setattr(native_log,'MAX_BYTES',10)
    assert read_runtime_summary(tmp_path,['one'])['coverage']['reason']=='log_size_limit'
    os.link(current,tmp_path/'copy.log')
    assert read_runtime_summary(tmp_path,['one'])['status']=='unreadable'


def test_cancel_propagates(tmp_path):
    (tmp_path/'Player.log').write_text(HEADER+audit('one'))
    def cancel():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        read_runtime_summary(tmp_path,['one'],check=cancel)
    count=0
    def later_cancel():
        nonlocal count
        count+=1
        if count>2:raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        read_runtime_summary(tmp_path,['one'],check=later_cancel)


def test_file_growth_during_reconciliation_stays_unknown(tmp_path):
    current=tmp_path/'Player.log'
    current.write_text(HEADER+audit('one'))
    calls=0
    def append_during_read():
        nonlocal calls
        calls+=1
        if calls==2:
            with current.open('a') as output:output.write(audit('two'))
    value=read_runtime_summary(tmp_path,['one'],check=append_during_read)
    assert value['status']=='unreadable'
    assert value['running_count']==0
    assert value['by_id']['one']['runtime_state']=='unknown'


def test_native_sessions_boot_audit_and_exception_stack():
    rows=SessionTracker('native').classify(records('Initialize engine version: 2021.3.45f2',
        '[ModLoader] Loading: Test.dll', '[ModLoader]   Initialized: Test v1.0',
        '[ModLoader] audit: game 0.46D, trust list: fixture',
        'System.InvalidOperationException: failed', ' at Test.Init()',
        'Initialize engine version: 2021.3.45f2'))
    assert rows[0]['session']['association_confidence']=='inferred'
    assert rows[0]['session']['session_id']==rows[3]['session']['session_id']
    assert rows[0]['session']['session_id']!=rows[-1]['session']['session_id']
    assert [row['stage'] for row in rows[:4]]==['bootstrap','assembly_loading','initialization','native_audit']
    assert rows[4]['event_group_id']==rows[5]['event_group_id']
