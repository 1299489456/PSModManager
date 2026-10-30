"""Local, content-bound defense. This service never executes a Mod."""
from datetime import datetime, timezone
import copy
from pathlib import Path
import threading
import time
from uuid import uuid4

from backend.native_mods.paths import installation_dict, valid_id
from backend.scanning.worker_client import MetadataWorker
from backend.runtime.events import RuntimePolicyError
from .errors import SecurityError
from .evidence import content, difference, statistics
from .rules import RULE_VERSION, analyze
from .storage import EvidenceStore, scope


def now():
    return datetime.now(timezone.utc).isoformat()


def antivirus_state(state='not_scanned', **extra):
    return dict(state=state, engine='Microsoft Defender', cloud_policy='system_managed_unknown',
                remediation=False, checked_at=None, reason=None, detections=[], **extra)


class ModSecurityService:
    def __init__(self, paths, native_mods, *, worker=None, antivirus=None):
        self.paths, self.native = paths, native_mods
        self.worker = worker if worker is not None else MetadataWorker(paths.root)
        if antivirus is None:
            from .defender import DefenderScanner
            antivirus = DefenderScanner()
        self.antivirus = antivirus
        self.store = EvidenceStore(paths)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._operations, self._threads, self._previews, self._mutations = {}, [], {}, set()

    @staticmethod
    def _id(installation):
        return str(installation_dict(installation).get('installation_id', ''))

    def _open(self):
        if self._closed.is_set():
            raise SecurityError('SECURITY_CLOSED')

    def _busy(self, ident):
        return ident in self._mutations or self._operations.get(ident, {}).get('state') in {'queued', 'running'}

    def is_busy(self):
        with self._lock:
            return bool(self._mutations) or any(t.is_alive() for t in self._threads)

    def close(self):
        self._closed.set()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join()

    @staticmethod
    def _mod(snapshot, ident):
        if not valid_id(ident):
            raise SecurityError('SECURITY_INVALID_REQUEST')
        mod = next((m for m in snapshot['mods'] if m['id'] == ident), None)
        if mod is None:
            raise SecurityError('SECURITY_MOD_NOT_FOUND')
        return mod

    def _report(self, installation, state, mod):
        key = scope(mod)
        token = state['reports'].get(key)
        report = self.store.report(installation, token) if token else None
        if report and (report['scope'] != key or report.get('installation_id') != self._id(installation)):
            raise SecurityError('SECURITY_RECORD_INVALID')
        return report

    @staticmethod
    def _active_records(state, mod):
        # Content changes never silently release a quarantine of this source.
        return [r for r in state['records'] if r['mod_id'] == mod['id'] and r['source'] == mod['source']
                and r.get('workshop_id') == mod.get('workshop_id') and r['status'] != 'restored']

    def _item(self, installation, state, mod):
        report = self._report(installation, state, mod)
        records = self._active_records(state, mod)
        blocked = any(r['mode'] == 'quarantine' or r['status'] in {'prepared', 'failed'} for r in records)
        reasons, verdict = [], None
        if report is None:
            stage = 'unscanned'; reasons.append('SECURITY_SCAN_REQUIRED')
        else:
            verdict = report['verdict']
            reasons.extend(report['blocking_reasons'])
            reasons.extend(report.get('native_blocking_reasons', []))
            stage = ('incomplete' if verdict == 'incomplete' else 'high_risk' if verdict == 'high_risk'
                     else 'attention' if verdict == 'review_required' else 'reviewed')
            if report.get('native_blocking_reasons'):
                stage = 'attention'
            if (report.get('content_hash') is not None and report.get('stat_hint') != mod.get('package_revision')) or report.get('rule_version') != RULE_VERSION:
                stage = 'stale'; reasons = ['SECURITY_CONTENT_CHANGED']
        if scope(mod) in state['pending']:
            stage = 'incomplete'; reasons.insert(0, 'SECURITY_INCOMPLETE')
        operation = self._operations.get(self._id(installation), {})
        if operation.get('state') in {'queued', 'running'} and mod['id'] in operation.get('ids', []):
            stage = 'scanning'
        if blocked:
            stage = 'blocked'
        return dict(id=mod['id'], name=mod['name'], source=mod['source'], source_id=mod.get('source_id'),
                    workshop_id=mod.get('workshop_id'), package_revision=mod.get('package_revision'),
                    content_hash=(report or {}).get('content_hash'), state=stage, verdict=verdict,
                    blocking=blocked, blocking_reasons=['SECURITY_QUARANTINED'] if blocked else [],
                    review_reasons=list(dict.fromkeys(reasons)),
                    target_enabled=bool(mod.get('target_enabled')), scanned_at=(report or {}).get('scanned_at'),
                    report_id=(report or {}).get('report_id'), disposition=records[-1] if records else None)

    def status(self, installation):
        with self._lock:
            snap = self.native.snapshot(installation)
            ident = self._id(installation)
            error, state, items = None, None, []
            try:
                state, _ = self.store.load(installation)
            except SecurityError as exc:
                error = exc.code
            for mod in snap['mods']:
                try:
                    if state is None:
                        raise SecurityError(error)
                    items.append(self._item(installation, state, mod))
                except SecurityError as exc:
                    error = exc.code
                    records = self._active_records(state, mod) if state is not None else []
                    blocked = any(r['mode'] == 'quarantine' or r['status'] in {'prepared', 'failed'} for r in records)
                    items.append(dict(id=mod['id'], name=mod['name'], source=mod['source'],
                                      workshop_id=mod.get('workshop_id'), state='blocked' if blocked else 'unavailable',
                                      blocking=blocked, blocking_reasons=['SECURITY_QUARANTINED'] if blocked else [],
                                      review_reasons=[error], report_id=None, content_hash=None,
                                      target_enabled=mod.get('target_enabled', False),
                                      disposition=records[-1] if records else None))
            reasons = [dict(id=m['id'], code=code) for m in items if m.get('target_enabled') for code in m['blocking_reasons']]
            operation = copy.deepcopy(self._operations.get(ident))
            scanning = operation is not None and operation['state'] in {'queued', 'running'}
            return dict(installation_id=ident, state='blocked' if reasons else 'error' if error else 'scanning' if scanning else 'ready',
                        scanning=scanning, operation=operation, items=copy.deepcopy(items),
                        blocking_reasons=reasons, records=copy.deepcopy((state or {}).get('records', [])), error=error)

    def activity(self, installation):
        """Small polling DTO; never re-enumerate packages or read report files."""
        with self._lock:
            ident = self._id(installation)
            operation = copy.deepcopy(self._operations.get(ident))
            return dict(installation_id=ident, operation=operation,
                        scanning=operation is not None and operation['state'] in {'queued', 'running'})

    def _progress(self, installation, **values):
        with self._lock:
            operation = self._operations.get(self._id(installation))
            if operation and operation['state'] in {'queued', 'running'}:
                operation.update(values)

    def detail(self, installation, id):
        with self._lock:
            status = self.status(installation)
            item = next((m for m in status['items'] if m['id'] == id), None)
            if item is None:
                raise SecurityError('SECURITY_MOD_NOT_FOUND')
            report = None
            if item.get('report_id'):
                report = self.store.report(installation, item['report_id'])
            return dict(item=item, report=report, records=[r for r in status['records'] if r['mod_id'] == id], error=status['error'])

    def scan(self, installation, ids=None, antivirus=False):
        with self._lock:
            self._open()
            ident = self._id(installation)
            if self._busy(ident):
                raise SecurityError('SECURITY_BUSY')
            if not isinstance(antivirus, bool) or (ids is not None and (not isinstance(ids, list) or len(ids) > 1024
                    or any(not valid_id(i) for i in ids) or len(ids) != len(set(ids)))):
                raise SecurityError('SECURITY_INVALID_REQUEST')
            state, expected = self.store.load(installation)
            snap = self.native.snapshot(installation)
            selected = list(snap['mods']) if ids is None else [self._mod(snap, i) for i in ids]
            op = dict(id=uuid4().hex, state='queued', done=0, total=len(selected), error=None,
                      ids=[m['id'] for m in selected], current_id=None, current_name=None,
                      current_file=None, phase='queued')
            # An interrupted check must remain visibly incomplete, not inherit
            # the old report's conclusion. It does not gate ordinary Mod use.
            for mod in selected:
                state['pending'][scope(mod)] = op['id']
            self.store.save(installation, state, expected)
            self._operations[ident] = op
            thread = threading.Thread(target=self._scan, args=(installation, selected, antivirus, op),
                                      name='psmm-mod-security', daemon=True)
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
            thread.start()
            return self.status(installation)

    def _scan(self, installation, packages, antivirus, op):
        with self._lock:
            op['state'] = 'running'
        try:
            for package in packages:
                self._open()
                self._progress(installation, current_id=package['id'], current_name=package['name'],
                               current_file=None, phase='reading')
                report = self._inspect(installation, package, antivirus)
                self._progress(installation, phase='saving_report', current_file=None)
                with self._lock:
                    self._open()
                    state, expected = self.store.load(installation)
                    baseline_error = None
                    try:
                        previous = self._report(installation, state, package)
                    except SecurityError as exc:
                        # Keep the damaged report for diagnosis, but allow an
                        # explicit fresh check to replace its index entry.
                        if exc.code != 'SECURITY_RECORD_INVALID':
                            raise
                        previous, baseline_error = None, exc.code
                    report['diff'] = difference(previous, report['files'])
                    if baseline_error:
                        report['diff']['baseline_error'] = baseline_error
                    self.store.put_report(installation, report, state, expected)
                    op['done'] += 1
            with self._lock:
                op.update(state='completed', phase='completed', current_id=None, current_name=None, current_file=None)
        except Exception as exc:
            with self._lock:
                op.update(state='failed', error=getattr(exc, 'code', 'SECURITY_IO'))

    def _inspect(self, installation, package, use_antivirus):
        captured, parsed, findings, gaps = {}, {}, [], []
        av = antivirus_state()
        embedded, scanned = 0, 0
        try:
            captured = content(package, self._open)
            if captured['stat_hint'] != package.get('package_revision'):
                raise SecurityError('SECURITY_CONTENT_CHANGED')
            source_gaps = self._source_gaps(package, captured)
            for row in captured['files']:
                if row['kind'] != 'pe':
                    continue
                self._open()
                self._progress(installation, phase='analyzing', current_file=row['path'])
                result = self.store.cache(row['sha256'])
                if result and (result.get('metadata') or {}).get('security', {}).get('coverage', {}).get('status') != 'complete':
                    result = None
                if result is None:
                    result = self.worker.inspect_security(Path(package['folder_path']), row['path'], cancelled=self._closed.is_set)
                    if result.get('sha256') is not None and result.get('sha256') != row['sha256']:
                        raise SecurityError('SECURITY_CONTENT_CHANGED')
                    if (result.get('status') in {'managed', 'native'} and
                            (result.get('metadata') or {}).get('security', {}).get('coverage', {}).get('status') == 'complete'):
                        self.store.cache(row['sha256'], result)
                sec = (result.get('metadata') or {}).get('security') or {}
                if sec and (sec.get('schema_version') != 2 or sec.get('analyzer') != 'psmm-static-security-v2'):
                    raise SecurityError('SECURITY_INCOMPLETE')
                parsed[row['path']] = result
                if sec.get('coverage', {}).get('status') == 'complete':
                    scanned += 1
            findings, gaps, embedded = analyze(package, captured['files'], parsed)
            gaps.extend(source_gaps)
            if use_antivirus:
                self._progress(installation, phase='antivirus', current_file=None)
                av = self.antivirus.scan(Path(package['folder_path']), cancelled=self._closed.is_set)
            self._progress(installation, phase='verifying', current_file=None)
            if content(package, self._open)['content_hash'] != captured['content_hash']:
                raise SecurityError('SECURITY_CONTENT_CHANGED')
        except Exception as exc:
            gaps.append(dict(path=None, reason=getattr(exc, 'code', 'analysis_failed')))
        files = captured.get('files', [])
        incomplete = bool(gaps) or not captured
        high = av.get('state') == 'detected' or any(f['severity'] == 'high' for f in findings)
        if av.get('state') == 'detected':
            findings.append(dict(id='DEFENDER', rule='antivirus_detection', severity='high', title='本机防病毒引擎报告检测项',
                                 evidence=[dict(path=None, detail=d) for d in av.get('detections', [])[:24]], basis='antivirus_detection'))
        verdict = ('incomplete' if incomplete else 'high_risk' if high else
                   'review_required' if any(f['severity'] == 'warning' for f in findings) else 'no_high_risk_findings')
        reasons = (['SECURITY_INCOMPLETE'] if incomplete else []) + (['SECURITY_HIGH_RISK'] if high else [])
        assemblies = [dict(path=path, status=result.get('status'), error=result.get('error'),
                           **{k: (result.get('metadata') or {}).get('security', {}).get(k)
                              for k in ('analyzer', 'coverage', 'sizes', 'counts', 'category_counts', 'resource_kind_counts')})
                      for path, result in parsed.items()]
        stats = statistics(files, embedded)
        stats.update(method_il_bytes=sum((a.get('sizes') or {}).get('method_il_bytes', 0) for a in assemblies),
                     metadata_bytes=sum((a.get('sizes') or {}).get('metadata_bytes', 0) for a in assemblies),
                     resource_count=sum((a.get('counts') or {}).get('embedded_resources', 0) for a in assemblies))
        duplicates = {}
        for row in files:
            duplicates.setdefault(row['sha256'], []).append(row['path'])
        stats['duplicate_files'] = [dict(sha256=key, paths=paths) for key, paths in duplicates.items() if len(paths) > 1]
        return dict(schema_version=1, report_id=uuid4().hex, installation_id=self._id(installation), scope=scope(package),
                    id=package['id'], name=package['name'], source=package['source'], workshop_id=package.get('workshop_id'),
                    content_hash=captured.get('content_hash'), stat_hint=captured.get('stat_hint'), rule_version=RULE_VERSION,
                    scanned_at=now(), files=files, stats=stats, assemblies=assemblies, findings=findings, verdict=verdict,
                    coverage=dict(status='incomplete' if incomplete else 'complete', files_total=len(package.get('files', [])),
                                  files_hashed=len(files), pe_total=sum(r['kind'] == 'pe' for r in files), pe_scanned=scanned, gaps=gaps,
                                  limitations=['static_instruction_presence_not_execution', 'no_control_flow_or_dataflow',
                                               'no_resource_deserialization_or_unpacking', 'no_deobfuscation', 'no_runtime_generated_code']),
                    antivirus=av, blocking_reasons=reasons)

    @staticmethod
    def _source_gaps(package, captured):
        files = {row['path'].casefold() for row in captured['files']}
        gaps = []
        if (not captured.get('imod_marked_assemblies') or
                any(name.casefold() not in files for name in package.get('assemblies', []))):
            gaps.append(dict(path=None, reason='source_resolution_unproven'))
        sources = package.get('sources') or [package]
        if sum(p['source'] == package['source'] for p in sources) > 1:
            gaps.append(dict(path=None, reason='source_ambiguous'))
        return gaps

    def check_launch(self, installation, check=lambda: None, *, enabled_ids=None):
        """Respect explicit quarantine only; reports never gate ordinary use.

        No content reads or analysis run here. With no active dispositions this
        is a small journal read and does not even enumerate installed packages.
        """
        self._open(); check()
        with self._lock:
            if self._id(installation) in self._mutations:
                raise SecurityError('SECURITY_BUSY')
            state, _ = self.store.load(installation)
            records = [r for r in state['records'] if r['status'] != 'restored'
                       and (r['mode'] == 'quarantine' or r['status'] in {'prepared', 'failed'})]
        if records:
            if enabled_ids is not None and not any(r['mod_id'] in enabled_ids for r in records):
                return dict(checked_ids=[], content_hashes={})
            snap = self.native.snapshot(installation)
            selected = set(snap['enabled_ids'] if enabled_ids is None else enabled_ids)
            mods = {m['id']: m for m in snap['mods']}
            for record in records:
                if record['mod_id'] not in selected:
                    continue
                mod = mods.get(record['mod_id'])
                if mod is None or (record['source'] == mod['source']
                                   and record.get('workshop_id') == mod.get('workshop_id')):
                    raise SecurityError('SECURITY_QUARANTINED', {'ids': [record['mod_id']]})
        check()
        return dict(checked_ids=[], content_hashes={})

    def dispose_preview(self, installation, id, mode='disable'):
        with self._lock:
            self._open()
            if mode not in {'disable', 'quarantine'}:
                raise SecurityError('SECURITY_INVALID_REQUEST')
            self.store.load(installation)
            snap = self.native.snapshot(installation)
            mod = self._mod(snap, id)
            evidence = content(mod)
            closure = self.native.disable_preview(installation, snap['enabled_ids'], id)
            result = dict(preview_id=uuid4().hex, id=id, mode=mode, source=mod['source'], content_hash=evidence['content_hash'],
                          affected_ids=closure['affected_ids'], expected_revision=snap['revision'],
                          effect='local_package_move' if mode == 'quarantine' and mod['source'] == 'local'
                          else 'logical_quarantine_files_remain' if mode == 'quarantine' else 'disable_only')
            if len(self._previews) > 128:
                self._previews.clear()
            self._previews[result['preview_id']] = (time.monotonic(), self._id(installation), scope(mod), result)
            return copy.deepcopy(result)

    def _record_update(self, installation, record):
        state, expected = self.store.load(installation)
        state['records'] = [r for r in state['records'] if r['id'] != record['id']] + [copy.deepcopy(record)]
        self.store.save(installation, state, expected)

    @staticmethod
    def _writable(snapshot, *, recovery=False):
        runtime = snapshot.get('runtime', {})
        own_recovery_candidate = recovery and runtime.get('blocking_reason') == 'NATIVE_RECOVERY_PENDING'
        if runtime.get('state') != 'stopped' or (runtime.get('writable') is not True and not own_recovery_candidate):
            raise SecurityError('SECURITY_PROCESS_UNSAFE')

    def dispose(self, installation, preview_id):
        with self._lock:
            self._open()
            ident = self._id(installation)
            if self._busy(ident):
                raise SecurityError('SECURITY_BUSY')
            preview = self._previews.pop(preview_id, None)
            if not preview or preview[1] != ident or time.monotonic() - preview[0] > 300:
                raise SecurityError('SECURITY_PREVIEW_EXPIRED')
            value = preview[3]
            snap = self.native.snapshot(installation)
            self._writable(snap)
            mod = self._mod(snap, value['id'])
            if (snap['revision'] != value['expected_revision'] or scope(mod) != preview[2]
                    or content(mod)['content_hash'] != value['content_hash']):
                raise SecurityError('SECURITY_CONTENT_CHANGED')
            record = dict(id=uuid4().hex, mod_id=mod['id'], name=mod['name'], scope=scope(mod), source=mod['source'],
                          workshop_id=mod.get('workshop_id'), content_hash=value['content_hash'], mode=value['mode'],
                          status='prepared', created_at=now(), restored_at=None, restorable=True,
                          recovery_ids=[], package_recovery_id=None, effect=value['effect'], error=None,
                          affected_ids=value['affected_ids'])
            self._record_update(installation, record)
            self._mutations.add(ident)
            try:
                snap = self.native.security_disable(installation, snap['revision'], mod['id'], operation_id=record['id'])
                record['recovery_ids'].append(snap['recovery_id'])
                self._record_update(installation, record)
                if value['mode'] == 'quarantine' and mod['source'] == 'local':
                    fresh = self._mod(snap, mod['id'])
                    if content(fresh)['content_hash'] != record['content_hash']:
                        raise SecurityError('SECURITY_CONTENT_CHANGED')
                    snap = self.native.local_remove(installation, mod['id'], snap['revision'], operation_id=record['id'])
                    record['package_recovery_id'] = snap['recovery_id']
                    record['recovery_ids'].append(snap['recovery_id'])
                record['status'] = 'applied'
                self._record_update(installation, record)
            except Exception as exc:
                record.update(status='failed', error=getattr(exc, 'code', 'SECURITY_IO'))
                self._record_update(installation, record)
                raise
            finally:
                self._mutations.discard(ident)
            return dict(record=copy.deepcopy(record), status=self.status(installation))

    def restore(self, installation, record_id):
        with self._lock:
            self._open()
            ident = self._id(installation)
            if self._busy(ident):
                raise SecurityError('SECURITY_BUSY')
            state, _ = self.store.load(installation)
            record = next((r for r in state['records'] if r['id'] == record_id and r['status'] != 'restored'), None)
            if record is None:
                raise SecurityError('SECURITY_INVALID_REQUEST')
            snap = self.native.snapshot(installation)
            self._writable(snap, recovery=True)
            self._mutations.add(ident)
            try:
                # Undoing a safety disposition is not permission to re-enable.
                snap = self.native.security_disable(installation, snap['revision'], record['mod_id'], operation_id=record_id, recovery=True)
                recovery_rows = self.native.recovery_list(installation)['items']
                matches = [r for r in recovery_rows
                           if r.get('security_operation_id') == record_id and r.get('kind') == 'local_remove']
                if len(matches) > 1:
                    raise SecurityError('SECURITY_RECORD_INVALID')
                recovery = matches[0] if matches else None
                if recovery and (recovery['kind'] != 'local_remove' or recovery['mod_id'] != record['mod_id']
                                 or record.get('package_recovery_id') not in (None, recovery['id'])):
                    raise SecurityError('SECURITY_RECORD_INVALID')
                if record.get('package_recovery_id') and not recovery:
                    raise SecurityError('SECURITY_RECORD_INVALID')
                if recovery:
                    record['package_recovery_id'] = recovery['id']
                    # If a previous restore committed its package but the
                    # security journal write failed, exact content is an inert,
                    # reproducible checkpoint; never overwrite changed files.
                    current = next((m for m in snap['mods'] if scope(m) == record['scope']), None)
                    if current is None and any(m['id'] == record['mod_id'] and m['source'] == 'local' for m in snap['mods']):
                        raise SecurityError('SECURITY_CONTENT_CHANGED')
                    def verify_package(candidate):
                        if content(candidate)['content_hash'] != record['content_hash']:
                            raise SecurityError('SECURITY_CONTENT_CHANGED')
                    pending_publication = any(r.get('security_restore_of') == recovery['id']
                                              and r['status'] in {'prepared', 'failed'} for r in recovery_rows)
                    if current is None or recovery['status'] in {'prepared', 'failed'} or pending_publication:
                        snap = self.native.recovery_restore(installation, recovery['id'], snap['revision'],
                                                            security_operation_id=record_id, verify_package=verify_package)
                    elif content(current)['content_hash'] != record['content_hash']:
                        raise SecurityError('SECURITY_CONTENT_CHANGED')
                elif record['mode'] == 'quarantine' and record['source'] == 'local' and not any(scope(m) == record['scope'] for m in snap['mods']):
                    # Do not claim restored when an interrupted native operation
                    # has not supplied an attributable recovery checkpoint.
                    raise SecurityError('SECURITY_RECORD_INVALID')
                state, expected = self.store.load(installation)
                state['reports'].pop(record['scope'], None)
                record.update(status='restored', restored_at=now(), restorable=False, error=None)
                state['records'] = [r if r['id'] != record_id else record for r in state['records']]
                self.store.save(installation, state, expected)
            finally:
                self._mutations.discard(ident)
            return dict(record=copy.deepcopy(record), status=self.status(installation))
