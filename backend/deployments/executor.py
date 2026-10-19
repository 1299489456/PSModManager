"""Approved, handle-bound file transactions shared by desktop and AI consumers.

File publication, manifest publication and SQLite commit are separate durable
boundaries. Recovery interprets the authenticated journal, never a DB flag alone.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
from uuid import UUID, uuid4

from backend.contracts import ActionPlan, Transaction, DeploymentGeneration
from backend.contracts.plans import PlanApply, plan_digest
from backend.storage import Store
from backend.storage.package_repository import content_hash
from .paths import (GuardRejected, TargetSnapshot, ROOTS, pinned_chain, pinned_file,
                    open_handle, _information, file_identity, strong_hash, capture_target,
                    native_rename, quarantine_file, open_recovery_handle)
from .policy import PlanRejected, check_value, owned_after, target_key
from .journal import Journal, canonical, private_dir, read_private, write_private, copy_private


TERMINAL = {'committed', 'rolled_back', 'cancelled', 'failed'}
MAX_TRANSACTIONS = 200


class ExecutionRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Permit:
    """Host-only capability, never constructed from a bridge field."""
    def __init__(self, owner, installation, transaction_id):
        self.owner = owner
        self.installation = installation
        self.transaction_id = transaction_id
        self.journals = {}


class DeploymentExecutor:
    def __init__(self, paths, guards, plans, *, fault=lambda point, details: None):
        self.paths, self.guards, self.plans, self.fault = paths, guards, plans, fault
        self._lock = threading.RLock()
        self._permits = set()
        self.startup_recovery = dict(status='clear', recovered_count=0, pending_count=0)
        guards.recovery_check = self.probe

    @contextmanager
    def _store(self, *, verify_library_bytes=True):
        self.paths.check_owned_paths()
        store = Store(self.paths.database, library_root=self.paths.root / 'library', validate=False,
                      verify_library_bytes=verify_library_bytes)
        try:
            yield store
        finally:
            store.close()

    def _fault(self, point, transaction, operation=None):
        details = dict(transaction_id=str(transaction.transaction_id))
        if operation is not None:
            details['operation_id'] = str(operation.operation_id)
        if self.fault is not None:
            self.fault(point, details)

    def _save(self, transaction):
        with self._store() as store:
            old = store.connection.execute('SELECT payload FROM transactions WHERE transaction_id=?',
                (str(transaction.transaction_id),)).fetchone()
            if old:
                previous = Transaction.model_validate_json(old['payload'])
                if previous.model_dump(exclude={'state','last_sequence'}) != transaction.model_dump(exclude={'state','last_sequence'}):
                    raise ExecutionRejected('EXEC_TRANSACTION_INVALID')
                store.connection.execute('UPDATE transactions SET payload=? WHERE transaction_id=?',
                    (transaction.model_dump_json(), str(transaction.transaction_id)))
            else:
                store.put(transaction)
            store.connection.commit()

    def _record(self, journal, transaction, kind, data=None, state=None, *, checkpoint=True):
        record = journal.append(kind, data or {})
        transaction.last_sequence = record['sequence']
        if state:
            transaction.state = state
        # Workspace per-file records are already authenticated and flushed by
        # append(). SQLite records phase/terminal checkpoints; crash recovery
        # reconciles the latest durable sequence from that same journal.
        if checkpoint:
            self._save(transaction)
        return record

    def _transactions(self, store, installation_id):
        rows = store.connection.execute('''SELECT t.payload FROM transactions t JOIN action_plans p
            ON p.plan_id=t.plan_id WHERE p.installation_id=? ORDER BY t.rowid LIMIT ?''',
            (str(installation_id), MAX_TRANSACTIONS+1)).fetchall()
        if len(rows)>MAX_TRANSACTIONS:
            raise ExecutionRejected('EXEC_LIMIT')
        return [Transaction.model_validate_json(row['payload']) for row in rows]

    @staticmethod
    def _area(transaction):
        return 'deployments/transactions/'+str(transaction.transaction_id)

    @staticmethod
    def _slot(installation):
        return hashlib.sha256(installation.file_identity.encode('ascii')).hexdigest()

    def _current_path(self, installation):
        return 'deployments/installations/'+self._slot(installation)+'/current.json'

    def _marker_path(self, installation):
        return '.psmm-'+self._slot(installation)+'/active.json'

    def _target_area(self, installation, transaction):
        # A fixed sibling namespace is outside every loader scan root. Neither
        # the bridge nor a journal-provided absolute path can redirect writes.
        return '.psmm-'+self._slot(installation)+'/transactions/'+str(transaction.transaction_id)

    @contextmanager
    def _transaction_files(self, installation, transaction, header):
        """Pin the authenticated file area; old journals retain their data root."""
        storage = header.get('file_storage')
        if storage is None:
            yield self.paths.root, self._area(transaction)
            return
        if (not isinstance(storage, dict) or set(storage) != {'kind', 'ancestors'}
                or storage['kind'] != 'installation_sibling_v1'):
            raise ExecutionRejected('EXEC_JOURNAL_INVALID')
        base = Path(installation.canonical_root).parent
        area = self._target_area(installation, transaction)
        with private_dir(base, area) as chain:
            if (list(map(list, chain)) != storage['ancestors']
                    or chain[-1][1].split(':')[0] != installation.file_identity.split(':')[0]):
                raise GuardRejected('EXEC_STAGE_INVALID')
            yield base, area
            chain.check()

    def _marker(self, installation):
        try:
            value = json.loads(read_private(Path(installation.canonical_root).parent,
                self._marker_path(installation), max_bytes=4096))
            if (not isinstance(value,dict) or set(value)!={'transaction_id','installation_identity','data_identity'}
                    or not all(isinstance(v,str) for v in value.values())):
                raise ValueError()
            UUID(value['transaction_id'])
            return value
        except FileNotFoundError:
            return None
        except (ValueError, OSError):
            raise GuardRejected('GUARD_RECOVERY_SOURCE_UNKNOWN') from None

    def _mark(self, installation, transaction):
        with pinned_chain(self.paths.root) as data:
            value = dict(transaction_id=str(transaction.transaction_id), installation_identity=installation.file_identity,
                         data_identity=data[-1][1])
        write_private(Path(installation.canonical_root).parent, self._marker_path(installation), canonical(value))

    def _unmark(self, installation, transaction):
        import win32file
        value = self._marker(installation)
        if value is None:
            return
        if value['transaction_id'] != str(transaction.transaction_id):
            raise GuardRejected('GUARD_RECOVERY_SOURCE_UNKNOWN')
        base = Path(installation.canonical_root).parent
        relative = self._marker_path(installation)
        with private_dir(base, Path(relative).parent.as_posix()) as chain:
            handle = open_handle(base/relative, parent=chain.handles[-1], delete=True)
            try:
                _, encoded = win32file.ReadFile(handle,4096)
                if json.loads(encoded)!=value:
                    raise GuardRejected('GUARD_RECOVERY_SOURCE_UNKNOWN')
                # Delete only this authenticated marker object through its handle.
                win32file.SetFileInformationByHandle(handle, 4, True)
                chain.check()
            finally:
                handle.Close()

    def _permit(self, installation, transaction):
        permit = _Permit(self, str(installation.installation_id), str(transaction.transaction_id))
        self._permits.add(permit)
        return permit

    def _header(self, journal, transaction, plan):
        records = journal.load()
        if not records or records[0]['kind'] != 'header':
            raise ExecutionRejected('EXEC_JOURNAL_INVALID')
        header = records[0]['data']
        if (header['transaction_id'] != str(transaction.transaction_id) or header['plan_id'] != str(plan.plan_id)
                or header['plan_hash'] != plan.plan_hash or header['target_generation'] != str(transaction.target_generation)
                or transaction.journal_path != journal.relative_path or plan_digest(plan) != plan.plan_hash):
            raise ExecutionRejected('EXEC_JOURNAL_INVALID')
        with pinned_chain(self.paths.root) as data:
            if header['data_identity'] != data[-1][1]:
                raise ExecutionRejected('EXEC_JOURNAL_INVALID')
        with self._store() as store:
            self.plans._approval(plan, store.get('Approval', header['approval_id']), historical=True)
        return records, header

    def probe(self, installation, permit=None):
        """All apply/rollback/launch checks reject foreign or unresolved state."""
        trusted = (isinstance(permit,_Permit) and permit in self._permits and permit.owner is self
                   and permit.installation == str(installation.installation_id))
        marker = self._marker(installation)
        if marker is not None:
            with pinned_chain(self.paths.root) as data:
                if (marker.get('installation_identity') != installation.file_identity
                        or marker.get('data_identity') != data[-1][1]):
                    raise GuardRejected('GUARD_RECOVERY_SOURCE_UNKNOWN')
        try:
            with self._store() as store:
                transactions = self._transactions(store, installation.installation_id)
                indexed = {str(t.transaction_id):t for t in transactions}
                if marker and marker.get('transaction_id') not in indexed:
                    raise ExecutionRejected('EXEC_JOURNAL_INVALID')
                for transaction in transactions:
                    own = trusted and permit.transaction_id == str(transaction.transaction_id)
                    if own:
                        # The live executor owns this journal and append() still
                        # verifies its authenticated prefix on every write.
                        # Re-reading its growing history at every pinned-file
                        # check made a multi-file install quadratic in I/O.
                        continue
                    try:
                        identity = str(transaction.transaction_id)
                        if trusted:
                            journal = permit.journals.get(identity)
                            if journal is None:
                                journal = permit.journals[identity] = Journal(self.paths.root, transaction.transaction_id)
                        else:
                            journal = Journal(self.paths.root, transaction.transaction_id)
                        records, header = self._header(journal, transaction, store.get('ActionPlan', transaction.plan_id))
                        if header['installation_identity'] != installation.file_identity:
                            raise ExecutionRejected('EXEC_JOURNAL_INVALID')
                        last = records[-1]
                        authoritative = next((r['kind'] for r in reversed(records) if r['kind'] in TERMINAL),None)
                        if not own and (transaction.state not in TERMINAL or last['kind'] not in TERMINAL | {'cleanup'}):
                            raise ExecutionRejected('EXEC_RECOVERY_REQUIRED')
                        if not own and authoritative!=transaction.state:
                            raise ExecutionRejected('EXEC_RECOVERY_REQUIRED')
                        if not own and transaction.last_sequence != last['sequence']:
                            raise ExecutionRejected('EXEC_RECOVERY_REQUIRED')
                    except FileNotFoundError:
                        # Only our live, approved, pre-header transaction may proceed.
                        if not ((own and transaction.state == 'approved' and transaction.last_sequence == 0)
                                or transaction.state in {'cancelled','failed'} and transaction.last_sequence==0):
                            raise ExecutionRejected('EXEC_RECOVERY_REQUIRED') from None
                try:
                    with private_dir(self.paths.root, 'deployments/journals') as directory:
                        entries = list((self.paths.root/'deployments/journals').iterdir())
                        if len(entries)>MAX_TRANSACTIONS:
                            raise ExecutionRejected('EXEC_LIMIT')
                        if any(p.name != str(UUID(p.stem))+'.jsonl' or p.stem not in indexed for p in entries):
                            raise ExecutionRejected('EXEC_JOURNAL_INVALID')
                        directory.check()
                except FileNotFoundError:
                    pass
                if marker and not trusted:
                    transaction = indexed[marker['transaction_id']]
                    if transaction.state not in TERMINAL:
                        raise ExecutionRejected('EXEC_RECOVERY_REQUIRED')
        except (ValueError, KeyError, OSError) as exc:
            raise GuardRejected('GUARD_RECOVERY_REQUIRED') from exc

    def status(self, payload):
        installation_id = str(payload['installation_id'])
        with self._store() as store:
            installation = store.get('Installation', installation_id)
            transactions = self._transactions(store, installation_id)
            if payload.get('plan_id'):
                transactions = [t for t in transactions if str(t.plan_id)==str(payload['plan_id'])]
            transaction = transactions[-1] if transactions else None
            pointer = store.connection.execute('SELECT generation_id FROM deployment_current WHERE installation_id=?',
                (installation_id,)).fetchone()
        guard = self.guards.status(installation_id)
        blockers = [dict(code=code, message=code) for code in guard['blockers']]
        return dict(installation_id=installation_id, transaction=transaction.model_dump(mode='json') if transaction else None,
            current_generation=pointer['generation_id'] if pointer else None,
            recovery_required=any('RECOVERY' in b['code'] for b in blockers), blockers=blockers,
            verification_status='not_run', execution_available=not blockers, startup_recovery=dict(self.startup_recovery))

    @staticmethod
    def _snapshot(data):
        return TargetSnapshot(**dict(data, ancestors=tuple(tuple(a) for a in data['ancestors'])))

    def _disk(self, installation, check=lambda: None, *, plan=None):
        scope = self.plans.scan_scope(plan) if plan is not None else None
        return self.plans._scan(Path(installation.canonical_root), installation.file_identity, check, scope=scope)[0]

    def _new_header(self, store, plan, approval, installation, transaction, check):
        before, snapshots = self.plans._scan(Path(installation.canonical_root), installation.file_identity, check,
            scope=self.plans.scan_scope(plan))
        after = {key:dict(file) for key,file in before.items()}
        for operation in plan.operations:
            key = target_key(operation.target_root_kind,operation.relative_path)
            if operation.desired_after == 'absent':
                after.pop(key, None)
            else:
                file = owned_after(plan)[key]
                after[key] = {k:file[k] for k in ('target_root_kind','relative_path','sha256','size')}
            if key not in snapshots:
                snapshots[key] = capture_target(installation.canonical_root, installation.file_identity,
                    operation.target_root_kind,operation.relative_path)
            if snapshots[key].before_hash != (None if operation.expected_before=='absent' else operation.expected_before):
                raise PlanRejected('PLAN_DISK_CHANGED')
        sealed = content_hash([dict(root_kind=s.root_kind,relative_path=s.relative_path,ancestors=list(s.ancestors),
            target_identity=s.target_identity,before_hash=s.before_hash) for _,s in sorted(snapshots.items())])
        baseline = store.get('BaselineSnapshot',plan.baseline_snapshot_id)
        if (sealed!=check_value(plan,'disk_snapshot') or
                before!=self.plans._baseline_files(baseline,plan.installation_id,plan.baseline_manifest_hash)):
            raise PlanRejected('PLAN_DISK_CHANGED')
        pointer = store.connection.execute('SELECT * FROM deployment_current WHERE installation_id=?',
            (str(installation.installation_id),)).fetchone()
        try:
            old_manifest = json.loads(read_private(self.paths.root,self._current_path(installation)))
        except FileNotFoundError:
            old_manifest = None
        if (pointer is None) != (old_manifest is None) or (pointer and
                (pointer['generation_id'] != old_manifest['generation_id'] or
                 pointer['manifest_file_hash'] != hashlib.sha256(canonical(old_manifest)).hexdigest())):
            raise ExecutionRejected('EXEC_CURRENT_MISMATCH')
        with pinned_chain(self.paths.root) as data:
            header = dict(transaction_id=str(transaction.transaction_id),plan_id=str(plan.plan_id),plan_hash=plan.plan_hash,
                approval_id=str(approval.approval_id),target_generation=str(transaction.target_generation),
                installation_identity=installation.file_identity,data_identity=data[-1][1], before=before,after=after,
                snapshots={target_key(op.target_root_kind,op.relative_path):asdict(snapshots[target_key(op.target_root_kind,op.relative_path)])
                           for op in plan.operations},old_manifest=old_manifest,old_pointer=dict(pointer) if pointer else None)
            if data[-1][1].split(':')[0] != installation.file_identity.split(':')[0]:
                with private_dir(Path(installation.canonical_root).parent,
                                 self._target_area(installation, transaction), create=True) as files:
                    if files[-1][1].split(':')[0] != installation.file_identity.split(':')[0]:
                        raise PlanRejected('PLAN_STAGING_UNAVAILABLE')
                    header['file_storage'] = dict(kind='installation_sibling_v1', ancestors=list(map(list, files)))
            return header

    def apply(self, payload, *, check=lambda: None, commit=None, progress=lambda *args: None):
        payload = PlanApply.model_validate(payload)
        transaction = journal = permit = None
        with self._lock:
            try:
                with self._store() as store:
                    plan = store.get('ActionPlan',payload.plan_id)
                    if plan.plan_hash != payload.plan_hash or plan_digest(plan) != plan.plan_hash:
                        raise PlanRejected('PLAN_HASH_MISMATCH')
                    old = [t for t in self._transactions(store,plan.installation_id) if t.plan_id==plan.plan_id]
                    if old:
                        receipt = old[-1]
                        records, _ = self._header(Journal(self.paths.root,receipt.transaction_id),receipt,plan)
                        if receipt.state=='committed' and records[-1]['kind'] in {'committed','cleanup'}:
                            self.plans._approval(plan,store.get('Approval',payload.approval_id),historical=True)
                            return dict(transaction=receipt.model_dump(mode='json'),idempotent=True,no_op=False,verification_status='not_run')
                        raise ExecutionRejected('EXEC_ALREADY_ATTEMPTED')
                rollback = any(p.kind == 'rollback_source' for p in plan.preconditions)
                if rollback:
                    # Drift must be preserved before the ordinary recovery
                    # guard refuses execution. This grants no write capability.
                    with self.plans._store(check) as store:
                        self.plans._revalidate(store,plan,check)
                with self.guards.operation(str(plan.installation_id),'rollback' if rollback else 'apply') as session:
                    with self.plans._store(check) as store:
                        self.plans._revalidate(store,plan,check,session=session)
                        if not plan.operations:
                            self.plans.commit_direct_state(store,plan)
                            if commit:
                                commit(store.connection.commit)
                            return dict(no_op=True,idempotent=False,transaction=None,verification_status='not_run')
                        try:
                            approval = store.get('Approval',payload.approval_id)
                        except (KeyError, ValueError, TypeError):
                            raise PlanRejected('PLAN_APPROVAL_INVALID') from None
                        self.plans._approval(plan,approval)
                        transaction = Transaction(transaction_id=uuid4(),plan_id=plan.plan_id,state='approved',
                            journal_path='deployments/journals/pending.jsonl',target_generation=uuid4(),last_sequence=0)
                        journal = Journal(self.paths.root,transaction.transaction_id)
                        transaction.journal_path = journal.relative_path
                        header = self._new_header(store,plan,approval,session.installation,transaction,check)
                        # Extra conservative budget covers bounded journal, manifests and DB metadata.
                        with pinned_chain(self.paths.root) as data:
                            locations = {session.installation.file_identity.split(':')[0]: Path(session.installation.canonical_root),
                                         data[-1][1].split(':')[0]: self.paths.root}
                            for volume, location in locations.items():
                                required = plan.disk_estimate_by_volume.get(volume, 0)+4*1024*1024
                                if shutil.disk_usage(location).free < required:
                                    raise PlanRejected('PLAN_SPACE_INSUFFICIENT')
                        store.put(transaction)
                    self._fault('transaction.created',transaction)
                    permit = self._permit(session.installation,transaction)
                    session.permit = permit
                    self._mark(session.installation,transaction)
                    journal.create(header)
                    transaction.last_sequence = 1
                    self._save(transaction)
                    self._fault('journal.header',transaction)
                    self._record(journal,transaction,'staging',state='staging')
                    self._stage(plan,transaction,journal,header,session,check,progress)
                    self._record(journal,transaction,'ready',state='ready')
                    self._fault('transaction.ready',transaction)
                    check(); session.check()
                    # Staging can take time. Recheck profile/compatibility/library,
                    # approval, complete disk identities and free space at the
                    # last boundary before any target name changes.
                    with self.plans._store(check) as store:
                        self.plans._revalidate(store,plan,check,session=session)
                        self.plans._approval(plan,store.get('Approval',payload.approval_id))
                    self._record(journal,transaction,'applying',state='applying')
                    self._fault('transaction.applying',transaction)
                    self._execute(plan,transaction,journal,header,session,check,progress)
                    check(); session.check()
                    if self._disk(session.installation,check,plan=plan) != header['after']:
                        raise ExecutionRejected('EXEC_FINAL_MISMATCH')
                    self._verify_published(plan,journal,session.installation)
                    self._record(journal,transaction,'targets_verified')
                    self._fault('targets.verified',transaction)
                    return self._finish(plan,transaction,journal,header,session,check,commit)
            except BaseException as exc:
                from backend.runtime.jobs import _Cancelled
                if transaction is not None:
                    try:
                        self._recover_transaction(transaction, allow_forward=not isinstance(exc,_Cancelled))
                    except BaseException:
                        transaction.state = 'recovery_required'
                        self._save(transaction)
                # Cancellation keeps the real JobManager terminal; status is read from DB.
                if isinstance(exc,_Cancelled):
                    raise
                if isinstance(exc,(ValueError,OSError,KeyError)):
                    if transaction is not None and transaction.state=='committed':
                        return dict(transaction=transaction.model_dump(mode='json'),deployed=True,idempotent=False,
                            no_op=False,cleanup_pending=True,verification_status='not_run')
                    native = getattr(exc,'native_error',None) or getattr(exc,'winerror',None)
                    number = getattr(exc,'errno',None)
                    reason = 'disk_full' if native==112 or number==28 else 'file_busy' if native in {32,33} else 'permission_denied' if native==5 or number==13 else 'io_or_guard'
                    return dict(error_code=getattr(exc,'code','EXEC_IO_FAILED'),message=str(exc)[:300],
                        transaction=transaction.model_dump(mode='json') if transaction else None,verification_status='not_run',
                        io_error=dict(kind=reason,native_error=native,errno=number,attempts=getattr(exc,'attempts',1)))
                raise
            finally:
                if permit:
                    self._permits.discard(permit)

    def prepare_mod_directories(self, files, session, check):
        """Create only missing target parents for an ordinary install/enable.

        Existing guard snapshots require a real parent. Directory creation uses
        the same pinned root/relative handles and installation operation; it
        never follows links or removes pre-existing directories. Empty new
        directories may remain if later planning fails.
        """
        from contextlib import ExitStack
        from .paths import target_path, open_handle, _information
        root = Path(session.installation.canonical_root)
        parents = sorted({target_path(root, f['target_root_kind'], f['relative_path']).parent for f in files})
        for parent in parents:
            check(); session.check()
            with pinned_chain(root) as root_chain, ExitStack() as stack:
                if root_chain[-1][1] != session.installation.file_identity:
                    raise GuardRejected('GUARD_INSTALLATION_CHANGED')
                handles = list(root_chain.handles)
                path = root
                for name in parent.relative_to(root).parts:
                    root_chain.check()
                    for handle in handles:
                        _information(handle, True)
                    path = path/name
                    try:
                        handle = open_handle(path, directory=True, parent=handles[-1])
                    except FileNotFoundError:
                        check(); session.check()
                        handle = open_handle(path, directory=True, create=True, parent=handles[-1])
                    stack.callback(handle.Close)
                    handles.append(handle)
                for handle in handles:
                    _information(handle, True)
                root_chain.check(); session.check(); check()

    def _stage(self, plan, transaction, journal, header, session, check, progress):
        with self._transaction_files(session.installation, transaction, header) as (base, area):
            self._stage_files(plan, transaction, journal, header, session, check, progress, base, area)

    def _stage_files(self, plan, transaction, journal, header, session, check, progress, base, area):
        binding = self.plans.direct_binding(plan)
        checkpoint = (binding is None or 'enabled_ids' not in binding) and len(plan.operations) <= 128
        for folder in ('backup','stage','displaced','conflicts','returned'):
            with private_dir(base,area+'/'+folder,create=True) as chain:
                if chain[-1][1].split(':')[0] != session.installation.file_identity.split(':')[0]:
                    raise PlanRejected('PLAN_STAGING_UNAVAILABLE')
        for index,operation in enumerate(plan.operations):
            # Staging publishes only private copies. Rechecking the entire
            # installation/process set for every resource made small packages
            # slow; the shared guard runs again before target publication.
            check()
            progress(index,len(plan.operations),'staging','DEPLOYMENT_STAGING')
            key = target_key(operation.target_root_kind,operation.relative_path)
            snapshot = self._snapshot(header['snapshots'][key])
            stem = str(operation.operation_id)+'.bin'
            if snapshot.before_hash is not None:
                with session.step(snapshot) as target:
                    identity = copy_private(base,area+'/backup/'+stem,target.handle,snapshot.before_hash,
                        fault=lambda point: self._fault('backup.'+point,transaction,operation))
                self._record(journal,transaction,'backup',dict(operation_id=str(operation.operation_id),identity=identity,sha256=snapshot.before_hash), checkpoint=checkpoint)
                self._fault('backup.verified',transaction,operation)
            if operation.desired_after != 'absent':
                if any(p.kind == 'rollback_source' for p in plan.preconditions):
                    source_context = self.plans.rollback.open_source(plan,operation)
                else:
                    with self._store() as store:
                        artifact = store.get('Artifact',operation.desired_after)
                    source_context = pinned_file(self.paths.root/'library'/artifact.library_relative_path)
                with source_context as source:
                    identity = copy_private(base,area+'/stage/'+stem,source,operation.desired_after,
                        fault=lambda point: self._fault('stage.'+point,transaction,operation))
                self._record(journal,transaction,'stage',dict(operation_id=str(operation.operation_id),identity=identity,sha256=operation.desired_after), checkpoint=checkpoint)
                self._fault('stage.verified',transaction,operation)

    def _execute(self, plan, transaction, journal, header, session, check, progress):
        with self._transaction_files(session.installation, transaction, header) as (base, area):
            self._execute_files(plan, transaction, journal, header, session, check, progress, base, area)

    def _execute_files(self, plan, transaction, journal, header, session, check, progress, base, area):
        binding = self.plans.direct_binding(plan)
        checkpoint = (binding is None or 'enabled_ids' not in binding) and len(plan.operations) <= 128
        for index,operation in enumerate(plan.operations):
            check()
            progress(len(plan.operations)+index,2*len(plan.operations),'applying','DEPLOYMENT_APPLYING')
            key = target_key(operation.target_root_kind,operation.relative_path)
            snapshot = self._snapshot(header['snapshots'][key])
            stem = str(operation.operation_id)+'.bin'
            self._record(journal,transaction,'intent',dict(operation_id=str(operation.operation_id)), checkpoint=checkpoint)
            self._fault('operation.intent',transaction,operation)
            with session.step(snapshot,delete=True) as target:
                if target.handle is not None:
                    with private_dir(base,area+'/displaced') as displaced:
                        target.rename_to(displaced.handles[-1],stem)
                        displaced.check()
                    self._record(journal,transaction,'evacuated',dict(operation_id=str(operation.operation_id)), checkpoint=checkpoint)
                    self._fault('operation.evacuated',transaction,operation)
                if operation.desired_after != 'absent':
                    with private_dir(base,area+'/stage') as stage:
                        source = open_handle(base/area/'stage'/stem,parent=stage.handles[-1],delete=True)
                        identity = file_identity(_information(source,False))
                        try:
                            staged = [r['data'] for r in journal.load() if r['kind']=='stage'
                                      and r['data']['operation_id']==str(operation.operation_id)]
                            if (len(staged)!=1 or identity!=staged[0]['identity'] or
                                    strong_hash(source)!=operation.desired_after):
                                raise ExecutionRejected('EXEC_STAGE_INVALID')
                            try:
                                native_rename(source,target.parent_handle,target.leaf)
                            except GuardRejected as exc:
                                if exc.code in {'GUARD_REPARSE_POINT','GUARD_HARDLINK'}:
                                    with private_dir(base,area+'/conflicts') as conflicts:
                                        quarantine_file(source,conflicts.handles[-1],str(uuid4())+'.bin',identity)
                                raise
                        finally:
                            source.Close()
                    self._record(journal,transaction,'published',dict(operation_id=str(operation.operation_id),identity=identity), checkpoint=checkpoint)
                    self._fault('operation.published',transaction,operation)
            current = capture_target(session.installation.canonical_root,session.installation.file_identity,
                operation.target_root_kind,operation.relative_path)
            if (current.before_hash != (None if operation.desired_after=='absent' else operation.desired_after)
                    or operation.desired_after!='absent' and current.target_identity!=identity):
                raise ExecutionRejected('EXEC_TARGET_MISMATCH')
            self._fault('operation.verified',transaction,operation)
            self._record(journal,transaction,'done',dict(operation_id=str(operation.operation_id)), checkpoint=checkpoint)
            self._fault('operation.done',transaction,operation)

    def _verify_published(self, plan, journal, installation):
        staged = {r['data']['operation_id']:r['data'] for r in journal.load() if r['kind']=='stage'}
        for operation in plan.operations:
            current = capture_target(installation.canonical_root,installation.file_identity,
                operation.target_root_kind,operation.relative_path)
            expected = None if operation.desired_after=='absent' else staged[str(operation.operation_id)]['identity']
            if current.target_identity!=expected or current.before_hash!=(None if expected is None else operation.desired_after):
                raise ExecutionRejected('EXEC_FINAL_MISMATCH')

    def _manifest(self, plan, transaction, header):
        previous = header['old_manifest']
        return dict(generation_id=str(transaction.target_generation),installation_id=str(plan.installation_id),
            sequence=previous['sequence']+1 if previous else 1,transaction_id=str(transaction.transaction_id),
            plan_hash=plan.plan_hash,manifest_hash=check_value(plan,'result_manifest'),owned_files=list(owned_after(plan).values()),
            files=header['after'],committed_at=datetime.now(timezone.utc).isoformat())

    def _publish_manifest(self, installation, transaction, manifest):
        relative = self._current_path(installation)
        area = self._area(transaction)
        encoded = canonical(manifest)
        try:
            existing = read_private(self.paths.root,relative)
            if existing==encoded:
                return
            with private_dir(self.paths.root,Path(relative).parent.as_posix()) as current:
                old = open_handle(self.paths.root/relative,parent=current.handles[-1],delete=True)
                try:
                    with private_dir(self.paths.root,area) as displaced:
                        native_rename(old,displaced.handles[-1],'previous-manifest.bin')
                finally:
                    old.Close()
        except FileNotFoundError:
            pass
        prepared = area+'/manifest.bin'
        with private_dir(self.paths.root,area) as stage:
            source = open_handle(self.paths.root/prepared,parent=stage.handles[-1],delete=True)
            try:
                if strong_hash(source) != hashlib.sha256(encoded).hexdigest():
                    raise ExecutionRejected('EXEC_CURRENT_MISMATCH')
                with private_dir(self.paths.root,Path(relative).parent.as_posix(),create=True) as current:
                    native_rename(source,current.handles[-1],Path(relative).name)
            finally:
                source.Close()

    def _commit_db(self, plan, transaction, manifest, commit=None):
        generation = DeploymentGeneration(generation_id=transaction.target_generation,installation_id=plan.installation_id,
            sequence=manifest['sequence'],manifest_hash=manifest['manifest_hash'],transaction_id=transaction.transaction_id,
            committed_at=manifest['committed_at'])
        is_ordinary = any(p.kind == 'execution_policy' and p.expected == 'ordinary-mods-v1' for p in plan.preconditions)
        with self._store(verify_library_bytes=not is_ordinary) as store:
            store.connection.execute('BEGIN IMMEDIATE')
            try:
                transaction.state='committed'
                store.connection.execute('UPDATE transactions SET payload=? WHERE transaction_id=?',
                    (transaction.model_dump_json(),str(transaction.transaction_id)))
                store.put(generation)
                store.connection.execute('''INSERT INTO deployment_current VALUES(?,?,?,?)
                    ON CONFLICT(installation_id) DO UPDATE SET generation_id=excluded.generation_id,
                    manifest_hash=excluded.manifest_hash,manifest_file_hash=excluded.manifest_file_hash''',
                    (str(plan.installation_id),str(generation.generation_id),generation.manifest_hash,hashlib.sha256(canonical(manifest)).hexdigest()))
                self.plans.commit_direct_state(store,plan)
                if commit:
                    commit(store.connection.commit)
                else:
                    store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise

    def _finish(self, plan, transaction, journal, header, session, check, commit):
        manifest = self._manifest(plan,transaction,header)
        if datetime.fromisoformat(manifest['committed_at'])>=plan.expires_at:
            raise PlanRejected('PLAN_EXPIRED')
        write_private(self.paths.root,self._area(transaction)+'/manifest.bin',canonical(manifest))
        self._fault('manifest.prepared',transaction)
        check(); session.check()
        self._record(journal,transaction,'commit_intent',dict(manifest=manifest))
        self._fault('commit.intent',transaction)
        self._publish_manifest(session.installation,transaction,manifest)
        self._fault('manifest.published',transaction)
        session.check(); check()
        self._fault('db.before_commit',transaction)
        self._commit_db(plan,transaction,manifest,commit)
        self._fault('db.committed',transaction)
        self._record(journal,transaction,'committed',state='committed')
        self._fault('journal.committed',transaction)
        self._cleanup(session.installation,transaction,journal)
        return dict(transaction=transaction.model_dump(mode='json'),no_op=False,idempotent=False,verification_status='not_run',deployed=True)

    def _cleanup(self, installation, transaction, journal):
        try:
            self._fault('cleanup.before',transaction)
            # Verified backup/displaced originals are retained for future rollback.
            self._unmark(installation,transaction)
            self._fault('cleanup.after',transaction)
            self._record(journal,transaction,'cleanup',dict(status='complete'))
        except (OSError, ValueError):
            # A committed generation stays committed. Cleanup is independently retryable.
            self._record(journal,transaction,'cleanup',dict(status='pending'))

    def _recover_transaction(self, transaction, *, allow_forward=True):
        from .recovery import recover_transaction
        return recover_transaction(self,transaction,allow_forward=allow_forward)

    def recover(self, payload, *, check=lambda: None, progress=lambda *args: None, resolve_history=False):
        with self._lock:
            try:
                self._discover_orphans(str(payload['installation_id']))
            except (ValueError,OSError,KeyError):
                return dict(recovered_count=0,pending_count=1,recovery_required=True,verification_status='not_run')
            with self._store() as store:
                transactions = self._transactions(store,str(payload['installation_id']))
            # An unfinished newer generation temporarily changes the old
            # generation's files. Reconcile authenticated terminal receipts in
            # metadata first, then recover pending writes before checking current.
            history_blocks = set()
            for transaction in transactions:
                try:
                    with self._store() as store:
                        plan = store.get('ActionPlan',transaction.plan_id)
                        generations = [g for g in store.list('DeploymentGeneration') if g.transaction_id==transaction.transaction_id]
                    records,_ = self._header(Journal(self.paths.root,transaction.transaction_id),transaction,plan)
                    state = next((r['kind'] for r in reversed(records) if r['kind'] in TERMINAL),None)
                    if transaction.state == 'recovery_required' and state == 'committed' and not resolve_history:
                        # A rejected historical undo can fail before its issue
                        # journal is created. Only the host-owned full history
                        # recheck may clear this conservative durable DB block.
                        history_blocks.add(transaction.transaction_id)
                        continue
                    if (state=='committed' and len(generations)==1 and generations[0].generation_id==transaction.target_generation
                            or state in {'rolled_back','cancelled','failed'} and not generations):
                        transaction.state=state; transaction.last_sequence=records[-1]['sequence']; self._save(transaction)
                except (ValueError,OSError,KeyError):
                    pass
            transactions.sort(key=lambda t:t.state in TERMINAL)
        recovered = pending = 0
        for transaction in transactions:
            check()
            if transaction.transaction_id in history_blocks:
                pending += 1
                continue
            try:
                if self._recover_transaction(transaction):
                    recovered += 1
            except (ValueError,OSError,KeyError):
                pending += 1
                transaction.state='recovery_required'
                self._save(transaction)
        try:
            with self.guards._installation(str(payload['installation_id'])) as (installation,_):
                self.guards._check_recovery(installation)
        except (ValueError,OSError,KeyError):
            pending = max(1,pending)
        return dict(recovered_count=recovered,pending_count=pending,recovery_required=bool(pending),verification_status='not_run')

    def _discover_orphans(self, installation_id):
        try:
            with private_dir(self.paths.root,'deployments/journals') as chain:
                entries = list((self.paths.root/'deployments/journals').iterdir())
                if len(entries)>MAX_TRANSACTIONS:
                    raise ExecutionRejected('EXEC_LIMIT')
                for path in entries:
                    transaction_id = UUID(path.stem)
                    if path.name!=str(transaction_id)+'.jsonl':
                        raise ExecutionRejected('EXEC_JOURNAL_INVALID')
                    with self._store() as store:
                        try:
                            store.get('Transaction',transaction_id)
                            continue
                        except KeyError:
                            pass
                        journal = Journal(self.paths.root,transaction_id)
                        records = journal.load(repair_tail=True)
                        header = records[0]['data']
                        plan = store.get('ActionPlan',header['plan_id'])
                        if str(plan.installation_id)!=installation_id:
                            continue
                        transaction = Transaction(transaction_id=transaction_id,plan_id=plan.plan_id,state='applying',
                            journal_path=journal.relative_path,target_generation=header['target_generation'],last_sequence=records[-1]['sequence'])
                    self._header(journal,transaction,plan)
                    self._save(transaction)
                chain.check()
        except FileNotFoundError:
            return

    def startup(self):
        with self._store() as store:
            installations = store.list('Installation')
        recovered = pending = 0
        for installation in installations:
            result = self.recover(dict(installation_id=str(installation.installation_id)))
            recovered += result['recovered_count']; pending += result['pending_count']
            try:
                self.guards._check_recovery(installation)
            except GuardRejected:
                pending += int(not result['pending_count'])
        self.startup_recovery = dict(status='blocked' if pending else 'recovered' if recovered else 'clear',
            recovered_count=recovered,pending_count=pending)
        return self.startup_recovery
