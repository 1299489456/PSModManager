"""Ordinary Mod file operations, with one recovery record for the whole action.

The advanced deployment executor keeps its per-file durable protocol. Normal
save/enable/disable/uninstall instead checks affected files, prepares copies,
and publishes with normal filesystem operations. No per-file fsync or global
process/recovery query is performed. Existing ownership and history DTOs remain
the common interface for both kinds of operation.
"""
from contextlib import nullcontext
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4, uuid5

from backend.app.paths import reject_reparse
from backend.contracts import ActionPlan, Approval, BaselineSnapshot, Profile, Transaction
from backend.contracts.plans import plan_digest
from backend.storage.package_repository import PackageRepository, content_hash
from backend.catalog.versions import library_attachment
from backend.catalog.file_policy import ordinary_file, scoped_supplement
from .journal import Journal, canonical, private_dir, read_private, write_private
from .paths import ROOTS, target_path, pinned_chain
from .policy import PlanRejected, check_value, native_approval_id, owned_after, target_key, writable_file


POLICY = 'ordinary-mods-v1'


def ordinary(plan):
    return any(p.kind == 'execution_policy' and p.expected == POLICY for p in plan.preconditions)


def before_owned(plan):
    return json.loads(check_value(plan, 'ordinary_before'))


def safe_target(root, file, checked_parents):
    path = target_path(root, file['target_root_kind'], file['relative_path'])
    # The installation is pinned for the action. Check common directory chains
    # once per phase, not once per resource; each leaf is still checked.
    if path.parent not in checked_parents:
        reject_reparse(path.parent)
        checked_parents.add(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if (getattr(info, 'st_file_attributes', 0) & 0x400
            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise PlanRejected('MOD_WORKSPACE_UNSAFE')
    return path


def digest(path):
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise PlanRejected('PLAN_DISK_CHANGED')
    return value


def identity(path):
    value = path.stat()
    return f'{value.st_dev:x}:{value.st_ino:x}'


def copy_verified(source, destination, expected):
    """Validate the library bytes during the one copy already required."""
    checksum = hashlib.sha256()
    with source.open('rb') as reader, destination.open('xb') as writer:
        for chunk in iter(lambda: reader.read(256*1024), b''):
            checksum.update(chunk)
            writer.write(chunk)
    if checksum.hexdigest() != expected:
        raise PlanRejected('EXEC_STAGE_INVALID')


def backups(records):
    batch = next((r['data'] for r in records if r['kind'] == 'ordinary_prepared'), {})
    return batch.get('backups', {})


class OrdinaryMods:
    def __init__(self, mods):
        self.mods, self.plans, self.executor = mods, mods.plans, mods.executor

    def metadata(self, store, installation):
        """Current committed owners plus explicit adoptions, without file I/O."""
        from .mod_workspace import ModWorkspaceService
        managed, _, generation = ModWorkspaceService._metadata_owners(store, installation.installation_id)
        managed = {key: dict(file, owner_ids=sorted(file['owner_ids'])) for key, file in managed.items()}
        for file in managed.values():
            if 'size' not in file:
                file['size'] = store.get('Artifact', file['sha256']).size
        state = self.mods._state(store, installation.installation_id)
        members = {str(s.release_id): s.model_dump(mode='json') for s in state.selections if not s.selected}
        for file in managed.values():
            for owner in file['owner_ids']:
                release = store.get('ModRelease', owner)
                members[owner] = dict(release_id=owner, manifest_hash=release.manifest_hash, selected=True)
        return state, members, dict(sorted(managed.items())), generation

    def prepare(self, store, request, session, workspace, check, progress):
        installation = session.installation
        root = Path(installation.canonical_root)
        if workspace is not None:
            view = workspace._view(store, installation, check)
            if view['snapshot_id'] != request.snapshot_id:
                raise PlanRejected('MOD_WORKSPACE_STALE')
            items = {item['id']: item for item in view['items']}
            enabled = [str(value) for value in request.enabled_ids]
            if len(set(enabled)) != len(enabled) or not set(enabled) <= items.keys():
                raise PlanRejected('MOD_WORKSPACE_INVALID')
            adopted = {}
            for value in sorted(set(enabled) ^ set(view['enabled_ids'])):
                item = items[value]
                if not item['manageable']:
                    raise PlanRejected('MOD_WORKSPACE_UNSAFE')
                if item['source'] == 'external':
                    adopted[value] = workspace._adopt(store, installation, item, check, progress, ordinary=True)
        state, members, managed, generation = self.metadata(store, installation)
        if workspace is not None:
            for value, item in items.items():
                if item['source'] == 'external' and value not in adopted:
                    continue
                member = adopted.get(value) or dict(release_id=item['release_id'], manifest_hash=item['manifest_hash'])
                if value in enabled or member['release_id'] in members or value in adopted:
                    members[member['release_id']] = dict(member, selected=value in enabled)
        else:
            repository = PackageRepository(store, self.plans.library)
            repository.release(request.release_id, request.manifest_hash, check=check, verify_bytes=False)
            owner = str(request.release_id)
            if request.action in {'enable', 'disable'} and owner not in members:
                raise PlanRejected('MOD_NOT_INSTALLED')
            if request.action == 'uninstall':
                members.pop(owner, None)
            else:
                members[owner] = dict(release_id=owner, manifest_hash=request.manifest_hash, selected=request.action != 'disable')
        desired = {}
        selected_packages = set()
        repository = PackageRepository(store, self.plans.library)
        for member in members.values():
            if not member['selected']:
                continue
            release = repository.release(member['release_id'], member['manifest_hash'], check=check, verify_bytes=False)
            package = release['release']['package_id']
            if package in selected_packages:
                raise PlanRejected('MOD_VERSION_CONFLICT')
            selected_packages.add(package)
            for file in release['files']:
                if library_attachment(file):
                    continue
                if not ordinary_file(file):
                    raise PlanRejected('MOD_WORKSPACE_UNSAFE')
                # An explicitly selected, Mod-directory-scoped resource/config
                # uses the same hash/unknown-target/recovery protection. Keep
                # its original published lifecycle and release hash unchanged.
                target = dict(file, lifecycle='immutable', ownership='shared_identical') if scoped_supplement(file) else file
                key = writable_file(target, root)
                if key in desired and desired[key]['sha256'] != file['source_hash']:
                    raise PlanRejected('PLAN_TARGET_CONFLICT')
                value = desired.setdefault(key, dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                    sha256=file['source_hash'], size=store.get('Artifact', file['source_hash']).size, owner_ids=[]))
                value['owner_ids'].append(member['release_id'])
        desired = {key: dict(value, owner_ids=sorted(set(value['owner_ids']))) for key, value in sorted(desired.items())}
        changed = {key for key in managed.keys() | desired.keys() if managed.get(key) != desired.get(key)}
        if len(changed) > 4000 or sum(value['size'] for value in desired.values()) > 1024**3:
            raise PlanRejected('MOD_WORKSPACE_LIMIT')
        operations, checked_parents = [], set()
        for key in sorted(changed):
            check()
            old, new = managed.get(key), desired.get(key)
            file = new or old
            path = safe_target(root, file, checked_parents)
            actual = digest(path) if path.exists() else None
            if old and actual != old['sha256']:
                raise PlanRejected('PLAN_MANAGED_DRIFT')
            if not old and actual is not None:
                raise PlanRejected('PLAN_UNMANAGED_CONFLICT')
            operations.append(dict(operation_id=uuid5(request.idempotency_key, key),
                kind='replace_owned' if old and new else 'remove_owned' if old else 'install_owned',
                target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                expected_before=old['sha256'] if old else 'absent', desired_after=new['sha256'] if new else 'absent',
                owner_ids=file['owner_ids']))
        profile = Profile(profile_id=uuid5(request.idempotency_key, 'PSModManager.direct-mods.profile.v1'),
            installation_id=request.installation_id, revision=0, selections=list(members.values()), config_overrides={}, display_order=[],
            workspace_order=request.enabled_ids if workspace else state.workspace_order)
        plan_id = uuid5(request.idempotency_key, 'PSModManager.ActionPlan.v1')
        original = [{k: v for k, v in f.items() if k != 'owner_ids'} for f in managed.values()]
        files = sorted([dict(relative_path=ROOTS[f['target_root_kind']]+'/'+f['relative_path'], sha256=f['sha256'], size=f['size'])
                        for f in original], key=lambda f: f['relative_path'])
        baseline = BaselineSnapshot(snapshot_id=uuid5(plan_id, 'baseline'), installation_id=request.installation_id,
            scan_generation=0, manifest_hash=content_hash(files), files=files, coverage='complete')
        binding = request.model_dump(mode='json') | dict(state_revision=state.revision)
        scope = [dict(target_root_kind=(desired.get(k) or managed[k])['target_root_kind'],
                      relative_path=(desired.get(k) or managed[k])['relative_path']) for k in sorted(managed.keys() | desired.keys())]
        facts = {'execution_policy': POLICY, 'ordinary_before': json.dumps(managed, sort_keys=True),
            'direct_mod_action': json.dumps(binding, sort_keys=True), 'owned_before': content_hash(managed),
            'owned_after': json.dumps(list(desired.values()), sort_keys=True), 'result_manifest': content_hash(list(desired.values())),
            'adoption_receipts': json.dumps(self.plans._receipt_facts(store, installation.installation_id), sort_keys=True),
            'owner_policy': 'explicit-v1', 'target_scope': json.dumps(scope, sort_keys=True)}
        lifetime = __import__('datetime').timedelta(minutes=30 if workspace or len(operations)>128 else 10)
        plan = ActionPlan(plan_id=plan_id, installation_id=request.installation_id, profile_id=profile.profile_id,
            profile_revision=0, base_generation=generation, baseline_snapshot_id=baseline.snapshot_id,
            baseline_manifest_hash=baseline.manifest_hash, fingerprint_id=installation.fingerprint_id,
            plan_hash='0'*64, idempotency_key=request.idempotency_key, operations=operations,
            preconditions=[dict(kind=k, reference='installation', expected=v) for k, v in facts.items()],
            verification_checks=[], evidence_ids=[], disk_estimate_by_volume={}, backup_scope=[],
            restart_required=bool(operations), approval_scope=sorted({op['kind'] for op in operations}),
            expires_at=datetime.now(timezone.utc)+lifetime)
        plan.plan_hash = plan_digest(plan)
        store.put(profile); store.put(baseline); store.plans.add(plan)
        if operations:
            approval = Approval(approval_id=native_approval_id(self.plans.paths.root, plan.plan_hash, create=True),
                plan_id=plan_id, plan_hash=plan.plan_hash, actor='direct-action', scope=plan.approval_scope, expires_at=plan.expires_at)
            store.plans.add(approval)
        return plan

    def save(self, request, *, workspace=None, check=lambda: None, commit=None, progress=lambda *a: None):
        ex = self.executor
        legacy = None
        with self.plans._store(check, verify_library_bytes=False) as store:
            old = store.connection.execute('SELECT payload FROM action_plans WHERE idempotency_key=?', (str(request.idempotency_key),)).fetchone()
            if old:
                plan = ActionPlan.model_validate_json(old['payload'])
                binding = self.plans.direct_binding(plan)
                if binding is None or {k:v for k,v in binding.items() if k != 'state_revision'} != request.model_dump(mode='json'):
                    raise PlanRejected('PLAN_IDEMPOTENCY_CONFLICT')
                receipts = [t for t in ex._transactions(store, plan.installation_id) if t.plan_id == plan.plan_id]
                if receipts:
                    receipt = receipts[-1]
                    if receipt.state == 'committed':
                        return dict(idempotent=True, deployed=True, no_op=False)
                    raise PlanRejected('EXEC_ALREADY_ATTEMPTED')
                if plan.operations:
                    # An old prepared action still uses its original executor.
                    if not ordinary(plan):
                        legacy = dict(plan_id=plan.plan_id, plan_hash=plan.plan_hash,
                            approval_id=native_approval_id(ex.paths.root, plan.plan_hash))
                    else:
                        raise PlanRejected('EXEC_ALREADY_ATTEMPTED')
                else:
                    return dict(idempotent=True, no_op=True)
        if legacy:
            return ex.apply(legacy, check=check, commit=commit, progress=progress)
        with self.plans.guards.operation(str(request.installation_id), 'apply') as session:
            with self.plans._store(check, verify_library_bytes=False) as store:
                plan = self.prepare(store, request, session, workspace, check, progress)
                if not plan.operations:
                    self.plans.commit_direct_state(store, plan)
                    if commit:
                        commit(store.connection.commit)
                    return dict(no_op=True, idempotent=False)
            return self.apply(plan, session, check, commit, progress)

    def apply(self, plan, session, check, commit, progress):
        ex, installation = self.executor, session.installation
        transaction = Transaction(transaction_id=uuid4(), plan_id=plan.plan_id, state='approved',
            journal_path='deployments/journals/pending.jsonl', target_generation=uuid4(), last_sequence=0)
        journal = Journal(ex.paths.root, transaction.transaction_id)
        transaction.journal_path = journal.relative_path
        before = before_owned(plan)
        after = owned_after(plan)
        public = lambda values: {k: {name: f[name] for name in ('target_root_kind','relative_path','sha256','size')} for k,f in values.items()}
        with ex._store() as store:
            pointer = store.connection.execute('SELECT * FROM deployment_current WHERE installation_id=?', (str(plan.installation_id),)).fetchone()
        try:
            old_manifest = json.loads(read_private(ex.paths.root, ex._current_path(installation)))
        except FileNotFoundError:
            old_manifest = None
        base = Path(installation.canonical_root).parent
        area = ex._target_area(installation, transaction)
        with private_dir(base, area, create=True) as directory, pinned_chain(ex.paths.root) as data:
            header = dict(transaction_id=str(transaction.transaction_id), plan_id=str(plan.plan_id), plan_hash=plan.plan_hash,
                approval_id=str(native_approval_id(ex.paths.root, plan.plan_hash)), target_generation=str(transaction.target_generation),
                installation_identity=installation.file_identity, data_identity=data[-1][1], before=public(before), after=public(after),
                snapshots={}, old_manifest=old_manifest, old_pointer=dict(pointer) if pointer else None,
                file_storage=dict(kind='installation_sibling_v1', ancestors=list(map(list, directory))), execution_policy=POLICY)
        permit = ex._permit(installation, transaction)
        session.permit = permit
        try:
            ex._save(transaction)
            ex._mark(installation, transaction)
            journal.create(header, discard_on_error=True); transaction.last_sequence = 1
            ex._fault('ordinary.header', transaction)
            with ex._transaction_files(installation, transaction, header) as (base, area):
                private = base/area
                for folder in ('backup', 'stage', 'returned', 'conflicts'):
                    (private/folder).mkdir(exist_ok=True)
                copied, checked_parents = {}, set()
                with ex._store() as store:
                    repository = PackageRepository(store, self.plans.library)
                    verified = set()
                    for index, operation in enumerate(plan.operations):
                        check()
                        progress(index, 2*len(plan.operations), 'preparing', 'MOD_WORKSPACE_BACKING_UP')
                        file = operation.model_dump(mode='json')
                        target = safe_target(Path(installation.canonical_root), file, checked_parents)
                        identifier = str(operation.operation_id)
                        if operation.expected_before != 'absent':
                            destination = private/'backup'/(identifier+'.bin')
                            shutil.copyfile(target, destination)
                            copied[identifier] = dict(operation_id=identifier, sha256=operation.expected_before, identity=identity(destination))
                        if operation.desired_after != 'absent' and operation.desired_after != operation.expected_before:
                            source = repository.ordinary_path(operation.desired_after)
                            destination = private/'stage'/(identifier+'.bin')
                            if operation.desired_after not in verified:
                                copy_verified(source, destination, operation.desired_after)
                                verified.add(operation.desired_after)
                            else:
                                shutil.copyfile(source, destination)
                ex._record(journal, transaction, 'ordinary_prepared', dict(backups=copied), state='ready')
                ex._fault('ordinary.prepared', transaction)
                check(); session.process.check()
                checked_parents = set()
                for index, operation in enumerate(plan.operations):
                    check()
                    progress(len(plan.operations)+index, 2*len(plan.operations), 'applying', 'DEPLOYMENT_APPLYING')
                    target = safe_target(Path(installation.canonical_root), operation.model_dump(mode='json'), checked_parents)
                    if operation.desired_after == operation.expected_before:
                        continue
                    # A normal concurrent edit is detected before this file is
                    # touched; no unknown file is silently overwritten.
                    current = digest(target) if target.exists() else 'absent'
                    if current != operation.expected_before:
                        raise PlanRejected('PLAN_MANAGED_DRIFT' if operation.expected_before != 'absent' else 'PLAN_UNMANAGED_CONFLICT')
                    if operation.desired_after == 'absent':
                        target.unlink()
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(private/'stage'/(str(operation.operation_id)+'.bin'), target)
                    ex._fault('ordinary.file_applied', transaction, operation)
                check(); session.process.check()
                manifest = ex._manifest(plan, transaction, header)
                write_private(ex.paths.root, ex._area(transaction)+'/manifest.bin', canonical(manifest))
                ex._record(journal, transaction, 'commit_intent', dict(manifest=manifest))
                ex._fault('ordinary.commit_intent', transaction)
                ex._publish_manifest(installation, transaction, manifest)
                ex._fault('db.before_commit', transaction)
                ex._commit_db(plan, transaction, manifest, commit)
                ex._fault('ordinary.db_committed', transaction)
                ex._record(journal, transaction, 'committed', state='committed')
                ex._cleanup(installation, transaction, journal)
            return dict(no_op=False, idempotent=False, deployed=True)
        except BaseException as error:
            from backend.runtime.jobs import _Cancelled
            try:
                try:
                    records = journal.load()
                except FileNotFoundError:
                    if transaction.last_sequence:
                        raise
                    # No header means this action never reached target writes.
                    # Clear only its own marker; do not invent recovery work.
                    ex._unmark(installation, transaction)
                    transaction.state = 'cancelled' if isinstance(error, _Cancelled) else 'failed'
                    ex._save(transaction)
                else:
                    # Keep the current installation lease. Taking a second
                    # lease here would block immediate ordinary rollback.
                    recover(ex, installation, plan, transaction, journal, header, records,
                        allow_forward=not isinstance(error, _Cancelled), session=session)
            except BaseException:
                transaction.state = 'recovery_required'; ex._save(transaction)
            if isinstance(error, _Cancelled):
                raise
            if transaction.state == 'committed':
                return dict(no_op=False, idempotent=False, deployed=True)
            if isinstance(error, (ValueError, OSError, KeyError)):
                native = getattr(error, 'native_error', None) or getattr(error, 'winerror', None)
                number = getattr(error, 'errno', None)
                code = ('PLAN_SPACE_INSUFFICIENT' if native == 112 or number == errno.ENOSPC
                        else getattr(error, 'code', 'EXEC_IO_FAILED'))
                reason = ('disk_full' if code == 'PLAN_SPACE_INSUFFICIENT' else 'file_busy' if native in {32, 33}
                    else 'permission_denied' if native == 5 or number == errno.EACCES else 'io_or_guard')
                return dict(error_code=code, message=str(error),
                    transaction=transaction.model_dump(mode='json'),
                    io_error=dict(kind=reason, native_error=native, errno=number, attempts=getattr(error, 'attempts', 1)),
                    recovery_required=transaction.state == 'recovery_required', verification_status='not_run')
            raise
        finally:
            ex._permits.discard(permit)


def recover(ex, installation, plan, transaction, journal, header, records, *, allow_forward=True, session=None):
    """Whole-action recovery. External changes are retained, never guessed away."""
    from .recovery import _restore_manifest
    terminal = next((r['kind'] for r in reversed(records) if r['kind'] in {'committed','rolled_back','failed','cancelled'}), None)
    if terminal:
        transaction.state = terminal; transaction.last_sequence = records[-1]['sequence']; ex._save(transaction)
        marker = ex._marker(installation)
        if marker and marker['transaction_id'] == str(transaction.transaction_id):
            ex._cleanup(installation, transaction, journal)
        return False
    permit = session.permit if session else ex._permit(installation, transaction)
    try:
        guard = nullcontext(session) if session else ex.guards.operation(str(plan.installation_id), 'rollback', permit=permit)
        with guard as active:
            active.process.check()
            with ex._transaction_files(installation, transaction, header) as (base, area):
                private = base/area
                prepared = next((r for r in records if r['kind'] == 'ordinary_prepared'), None)
                intended = next((r['data']['manifest'] for r in records if r['kind'] == 'commit_intent'), None)
                states, checked_parents = [], set()
                if prepared:
                    for operation in plan.operations:
                        target = safe_target(Path(installation.canonical_root), operation.model_dump(mode='json'), checked_parents)
                        current = digest(target) if target.exists() else 'absent'
                        states.append((operation, target, current))
                        if current not in {operation.expected_before, operation.desired_after}:
                            if target.exists():
                                shutil.copyfile(target, private/'conflicts'/(str(uuid4())+'.bin'))
                            raise PlanRejected('EXEC_EXTERNAL_CONFLICT')
                if intended and allow_forward and all(current == op.desired_after for op, _, current in states):
                    ex._publish_manifest(installation, transaction, intended)
                    ex._commit_db(plan, transaction, intended)
                    ex._record(journal, transaction, 'committed', state='committed')
                else:
                    for operation, target, current in reversed(states):
                        if current == operation.expected_before:
                            continue
                        if operation.expected_before == 'absent':
                            target.unlink()
                        else:
                            source = private/'backup'/(str(operation.operation_id)+'.bin')
                            reject_reparse(source)
                            if digest(source) != operation.expected_before:
                                raise PlanRejected('EXEC_BACKUP_INVALID')
                            temporary = private/'returned'/(str(operation.operation_id)+'.bin')
                            shutil.copyfile(source, temporary)
                            os.replace(temporary, target)
                    _restore_manifest(ex, installation, transaction, header, records)
                    ex._record(journal, transaction, 'rolled_back', state='rolled_back')
                ex._cleanup(installation, transaction, journal)
        return True
    finally:
        if session is None:
            ex._permits.discard(permit)
