"""Immutable, bounded log windows in the product database, never live file reads."""
import base64
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import re
from uuid import UUID, NAMESPACE_URL, uuid5
from backend.games.probably_stolen.historical import KNOWN_LOG_REFERENCES

MAX_RECORDS = 20000
MAX_BYTES = 32 * 1024 * 1024
MAX_SOURCES = 64

# The CTE uses bounded indexed rows, not one SQL parameter per accumulated epoch.
# Only page payloads leave SQLite; count/dedup never materialize historical text.
_VISIBLE = '''WITH epoch_first AS (
    SELECT e.source_id,e.epoch,min(e.seq) AS first_seq FROM log_evidence e
    JOIN log_sources s USING(source_id) WHERE s.installation_id=? AND s.kind IN ('latest','archive')
    GROUP BY e.source_id,e.epoch), epochs AS (
    SELECT n.source_id,n.epoch,n.sha256,n.size,s.kind,f.first_seq
    FROM log_snapshots n JOIN log_sources s USING(source_id)
    JOIN epoch_first f ON f.source_id=n.source_id AND f.epoch=n.epoch),
    aliases AS (SELECT a.source_id,a.epoch,max(a.size) AS copy_size FROM epochs a WHERE EXISTS (
        SELECT 1 FROM epochs b WHERE b.sha256=a.sha256 AND b.size=a.size
        AND b.source_id<>a.source_id AND (a.kind='archive' OR b.kind='archive')
        AND b.first_seq<a.first_seq) GROUP BY a.source_id,a.epoch) '''
_NOT_ALIAS = 'NOT EXISTS (SELECT 1 FROM aliases a WHERE a.source_id=e.source_id AND a.epoch=e.epoch AND e.byte_end<=a.copy_size)'


