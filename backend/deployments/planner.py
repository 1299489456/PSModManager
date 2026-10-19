"""Immutable, persisted previews and native-user approvals. Never writes a game."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from uuid import UUID, uuid4, uuid5

from backend.contracts import ActionPlan, Approval, BaselineSnapshot, DeploymentGeneration, FileEntry, Profile
from backend.contracts.plans import ModAction, ModWorkspaceSave, PlanProposal, PlanIdentity, PlanPage, plan_digest
from backend.storage import Store
from backend.storage.package_repository import PackageRepository, content_hash
from backend.profiles.compatibility import evaluate_store
from .lease import InstallationLease
from .paths import (ROOTS, GuardRejected, TargetSnapshot, capture_target, pinned_chain, open_handle,
                    _information, file_identity, strong_hash, canonical_relative, target_path)
from .policy import (PlanRejected, target_key, writable_file, check_value, owned_after, capacity,
                     native_approval_id, MAX_MANAGED_FILES)


TTL = timedelta(minutes=10)
WORKSPACE_TTL = timedelta(minutes=30)
MAX_FILES = 128
MAX_BYTES = 512 * 1024 * 1024


class PlanService:
    def __init__(self, paths, guards):
        self.paths, self.guards = paths, guards
        self.library = paths.root / 'library'
        self._lock = threading.RLock()
        self.rollback = None

    @contextmanager
    def _store(self, check, *, verify_library_bytes=True):
        self.paths.check_owned_paths()
        store = Store(self.paths.database, library_root=self.library, verification_check=check,
                      validate=False, verify_library_bytes=verify_library_bytes)
        deferred = None
        try:
            # Metadata stays fixed while hashing. All failed writes roll back.
            store.connection.execute('BEGIN IMMEDIATE')
            yield store
            check()
            store.connection.commit()
        except BaseException as error:
            store.connection.rollback()
            deferred = getattr(error, '_preserve', None)
            raise
        finally:
            store.close()
            if deferred is not None:
                deferred()

    @staticmethod
    def _budget(check, *, seconds=120):
        # Nested planner/executor calls share the outer operation's deadline.
        # Workspace saves explicitly budget durable writes for a complete Mod;
        # ordinary actions continue to use the 120-second default.
        deadline = getattr(check, '_plan_deadline', time.monotonic() + seconds)
        def bounded():
            check()
            if time.monotonic() > deadline:
                raise PlanRejected('PLAN_LIMIT')
        bounded._plan_deadline = deadline
        return bounded

    @staticmethod
    def _draft(store, profile):
        row = store.connection.execute('SELECT revision,committed_revision,payload FROM profile_drafts WHERE profile_id=?',
                                       (str(profile.profile_id),)).fetchone()
        if not row:
            return None
        data = json.loads(row['payload'])
        # Draft and public Profile revisions are separate counters.
        if row['committed_revision'] is None or data['current'] != data['committed']:
            raise PlanRejected('PLAN_PROFILE_DIRTY')
        return row['revision']

    @staticmethod
    def _scan(root, installation_identity, check, *, scope=None):
        """Hash affected targets; legacy plans retain their original full scan."""
        files, snapshots = {}, {}
        total = entry_count = 0
        if scope is not None:
            for target in scope:
                check()
                kind, relative = target['target_root_kind'], target['relative_path']
                key = target_key(kind, relative)
                if key in files:
                    raise PlanRejected('PLAN_CORRUPT')
                path = target_path(root, kind, relative)
                try:
                    with pinned_chain(path.parent) as chain:
                        if dict(chain).get(str(root).casefold()) != installation_identity:
                            raise GuardRejected('GUARD_INSTALLATION_CHANGED')
                        try:
                            handle = open_handle(path, parent=chain.handles[-1])
                        except FileNotFoundError:
                            continue
                        try:
                            info = _information(handle, False)
                            size = (info[5] << 32) | info[6]
                            total += size
                            if len(files) >= MAX_MANAGED_FILES or total > MAX_BYTES:
                                raise PlanRejected('PLAN_LIMIT')
                            digest = strong_hash(handle)
                            snapshot = TargetSnapshot(installation_identity, str(root), kind, relative,
                                tuple(chain), file_identity(info), digest)
                        finally:
                            handle.Close()
                        chain.check()
                except FileNotFoundError:
                    continue
                files[key] = dict(target_root_kind=kind, relative_path=relative, sha256=digest, size=size)
                snapshots[key] = snapshot
            return files, snapshots
        def walk(directory, kind, prefix=''):
            nonlocal total,entry_count
            with pinned_chain(directory) as chain:
                with os.scandir(directory) as entries:
                    names = []
                    for entry in entries:
                        check()
                        entry_count += 1
                        if entry_count>4096:
                            raise PlanRejected('PLAN_LIMIT')
                        names.append(entry.name)
                    names.sort()
                for name in names:
                    check()
                    relative = prefix + name
                    canonical_relative(relative)
                    path = directory / name
                    # Open a directory relative to its pinned parent; never follow links.
                    try:
                        handle = open_handle(path, directory=True, parent=chain.handles[-1])
                    except GuardRejected as exc:
                        if exc.code != 'GUARD_PATH_UNAVAILABLE':
                            raise
                        handle = None
                    except OSError as exc:
                        # STATUS_NOT_A_DIRECTORY is returned for ordinary files.
                        if getattr(exc, 'winerror', None) != 267:
                            raise
                        handle = None
                    if handle is not None:
                        handle.Close()
                        walk(path, kind, relative + '/')
                        continue
                    handle = open_handle(path, parent=chain.handles[-1])
                    try:
                        info = _information(handle, False)
                        size = (info[5] << 32) | info[6]
                        digest = strong_hash(handle)
                        snapshot = TargetSnapshot(installation_identity,str(root),kind,relative,
                            tuple(chain),file_identity(info),digest)
                    finally:
                        handle.Close()
                    total += size
                    key = target_key(kind, relative)
                    if key in files or len(files) >= MAX_FILES or total > MAX_BYTES:
                        raise PlanRejected('PLAN_LIMIT')
                    files[key] = dict(target_root_kind=kind, relative_path=relative,
                                      sha256=snapshot.before_hash, size=size)
                    snapshots[key] = snapshot
                chain.check()
        for kind, name in ROOTS.items():
            directory = root / name
            # Missing roots are observed as empty, but new targets still need a parent.
            try:
                with pinned_chain(directory):
                    pass
            except FileNotFoundError:
                continue
            walk(directory, kind)
        return files, snapshots

    @staticmethod
    def scan_scope(plan):
        """Scope is sealed into a new direct action; absent means legacy full scan."""
        fields = [item.expected for item in plan.preconditions if item.kind == 'target_scope']
        if not fields:
            return None
        try:
            if len(fields) != 1:
                raise ValueError()
            scope = json.loads(fields[0])
            if not isinstance(scope, list) or len(scope) > MAX_MANAGED_FILES:
                raise ValueError()
            keys = []
            for target in scope:
                if set(target) != {'target_root_kind', 'relative_path'}:
                    raise ValueError()
                keys.append(target_key(target['target_root_kind'], target['relative_path']))
            if keys != sorted(set(keys)):
                raise ValueError()
            return scope
        except (ValueError, TypeError, KeyError):
            raise PlanRejected('PLAN_CORRUPT') from None

    @staticmethod
    def _adoption_targets(store, installation_id, root, check, *, after=None):
        # These paths only determine what to observe. _adopted subsequently
        # verifies every receipt, baseline and immutable package before ownership.
        targets = {}
        rows = store.connection.execute('''SELECT f.payload,a.adopted_at FROM file_entries f
            JOIN package_adoptions a ON a.release_id=f.release_id
            JOIN baseline_snapshots b ON b.snapshot_id=a.snapshot_id
            WHERE b.installation_id=?''', (str(installation_id),))
        for row in rows:
            check()
            if after is not None and datetime.fromisoformat(row['adopted_at']) <= after:
                continue
            file = FileEntry.model_validate_json(row['payload']).model_dump(mode='json')
            key = writable_file(file, root)
            targets[key] = dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'])
        return targets

    @staticmethod
    def _baseline_files(baseline, installation_id, expected_hash):
        if (baseline.installation_id!=installation_id or baseline.coverage!='complete'
                or baseline.manifest_hash!=expected_hash
                or content_hash([f.model_dump(exclude={'schema_version'}) for f in baseline.files])!=expected_hash):
            raise PlanRejected('PLAN_GENERATION_INVALID')
        observed = {}
        for file in baseline.files:
            try:
                name,relative = file.relative_path.split('/',1)
                kind = {v.casefold():k for k,v in ROOTS.items()}[name.casefold()]
                key = target_key(kind,relative)
            except (KeyError,ValueError):
                raise PlanRejected('PLAN_GENERATION_INVALID') from None
            if key in observed:
                raise PlanRejected('PLAN_GENERATION_INVALID')
            observed[key] = dict(target_root_kind=kind,relative_path=relative,sha256=file.sha256,size=file.size)
        return observed

    @staticmethod
    def _receipt_facts(store, installation_id):
        rows = store.connection.execute('''SELECT a.snapshot_id,a.release_id,a.adopted_at FROM package_adoptions a
            JOIN baseline_snapshots b ON b.snapshot_id=a.snapshot_id
            WHERE b.installation_id=? ORDER BY a.snapshot_id,a.release_id LIMIT 201''', (str(installation_id),)).fetchall()
        if len(rows)>200:
            raise PlanRejected('PLAN_LIMIT')
        facts = [dict(row) for row in rows]
        try:
            for row in facts:
                UUID(row['snapshot_id']); UUID(row['release_id'])
                if len(row['adopted_at'])>64 or datetime.fromisoformat(row['adopted_at']).utcoffset()!=timedelta(0):
                    raise ValueError()
        except (ValueError,TypeError):
            raise PlanRejected('PLAN_OWNERSHIP_INVALID') from None
        return facts

    @staticmethod
    def _check_receipt_seal(plan, current):
        try:
            sealed = json.loads(check_value(plan,'adoption_receipts'))
            if not isinstance(sealed,list) or len(sealed)>200:
                raise ValueError()
            indexed = {(r['snapshot_id'],r['release_id']):r for r in current}
            for receipt in sealed:
                if (set(receipt)!= {'snapshot_id','release_id','adopted_at'}
                        or indexed.get((receipt['snapshot_id'],receipt['release_id']))!=receipt):
                    raise ValueError()
        except (ValueError,KeyError,TypeError):
            raise PlanRejected('PLAN_OWNERSHIP_INVALID') from None

    @staticmethod
    def _adopted(store, installation_id, disk, root, library, check, *, after=None, before=None):
        receipts = store.connection.execute('''SELECT a.snapshot_id,a.release_id,a.adopted_at FROM package_adoptions a
            JOIN baseline_snapshots b ON b.snapshot_id=a.snapshot_id
            WHERE b.installation_id=? LIMIT 201''', (str(installation_id),)).fetchall()
        if len(receipts)>200:
            raise PlanRejected('PLAN_LIMIT')
        output = {}
        try:
            for receipt in receipts:
                check()
                observed_at = datetime.fromisoformat(receipt['adopted_at'])
                if observed_at.utcoffset()!=timedelta(0):
                    raise ValueError()
                if (after and observed_at<=after) or (before and observed_at>before):
                    continue
                baseline = store.get('BaselineSnapshot', receipt['snapshot_id'])
                if (baseline.coverage!='complete' or baseline.installation_id!=installation_id
                        or content_hash([f.model_dump(exclude={'schema_version'}) for f in baseline.files])!=baseline.manifest_hash):
                    raise ValueError()
                declared = {}
                for item in baseline.files:
                    name, relative = item.relative_path.split('/',1)
                    kinds = {v.casefold():k for k,v in ROOTS.items()}
                    key = target_key(kinds[name.casefold()],relative)
                    if key in declared:
                        raise ValueError()
                    declared[key] = item.sha256
                release = PackageRepository(store,library).release(receipt['release_id'],check=check)
                for raw in release['files']:
                    file = FileEntry.model_validate(raw).model_dump(mode='json')
                    key = writable_file(file,root)
                    if declared.get(key)!=file['source_hash']:
                        raise ValueError()
                    current = disk.get(key)
                    if current and current['sha256']==file['source_hash']:
                        found = output.setdefault(key,dict(current,owner_ids=[]))
                        if file['release_id'] not in found['owner_ids']:
                            found['owner_ids'].append(file['release_id'])
        except (KeyError,ValueError,OSError):
            raise PlanRejected('PLAN_OWNERSHIP_INVALID') from None
        for file in output.values():
            file['owner_ids'].sort()
        return output

    def _generation(self, store, installation_id, root, check, *, frames=None, allow_blocked=False):
        row = store.connection.execute('SELECT payload FROM deployment_generations WHERE installation_id=? ORDER BY sequence DESC LIMIT 1',
                                       (str(installation_id),)).fetchone()
        if not row:
            return None, None
        generation = DeploymentGeneration.model_validate_json(row['payload'])
        transaction = store.get('Transaction', generation.transaction_id)
        plan = store.get('ActionPlan', transaction.plan_id)
        states = {'committed', 'recovery_required'} if allow_blocked else {'committed'}
        if (transaction.state not in states or transaction.target_generation != generation.generation_id
                or plan.installation_id != generation.installation_id or plan_digest(plan) != plan.plan_hash):
            raise PlanRejected('PLAN_GENERATION_INVALID')
        files = owned_after(plan)
        if content_hash(list(files.values())) != generation.manifest_hash:
            raise PlanRejected('PLAN_GENERATION_INVALID')
        # A generation produced by arbitrary DTO registration is not provenance.
        # Follow the actual committed chain and verify each native approval and
        # file/owner transition. Disk effects of the fixture are not executor proof.
        # Ordinary use must not stop after 100 successful clicks. The caller's
        # existing time/file budgets still bound verification of this chain.
        history = store.connection.execute('SELECT payload FROM deployment_generations WHERE installation_id=? ORDER BY sequence',
                                           (str(installation_id),)).fetchall()
        receipt_facts = self._receipt_facts(store,installation_id)
        previous, managed = None, None
        for row in history:
            check()
            current = DeploymentGeneration.model_validate_json(row['payload'])
            transaction = store.get('Transaction',current.transaction_id)
            item = store.get('ActionPlan',transaction.plan_id)
            lifetime = self.plan_lifetime(item)
            if (current.sequence!=(previous.sequence+1 if previous else 1) or transaction.state not in states
                    or transaction.target_generation!=current.generation_id or item.installation_id!=installation_id
                    or item.base_generation!=(previous.generation_id if previous else None)
                    or plan_digest(item)!=item.plan_hash or not item.operations
                    or not item.expires_at-lifetime<=current.committed_at<=item.expires_at
                    or (previous and current.committed_at<previous.committed_at)):
                raise PlanRejected('PLAN_GENERATION_INVALID')
            approval_rows = store.connection.execute('SELECT payload FROM approvals WHERE plan_id=? AND plan_hash=?',
                (str(item.plan_id),item.plan_hash)).fetchall()
            if len(approval_rows)!=1:
                raise PlanRejected('PLAN_GENERATION_INVALID')
            self._approval(item,Approval.model_validate_json(approval_rows[0]['payload']),historical=True)
            self._check_receipt_seal(item,receipt_facts)
            after = owned_after(item)
            if content_hash(list(after.values()))!=current.manifest_hash or check_value(item,'result_manifest')!=current.manifest_hash:
                raise PlanRejected('PLAN_GENERATION_INVALID')
            try:
                baseline = store.get('BaselineSnapshot',item.baseline_snapshot_id)
            except (ValueError,KeyError):
                raise PlanRejected('PLAN_GENERATION_INVALID') from None
            observed = self._baseline_files(baseline,installation_id,item.baseline_manifest_hash)
            is_ordinary = any(p.kind == 'execution_policy' and p.expected == 'ordinary-mods-v1' for p in item.preconditions)
            if is_ordinary:
                # Ordinary actions attest their whole-action ownership snapshot;
                # they do not re-read every historical package on each click.
                from .ordinary import before_owned
                managed = before_owned(item)
            elif managed is None:
                managed = self._adopted(store,installation_id,observed,root,self.library,check,before=item.expires_at-lifetime)
            else:
                newly_adopted = self._adopted(store,installation_id,observed,root,self.library,check,
                    after=previous.committed_at,before=item.expires_at-lifetime)
                managed = self._merge_owners(managed,newly_adopted)
            if content_hash(managed)!=check_value(item,'owned_before'):
                raise PlanRejected('PLAN_GENERATION_INVALID')
            expected = {}
            for key in sorted(managed.keys()|after.keys()):
                old,new = managed.get(key),after.get(key)
                explicit_owners = any(p.kind == 'owner_policy' and p.expected == 'explicit-v1' for p in item.preconditions)
                if old and new and old['sha256']==new['sha256'] and (not explicit_owners or old['owner_ids']==new['owner_ids']):
                    continue
                expected[key] = ('replace_owned' if old and new else ('remove_owned' if old else 'install_owned'),
                    old['sha256'] if old else 'absent',new['sha256'] if new else 'absent',
                    new['owner_ids'] if new else old['owner_ids'])
            actual = {target_key(op.target_root_kind,op.relative_path):(op.kind,op.expected_before,op.desired_after,
                [str(owner) for owner in op.owner_ids]) for op in item.operations}
            if len(actual)!=len(item.operations) or actual!=expected:
                raise PlanRejected('PLAN_GENERATION_INVALID')
            if frames is not None:
                frames.append(dict(generation=current,transaction=transaction,plan=item,
                    before_owned={key:dict(file,owner_ids=list(file['owner_ids'])) for key,file in managed.items()},
                    after_owned=after))
            managed,previous = after,current
        return generation, files

    @staticmethod
    def _merge_owners(managed, adopted):
        managed = {key:dict(file,owner_ids=list(file['owner_ids'])) for key,file in managed.items()}
        for key,file in adopted.items():
            if key in managed:
                if file['sha256']!=managed[key]['sha256']:
                    raise PlanRejected('PLAN_MANAGED_DRIFT')
                managed[key]['owner_ids'] = sorted(set(managed[key]['owner_ids']+file['owner_ids']))
            else:
                managed[key] = file
        return managed

    def _calculate(self, store, proposal, *, plan_id, expires_at, baseline=None, check, session=None,
                   desired_override=None, rollback_binding=None, direct_binding=None, scoped=None):
        profile = store.get('Profile', proposal.profile_id)
        if profile.revision != proposal.expected_revision:
            raise PlanRejected('PLAN_PROFILE_STALE')
        if profile.config_overrides:
            raise PlanRejected('PLAN_CONFIG_UNSUPPORTED')
        draft_revision = self._draft(store, profile)
        report = evaluate_store(store, str(profile.profile_id), profile.revision, check=check)
        if report['blocking'] and direct_binding is None:
            raise PlanRejected('PLAN_COMPATIBILITY_BLOCKED')
        with self.guards._installation(str(profile.installation_id)) as (installation, _):
            self.guards._check_recovery(installation, session.permit if session is not None else None)
            if session is not None:
                if (session.service is not self.guards or session.closed or session.intent != ('rollback' if rollback_binding else 'apply')
                        or session.installation != installation):
                    raise GuardRejected('GUARD_LEASE_LOST')
                session.check()
            with nullcontext(session.lease) if session is not None else InstallationLease(installation.file_identity):
                root = Path(installation.canonical_root)
                generation, managed = self._generation(store, installation.installation_id, root, check)
                desired, library_bytes = {}, 0
                repository = PackageRepository(store, self.library)
                for selection in profile.selections:
                    if not selection.selected:
                        continue
                    verified = repository.release(selection.release_id, selection.manifest_hash, check=check)
                    for file in verified['files']:
                        check()
                        file = FileEntry.model_validate(file).model_dump(mode='json')
                        key = writable_file(file, root)
                        size = store.get('Artifact', file['source_hash']).size
                        library_bytes += size
                        if library_bytes > MAX_BYTES:
                            raise PlanRejected('PLAN_LIMIT')
                        if key in desired:
                            if desired[key]['sha256'] != file['source_hash']:
                                raise PlanRejected('PLAN_TARGET_CONFLICT')
                            desired[key]['owner_ids'].append(str(selection.release_id))
                        else:
                            desired[key] = dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                                sha256=file['source_hash'], size=size, owner_ids=[str(selection.release_id)])
                scope = None
                use_scope = direct_binding is not None if scoped is None else scoped
                if use_scope:
                    targets = self._adoption_targets(store, installation.installation_id, root, check,
                        after=generation.committed_at if generation else None)
                    targets.update(managed or {})
                    targets.update(desired)
                    scope = [dict(target_root_kind=targets[key]['target_root_kind'],
                                  relative_path=targets[key]['relative_path']) for key in sorted(targets)]
                    if len(scope) > MAX_MANAGED_FILES:
                        raise PlanRejected('PLAN_LIMIT')
                disk, snapshots = self._scan(root, installation.file_identity, check, scope=scope)
                if managed is None:
                    managed = self._adopted(store, installation.installation_id, disk, root, self.library, check)
                else:
                    managed = self._merge_owners(managed,self._adopted(store, installation.installation_id,
                        disk,root,self.library,check,after=generation.committed_at))
                if len(disk.keys() | desired.keys() | managed.keys()) > (MAX_MANAGED_FILES if use_scope else MAX_FILES):
                    raise PlanRejected('PLAN_LIMIT')
                for file in desired.values():
                    file['owner_ids'] = sorted(set(file['owner_ids']))
                if desired_override is not None:
                    # Host-derived selective undo; owners and bytes must still be
                    # authenticated members of the selected immutable releases.
                    for key,file in desired_override.items():
                        selected = desired.get(key)
                        if (not selected or selected['sha256'] != file['sha256'] or selected['size'] != file['size']
                                or not set(file['owner_ids']).issubset(selected['owner_ids'])):
                            raise PlanRejected('ROLLBACK_SOURCE_INVALID')
                    desired = desired_override
                rows, operations = [], []
                staging = backup_bytes = growth = 0
                for key in sorted(disk.keys() | desired.keys() | managed.keys()):
                    check()
                    before, after, owner = disk.get(key), desired.get(key), managed.get(key)
                    file = before or after or owner
                    conflict = dict(target_root_kind=file['target_root_kind'],relative_path=file['relative_path'],
                        change='conflict',before=before['sha256'] if before else 'absent',
                        after=after['sha256'] if after else 'absent')
                    if owner and (not before or before['sha256'] != owner['sha256']):
                        raise PlanRejected('PLAN_MANAGED_DRIFT',[conflict])
                    if before and not owner and after:
                        # Even identical bytes require an explicit adoption receipt.
                        raise PlanRejected('PLAN_UNMANAGED_CONFLICT',[conflict])
                    change = 'keep'
                    if after and not before:
                        change = 'add'
                    elif after and (before['sha256'] != after['sha256'] or owner and owner['owner_ids'] != after['owner_ids']):
                        change = 'replace'
                    elif owner and not after:
                        change = 'remove'
                    snapshot = snapshots[key] if key in disk and change == 'keep' else capture_target(
                        root, installation.file_identity, file['target_root_kind'], file['relative_path'])
                    snapshots[key] = snapshot
                    if snapshot.before_hash != (before['sha256'] if before else None):
                        raise PlanRejected('PLAN_DISK_CHANGED')
                    row = dict(target_root_kind=file['target_root_kind'], relative_path=file['relative_path'], change=change,
                        before=before['sha256'] if before else 'absent', after=(after['sha256'] if after else
                            (before['sha256'] if change == 'keep' and before else 'absent')),
                        before_owner_ids=owner['owner_ids'] if owner else [], owner_ids=after['owner_ids'] if after else [],
                        size_before=before['size'] if before else 0, size_after=after['size'] if after else (before['size'] if change == 'keep' and before else 0),
                        backup_required=change in {'replace', 'remove'})
                    rows.append(row)
                    if change != 'keep':
                        operations.append(dict(operation_id=uuid5(proposal.idempotency_key, key),
                            kind={'add':'install_owned','replace':'replace_owned','remove':'remove_owned'}[change],
                            target_root_kind=file['target_root_kind'], relative_path=file['relative_path'],
                            expected_before=row['before'], desired_after=row['after'], owner_ids=row['owner_ids'] or row['before_owner_ids']))
                        staging += row['size_after']
                        backup_bytes += row['size_before']
                        growth += max(0, row['size_after'] - row['size_before'])
                operation_limit = 4000 if direct_binding is not None and (
                    'enabled_ids' in direct_binding or direct_binding.get('action') == 'uninstall') else MAX_FILES
                if len(operations) > operation_limit:
                    raise PlanRejected('PLAN_LIMIT')
                # Recheck the same affected scope immediately before sealing it.
                again, identities = self._scan(root, installation.file_identity, check, scope=scope)
                if disk != again or any(snapshots[k] != identities[k] for k in disk):
                    raise PlanRejected('PLAN_DISK_CHANGED')
                baseline_files = [dict(relative_path=ROOTS[f['target_root_kind']]+'/'+f['relative_path'], sha256=f['sha256'], size=f['size'])
                                  for f in sorted(disk.values(), key=lambda f: ROOTS[f['target_root_kind']]+'/'+f['relative_path'])]
                if baseline is None:
                    baseline = BaselineSnapshot(snapshot_id=uuid5(plan_id, 'baseline'), installation_id=installation.installation_id,
                        scan_generation=0, manifest_hash=content_hash(baseline_files), files=baseline_files, coverage='complete')
                if (baseline.coverage != 'complete' or baseline.installation_id != installation.installation_id
                        or content_hash([f.model_dump(exclude={'schema_version'}) for f in baseline.files]) != baseline.manifest_hash
                        or content_hash(baseline_files) != baseline.manifest_hash):
                    raise PlanRejected('PLAN_BASELINE_STALE')
                with pinned_chain(self.paths.root) as chain:
                    data_volume = chain[-1][1].split(':')[0]
                    target_volume = installation.file_identity.split(':')[0]
                    available = {target_volume: shutil.disk_usage(root).free, data_volume: shutil.disk_usage(self.paths.root).free}
                    space = capacity(set(available), library_volume=data_volume, target_volume=target_volume,
                        backup_volume=target_volume, staging=staging, backup=backup_bytes, target_growth=growth,
                        recovery=backup_bytes if data_volume != target_volume else 0, available=available)
                    chain.check()
                snapshot_hash = content_hash([dict(root_kind=s.root_kind, relative_path=s.relative_path,
                    ancestors=list(s.ancestors), target_identity=s.target_identity, before_hash=s.before_hash) for _,s in sorted(snapshots.items())])
                after_files = [desired[key] for key in sorted(desired)]
                preconditions = [dict(kind=k,reference='installation',expected=v) for k,v in (
                    ('disk_snapshot',snapshot_hash), ('diff',content_hash(rows)), ('owned_before',content_hash(managed)),
                    ('owned_after',json.dumps(after_files,sort_keys=True,separators=(',',':'))),
                    ('result_manifest',content_hash(after_files)), ('draft_revision',draft_revision),
                    ('adoption_receipts',json.dumps(self._receipt_facts(store,installation.installation_id),sort_keys=True,separators=(',',':'))),
                    ('compatibility',report['report_hash']), ('space_policy',content_hash({k:{x:y for x,y in v.items() if x!='available'} for k,v in space.items()})),
                    ('data_identity',chain[-1][1]), ('owner_policy','explicit-v1'))]
                if rollback_binding is not None:
                    preconditions.append(dict(kind='rollback_source',reference='transaction',
                        expected=json.dumps(rollback_binding,sort_keys=True,separators=(',',':'))))
                if direct_binding is not None:
                    preconditions.append(dict(kind='direct_mod_action', reference='mod',
                        expected=json.dumps(direct_binding,sort_keys=True,separators=(',',':'))))
                if scope is not None:
                    preconditions.append(dict(kind='target_scope', reference='installation',
                        expected=json.dumps(scope,sort_keys=True,separators=(',',':'))))
                plan = ActionPlan(plan_id=plan_id, installation_id=installation.installation_id, profile_id=profile.profile_id,
                    profile_revision=profile.revision, base_generation=generation.generation_id if generation else None,
                    baseline_snapshot_id=baseline.snapshot_id, baseline_manifest_hash=baseline.manifest_hash,
                    fingerprint_id=installation.fingerprint_id, plan_hash='0'*64, idempotency_key=proposal.idempotency_key,
                    operations=operations, preconditions=preconditions,
                    verification_checks=[dict(kind='target_hash',reference=ROOTS[r['target_root_kind']]+'/'+r['relative_path'],expected=r['after']) for r in rows]
                        + [dict(kind='runtime',reference='new_session',expected='not_run')],
                    evidence_ids=[], disk_estimate_by_volume={k:v['required'] for k,v in space.items()},
                    backup_scope=[ROOTS[r['target_root_kind']]+'/'+r['relative_path'] for r in rows if r['backup_required']],
                    restart_required=bool(operations), approval_scope=sorted(set(op['kind'] for op in operations)), expires_at=expires_at)
                plan.plan_hash = plan_digest(plan)
                if len(plan.model_dump_json().encode('utf-8')) > (8*1024*1024 if use_scope else 128*1024):
                    raise PlanRejected('PLAN_LIMIT')
                return plan, baseline, rows, space

    def create(self, proposal, *, check=lambda: None, commit=None):
        proposal = PlanProposal.model_validate(proposal)
        check = self._budget(check)
        with self._lock, self._store(check) as store:
            found = store.connection.execute('SELECT payload FROM action_plans WHERE idempotency_key=?',
                                             (str(proposal.idempotency_key),)).fetchone()
            old = ActionPlan.model_validate_json(found['payload']) if found else None
            if old:
                if old.profile_id != proposal.profile_id or old.profile_revision != proposal.expected_revision:
                    raise PlanRejected('PLAN_IDEMPOTENCY_CONFLICT')
                result = self._revalidate(store, old, check)
                return self._preview(*result, 0, 20)
            plan_id = uuid5(proposal.idempotency_key, 'PSModManager.ActionPlan.v1')
            result = self._calculate(store, proposal, plan_id=plan_id, expires_at=datetime.now(timezone.utc)+TTL, check=check)
            plan, baseline, _, _ = result
            preview = self._preview(*result, 0, 20)
            store.put(baseline)
            store.plans.add(plan)
            check()
            if commit:
                commit(store.connection.commit)
            return preview

    def _revalidate(self, store, plan, check, session=None):
        if plan_digest(plan) != plan.plan_hash:
            raise PlanRejected('PLAN_CORRUPT')
        if datetime.now(timezone.utc) >= plan.expires_at:
            raise PlanRejected('PLAN_EXPIRED')
        if any(item.kind == 'rollback_source' for item in plan.preconditions):
            if self.rollback is None:
                raise PlanRejected('ROLLBACK_SOURCE_INVALID')
            return self.rollback._revalidate(store, plan, check, session)
        baseline = store.get('BaselineSnapshot', plan.baseline_snapshot_id)
        result = self._calculate(store, PlanProposal(profile_id=plan.profile_id, expected_revision=plan.profile_revision,
            idempotency_key=plan.idempotency_key), plan_id=plan.plan_id, expires_at=plan.expires_at, baseline=baseline, check=check, session=session,
            direct_binding=self.direct_binding(plan), scoped=self.scan_scope(plan) is not None)
        if result[0].plan_hash != plan.plan_hash:
            raise PlanRejected('PLAN_STALE')
        return result

    @staticmethod
    def _preview(plan, baseline, rows, space, offset, limit):
        header = plan.model_dump(mode='json',exclude={'operations','preconditions','verification_checks','backup_scope'})
        header.update(precondition_count=len(plan.preconditions),verification_check_count=len(plan.verification_checks),
                      backup_count=len(plan.backup_scope))
        page = rows[offset:offset+limit]
        while True:
            header['backup_scope'] = [ROOTS[r['target_root_kind']]+'/'+r['relative_path'] for r in page if r['backup_required']]
            output = dict(plan=header,files=page,total=len(rows),
                next_offset=offset+len(page) if offset+len(page)<len(rows) else None,no_op=not plan.operations,
                can_approve=bool(plan.operations),configuration_changes=[],configuration_policy='unsupported',space=space,
                execution_available=False,boundary='Preview/approval only; executor and runtime verification are separate tasks.')
            source = [item for item in plan.preconditions if item.kind == 'rollback_source']
            if source:
                binding = json.loads(check_value(plan, 'rollback_source'))
                output.update(intent='rollback',source_transaction_id=binding['source_transaction_id'])
            if len(json.dumps(output,ensure_ascii=False,allow_nan=False).encode('utf-8'))<=30000:
                return output
            if len(page)<=1:
                raise PlanRejected('PLAN_LIMIT')
            page = page[:-1]

    def inspect(self, payload, *, check=lambda: None):
        payload = PlanPage.model_validate(payload)
        check = self._budget(check)
        with self._lock, self._store(check) as store:
            plan = store.get('ActionPlan', payload.plan_id)
            if plan.plan_hash != payload.plan_hash:
                raise PlanRejected('PLAN_HASH_MISMATCH')
            result = self._revalidate(store, plan, check)
            return self._preview(*result, payload.offset, payload.limit)

    def approve(self, payload, *, confirm, check=lambda: None, commit=None):
        """confirm is a host-owned native dialog callback, never a request field."""
        payload = PlanIdentity.model_validate(payload)
        cancellation_check = check
        # Validate first, release DB/lease while a person reads the native prompt.
        preview = self.inspect(payload.model_dump(), check=cancellation_check)
        if preview['no_op']:
            return dict(approval=None, approved=False, no_op=True, execution_available=False)
        if confirm(preview) is not True:
            return dict(approval=None, approved=False, no_op=False, execution_available=False)
        check = self._budget(cancellation_check)
        with self._lock, self._store(check) as store:
            plan = store.get('ActionPlan', payload.plan_id)
            result = self._revalidate(store, plan, check)
            if plan.plan_hash != payload.plan_hash:
                raise PlanRejected('PLAN_HASH_MISMATCH')
            # Approving does not execute, but game/lease/recovery must be safe now.
            with self.guards.operation(str(plan.installation_id), 'rollback' if preview.get('intent') == 'rollback' else 'apply'):
                rows = store.connection.execute('SELECT payload FROM approvals WHERE plan_id=? AND plan_hash=?',
                    (str(plan.plan_id),plan.plan_hash)).fetchall()
                if len(rows)>1:
                    raise PlanRejected('PLAN_APPROVAL_INVALID')
                approval = Approval.model_validate_json(rows[0]['payload']) if rows else Approval(
                    approval_id=native_approval_id(self.paths.root,plan.plan_hash,create=True), plan_id=plan.plan_id, plan_hash=plan.plan_hash,
                    actor='native-user', scope=plan.approval_scope, expires_at=plan.expires_at)
                self._approval(plan, approval)
                store.plans.add(approval)
                check()
                if commit:
                    commit(store.connection.commit)
                return dict(approval=approval.model_dump(mode='json'), approved=True, no_op=False, execution_available=False)

    def _approval(self, plan, approval, *, historical=False):
        direct = approval.actor == 'direct-action' and self.direct_binding(plan) is not None
        if (approval.plan_id != plan.plan_id or approval.plan_hash != plan.plan_hash or (approval.actor != 'native-user' and not direct)
                or approval.scope != plan.approval_scope or approval.expires_at != plan.expires_at
                or approval.approval_id != native_approval_id(self.paths.root,plan.plan_hash)
                or (not historical and datetime.now(timezone.utc) >= approval.expires_at) or not plan.operations):
            raise PlanRejected('PLAN_APPROVAL_INVALID')

    @staticmethod
    def direct_state_id(installation_id):
        return uuid5(UUID(str(installation_id)), 'PSModManager.direct-mods.state.v1')

    @classmethod
    def plan_lifetime(cls, plan):
        binding = cls.direct_binding(plan)
        large_action = binding is not None and len(plan.operations) > MAX_FILES and (
            binding.get('action') == 'uninstall' or any(p.kind == 'execution_policy' and p.expected == 'ordinary-mods-v1' for p in plan.preconditions))
        return WORKSPACE_TTL if binding is not None and ('enabled_ids' in binding or large_action) else TTL

    @classmethod
    def direct_binding(cls, plan):
        values = [p.expected for p in plan.preconditions if p.kind == 'direct_mod_action']
        if not values:
            return None
        try:
            if len(values) != 1:
                raise ValueError()
            binding = json.loads(values[0])
            revision = binding.pop('state_revision')
            request = (ModWorkspaceSave if 'enabled_ids' in binding else ModAction).model_validate(binding)
            if (type(revision) is not int or revision < 0 or request.installation_id != plan.installation_id
                    or request.idempotency_key != plan.idempotency_key
                    or plan.profile_id != uuid5(request.idempotency_key, 'PSModManager.direct-mods.profile.v1')):
                raise ValueError()
            return request.model_dump(mode='json') | dict(state_revision=revision)
        except (ValueError, TypeError, KeyError):
            raise PlanRejected('PLAN_CORRUPT') from None

    def commit_direct_state(self, store, plan):
        """Publish membership with the verified file commit, also during recovery."""
        binding = self.direct_binding(plan)
        if binding is None:
            return
        proposed = store.get('Profile', plan.profile_id)
        state_id = self.direct_state_id(plan.installation_id)
        expected = binding['state_revision']
        try:
            current = store.get('Profile', state_id)
        except KeyError:
            current = None
        result = proposed.model_copy(update=dict(profile_id=state_id, revision=expected+1))
        if 'enabled_ids' in binding:
            result.workspace_order = [UUID(value) for value in binding['enabled_ids']]
        elif current is not None:
            result.workspace_order = current.workspace_order
        if current is not None and current == result:
            return  # Repeated recovery of the same committed operation.
        if (current.revision if current else 0) != expected:
            raise PlanRejected('MOD_STATE_CHANGED')
        if current is None:
            store.put(result)
        else:
            store.profiles.update(result, expected_revision=expected)

    def validate_approval(self, payload, approval_id, *, check=lambda: None):
        """Read-only prerequisite for the future executor; does not authorize a write."""
        payload = PlanIdentity.model_validate(payload)
        check = self._budget(check)
        with self._lock, self._store(check) as store:
            plan = store.get('ActionPlan', payload.plan_id)
            if plan.plan_hash != payload.plan_hash:
                raise PlanRejected('PLAN_HASH_MISMATCH')
            self._revalidate(store, plan, check)
            try:
                approval = store.get('Approval', UUID(str(approval_id)))
            except (KeyError, ValueError):
                raise PlanRejected('PLAN_APPROVAL_INVALID') from None
            self._approval(plan, approval)
            return plan, approval


class PlanProposalConsumer:
    """Bounded AI tool consumer delegates to exactly the UI's planner.

    Models can request a preview/inspection. Approval stays on the native human
    bridge; calling it there still displays the same native confirmation.
    """
    def __init__(self, service):
        self.service = service

    def preview(self, proposal, *, check=lambda: None, commit=None):
        return self.service.create(proposal, check=check,commit=commit)

    def inspect(self, payload, *, check=lambda: None):
        return self.service.inspect(payload, check=check)
