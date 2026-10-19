"""Immutable scan observations in the product SQLite; these are not package DTOs."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
from uuid import NAMESPACE_URL, uuid5


MAX_ENTRIES = 10000
MAX_JSON = 512 * 1024


def _json(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(data.encode('utf-8')) > MAX_JSON:
        raise ValueError('Inventory value exceeds size limit')
    return data


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('Invalid inventory relative path')
    value = value.replace('\\', '/')
    parts = value.split('/')
    if any(not part or part in ('.', '..') or part.endswith((' ', '.')) or
           any(ord(c) < 32 or c in ':*?"<>|' for c in part) for part in parts):
        raise ValueError('Invalid inventory relative path')
    return value, value.casefold()


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class InventoryRepository:
    """Use a connection owned by this thread. Each mutation reserves the writer."""

    def __init__(self, store):
        self.connection = store.connection

    @contextmanager
    def _write(self):
        # Do not nest a deferred savepoint: a competing reader/writer must not
        # allocate the same generation or win publication using stale pointers.
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def begin(self, installation_id, policy):
        installation_id = str(installation_id)
        policy = _json(policy)
        with self._write():
            row = self.connection.execute('SELECT latest_requested FROM inventory_current WHERE installation_id=?', (installation_id,)).fetchone()
            generation = row[0] + 1 if row else 1
            self.connection.execute('INSERT INTO scan_generations VALUES (?,?,?,?,?,?,?,?)',
                                    (installation_id, generation, 'running', policy, '{}', '[]', _now(), None))
            self.connection.execute('''INSERT INTO inventory_current(installation_id,latest_requested) VALUES (?,?)
                ON CONFLICT(installation_id) DO UPDATE SET latest_requested=excluded.latest_requested''', (installation_id, generation))
        return generation

    def finish(self, installation_id, generation, entries, status, coverage, errors):
        installation_id = str(installation_id)
        if status not in ('complete', 'partial', 'failed') or type(generation) is not int or generation < 1:
            raise ValueError('Invalid scan completion')
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise ValueError('Inventory row limit exceeded')
        coverage_json, errors_json = _json(coverage), _json(errors)
        prepared = {}
        for entry in entries:
            row = dict(entry)
            relative, key = _path(row['relative_path'])
            required = {'file_identity', 'size', 'mtime_ns', 'sha256', 'status', 'metadata', 'prediction'}
            if not required <= row.keys():
                raise ValueError('Incomplete inventory observation')
            for field, minimum in (('size', 0), ('mtime_ns', -(2**63))):
                if row[field] is not None and (type(row[field]) is not int or not minimum <= row[field] <= 2**63 - 1):
                    raise ValueError('Invalid inventory file facts')
            if row['sha256'] is not None and (not isinstance(row['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
                raise ValueError('Invalid inventory hash')
            if not isinstance(row['status'], str) or not row['status'] or len(row['status']) > 64:
                raise ValueError('Invalid inventory status')
            if key in prepared:
                raise ValueError('Duplicate case-insensitive inventory path')
            row.update(relative_path=relative, entry_id=str(uuid5(NAMESPACE_URL, 'psmm:' + installation_id + ':' + key)), presence='present')
            row.pop('tags', None)
            prepared[key] = row
            _json(row)
        with self._write():
            old = self.connection.execute('SELECT status FROM scan_generations WHERE installation_id=? AND generation=?', (installation_id, generation)).fetchone()
            if not old or old[0] != 'running':
                raise ValueError('Scan does not exist or already finished')
            pointer = self.connection.execute('SELECT * FROM inventory_current WHERE installation_id=?', (installation_id,)).fetchone()
            latest = generation == pointer['latest_requested']
            effective = status if latest else 'superseded'
            if effective == 'complete' and pointer['latest_complete'] is not None:
                for previous in self.connection.execute('SELECT path_key,payload FROM scan_entries WHERE installation_id=? AND generation=?', (installation_id, pointer['latest_complete'])):
                    if previous['path_key'] not in prepared:
                        missing = json.loads(previous['payload'])
                        missing['presence'] = 'missing'
                        prepared[previous['path_key']] = missing
            if len(prepared) > MAX_ENTRIES:
                raise ValueError('Inventory historical row limit exceeded')
            for key, row in prepared.items():
                self.connection.execute('INSERT INTO scan_entries VALUES (?,?,?,?,?)', (installation_id, generation, key, row['entry_id'], _json(row)))
            self.connection.execute('UPDATE scan_generations SET status=?,coverage=?,errors=?,finished_at=? WHERE installation_id=? AND generation=?',
                                    (effective, coverage_json, errors_json, _now(), installation_id, generation))
            self.connection.execute('''UPDATE inventory_current SET latest_attempt=CASE WHEN latest_attempt IS NULL OR latest_attempt < ? THEN ? ELSE latest_attempt END
                WHERE installation_id=?''', (generation, generation, installation_id))
            if effective == 'complete':
                self.connection.execute('UPDATE inventory_current SET latest_complete=? WHERE installation_id=?', (generation, installation_id))
        return self.list(installation_id, generation=generation, limit=1) | {'entries': []}

    def list(self, installation_id, generation=None, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid pagination')
        if generation is not None and (type(generation) is not int or generation < 1):
            raise ValueError('Invalid generation')
        installation_id = str(installation_id)
        # Pin pointer, count and page to one read snapshot during concurrent scans.
        self.connection.execute('BEGIN')
        try:
            pointer = self.connection.execute('SELECT * FROM inventory_current WHERE installation_id=?', (installation_id,)).fetchone()
            pointers = {key: pointer[key] if pointer else None for key in ('latest_complete', 'latest_attempt', 'latest_requested')}
            generation = generation if generation is not None else pointers['latest_complete'] or pointers['latest_attempt'] or pointers['latest_requested']
            scan = self.connection.execute('SELECT * FROM scan_generations WHERE installation_id=? AND generation=?', (installation_id, generation)).fetchone()
            attempt = self.connection.execute('SELECT * FROM scan_generations WHERE installation_id=? AND generation=?', (installation_id, pointers['latest_attempt'])).fetchone()
            if generation is not None and not scan:
                raise KeyError('Scan generation not found')
            total = self.connection.execute('SELECT count(*) FROM scan_entries WHERE installation_id=? AND generation=?', (installation_id, generation)).fetchone()[0]
            rows = self.connection.execute('''SELECT e.payload,t.tags FROM scan_entries e LEFT JOIN inventory_tags t
                ON t.installation_id=e.installation_id AND t.path_key=e.path_key
                WHERE e.installation_id=? AND e.generation=? ORDER BY e.path_key LIMIT ? OFFSET ?''', (installation_id, generation, limit, offset)).fetchall()
            result = [json.loads(row['payload']) | {'tags': json.loads(row['tags']) if row['tags'] else []} for row in rows]
            return dict(installation_id=installation_id, generation=generation, status=scan['status'] if scan else 'not_run',
                        entries=result, total=total, limit=limit, offset=offset, **pointers,
                        last_attempt=self._summary(attempt) if attempt else None,
                        coverage=json.loads(scan['coverage']) if scan else {}, errors=json.loads(scan['errors']) if scan else [])
        finally:
            self.connection.rollback()

    @staticmethod
    def _summary(row):
        return {key: json.loads(row[key]) if key in ('coverage', 'errors', 'policy') else row[key]
                for key in ('installation_id', 'generation', 'status', 'coverage', 'errors', 'policy', 'started_at', 'finished_at')}

    def summary(self, installation_id, generation):
        row = self.connection.execute('SELECT * FROM scan_generations WHERE installation_id=? AND generation=?', (str(installation_id), generation)).fetchone()
        if row is None:
            raise KeyError('Scan generation not found')
        return self._summary(row)

    def entry(self, installation_id, generation, entry_id):
        """Read one immutable observation, never infer a file path from an ID."""
        row = self.connection.execute('SELECT payload FROM scan_entries WHERE installation_id=? AND generation=? AND entry_id=?',
                                      (str(installation_id), generation, str(entry_id))).fetchone()
        if row is None:
            raise KeyError('Inventory entry not found')
        return json.loads(row['payload'])

    def set_tags(self, installation_id, relative_path, tags):
        _, key = _path(relative_path)
        if not isinstance(tags, list) or len(tags) > 32 or any(not isinstance(t, str) or not t or len(t) > 128 for t in tags):
            raise ValueError('Invalid inventory tags')
        with self._write():
            self.connection.execute('INSERT INTO inventory_tags VALUES (?,?,?) ON CONFLICT(installation_id,path_key) DO UPDATE SET tags=excluded.tags',
                                    (str(installation_id), key, _json(sorted(set(tags)))))

    @staticmethod
    def _cache_key(sha256, parser_version):
        if not isinstance(sha256, str) or not re.fullmatch('[a-f0-9]{64}', sha256):
            raise ValueError('Invalid cache hash')
        if not isinstance(parser_version, str) or not parser_version or len(parser_version) > 512:
            raise ValueError('Invalid parser version')

    def cache_get(self, sha256, parser_version):
        self._cache_key(sha256, parser_version)
        row = self.connection.execute('SELECT metadata FROM inventory_parser_cache WHERE sha256=? AND parser_version=?', (sha256, parser_version)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, sha256, parser_version, metadata):
        self._cache_key(sha256, parser_version)
        payload = _json(metadata)
        with self._write():
            self.connection.execute('INSERT INTO inventory_parser_cache VALUES (?,?,?) ON CONFLICT(sha256,parser_version) DO NOTHING', (sha256, parser_version, payload))
