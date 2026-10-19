"""Offline diagnostics from one scoped, read-only product database snapshot."""
import hashlib
import json
import sqlite3
import time

from backend.app.identity import DATABASE_APPLICATION_ID
from backend.catalog.service import CatalogService
from backend.runtime.jobs import RuntimePolicyError
from .evidence import _VISIBLE, _NOT_ALIAS
from .rules import evaluate

MAX_ENTRIES = 512
MAX_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_LOGS = 2000
MAX_LOG_BYTES = 4 * 1024 * 1024


def _bounded(cursor, limit, byte_limit):
    rows=[];used=0;truncated=False
    for item in cursor:
        payload=item['payload'];used+=len(payload.encode('utf-8'))
        if len(rows)>=limit or used>byte_limit:
            truncated=True;break
        value=json.loads(payload)
        if 'seq' in item.keys():value['seq']=item['seq']
        if 'session' in item.keys():value['session']=json.loads(item['session'])
        rows.append(value)
    return rows,truncated


class DiagnosticService:
    def __init__(self, paths):
        self.paths=paths

    def explain(self, installation_id, generation=None, session_id=None, offset=0,
                limit=20, snapshot_id=None):
        if offset and snapshot_id is None:
            raise ValueError('Diagnostic continuation requires a snapshot ID')
        self.paths.check_owned_paths()
        db=sqlite3.connect(self.paths.database.as_uri()+'?mode=ro',uri=True,timeout=1)
        db.row_factory=sqlite3.Row
        deadline=time.monotonic()+2
        db.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
        try:
            db.execute('BEGIN')
            if db.execute('PRAGMA application_id').fetchone()[0]!=DATABASE_APPLICATION_ID:
                raise RuntimePolicyError('DIAGNOSTIC_DATABASE_INVALID')
            row=db.execute('SELECT payload FROM installations WHERE installation_id=?',(installation_id,)).fetchone()
            if row is None:raise RuntimePolicyError('INSTALLATION_NOT_FOUND')
            installation=json.loads(row[0])
            fingerprint=json.loads(db.execute('SELECT payload FROM runtime_fingerprints WHERE fingerprint_id=?',
                                              (installation['fingerprint_id'],)).fetchone()[0])
            pointer=db.execute('SELECT * FROM inventory_current WHERE installation_id=?',(installation_id,)).fetchone()
            if generation is None and pointer:
                generation=pointer['latest_complete'] or pointer['latest_attempt']
            scan=None;entries=[];inventory_truncated=False
            if generation is not None:
                scan=db.execute('SELECT * FROM scan_generations WHERE installation_id=? AND generation=?',
                                (installation_id,generation)).fetchone()
                if scan is None:raise RuntimePolicyError('INVENTORY_SCAN_NOT_FOUND')
                entries,inventory_truncated=_bounded(db.execute(
                    "SELECT payload FROM scan_entries WHERE installation_id=? AND generation=? "
                    "ORDER BY CASE WHEN path_key LIKE '%.dll' THEN 0 ELSE 1 END,path_key LIMIT ?",
                    (installation_id,generation,MAX_ENTRIES+1)),MAX_ENTRIES,MAX_INVENTORY_BYTES)
                policy=json.loads(scan['coverage']).get('policy',json.loads(scan['policy']))
                # Scan-time policy is authoritative for that immutable generation.
                fingerprint=dict(fingerprint,loader_version=policy.get('loader_version'))
                for entry in entries:
                    if time.monotonic()>deadline:raise RuntimePolicyError('DIAGNOSTIC_QUERY_LIMIT')
                    entry['analysis']=CatalogService._analysis(entry,scan['finished_at'] or scan['started_at'])
            if session_id is not None and db.execute(
                    'SELECT 1 FROM log_sessions WHERE installation_id=? AND session_id=?',
                    (installation_id,session_id)).fetchone() is None:
                raise RuntimePolicyError('DIAGNOSTIC_SESSION_NOT_FOUND')
            clause=' AND e.session_id=?' if session_id else ''
            args=[installation_id,installation_id]+([session_id] if session_id else [])
            evidence,log_truncated=_bounded(db.execute(_VISIBLE+
                'SELECT e.seq,e.payload,ls.payload AS session FROM log_evidence e JOIN log_sources s USING(source_id) '
                'JOIN log_sessions ls ON ls.session_id=e.session_id AND ls.installation_id=s.installation_id '
                'WHERE s.installation_id=? AND '+_NOT_ALIAS+clause+' ORDER BY e.seq LIMIT ?',
                [*args,MAX_LOGS+1]),MAX_LOGS,MAX_LOG_BYTES)
            log_through=db.execute('SELECT coalesce(max(e.seq),0) FROM log_evidence e JOIN log_sources s USING(source_id) '
                                   'WHERE s.installation_id=?'+clause,args[1:]).fetchone()[0]
            partial=(not scan or scan['status']!='complete' or inventory_truncated or log_truncated)
            output=evaluate(entries,evidence,installation_id=installation_id,generation=generation,
                            fingerprint=fingerprint,coverage='partial' if partial else 'complete')
            if time.monotonic()>deadline:raise RuntimePolicyError('DIAGNOSTIC_QUERY_LIMIT')
            output.update(installation_id=installation_id,generation=generation,session_id=session_id,
                          log_through=log_through,inventory_status=scan['status'] if scan else 'not_run',
                          inventory_truncated=inventory_truncated,log_truncated=log_truncated,
                          deployment_verification='not_run',session_scope='selected' if session_id else 'historical_all',
                          coverage_scope='stored_inputs_only',log_collection_coverage='unknown',
                          freshness='stored_observation',last_attempt=pointer['latest_attempt'] if pointer else None)
            digest=hashlib.sha256(json.dumps(output,sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
            if snapshot_id is not None and snapshot_id!=digest:
                raise RuntimePolicyError('DIAGNOSTIC_SNAPSHOT_CHANGED')
            rows=output.pop('diagnostics');selected=[]
            for row in rows[offset:offset+limit]:
                if len(json.dumps(output|{'diagnostics':selected+[row]},ensure_ascii=False).encode('utf-8'))>40*1024:
                    break
                selected.append(row)
            if offset<len(rows) and not selected:raise RuntimePolicyError('DIAGNOSTIC_RESULT_LIMIT')
            output.update(diagnostics=selected,total=len(rows),offset=offset,snapshot_id=digest,
                          next_offset=offset+len(selected) if offset+len(selected)<len(rows) else None)
            return output
        except sqlite3.OperationalError as exc:
            if 'interrupt' in str(exc):raise RuntimePolicyError('DIAGNOSTIC_QUERY_LIMIT') from None
            raise
        finally:
            db.close()
