"""Ordinary Mod actions and the compatible legacy list contract."""
from pathlib import Path

from backend.contracts import Profile
from backend.contracts.plans import ModAction
from backend.storage.package_repository import PackageRepository
from .planner import WORKSPACE_TTL
from .policy import PlanRejected, target_key


class ModService:
    def __init__(self, plans, executor):
        self.plans, self.executor = plans, executor

    def _state(self, store, installation_id):
        try:
            return store.get('Profile', self.plans.direct_state_id(installation_id))
        except KeyError:
            return Profile(profile_id=self.plans.direct_state_id(installation_id), installation_id=installation_id,
                revision=0, selections=[], config_overrides={}, display_order=[])

    def _members(self, store, installation, check):
        state = self._state(store, installation.installation_id)
        root = Path(installation.canonical_root)
        generation, managed = self.plans._generation(store, installation.installation_id,
            root, check)
        targets = self.plans._adoption_targets(store, installation.installation_id, root, check,
            after=generation.committed_at if generation else None)
        targets.update(managed or {})
        scope = [dict(target_root_kind=file['target_root_kind'],relative_path=file['relative_path'])
                 for _,file in sorted(targets.items())]
        disk, _ = self.plans._scan(root, installation.file_identity, check, scope=scope)
        if managed is None:
            managed = self.plans._adopted(store, installation.installation_id, disk,
                root, self.plans.library, check)
        else:
            managed = self.plans._merge_owners(managed, self.plans._adopted(store, installation.installation_id,
                disk, root, self.plans.library, check, after=generation.committed_at))
        # Take enabled owners from the actual committed deployment, including
        # optional Profile deployments. Never remove another Mod as a side effect.
        members = {str(s.release_id): s.model_dump(mode='json') for s in state.selections if not s.selected}
        for file in managed.values():
            for identity in file['owner_ids']:
                release = store.get('ModRelease', identity)
                members[identity] = dict(release_id=identity, manifest_hash=release.manifest_hash, selected=True)
        return state, members, managed, disk

    def list(self, installation_id, offset=0, limit=20, *, check=lambda: None):
        check = self.plans._budget(check)
        with self.plans._lock, self.plans._store(check) as store:
            with self.plans.guards._installation(str(installation_id),check=check) as (installation, _):
                _, members, managed, disk = self._members(store, installation, check)
                total = store.connection.execute('SELECT count(*) FROM mod_releases').fetchone()[0]
                items = []
                for release in store.list('ModRelease', offset=offset, limit=limit):
                    check()
                    identity = str(release.release_id)
                    item = dict(release_id=identity, manifest_hash=release.manifest_hash,
                        display_name=store.get('ModPackage', release.package_id).display_name,
                        version_raw=release.version_raw, file_count=0, state='not_installed', message='')
                    try:
                        verified = PackageRepository(store, self.plans.library).release(identity, release.manifest_hash, check=check)
                        item['file_count'] = len(verified['files'])
                        member = members.get(identity)
                        if member:
                            if not member['selected']:
                                item['state'] = 'disabled'
                            else:
                                matching = all(
                                    identity in managed.get(target_key(f['target_root_kind'], f['relative_path']), {}).get('owner_ids', [])
                                    and disk.get(target_key(f['target_root_kind'], f['relative_path']), {}).get('sha256') == f['source_hash']
                                    for f in verified['files'])
                                item['state'] = 'enabled' if matching else 'drifted'
                                if not matching:
                                    item['message'] = '安装文件缺失或被外部修改，请保留修改并检查文件。'
                    except (ValueError, OSError, KeyError):
                        item.update(state='unknown', message='包库内容缺失、不可读或不支持，请重新导入。')
                    items.append(item)
                return dict(items=items, total=total, offset=offset,
                    next_offset=offset+len(items) if offset+len(items)<total else None)

    def action(self, payload, *, check=lambda: None, commit=None, progress=lambda *args: None):
        request = ModAction.model_validate(payload)
        check = self.plans._budget(check, seconds=WORKSPACE_TTL.total_seconds() if request.action == 'uninstall' else 120)
        # Use the executor's lock order, also serialize this read/modify/apply
        # against advanced deployments so unrelated enabled Mods are retained.
        with self.executor._lock, self.plans._lock:
            from .ordinary import OrdinaryMods
            result = OrdinaryMods(self).save(request, check=check, commit=commit, progress=progress)
            if result.get('error_code'):
                return result
            if not result.get('no_op') and not (result.get('deployed') or result.get('idempotent')):
                raise PlanRejected('MOD_ACTION_FAILED')
            actual_state = {'install':'enabled', 'enable':'enabled', 'disable':'disabled', 'uninstall':'not_installed'}[request.action]
            if result.get('idempotent'):
                with self.plans._store(check) as store:
                    offset = store.connection.execute('SELECT count(*) FROM mod_releases WHERE release_id<?',
                        (str(request.release_id),)).fetchone()[0]
                actual_state = self.list(str(request.installation_id), offset, 1, check=check)['items'][0]['state']
            return dict(completed=True, action=request.action, release_id=str(request.release_id),
                state=actual_state,
                no_op=result.get('no_op', False), idempotent=result.get('idempotent', False))
