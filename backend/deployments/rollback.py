"""Selective, approved undo derived exclusively from authenticated transactions.

No client paths, directory restore or DB-only reversal. The existing executor
stages retained before bytes and commits another durable generation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID, uuid5

from backend.contracts import ActionPlan, Profile
from backend.contracts.plans import RollbackProposal, PlanProposal, plan_digest
from .planner import TTL
from .paths import capture_target, GuardRejected, pinned_file, open_handle, _information, file_identity, strong_hash
from .journal import Journal, private_dir
from .policy import PlanRejected, check_value, target_key


class _Drift(PlanRejected):
    def __init__(self, code, conflicts, preserve):
        super().__init__(code, conflicts)
        # Runs only after the planner's SQLite writer is released. Copies and
        # recovery metadata cannot be rolled back with a rejected preview.
        self._preserve = preserve


class RollbackService:
    def __init__(self, paths, guards, plans, executor, history):
        self.paths, self.guards, self.plans, self.executor, self.history = paths, guards, plans, executor, history

    @staticmethod
    def binding(plan):
        try:
            value = json.loads(check_value(plan, 'rollback_source'))
            if (not isinstance(value, dict) or set(value) != {'source_transaction_id','source_generation_id',
                    'source_plan_hash','source_signature','current_generation','backups'}
                    or not isinstance(value['backups'], dict)):
                raise ValueError()
            for key in ('source_transaction_id','source_generation_id','current_generation'):
                UUID(value[key])
            return value
        except (ValueError, TypeError, KeyError):
            raise PlanRejected('ROLLBACK_SOURCE_INVALID') from None

    @staticmethod
    def eligibility(frame, current_frame, check=lambda: None):
        check()
        if current_frame is None or frame['transaction'].state != 'committed':
            return ['HISTORY_SOURCE_UNCOMMITTED']
        before, after, current = frame['before_owned'], frame['after_owned'], current_frame['after_owned']
        for op in frame['plan'].operations:
            key = target_key(op.target_root_kind,op.relative_path)
            latest, produced = current.get(key), after.get(key)
            if latest and (not produced or latest['sha256'] != produced['sha256']):
                return ['ROLLBACK_SUPERSEDED']
            if produced and not latest and before.get(key):
                return ['ROLLBACK_SUPERSEDED']
        return []

    def _derive(self, store, installation_id, source_id, expected_generation, check, *, sealed=None):
        installation = store.get('Installation',installation_id)
        frames = self.history.frames(store,installation_id,check)
        current = frames[-1] if frames else None
        if current is None or current['generation'].generation_id != expected_generation:
            raise PlanRejected('ROLLBACK_GENERATION_STALE')
        pointer = store.connection.execute('SELECT generation_id FROM deployment_current WHERE installation_id=?',
            (str(installation_id),)).fetchone()
        if not pointer or pointer['generation_id'] != str(expected_generation):
            raise PlanRejected('ROLLBACK_GENERATION_STALE')
        source = next((f for f in frames if f['transaction'].transaction_id == source_id),None)
        if source is None:
            raise PlanRejected('ROLLBACK_SOURCE_INVALID')
        backups = {}

        def reject(code, conflict):
            raise _Drift(code,[conflict],lambda: self.history.record_issue(
                installation,current,source,code,[conflict]))

        # Check source target types first so an unsafe occupied original is a
        # persistent problem, without enumerating through a junction or link.
        targets = {target_key(op.target_root_kind,op.relative_path):dict(
            target_root_kind=op.target_root_kind,relative_path=op.relative_path) for op in source['plan'].operations}
        targets.update({key:dict(target_root_kind=file['target_root_kind'],relative_path=file['relative_path'])
            for key,file in current['after_owned'].items()})
        for key,target in targets.items():
            try:
                capture_target(installation.canonical_root,installation.file_identity,
                    target['target_root_kind'],target['relative_path'])
            except GuardRejected:
                latest = current['after_owned'].get(key)
                reject('ROLLBACK_DRIFT',dict(target,expected=latest['sha256'] if latest else 'absent',actual='unavailable'))
        disk,_ = self.plans._scan(Path(installation.canonical_root),installation.file_identity,check)
        for key,file in current['after_owned'].items():
            observed = disk.get(key)
            if not observed or observed['sha256'] != file['sha256']:
                reject('ROLLBACK_DRIFT',dict(target_root_kind=file['target_root_kind'],relative_path=file['relative_path'],
                    expected=file['sha256'],actual=observed['sha256'] if observed else 'absent'))
        adopted = self.plans._adopted(store,installation_id,disk,Path(installation.canonical_root),
            self.plans.library,check,after=current['generation'].committed_at)
        # Adoptions since the latest deployment are independent ownership; undo
        # must preserve them just as it preserves later committed owners.
        desired = self.plans._merge_owners(current['after_owned'],adopted)
        for operation in source['plan'].operations:
            check()
            key = target_key(operation.target_root_kind,operation.relative_path)
            before, after, latest = source['before_owned'].get(key), source['after_owned'].get(key), desired.get(key)
            conflict = dict(target_root_kind=operation.target_root_kind,relative_path=operation.relative_path,
                expected=latest['sha256'] if latest else 'absent',actual='unavailable')
            # A later genuine transaction is not an external edit. Never undo it
            # incidentally when reverting an older transaction.
            if latest and (not after or latest['sha256'] != after['sha256']):
                raise PlanRejected('ROLLBACK_SUPERSEDED',[conflict])
            if after and not latest and before:
                raise PlanRejected('ROLLBACK_SUPERSEDED',[conflict])
            try:
                snapshot = capture_target(installation.canonical_root,installation.file_identity,
                    operation.target_root_kind,operation.relative_path)
            except GuardRejected:
                reject('ROLLBACK_DRIFT',conflict)
            conflict['actual'] = snapshot.before_hash or 'absent'
            if snapshot.before_hash != (latest['sha256'] if latest else None):
                # An already-removed addition is a no-op and leaves a later
                # unknown occupant alone; restoring a removed original cannot.
                if not before and not latest:
                    continue
                reject('ROLLBACK_DRIFT',conflict)
            added = set(after['owner_ids'] if after else ()) - set(before['owner_ids'] if before else ())
            removed = set(before['owner_ids'] if before else ()) - set(after['owner_ids'] if after else ())
            owners = (set(latest['owner_ids'] if latest else ()) - added) | removed
            if not before:
                if owners:
                    desired[key] = dict(latest,owner_ids=sorted(owners))
                else:
                    desired.pop(key,None)
                continue
            if before['sha256'] != (after['sha256'] if after else None):
                # Later owners of different bytes must be removed by their own
                # transaction first. Unrelated owners always retain their files.
                other = owners - set(before['owner_ids'])
                if other:
                    raise PlanRejected('ROLLBACK_SUPERSEDED',[conflict])
                try:
                    backup = self.history.verify_backup(source,operation,check)
                except PlanRejected as error:
                    reject(error.code,conflict)
                backups[key] = backup
            if owners:
                desired[key] = dict(before,owner_ids=sorted(owners))
            else:
                desired.pop(key,None)
        descriptor = dict(source_transaction_id=str(source_id),source_generation_id=str(source['generation'].generation_id),
            source_plan_hash=source['plan'].plan_hash,source_signature=source['records'][0]['signature'],
            current_generation=str(expected_generation),backups=backups)
        if sealed is not None and sealed != descriptor:
            for operation in source['plan'].operations:
                key = target_key(operation.target_root_kind,operation.relative_path)
                if sealed['backups'].get(key) != backups.get(key):
                    current_file = current['after_owned'].get(key)
                    reject('ROLLBACK_SOURCE_CHANGED',dict(target_root_kind=operation.target_root_kind,
                        relative_path=operation.relative_path,expected=current_file['sha256'] if current_file else 'absent',
                        actual=current_file['sha256'] if current_file else 'absent'))
            raise PlanRejected('ROLLBACK_SOURCE_CHANGED')
        return desired,descriptor

    def _profile(self, store, proposal, desired):
        owners = sorted({owner for file in desired.values() for owner in file['owner_ids']})
        selections = []
        for owner in owners:
            release = store.get('ModRelease',UUID(owner))
            selections.append(dict(release_id=owner,manifest_hash=release.manifest_hash,selected=True))
        profile = Profile(profile_id=uuid5(proposal.idempotency_key,'PSModManager.Rollback.Profile.v1'),
            installation_id=proposal.installation_id,revision=0,selections=selections,config_overrides={},display_order=[])
        store.put(profile)
        return PlanProposal(profile_id=profile.profile_id,expected_revision=0,idempotency_key=proposal.idempotency_key)

    def create(self, payload, *, check=lambda: None, commit=None):
        proposal = RollbackProposal.model_validate(payload)
        check = self.plans._budget(check)
        with self.plans._lock,self.plans._store(check) as store:
            old = store.connection.execute('SELECT payload FROM action_plans WHERE idempotency_key=?',
                (str(proposal.idempotency_key),)).fetchone()
            if old:
                plan = ActionPlan.model_validate_json(old['payload'])
                binding = self.binding(plan)
                if (plan.installation_id != proposal.installation_id or
                        binding['source_transaction_id'] != str(proposal.transaction_id) or
                        binding['current_generation'] != str(proposal.expected_generation)):
                    raise PlanRejected('PLAN_IDEMPOTENCY_CONFLICT')
                return self.plans._preview(*self.plans._revalidate(store,plan,check),0,20)
            desired,binding = self._derive(store,proposal.installation_id,proposal.transaction_id,proposal.expected_generation,check)
            plan_proposal = self._profile(store,proposal,desired)
            result = self.plans._calculate(store,plan_proposal,plan_id=uuid5(proposal.idempotency_key,'PSModManager.ActionPlan.v1'),
                expires_at=datetime.now(timezone.utc)+TTL,check=check,desired_override=desired,rollback_binding=binding)
            plan,baseline,_,_ = result
            preview = self.plans._preview(*result,0,20)
            store.put(baseline); store.plans.add(plan)
            check()
            if commit:
                commit(store.connection.commit)
            return preview

    def _revalidate(self, store, plan, check, session=None):
        binding = self.binding(plan)
        desired,actual = self._derive(store,plan.installation_id,UUID(binding['source_transaction_id']),
            UUID(binding['current_generation']),check,sealed=binding)
        profile = store.get('Profile',plan.profile_id)
        expected_owners = {owner for file in desired.values() for owner in file['owner_ids']}
        if expected_owners != {str(s.release_id) for s in profile.selections if s.selected}:
            raise PlanRejected('ROLLBACK_SOURCE_CHANGED')
        result = self.plans._calculate(store,PlanProposal(profile_id=plan.profile_id,expected_revision=plan.profile_revision,
            idempotency_key=plan.idempotency_key),plan_id=plan.plan_id,expires_at=plan.expires_at,
            baseline=store.get('BaselineSnapshot',plan.baseline_snapshot_id),check=check,session=session,
            desired_override=desired,rollback_binding=actual)
        if result[0].plan_hash != plan.plan_hash:
            raise PlanRejected('PLAN_STALE')
        return result

    @contextmanager
    def open_source(self, plan, operation):
        binding = self.binding(plan)
        backup = binding['backups'].get(target_key(operation.target_root_kind,operation.relative_path))
        if backup:
            relative = Path(backup['relative_path'])
            with self.executor._store() as store:
                transaction = store.get('Transaction', binding['source_transaction_id'])
                source_plan = store.get('ActionPlan', transaction.plan_id)
                installation = store.get('Installation', plan.installation_id)
            _, header = self.executor._header(Journal(self.paths.root, transaction.transaction_id), transaction, source_plan)
            with self.executor._transaction_files(installation, transaction, header) as (base, area):
                if relative.parent.as_posix() != area+'/backup':
                    raise PlanRejected('ROLLBACK_SOURCE_CHANGED')
                with private_dir(base,relative.parent.as_posix()) as chain:
                    handle = open_handle(relative.name,parent=chain.handles[-1])
                    try:
                        if (strong_hash(handle) != operation.desired_after or
                                file_identity(_information(handle,False)) != backup['identity']):
                            raise PlanRejected('ROLLBACK_SOURCE_CHANGED')
                        chain.check()
                        yield handle
                        chain.check()
                    finally:
                        handle.Close()
            return
        else:
            with self.executor._store() as store:
                artifact = store.get('Artifact',operation.desired_after)
            path = self.paths.root/'library'/artifact.library_relative_path
        with pinned_file(path) as handle:
            if strong_hash(handle) != operation.desired_after:
                raise PlanRejected('ROLLBACK_SOURCE_CHANGED')
            yield handle
