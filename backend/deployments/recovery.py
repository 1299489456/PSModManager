"""Restart reconciliation for the same authenticated executor and shared guards."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from backend.contracts import Transaction
from .journal import Journal, canonical, private_dir, read_private, write_private, copy_private
from .paths import (capture_target, pinned_chain, open_handle, native_rename, strong_hash,
                    file_identity, _information, GuardRejected, ROOTS)
from .policy import target_key, check_value


def _error(code):
    from .executor import ExecutionRejected
    raise ExecutionRejected(code)


def _restore_manifest(executor, installation, transaction, header, records):
    relative = executor._current_path(installation)
    try:
        current = json.loads(read_private(executor.paths.root,relative))
    except FileNotFoundError:
        current = None
    old = header['old_manifest']
    intended = next((r['data']['manifest'] for r in records if r['kind']=='commit_intent'),None)
    if current == old:
        return
    if current is not None and current != intended:
        _error('EXEC_CURRENT_MISMATCH')
    area = executor._area(transaction)
    if current is not None:
        with private_dir(executor.paths.root,Path(relative).parent.as_posix()) as destination:
            handle = open_handle(executor.paths.root/relative,parent=destination.handles[-1],delete=True)
            try:
                with private_dir(executor.paths.root,area,create=True) as private:
                    native_rename(handle,private.handles[-1],str(uuid4())+'.manifest.bin')
            finally:
                handle.Close()
    if old is not None:
        temporary = area+'/'+str(uuid4())+'.restore-manifest.bin'
        write_private(executor.paths.root,temporary,canonical(old))
        with private_dir(executor.paths.root,area) as private:
            handle = open_handle(executor.paths.root/temporary,parent=private.handles[-1],delete=True)
            try:
                with private_dir(executor.paths.root,Path(relative).parent.as_posix(),create=True) as destination:
                    native_rename(handle,destination.handles[-1],Path(relative).name)
            finally:
                handle.Close()


def _rollback(executor, installation, plan, transaction, journal, header, records, session):
    with executor._transaction_files(installation, transaction, header) as (base, area):
        _rollback_files(executor, installation, plan, transaction, journal, header, records, session, base, area)


def _rollback_files(executor, installation, plan, transaction, journal, header, records, session, base, area):
    operations = {str(op.operation_id):op for op in plan.operations}
    intent = {r['data']['operation_id'] for r in records if r['kind']=='intent'}
    stages = {r['data']['operation_id']:r['data'] for r in records if r['kind']=='stage'}
    backups = {r['data']['operation_id']:r['data'] for r in records if r['kind']=='backup'}
    restored = {r['data']['operation_id']:r['data'] for r in records if r['kind']=='restore_prepared'}
    _isolate_known_anomalies(executor,installation,transaction,journal,header,operations,intent,stages,session,base,area)
    disk = executor._disk(installation,plan=plan)
    changed = {target_key(operations[identifier].target_root_kind,operations[identifier].relative_path) for identifier in intent}
    if {k:v for k,v in disk.items() if k not in changed} != {k:v for k,v in header['before'].items() if k not in changed}:
        _error('EXEC_EXTERNAL_CONFLICT')
    # Observe the entire affected set before changing any file. A conflict leaves
    # current bytes in place and preserves a private, verified conflict copy.
    pending = []
    for identifier in reversed(list(operations)):
        if identifier not in intent:
            continue
        operation = operations[identifier]
        key = target_key(operation.target_root_kind,operation.relative_path)
        original = executor._snapshot(header['snapshots'][key])
        current = capture_target(installation.canonical_root,installation.file_identity,
            operation.target_root_kind,operation.relative_path)
        old_identity = original.target_identity
        same_old = (current.before_hash==original.before_hash and
            (current.target_identity==old_identity or
             identifier in restored and current.target_identity==restored[identifier]['identity']))
        if same_old:
            continue
        own_new = (operation.desired_after!='absent' and identifier in stages and
            current.before_hash==operation.desired_after and current.target_identity==stages[identifier]['identity'])
        if current.target_identity is not None and not own_new:
            with session.step(current) as target:
                copy_private(base,area+'/conflicts/'+str(uuid4())+'.bin',target.handle,current.before_hash)
            executor._record(journal,transaction,'conflict',dict(operation_id=identifier),state='recovery_required')
            _error('EXEC_EXTERNAL_CONFLICT')
        if original.before_hash is not None:
            backup = backups.get(identifier)
            if backup is None or backup['sha256']!=original.before_hash:
                _error('EXEC_BACKUP_INVALID')
            with private_dir(base,area+'/backup') as directory:
                source = open_handle(base/area/'backup'/(identifier+'.bin'),parent=directory.handles[-1])
                try:
                    if (file_identity(_information(source,False))!=backup['identity'] or strong_hash(source)!=original.before_hash):
                        _error('EXEC_BACKUP_INVALID')
                finally:
                    source.Close()
        pending.append((identifier,operation,original,current))
    executor._record(journal,transaction,'rolling_back',state='rolling_back')
    deadline = __import__('time').monotonic()+120
    for identifier,operation,original,current in pending:
        if __import__('time').monotonic()>deadline:
            _error('EXEC_RECOVERY_TIMEOUT')
        session.check()
        executor._fault('recovery.before_restore',transaction,operation)
        restore = None
        if original.before_hash is not None:
            restore = area+'/returned/'+str(uuid4())+'.bin'
            with private_dir(base,area+'/backup') as directory:
                source = open_handle(base/area/'backup'/(identifier+'.bin'),parent=directory.handles[-1])
                try:
                    identity = copy_private(base,restore,source,original.before_hash)
                finally:
                    source.Close()
            executor._record(journal,transaction,'restore_prepared',dict(operation_id=identifier,identity=identity,path=restore))
        with session.step(current,delete=True) as target:
            if target.handle is not None:
                with private_dir(base,area+'/returned',create=True) as private:
                    target.rename_to(private.handles[-1],str(uuid4())+'.after.bin')
            if restore is not None:
                with private_dir(base,area+'/returned') as private:
                    handle = open_handle(base/restore,parent=private.handles[-1],delete=True)
                    try:
                        if file_identity(_information(handle,False))!=identity or strong_hash(handle)!=original.before_hash:
                            _error('EXEC_BACKUP_INVALID')
                        native_rename(handle,target.parent_handle,target.leaf)
                    finally:
                        handle.Close()
        executor._record(journal,transaction,'restored',dict(operation_id=identifier))
        executor._fault('recovery.after_restore',transaction,operation)
    if executor._disk(installation,plan=plan)!=header['before']:
        _error('EXEC_ROLLBACK_MISMATCH')
    _restore_manifest(executor,installation,transaction,header,records)
    with executor._store() as store:
        pointer = store.connection.execute('SELECT * FROM deployment_current WHERE installation_id=?',
            (str(installation.installation_id),)).fetchone()
        if (dict(pointer) if pointer else None)!=header['old_pointer']:
            _error('EXEC_CURRENT_MISMATCH')
    executor._record(journal,transaction,'rolled_back',state='rolled_back')
    executor._cleanup(installation,transaction,journal)


def _isolate_known_anomalies(executor, installation, transaction, journal, header, operations, intent, stages, session, base, area):
    """Quarantine only authenticated primary objects; never read reparse targets."""
    import win32file
    from .paths import open_recovery_handle, quarantine_file
    for identifier in intent:
        operation = operations[identifier]
        original = executor._snapshot(header['snapshots'][target_key(operation.target_root_kind,operation.relative_path)])
        identities = [stages[identifier]['identity']] if identifier in stages else []
        if original.target_identity:
            identities.append(original.target_identity)
        path = Path(installation.canonical_root)/ROOTS[operation.target_root_kind]/operation.relative_path
        with pinned_chain(path.parent) as parent:
            if tuple(parent)!=original.ancestors:
                _error('EXEC_EXTERNAL_CONFLICT')
            for identity in identities:
                try:
                    handle = open_recovery_handle(parent.handles[-1],path.name,identity)
                except FileNotFoundError:
                    break
                except GuardRejected as exc:
                    if exc.code=='GUARD_IDENTITY_CHANGED':
                        continue
                    raise
                try:
                    information = win32file.GetFileInformationByHandle(handle)
                    if information[0]&0x400 or information[7]!=1:
                        session.check()
                        leaf = str(uuid4())+'.quarantine.bin'
                        with private_dir(base,area+'/conflicts',create=True) as private:
                            quarantine_file(handle,private.handles[-1],leaf,identity)
                        executor._record(journal,transaction,'quarantine',dict(operation_id=identifier,identity=identity,path=leaf))
                finally:
                    handle.Close()
                break


def recover_transaction(executor, transaction, *, allow_forward=True):
    journal = Journal(executor.paths.root,transaction.transaction_id)
    with executor._store() as store:
        plan = store.get('ActionPlan',transaction.plan_id)
        installation = store.get('Installation',plan.installation_id)
        generations = [g for g in store.list('DeploymentGeneration') if g.transaction_id==transaction.transaction_id]
        baseline = store.get('BaselineSnapshot',plan.baseline_snapshot_id)
    try:
        # Repair only a MAC-verified prefix with an unterminated final append.
        journal.load(repair_tail=True)
        records,header = executor._header(journal,transaction,plan)
    except FileNotFoundError:
        if transaction.state in {'cancelled','failed'} and transaction.last_sequence==0 and executor._marker(installation) is None:
            return False
        if transaction.state in {'approved','failed','cancelled'} and transaction.last_sequence==0 and not generations:
            expected = executor.plans._baseline_files(baseline,plan.installation_id,plan.baseline_manifest_hash)
            if executor._disk(installation,plan=plan)!=expected:
                _error('EXEC_EXTERNAL_CONFLICT')
            transaction.state='cancelled'
            executor._save(transaction)
            executor._unmark(installation,transaction)
            return True
        raise
    if header['installation_identity']!=installation.file_identity:
        _error('EXEC_JOURNAL_INVALID')
    if header.get('execution_policy') == 'ordinary-mods-v1':
        from .ordinary import recover
        return recover(executor, installation, plan, transaction, journal, header, records, allow_forward=allow_forward)
    last = records[-1]
    terminal = last['kind'] in {'committed','rolled_back','cancelled','failed','cleanup'}
    committed_record = any(r['kind']=='committed' for r in records)
    rolled_back_record = any(r['kind']=='rolled_back' for r in records)
    if terminal and committed_record:
        transaction.state='committed'
    if terminal and not committed_record and rolled_back_record and not generations:
        transaction.state='rolled_back'
    if terminal and transaction.state in {'committed','rolled_back','cancelled','failed'}:
        transaction.last_sequence=last['sequence']; executor._save(transaction)
        if transaction.state=='committed':
            if not committed_record or len(generations)!=1 or generations[0].generation_id!=transaction.target_generation:
                _error('EXEC_CURRENT_MISMATCH')
            with executor._store() as store:
                pointer = store.connection.execute('SELECT * FROM deployment_current WHERE installation_id=?',
                    (str(installation.installation_id),)).fetchone()
                latest = max((g.sequence for g in store.list('DeploymentGeneration')
                    if g.installation_id==installation.installation_id),default=0)
                pending = any(t.transaction_id!=transaction.transaction_id and
                    t.state not in {'committed','rolled_back','cancelled','failed'}
                    for t in executor._transactions(store,installation.installation_id))
            if generations[0].sequence==latest and not pending:
                intents = [r['data']['manifest'] for r in records if r['kind']=='commit_intent']
                if len(intents)!=1:
                    _error('EXEC_CURRENT_MISMATCH')
                sealed = intents[0]
                generation = generations[0]
                if (generation.sequence!=sealed['sequence'] or generation.manifest_hash!=sealed['manifest_hash']
                        or generation.installation_id!=plan.installation_id
                        or generation.committed_at!=datetime.fromisoformat(sealed['committed_at'])):
                    _error('EXEC_CURRENT_MISMATCH')
                try:
                    manifest = json.loads(read_private(executor.paths.root,executor._current_path(installation)))
                except FileNotFoundError:
                    manifest = None
                current = executor._disk(installation,plan=plan) if not pending else None
                matches = current is None or all(current.get(target_key(f['target_root_kind'],f['relative_path']))==
                    {k:f[k] for k in ('target_root_kind','relative_path','sha256','size')} for f in sealed['owned_files'])
                if not matches or manifest not in (None,sealed):
                    _error('EXEC_CURRENT_MISMATCH')
                expected_hash = hashlib.sha256(canonical(sealed)).hexdigest()
                if (manifest is None or pointer is None or pointer['generation_id']!=str(transaction.target_generation)
                        or pointer['manifest_file_hash']!=expected_hash or pointer['manifest_hash']!=sealed['manifest_hash']):
                    permit = executor._permit(installation,transaction)
                    try:
                        with executor.guards.operation(str(plan.installation_id),'rollback',permit=permit):
                            if manifest is None:
                                prepared = executor._area(transaction)+'/manifest.bin'
                                try:
                                    if read_private(executor.paths.root,prepared)!=canonical(sealed):
                                        _error('EXEC_CURRENT_MISMATCH')
                                except FileNotFoundError:
                                    write_private(executor.paths.root,prepared,canonical(sealed))
                                executor._publish_manifest(installation,transaction,sealed)
                            executor._commit_db(plan,transaction,sealed)
                    finally:
                        executor._permits.discard(permit)
        marker = executor._marker(installation)
        if marker and marker['transaction_id']==str(transaction.transaction_id):
            executor._cleanup(installation,transaction,journal)
        return False
    permit = executor._permit(installation,transaction)
    try:
        with executor.guards.operation(str(plan.installation_id),'rollback',permit=permit) as session:
            transaction.last_sequence=last['sequence']
            executor._save(transaction)
            intent = [r['data']['manifest'] for r in records if r['kind']=='commit_intent']
            if len(intent)>1:
                _error('EXEC_JOURNAL_INVALID')
            if intent and (allow_forward or generations):
                manifest = intent[0]
                timestamp = datetime.fromisoformat(manifest['committed_at'])
                if (timestamp.utcoffset()!=timedelta(0) or timestamp>=plan.expires_at or
                        timestamp<plan.expires_at-executor.plans.plan_lifetime(plan) or
                        manifest['generation_id']!=str(transaction.target_generation) or
                        manifest['manifest_hash']!=check_value(plan,'result_manifest') or
                        manifest['files']!=header['after']):
                    _error('EXEC_CURRENT_MISMATCH')
                if executor._disk(installation,plan=plan)==header['after']:
                    relative = executor._current_path(installation)
                    try:
                        current = json.loads(read_private(executor.paths.root,relative))
                    except FileNotFoundError:
                        current = None
                    if current not in (None,header['old_manifest'],manifest):
                        _error('EXEC_CURRENT_MISMATCH')
                    executor._publish_manifest(installation,transaction,manifest)
                    executor._commit_db(plan,transaction,manifest)
                    executor._record(journal,transaction,'committed',state='committed')
                    executor._cleanup(installation,transaction,journal)
                    return True
            if generations:
                _error('EXEC_CURRENT_MISMATCH')
            _rollback(executor,installation,plan,transaction,journal,header,records,session)
            return True
    finally:
        executor._permits.discard(permit)


class RecoveryService:
    def __init__(self, executor):
        self.executor = executor

    def recover(self, payload, **kwargs):
        return self.executor.recover(payload,**kwargs)

    def startup(self):
        return self.executor.startup()
