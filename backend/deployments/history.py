"""Authenticated deployment history and non-destructive drift preservation.

History never grants a game write capability. All retained generations,
transaction files, plans and conflict copies are reference-protected; there is
deliberately no garbage-collection or partial-deletion endpoint.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import threading
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from backend.contracts import DeploymentGeneration
from backend.storage.package_repository import content_hash
from .journal import (Journal, JournalRejected, _key, canonical, private_dir,
                      write_private, copy_private)
from .paths import (GuardRejected, capture_target, pinned_chain, open_handle,
                    _information, file_identity, strong_hash)
from .policy import PlanRejected, check_value, owned_after, target_key

MAX_ISSUES = 100
MAX_HISTORY_BYTES = 30000
_ISSUE_DOMAIN = b'PSModManager.history-issue.v1:'


class HistoryPage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID
    offset: Annotated[StrictInt, Field(ge=0, le=1000)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=10)] = 10


class IssueRecheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID


class IssueJournal(Journal):
    """Separate MAC domain and namespace from executor transaction journals."""
    def __init__(self, data_root, issue_id):
        super().__init__(data_root, issue_id)
        self.relative_path = 'deployments/history/issues/' + self.transaction_id + '.jsonl'

    def _record(self, sequence, kind, data, previous, key):
        if kind not in {'header', 'preservation', 'resolved'} or not isinstance(data, dict):
            raise JournalRejected('HISTORY_ISSUE_INVALID')
        unsigned = dict(sequence=sequence, kind=kind, data=data, previous=previous)
        signature = hmac.digest(key, _ISSUE_DOMAIN + self.transaction_id.encode('ascii') + b':' +
                                canonical(unsigned), 'sha256').hex()
        return dict(unsigned, signature=signature)

    def create(self, header):
        record = self._record(1, 'header', header, '0' * 64, _key(self.data_root))
        write_private(self.data_root, self.relative_path, canonical(record) + b'\n')
        return record


class HistoryService:
    def __init__(self, paths, guards, executor, plans):
        self.paths, self.guards, self.executor, self.plans = paths, guards, executor, plans
        self.rollback = None
        self._lock = threading.RLock()

    def frames(self, store, installation_id, check=lambda: None):
        """Validate durable provenance before using any generation as history."""
        installation = store.get('Installation', installation_id)
        root = Path(installation.canonical_root)
        rows = store.connection.execute('''SELECT payload FROM deployment_generations
            WHERE installation_id=? ORDER BY sequence LIMIT 101''', (str(installation_id),)).fetchall()
        if len(rows) > 100:
            raise PlanRejected('PLAN_LIMIT')
        result, previous, managed = [], None, None
        for row in rows:
            check()
            generation = DeploymentGeneration.model_validate_json(row['payload'])
            transaction = store.get('Transaction', generation.transaction_id)
            plan = store.get('ActionPlan', transaction.plan_id)
            records, header = self.executor._header(Journal(self.paths.root, transaction.transaction_id),
                                                     transaction, plan)
            terminals = [r for r in records if r['kind'] in {'committed', 'rolled_back', 'cancelled', 'failed'}]
            intents = [r['data']['manifest'] for r in records if r['kind'] == 'commit_intent']
            if (not terminals or terminals[-1]['kind'] != 'committed' or len(intents) != 1
                    or records[-1]['kind'] not in {'committed', 'cleanup'}
                    or transaction.state not in {'committed', 'recovery_required'}
                    or transaction.last_sequence != records[-1]['sequence']
                    or header['installation_identity'] != installation.file_identity
                    or generation.generation_id != transaction.target_generation
                    or generation.installation_id != installation.installation_id):
                raise PlanRejected('HISTORY_SOURCE_INVALID')
            manifest = intents[0]
            after = owned_after(plan)
            if (manifest.get('generation_id') != str(generation.generation_id)
                    or manifest.get('installation_id') != str(installation.installation_id)
                    or manifest.get('transaction_id') != str(transaction.transaction_id)
                    or manifest.get('sequence') != generation.sequence
                    or manifest.get('manifest_hash') != generation.manifest_hash
                    or manifest.get('plan_hash') != plan.plan_hash
                    or manifest.get('owned_files') != list(after.values())
                    or manifest.get('files') != header['after']
                    or datetime.fromisoformat(manifest['committed_at']) != generation.committed_at
                    or content_hash(list(after.values())) != generation.manifest_hash):
                raise PlanRejected('HISTORY_SOURCE_INVALID')
            baseline = store.get('BaselineSnapshot', plan.baseline_snapshot_id)
            observed = self.plans._baseline_files(baseline, installation.installation_id, plan.baseline_manifest_hash)
            if observed != header['before']:
                raise PlanRejected('HISTORY_SOURCE_INVALID')
            if header.get('execution_policy') == 'ordinary-mods-v1':
                from .ordinary import before_owned
                managed = before_owned(plan)
            else:
                adopted = self.plans._adopted(store, installation.installation_id, observed, root,
                                             self.plans.library, check,
                                             after=previous.committed_at if previous else None,
                                             before=plan.expires_at - self.plans.plan_lifetime(plan))
                managed = adopted if managed is None else self.plans._merge_owners(managed, adopted)
            if content_hash(managed) != check_value(plan, 'owned_before'):
                raise PlanRejected('HISTORY_SOURCE_INVALID')
            result.append(dict(generation=generation, transaction=transaction, plan=plan,
                               before_owned=managed, after_owned=after, header=header, records=records))
            managed, previous = after, generation
        # The planner validates continuity, owner transitions and native approvals;
        # this additional history path only permits an authenticated committed
        # receipt whose present DB state is blocked by external drift.
        checked = []
        self.plans._generation(store, installation.installation_id, root, check,
                               frames=checked, allow_blocked=True)
        if len(checked) != len(result):
            raise PlanRejected('HISTORY_SOURCE_INVALID')
        for frame, verified in zip(result, checked):
            if (frame['generation'] != verified['generation'] or
                    frame['transaction'] != verified['transaction'] or
                    frame['before_owned'] != verified['before_owned'] or
                    frame['after_owned'] != verified['after_owned']):
                raise PlanRejected('HISTORY_SOURCE_INVALID')
        return result

    def verify_backup(self, frame, operation, check=lambda: None):
        check()
        if operation.expected_before == 'absent':
            raise PlanRejected('ROLLBACK_BACKUP_NOT_REQUIRED')
        transaction = frame['transaction']
        own = [op for op in frame['plan'].operations if op.operation_id == operation.operation_id]
        records = frame['records']
        backups = [r['data'] for r in records if r['kind'] == 'backup' and
                   r['data'].get('operation_id') == str(operation.operation_id)]
        if frame['header'].get('execution_policy') == 'ordinary-mods-v1':
            from .ordinary import backups as ordinary_backups
            backup = ordinary_backups(records).get(str(operation.operation_id))
            backups = [backup] if backup else []
        if len(own) != 1 or own[0] != operation or len(backups) != 1 or backups[0].get('sha256') != operation.expected_before:
            raise PlanRejected('ROLLBACK_BACKUP_CORRUPT')
        key = target_key(operation.target_root_kind, operation.relative_path)
        original = frame['header']['before'].get(key)
        if not original or original['sha256'] != operation.expected_before:
            raise PlanRejected('ROLLBACK_BACKUP_CORRUPT')
        try:
            with self.executor._store() as store:
                installation = store.get('Installation', frame['plan'].installation_id)
            with self.executor._transaction_files(installation, transaction, frame['header']) as (base, area):
                relative = area + '/backup/' + str(operation.operation_id) + '.bin'
                with private_dir(base, Path(relative).parent.as_posix()) as chain:
                    handle = open_handle(Path(relative).name, parent=chain.handles[-1])
                    try:
                        info = _information(handle, False)
                        # Legacy backups move with the data root; sibling
                        # transaction areas remain on the game's volume.
                        expected_volume = (frame['header']['installation_identity'].split(':', 1)[0]
                            if frame['header'].get('file_storage') is not None
                            else dict(chain)[str(base).casefold()].split(':', 1)[0])
                        if (f'{info[4]:x}' != expected_volume
                                or ((info[5] << 32) | info[6]) != original['size']
                                or strong_hash(handle) != operation.expected_before):
                            raise PlanRejected('ROLLBACK_BACKUP_CORRUPT')
                        identity = file_identity(info)
                        chain.check()
                        check()
                        return dict(relative_path=relative, sha256=operation.expected_before,
                                    identity=identity)
                    finally:
                        handle.Close()
        except FileNotFoundError:
            raise PlanRejected('ROLLBACK_BACKUP_MISSING') from None
        except (OSError, GuardRejected):
            raise PlanRejected('ROLLBACK_BACKUP_CORRUPT') from None

    def _issue_files(self):
        try:
            with private_dir(self.paths.root, 'deployments/history/issues') as chain:
                paths = sorted((self.paths.root / 'deployments/history/issues').iterdir())
                if len(paths) > MAX_ISSUES:
                    raise GuardRejected('GUARD_RECOVERY_REQUIRED')
                for path in paths:
                    issue_id = UUID(path.stem)
                    if path.name != str(issue_id) + '.jsonl':
                        raise GuardRejected('GUARD_RECOVERY_REQUIRED')
                chain.check()
                return paths
        except FileNotFoundError:
            return []

    def _issues(self, installation):
        issues = []
        with pinned_chain(self.paths.root) as chain:
            data_identity = chain[-1][1]
        for path in self._issue_files():
            journal = IssueJournal(self.paths.root, path.stem)
            records = journal.load()
            header = records[0]['data']
            if (set(header) != {'issue_id', 'installation_id', 'installation_identity', 'data_identity',
                                'source_transaction_id', 'current_transaction_id', 'code', 'created_at', 'conflicts'}
                    or header['issue_id'] != path.stem or header['data_identity'] != data_identity
                    or not isinstance(header['conflicts'], list) or len(header['conflicts']) > 128
                    or [r['kind'] for r in records] not in [
                        ['header'], ['header', 'preservation'], ['header', 'resolved'],
                        ['header', 'preservation', 'resolved']]):
                raise GuardRejected('GUARD_RECOVERY_REQUIRED')
            UUID(header['installation_id']); UUID(header['source_transaction_id']); UUID(header['current_transaction_id'])
            datetime.fromisoformat(header['created_at'])
            if header['installation_id'] != str(installation.installation_id):
                continue
            if header['installation_identity'] != installation.file_identity:
                raise GuardRejected('GUARD_RECOVERY_REQUIRED')
            effective = header
            if len(records) > 1 and records[1]['kind'] == 'preservation':
                copied = records[1]['data']
                if (set(copied) != {'conflicts'} or not isinstance(copied['conflicts'], list)
                        or [(c['target_root_kind'], c['relative_path'], c['expected']) for c in copied['conflicts']] !=
                            [(c['target_root_kind'], c['relative_path'], c['expected']) for c in header['conflicts']]):
                    raise GuardRejected('GUARD_RECOVERY_REQUIRED')
                effective = dict(header, conflicts=copied['conflicts'])
            issues.append(dict(header=effective, original_header=header, records=records, journal=journal,
                               resolved=records[-1]['kind'] == 'resolved'))
        return issues

    @staticmethod
    def _public(issue):
        header = issue['header']
        return {key: header[key] for key in ('issue_id', 'source_transaction_id', 'current_transaction_id',
                                            'code', 'created_at', 'conflicts')} | dict(
            resolved=issue['resolved'], recovery_required=not issue['resolved'],
            state='resolved' if issue['resolved'] else 'recovery_required',
            files=[{key: conflict[key] for key in ('target_root_kind', 'relative_path', 'expected', 'actual', 'preserved')
                    if key in conflict} | dict(copy_sha256=conflict.get('sha256'))
                   for conflict in header['conflicts']],
            retention=dict(protected=True, cleanup_available=False))

    @contextmanager
    def _read_session(self, installation, frame):
        # The executor authenticates the capability and still rejects any other
        # pending transaction or unknown source. This scope only reads game files.
        permit = self.executor._permit(installation, frame['transaction'])
        try:
            with self.guards.operation(str(installation.installation_id), 'rollback', permit=permit) as session:
                yield session
        finally:
            self.executor._permits.discard(permit)

    def probe(self, installation, permit=None):
        try:
            active = [issue for issue in self._issues(installation) if not issue['resolved']]
            if not active:
                return
            trusted = (permit in self.executor._permits and permit.owner is self.executor and
                       permit.installation == str(installation.installation_id))
            if trusted:
                with self.executor._store() as store:
                    frames = self.frames(store, installation.installation_id)
                if frames and permit.transaction_id == str(frames[-1]['transaction'].transaction_id):
                    return
            raise GuardRejected('GUARD_RECOVERY_REQUIRED')
        except (ValueError, KeyError, OSError):
            raise GuardRejected('GUARD_RECOVERY_REQUIRED') from None

    def record_issue(self, installation, current_frame, source_frame, code, conflicts, check=lambda: None):
        with self._lock:
            # Bound and derived from a verified plan: clients never supply paths.
            accepted = {target_key(op.target_root_kind, op.relative_path)
                        for op in source_frame['plan'].operations}
            accepted |= current_frame['before_owned'].keys() | current_frame['after_owned'].keys()
            if len(conflicts) > 128 or not isinstance(code, str) or len(code) > 80:
                raise PlanRejected('PLAN_LIMIT')
            safe = []
            for conflict in conflicts:
                key = target_key(conflict['target_root_kind'], conflict['relative_path'])
                if key not in accepted:
                    raise PlanRejected('HISTORY_SOURCE_INVALID')
                entry = {name: conflict[name] for name in ('target_root_kind', 'relative_path')}
                current = current_frame['header']['after'].get(key)
                entry.update(expected=current['sha256'] if current else 'absent', actual='unavailable')
                for name in ('expected', 'actual'):
                    if name in conflict:
                        value = conflict[name]
                        if (not isinstance(value, str) or
                                value not in {'absent', 'unavailable'} and not re.fullmatch('[0-9a-f]{64}', value)):
                            raise PlanRejected('HISTORY_SOURCE_INVALID')
                        entry[name] = value
                safe.append(entry)
            # Namespace inspection can itself fail (DACL/occupied parent).
            # Persist the authenticated current block before any issue I/O.
            transaction = current_frame['transaction'].model_copy(update={'state': 'recovery_required'})
            self.executor._save(transaction)
            existing = [i for i in self._issues(installation) if not i['resolved']]
            for issue in existing:
                header = issue['header']
                if (header['source_transaction_id'] == str(source_frame['transaction'].transaction_id)
                        and header['current_transaction_id'] == str(current_frame['transaction'].transaction_id)
                        and header['code'] == code
                        and [(c['target_root_kind'], c['relative_path'], c.get('expected'), c.get('actual'))
                             for c in header['conflicts']] ==
                            [(c['target_root_kind'], c['relative_path'], c.get('expected'), c.get('actual'))
                             for c in safe]):
                    return self._public(issue)
            if len(self._issue_files()) >= MAX_ISSUES:
                raise PlanRejected('PLAN_LIMIT')
            # Persist the block before copies or the issue append can fail.
            # This state alone never certifies a successful recovery.
            issue_id = str(uuid4())
            with pinned_chain(self.paths.root) as chain:
                data_identity = chain[-1][1]
            header = dict(issue_id=issue_id, installation_id=str(installation.installation_id),
                installation_identity=installation.file_identity, data_identity=data_identity,
                source_transaction_id=str(source_frame['transaction'].transaction_id),
                current_transaction_id=str(current_frame['transaction'].transaction_id), code=code,
                created_at=datetime.now(timezone.utc).isoformat(),
                conflicts=[dict(c, preserved=False, reason='preservation_pending') for c in safe])
            journal = IssueJournal(self.paths.root, issue_id)
            journal.create(header)
            saved = []
            try:
                with self._read_session(installation, current_frame) as session:
                    for conflict in safe:
                        check(); session.check()
                        entry = dict(conflict, preserved=False)
                        try:
                            snapshot = capture_target(installation.canonical_root, installation.file_identity,
                                                      conflict['target_root_kind'], conflict['relative_path'])
                            entry['actual'] = snapshot.before_hash or 'absent'
                            if snapshot.target_identity is None:
                                entry['reason'] = 'absent'
                            else:
                                relative = 'deployments/history/conflicts/' + issue_id + '/' + str(uuid4()) + '.bin'
                                with session.step(snapshot) as target:
                                    identity = copy_private(self.paths.root, relative, target.handle, snapshot.before_hash)
                                entry.update(preserved=True, copy_relative_path=relative, sha256=snapshot.before_hash,
                                             source_identity=snapshot.target_identity, copy_identity=identity)
                        except (OSError, GuardRejected) as exc:
                            entry['reason'] = getattr(exc, 'code', 'HISTORY_PRESERVATION_UNAVAILABLE')
                        saved.append(entry)
            except (OSError, GuardRejected) as exc:
                # A live/unknown process can prevent even a guarded copy. Retain
                # a blocking issue and explicitly report that no copy was made.
                saved.extend(dict(conflict, preserved=False,
                    reason=getattr(exc, 'code', 'HISTORY_PRESERVATION_UNAVAILABLE'))
                    for conflict in safe[len(saved):])
            journal.append('preservation', dict(conflicts=saved))
            return self._public(dict(header=dict(header, conflicts=saved), resolved=False))

    def list(self, payload, *, check=lambda: None):
        payload = HistoryPage.model_validate(payload)
        check = self.plans._budget(check)
        with self._lock, self.executor._store() as store:
            installation = store.get('Installation', payload.installation_id)
            invalid = None
            try:
                frames = self.frames(store, installation.installation_id, check)
            except (ValueError, OSError, KeyError) as exc:
                frames, invalid = [], getattr(exc, 'code', 'HISTORY_SOURCE_INVALID')
            current = frames[-1] if frames else None
            by_transaction = {str(f['transaction'].transaction_id): f for f in frames}
            records = []
            for transaction in reversed(self.executor._transactions(store, installation.installation_id)):
                check()
                frame = by_transaction.get(str(transaction.transaction_id))
                plan = store.get('ActionPlan', transaction.plan_id)
                rollback_kind = any(p.kind == 'rollback_source' for p in plan.preconditions)
                kind = 'rollback' if rollback_kind else 'deployment'
                reverts = next((p.expected for p in plan.preconditions if p.kind == 'rollback_source'), None)
                if reverts is not None:
                    try:
                        descriptor = json.loads(reverts)
                        if isinstance(descriptor, dict):
                            reverts = str(UUID(descriptor['source_transaction_id']))
                        else:
                            reverts = str(UUID(reverts))
                    except (ValueError, KeyError, TypeError):
                        reverts = None
                blockers = [invalid or 'HISTORY_SOURCE_UNCOMMITTED'] if not frame else (
                    self.rollback.eligibility(frame, current, check) if self.rollback else ['ROLLBACK_PREVIEW_UNAVAILABLE'])
                draft = store.connection.execute('SELECT payload FROM profile_drafts WHERE profile_id=?',
                    (str(plan.profile_id),)).fetchone()
                profile_name = json.loads(draft['payload'])['current']['name'] if draft else (
                    '回退计划' if rollback_kind else '(unavailable)')
                generation = frame['generation'] if frame else None
                records.append(dict(generation_id=str(generation.generation_id) if generation else None,
                    sequence=generation.sequence if generation else None, transaction_id=str(transaction.transaction_id),
                    plan_id=str(plan.plan_id), committed_at=generation.committed_at.isoformat() if generation else None,
                    profile_name=profile_name, kind=kind if frame else 'unknown', reverts_transaction=reverts,
                    state=transaction.state, rollback_allowed=not blockers, rollback_blockers=blockers,
                    retention=dict(protected=True, reasons=['signed_history', 'plan_baseline', 'owner_references',
                        'rollback_source', 'current_generation' if current and frame == current else 'history_chain'],
                        refs=[dict(kind='transaction', id=str(transaction.transaction_id)),
                              dict(kind='plan', id=str(plan.plan_id)),
                              dict(kind='baseline', id=str(plan.baseline_snapshot_id))] +
                             ([dict(kind='generation', id=str(generation.generation_id))] if generation else []),
                        cleanup_available=False)))
            try:
                issues = [self._public(issue) for issue in self._issues(installation)]
            except (ValueError, OSError, KeyError):
                issues = []
                invalid = 'HISTORY_ISSUE_INVALID'
            page = records[payload.offset:payload.offset + payload.limit]
            end = payload.offset + len(page)
            response = dict(installation_id=str(installation.installation_id),
                current_generation=str(current['generation'].generation_id) if current else None,
                records=page, total=len(records), next_offset=end if end < len(records) else None,
                issues=issues, recovery_required=bool(invalid or any(i['recovery_required'] for i in issues)
                    or any(r['state'] not in {'committed', 'rolled_back', 'cancelled', 'failed'} for r in records)),
                verification_status='not_run', cleanup_available=False)
            if invalid:
                response['error_code'] = invalid
            if len(canonical(response)) > MAX_HISTORY_BYTES:
                raise PlanRejected('HISTORY_PAGE_LIMIT')
            return response

    def _check_copy(self, conflict):
        if not conflict['preserved']:
            return  # No copy was promised for an absent/unsafe occupied target.
        relative = conflict['copy_relative_path']
        with private_dir(self.paths.root, Path(relative).parent.as_posix()) as chain:
            handle = open_handle(Path(relative).name, parent=chain.handles[-1])
            try:
                if (file_identity(_information(handle, False)) != conflict['copy_identity'] or
                        strong_hash(handle) != conflict['sha256']):
                    raise PlanRejected('HISTORY_CONFLICT_COPY_CORRUPT')
                chain.check()
            finally:
                handle.Close()

    def recheck(self, payload, *, check=lambda: None):
        payload = IssueRecheck.model_validate(payload)
        check = self.plans._budget(check)
        # apply may report drift while holding the executor lock. Keep the same
        # order when recheck subsequently calls executor.recover.
        with self.executor._lock, self._lock:
            with self.executor._store() as store:
                installation = store.get('Installation', payload.installation_id)
                try:
                    frames = self.frames(store, installation.installation_id, check)
                    issues = self._issues(installation)
                except (ValueError, OSError, KeyError):
                    return dict(resolved_count=0, remaining_count=1, recovery_required=True,
                                issues=[], error_code='HISTORY_SOURCE_INVALID', verification_status='not_run')
            pending = [i for i in issues if not i['resolved']]
            if not pending:
                status = self.executor.status(dict(installation_id=str(installation.installation_id)))
                if status['recovery_required'] and frames:
                    current = frames[-1]
                    try:
                        with self._read_session(installation, current) as session:
                            for file in current['after_owned'].values():
                                check(); session.check()
                                snapshot = capture_target(installation.canonical_root, installation.file_identity,
                                    file['target_root_kind'], file['relative_path'])
                                if snapshot.before_hash != file['sha256']:
                                    raise PlanRejected('ROLLBACK_EXTERNAL_DRIFT')
                            # If the first issue-header write failed, there is
                            # no authenticated source selector to trust. Verify
                            # all protected historical backup references.
                            for frame in frames:
                                for operation in frame['plan'].operations:
                                    check(); session.check()
                                    key = target_key(operation.target_root_kind, operation.relative_path)
                                    expected = current['header']['after'].get(key)
                                    actual = capture_target(installation.canonical_root, installation.file_identity,
                                        operation.target_root_kind, operation.relative_path)
                                    if actual.before_hash != (expected['sha256'] if expected else None):
                                        raise PlanRejected('ROLLBACK_EXTERNAL_DRIFT')
                                    if operation.expected_before != 'absent':
                                        self.verify_backup(frame, operation, check)
                            for issue in issues:
                                for conflict in issue['header']['conflicts']:
                                    self._check_copy(conflict)
                        self.executor.recover(dict(installation_id=str(installation.installation_id)),
                                              check=check, resolve_history=True)
                        status = self.executor.status(dict(installation_id=str(installation.installation_id)))
                    except (ValueError, OSError, KeyError):
                        pass  # The real shared guard/DB block remains intact.
                return dict(resolved_count=0, remaining_count=int(status['recovery_required']),
                            recovery_required=status['recovery_required'], blockers=status['blockers'],
                            issues=[self._public(i) for i in issues], verification_status='not_run')
            if not frames:
                return dict(resolved_count=0, remaining_count=len(pending), recovery_required=True,
                            issues=[self._public(i) for i in issues], verification_status='not_run')
            current = frames[-1]
            by_transaction = {str(f['transaction'].transaction_id): f for f in frames}
            resolved = 0
            try:
                with self._read_session(installation, current) as session:
                    for file in current['after_owned'].values():
                        check(); session.check()
                        snapshot = capture_target(installation.canonical_root, installation.file_identity,
                                                  file['target_root_kind'], file['relative_path'])
                        if snapshot.before_hash != file['sha256']:
                            raise PlanRejected('ROLLBACK_EXTERNAL_DRIFT')
                    for issue in pending:
                        source = by_transaction[issue['header']['source_transaction_id']]
                        for operation in source['plan'].operations:
                            if operation.expected_before != 'absent':
                                self.verify_backup(source, operation, check)
                        for conflict in issue['header']['conflicts']:
                            check(); session.check(); self._check_copy(conflict)
                            key = target_key(conflict['target_root_kind'], conflict['relative_path'])
                            expected = current['header']['after'].get(key)
                            actual = capture_target(installation.canonical_root, installation.file_identity,
                                conflict['target_root_kind'], conflict['relative_path'])
                            if actual.before_hash != (expected['sha256'] if expected else None):
                                raise PlanRejected('ROLLBACK_EXTERNAL_DRIFT')
                    # Resolve only after every referenced source and promised copy
                    # has been checked. No game file is restored by this endpoint.
                    for issue in pending:
                        check(); session.check()
                        latest = issue['journal'].load()
                        if latest[0]['data'] != issue['original_header'] or len(latest) not in {1, 2, 3}:
                            raise PlanRejected('HISTORY_ISSUE_INVALID')
                        if latest[-1]['kind'] != 'resolved':
                            issue['journal'].append('resolved', dict(checked_at=datetime.now(timezone.utc).isoformat(),
                                current_transaction_id=str(current['transaction'].transaction_id)))
                            resolved += 1
                        issue['resolved'] = True
                recovery = self.executor.recover(dict(installation_id=str(installation.installation_id)),
                                                  check=check, resolve_history=True)
                if recovery['recovery_required']:
                    raise PlanRejected('GUARD_RECOVERY_REQUIRED')
            except (ValueError, OSError, KeyError):
                return dict(resolved_count=resolved, remaining_count=sum(not i['resolved'] for i in pending),
                    recovery_required=True, issues=[self._public(i) for i in issues], verification_status='not_run')
            return dict(resolved_count=resolved, remaining_count=0, recovery_required=False,
                        issues=[self._public(i) for i in issues], verification_status='not_run')
