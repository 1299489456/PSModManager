"""Read-only native Player.log reconciliation; configuration is never execution proof.

The parser publishes IDs, states and source line numbers only. Owner identifiers,
approval fingerprints, exception messages and other log text do not leave it.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re

from backend.app.paths import reject_reparse
from backend.games.probably_stolen.layout import approved_root, safe_path, signature

MAX_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 16384
MAX_MODS = 2000
MAX_EVENTS = 32
_ID = r'[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,63}'
_AUDIT_HEADER = re.compile(r'^\[ModLoader\] audit: game ([^,\r\n]{1,128}), trust list:')
_AUDIT = re.compile(r'^\[ModLoader\] audit: (' + _ID + r') v.{0,200}? source=(Local|Workshop) '
                    r'workshop=\d+ owner=\S+ running=(True|False) trust=([A-Za-z]+)(?:/[A-Za-z]+)? caps=')
_ENABLE = re.compile(r"^\[ModLoader\] '(" + _ID + r")' enabled=(True|False) \(restart required\)\s*$")
_APPROVE = re.compile(r"^\[ModLoader\] '(" + _ID + r")' approved: hash(?:=| )")
_ENGINE = re.compile(r'^Initialize engine version: ([^\s]{1,80})')
_AUDIT_ID = re.compile(r'^\[ModLoader\] audit: (' + _ID + r') v')


def _ids(values):
    result = []
    for value in values:
        if isinstance(value, str) and re.fullmatch(_ID, value) and value not in result:
            result.append(value)
        if len(result) >= MAX_MODS:
            break
    return result


def _finish(rows, configured_ids, mod_ids, *, status, game_version=None, unity_version=None,
            audit_line=None, complete=True, line_count=0, error_count=0):
    configured = _ids(configured_ids)
    entries = []
    for mod_id in dict.fromkeys([*_ids(mod_ids), *configured, *rows]):
        facts = rows.get(mod_id, {})
        audits = facts.get('audits', []) if status not in {'missing', 'unreadable', 'stale'} else []
        running_count = sum(row['running'] for row in audits)
        failures = sum(row['error_present'] for row in audits)
        if not audits:
            state, running = 'unknown', None
        elif facts.get('incomplete'):
            state, running = 'partial', None
        elif failures and not running_count:
            state, running = 'failed', False
        elif running_count and (running_count != len(audits) or failures):
            state, running = 'partial', None
        elif running_count:
            state, running = 'running', True
        else:
            state, running = 'not_running', False
        selected = mod_id in configured
        pending = selected != running if running is not None else None
        events = facts.get('events', [])[-MAX_EVENTS:]
        entries.append(dict(mod_id=mod_id, configured_enabled=selected, runtime_state=state,
            running=running, restart_pending=pending,
            audit_line=audits[0]['line'] if audits else None, audit_lines=[row['line'] for row in audits],
            trust_state=audits[-1]['trust_state'] if audits else None,
            observed_entry_count=len(audits), running_entry_count=running_count,
            error_entry_count=failures, events=events))
    return dict(source='Player.log', scope='latest_log_startup_audit', status=status,
        game_version=game_version, unity_version=unity_version, audit_line=audit_line,
        configured_count=len(configured), running_count=sum(e['runtime_state'] == 'running' for e in entries),
        not_running_count=sum(e['runtime_state'] == 'not_running' for e in entries),
        failed_count=sum(e['runtime_state'] == 'failed' for e in entries),
        partial_count=sum(e['runtime_state'] == 'partial' for e in entries),
        unknown_count=sum(e['runtime_state'] == 'unknown' for e in entries),
        pending_restart_count=sum(e['restart_pending'] is True for e in entries),
        entries=entries, by_id={e['mod_id']: e for e in entries},
        coverage=dict(complete=complete, startup_audit_observed=audit_line is not None,
            entry_scope='reported_entries_only', lines=line_count, error_line_count=error_count),
        source_modified_at=None, process_verified=False)


def parse_runtime_log(text, configured_ids=(), mod_ids=(), *, complete=True, check=lambda: None):
    """Parse bounded, complete lines from the most recent startup in a log.

    An audit's running flag describes recorded native entries, not process liveness
    or gameplay compatibility. Repeated IDs represent multiple native entries.
    """
    rows, game, unity, audit_line, errors = {}, None, None, None, 0
    lines = text.splitlines(keepends=True)
    for number, raw in enumerate(lines, 1):
        check()
        if ((not raw.endswith(('\n', '\r')) and number == len(lines)) or
                len(raw.encode('utf-8')) > MAX_LINE_BYTES):
            complete = False
            if raw.startswith(('Initialize engine version:', '[ModLoader] audit: game ')):
                rows,game,audit_line,errors={},None,None,0
            else:
                damaged=_AUDIT_ID.match(raw)
                if damaged and (damaged[1] in rows or len(rows)<MAX_MODS):
                    rows.setdefault(damaged[1],dict(audits=[],events=[]))['incomplete']=True
            continue
        line = raw.rstrip('\r\n').lstrip('\ufeff')
        engine = _ENGINE.match(line)
        if engine:
            rows, game, audit_line, errors = {}, None, None, 0
            unity = engine[1]
        header = _AUDIT_HEADER.match(line)
        if header:
            # Each complete audit replaces earlier reports, not their counts.
            rows, game, audit_line = {}, header[1], number
            continue
        if re.search(r'\b(?:[\w.]*Exception|Error|failed|Failure)\b', line, re.I):
            errors += 1
        match = _AUDIT.match(line) if audit_line is not None else None
        if match:
            if match[1] not in rows and len(rows) >= MAX_MODS:
                complete = False
                continue
            row = rows.setdefault(match[1], dict(audits=[], events=[]))
            # Do not retain owner, hash, note or error contents.
            if len(row['audits']) < 128:
                row['audits'].append(dict(line=number, running=match[3] == 'True',
                    trust_state=match[4], error_present=' error="' in line))
                row['events'].append(dict(kind='startup_audit', line=number))
            else:
                complete = False
            continue
        damaged = _AUDIT_ID.match(line) if audit_line is not None else None
        if damaged:
            complete=False
            if damaged[1] in rows or len(rows)<MAX_MODS:
                rows.setdefault(damaged[1],dict(audits=[],events=[]))['incomplete']=True
            continue
        match = _ENABLE.match(line) or _APPROVE.match(line)
        if match and audit_line is not None:
            if match[1] not in rows and len(rows) >= MAX_MODS:
                complete = False
                continue
            row = rows.setdefault(match[1], dict(audits=[], events=[]))
            kind = ('enabled_for_restart' if match[2] == 'True' else 'disabled_for_restart') if match.re is _ENABLE else 'approved_in_game'
            row['events'] = (row['events'] + [dict(kind=kind, line=number)])[-MAX_EVENTS:]
    return _finish(rows, configured_ids, mod_ids,
        status='available' if complete and audit_line is not None else 'partial',
        game_version=game, unity_version=unity, audit_line=audit_line,
        complete=complete, line_count=len(lines), error_count=errors)


def read_runtime_summary(user_data_root, configured_ids=(), mod_ids=(), not_before=None, *, check=lambda: None):
    """Read only fixed Player.log; Player-prev.log never supplies current state.

    not_before is a UTC datetime or Unix seconds for a known launch lower bound.
    Absence, read drift and oversized files preserve unknown runtime states.
    """
    check()
    try:
        reject_reparse(Path(user_data_root))
        root = approved_root(user_data_root)
        path = safe_path(root, 'Player.log')
        with path.open('rb', buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if signature(safe_path(root, 'Player.log').stat()) != signature(before):
                raise ValueError('log_changed')
            if before.st_size > MAX_BYTES:
                value = _finish({}, configured_ids, mod_ids, status='partial', complete=False)
                value['coverage']['reason'] = 'log_size_limit'
                return value
            raw = bytearray()
            while len(raw) < before.st_size:
                check()
                block = stream.read(min(65536, before.st_size - len(raw)))
                if not block:
                    raise ValueError('log_changed')
                raw.extend(block)
            after = os.fstat(stream.fileno())
            final = safe_path(root, 'Player.log').stat()
            if signature(before) != signature(after) or signature(before) != signature(final):
                raise ValueError('log_changed')
        encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            value = _finish({}, configured_ids, mod_ids, status='partial', complete=False)
            value['coverage']['reason'] = 'log_encoding_invalid'
            return value
        lower = not_before.timestamp() if isinstance(not_before, datetime) else not_before
        if lower is not None and before.st_mtime < float(lower):
            value = _finish({}, configured_ids, mod_ids, status='stale', complete=False)
        else:
            value = parse_runtime_log(text, configured_ids, mod_ids, check=check)
        value['source_modified_at'] = datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()
        check()
        return value
    except FileNotFoundError:
        return _finish({}, configured_ids, mod_ids, status='missing', complete=False)
    except InterruptedError:
        raise
    except (OSError, ValueError):
        return _finish({}, configured_ids, mod_ids, status='unreadable', complete=False)