def _read_snapshot(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        self.connection.execute('BEGIN')
        try:
            return method(self, *args, **kwargs)
        finally:
            self.connection.rollback()
    return wrapped


def _json(value):
    result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(result.encode('utf-8')) > 128 * 1024:
        raise ValueError('LOG_CAPACITY_EXCEEDED')
    return result


def _uuid(value):
    return str(UUID(str(value)))


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid log range')
    return value


def _source_path(value, kind):
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError('Invalid log path')
    value = value.replace('\\', '/')
    parts = value.split('/')
    if any(not p or p in ('.', '..') or p.endswith((' ', '.')) or
           any(ord(c) < 32 or c in ':*?"<>|' for c in p) for p in parts):
        raise ValueError('Invalid log path')
    lower = [p.casefold() for p in parts]
    valid = ((kind == 'latest' and lower == ['melonloader', 'latest.log']) or
             (kind == 'archive' and len(parts) == 3 and lower[:2] == ['melonloader', 'logs'] and lower[-1].endswith('.log')) or
             (kind == 'user' and len(parts) >= 2 and lower[0] == 'userdata' and lower[-1].endswith('.log')))
    if not valid:
        raise ValueError('Invalid log source kind or root')
    return value, value.casefold()


class EvidenceRepository:
    def __init__(self, store):
        self.connection = store.connection

    @contextmanager
    def _write(self):
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def register_source(self, installation_id, relative_path, kind):
        installation_id = _uuid(installation_id)
        relative_path, key = _source_path(relative_path, kind)
        source_id = str(uuid5(NAMESPACE_URL, f'psmm-log:{installation_id}:{key}'))
        with self._write():
            existing = self.connection.execute('SELECT source_id FROM log_sources WHERE source_id=?', (source_id,)).fetchone()
            if existing:
                return source_id
            count = self.connection.execute('SELECT count(*) FROM log_sources WHERE installation_id=?', (installation_id,)).fetchone()[0]
            if count >= MAX_SOURCES:
                raise ValueError('LOG_CAPACITY_EXCEEDED')
            self.connection.execute('INSERT INTO log_sources VALUES (?,?,?,?,?,?)', (source_id, installation_id, key, relative_path, kind, '{}'))
        return source_id

    def sources(self, installation_id):
        rows = self.connection.execute('SELECT source_id,relative_path,kind,state FROM log_sources WHERE installation_id=? ORDER BY relative_path', (str(installation_id),))
        return [dict(row) | {'state': json.loads(row['state'])} for row in rows]

    def ingest(self, source_id, state, records, snapshot=None):
        source_id = _uuid(source_id)
        state_json = _json(state)
        if not isinstance(records, list) or len(records) > 4096:
            raise ValueError('LOG_CAPACITY_EXCEEDED')
        prepared = []
        for record in records:
            row = dict(record)
            raw = row.pop('raw')
            if not isinstance(raw, bytes) or len(raw) > 65536:
                raise ValueError('Invalid log excerpt')
            epoch = _uuid(row['epoch'])
            start = _integer(row['byte_start'], 0, 2**63 - 1)
            end = _integer(row['byte_end'], start, 2**63 - 1)
            if end - start != len(raw):
                raise ValueError('Log range does not match bytes')
            session = dict(row.pop('session'))
            session_id = _uuid(session['session_id'])
            if session.get('generation_id') is not None or session.get('association_confidence') not in ('unknown', 'inferred'):
                raise ValueError('Unsupported deployment association')
            if session.get('pid') is not None:
                _integer(session['pid'], 1, 2**32 - 1)
            group = _uuid(row['event_group_id'])
            digest = hashlib.sha256(raw).hexdigest()
            evidence_id = str(uuid5(NAMESPACE_URL, f'psmm-evidence:{source_id}:{epoch}:{start}:{end}:{digest}'))
            row.update(evidence_id=evidence_id, source_id=source_id, epoch=epoch, excerpt_hash=digest, session_id=session_id)
            prepared.append((row, raw, _json(session), _json(row), group))
        if snapshot is not None:
            snapshot = dict(snapshot)
            snapshot['epoch'] = _uuid(snapshot['epoch'])
            _integer(snapshot['size'], 0, 2**63 - 1)
            if not re.fullmatch('[a-f0-9]{64}', snapshot['sha256']):
                raise ValueError('Invalid snapshot hash')
        added = []
        with self._write():
            source = self.connection.execute('SELECT installation_id FROM log_sources WHERE source_id=?', (source_id,)).fetchone()
            if source is None:
                raise KeyError('LOG_SOURCE_NOT_FOUND')
            installation = source[0]
            count, size = self.connection.execute('SELECT count(*),coalesce(sum(length(raw)),0) FROM log_evidence').fetchone()
            for row, raw, session_json, payload, group in prepared:
                if self.connection.execute('SELECT 1 FROM log_evidence WHERE evidence_id=?', (row['evidence_id'],)).fetchone():
                    continue
                count += 1
                size += len(raw)
                if count > MAX_RECORDS or size > MAX_BYTES:
                    raise ValueError('LOG_CAPACITY_EXCEEDED')
                previous = self.connection.execute('SELECT installation_id,payload FROM log_sessions WHERE session_id=?', (row['session_id'],)).fetchone()
                if previous is not None and (previous[0] != installation or previous[1] != session_json):
                    raise ValueError('Immutable log session conflict')
                self.connection.execute('INSERT OR IGNORE INTO log_sessions VALUES (?,?,?)', (row['session_id'], installation, session_json))
                self.connection.execute('''INSERT INTO log_evidence(evidence_id,source_id,epoch,byte_start,byte_end,excerpt_hash,raw,session_id,event_group_id,payload)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''', (row['evidence_id'], source_id, row['epoch'], row['byte_start'], row['byte_end'], row['excerpt_hash'], raw, row['session_id'], group, payload))
                added.append(row['evidence_id'])
            if snapshot is not None:
                key = (source_id, snapshot['epoch'], snapshot['sha256'], snapshot['size'])
                exists = self.connection.execute('SELECT 1 FROM log_snapshots WHERE source_id=? AND epoch=? AND sha256=? AND size=?', key).fetchone()
                if exists is None:
                    if self.connection.execute('SELECT count(*) FROM log_snapshots').fetchone()[0] >= MAX_RECORDS:
                        raise ValueError('LOG_CAPACITY_EXCEEDED')
                    self.connection.execute('INSERT INTO log_snapshots VALUES (?,?,?,?)', key)
            self.connection.execute('UPDATE log_sources SET state=? WHERE source_id=?', (state_json, source_id))
        return added

    @_read_snapshot
    def list(self, installation_id, after=0, limit=20, session_id=None, group_id=None):
        _integer(after, 0, 2**63 - 1)
        _integer(limit, 1, 100)
        clauses, args = ['s.installation_id=?', _NOT_ALIAS], [str(installation_id), str(installation_id)]
        for column, value in (('session_id', session_id), ('event_group_id', group_id)):
            if value is not None:
                clauses.append('e.' + column + '=?')
                args.append(_uuid(value))
        query = ' FROM log_evidence e JOIN log_sources s USING(source_id) WHERE ' + ' AND '.join(clauses)
        total = self.connection.execute(_VISIBLE + 'SELECT count(*)' + query, args).fetchone()[0]
        rows = self.connection.execute(_VISIBLE + 'SELECT e.seq,e.payload' + query +
                                       ' AND e.seq>? ORDER BY e.seq LIMIT ?', [*args, after, limit])
        entries, next_cursor = [], after
        for row in rows:
            payload = json.loads(row['payload'])
            payload['text'] = payload.get('text', '')[:512]
            entries.append(payload | {'seq': row['seq']})
            next_cursor = row['seq']
        return {'entries': entries, 'total': total, 'next_cursor': next_cursor}

    def read(self, installation_id, evidence_id, offset=0, limit=4096):
        _integer(offset, 0, 2**63 - 1)
        _integer(limit, 1, 8192)
        row = self.connection.execute('''SELECT e.* FROM log_evidence e JOIN log_sources s USING(source_id)
            WHERE e.evidence_id=? AND s.installation_id=?''', (_uuid(evidence_id), str(installation_id))).fetchone()
        if row is None:
            raise KeyError('EVIDENCE_NOT_FOUND')
        raw = bytes(row['raw'])
        if offset > len(raw):
            raise ValueError('Invalid log range')
        window = raw[offset:offset + limit]
        end = offset + len(window)
        payload = json.loads(row['payload'])
        payload.update(text=window.decode('utf-8', errors='replace'), raw_base64=base64.b64encode(window).decode('ascii'),
                       window_start=row['byte_start'] + offset, window_end=row['byte_start'] + end,
                       total=len(raw), next_offset=end if end < len(raw) else None)
        return payload

    @_read_snapshot
    def sessions(self, installation_id, after=0, limit=20):
        _integer(after, 0, 2**63 - 1)
        _integer(limit, 1, 100)
        visible = _VISIBLE + ''', visible_sessions AS (
            SELECT e.session_id,min(e.seq) AS first_seq FROM log_evidence e
            JOIN log_sources s USING(source_id) WHERE s.installation_id=? AND ''' + _NOT_ALIAS + ' GROUP BY e.session_id) '
        args = [str(installation_id), str(installation_id)]
        total = self.connection.execute(visible + 'SELECT count(*) FROM visible_sessions', args).fetchone()[0]
        rows = self.connection.execute(visible + '''SELECT v.first_seq,s.payload FROM visible_sessions v
            JOIN log_sessions s USING(session_id) WHERE v.first_seq>? ORDER BY v.first_seq LIMIT ?''', [*args, after, limit])
        entries = [json.loads(row['payload']) | {'seq': row['first_seq']} for row in rows]
        for entry in entries:
            for digest, date in KNOWN_LOG_REFERENCES.items():
                proof = self.connection.execute('''SELECT 1 FROM log_evidence e JOIN log_snapshots n
                    ON n.source_id=e.source_id AND n.epoch=e.epoch
                    WHERE e.session_id=? AND n.sha256=? AND e.byte_end<=n.size
                    AND NOT EXISTS (SELECT 1 FROM log_evidence other WHERE other.session_id=e.session_id
                        AND (other.source_id<>n.source_id OR other.epoch<>n.epoch OR other.byte_end>n.size))
                    LIMIT 1''',
                    (entry['session_id'], digest)).fetchone()
                if proof:
                    entry['historical_reference'] = dict(date=date, sha256=digest, basis='known_reference_hash')
                    break
        return {'entries': entries, 'total': total, 'next_cursor': entries[-1]['seq'] if entries else after}
