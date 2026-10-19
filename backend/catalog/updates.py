"""User-confirmed version replacement through the ordinary batch workspace."""
from backend.catalog.versions import compare_versions, library_attachment
from backend.deployments.policy import PlanRejected, target_key
from backend.storage.package_repository import PackageRepository


class ModUpdateService:
    def __init__(self, workspace):
        self.workspace = workspace

    def preview(self, installation_id, release_id, manifest_hash, *, check=lambda: None):
        plans = self.workspace.plans
        with plans._lock, plans._store(check, verify_library_bytes=False) as store:
            with plans.guards._installation(installation_id, check=check) as (installation, _):
                view = self.workspace._view(store, installation, check)
                incoming = next((row for row in view['items'] if row['release_id'] == release_id), None)
                if not incoming or incoming.get('manifest_hash') != manifest_hash or not incoming['manageable']:
                    raise PlanRejected('MOD_WORKSPACE_UNSAFE')
                release = PackageRepository(store, plans.library).release(release_id, manifest_hash, check=check, verify_bytes=False)
                targets = {target_key(row['target_root_kind'], row['relative_path']) for row in release['files']
                           if not library_attachment(row)}
                replacements = []
                for item in view['items']:
                    if item['id'] == incoming['id'] or item['id'] not in view['enabled_ids']:
                        continue
                    overlap = targets.intersection(target_key(row['target_root_kind'], row['relative_path'])
                        for row in item['_files'] if not row.get('library_only'))
                    if item.get('package_id') == incoming.get('package_id') or overlap:
                        if not item['manageable']:
                            raise PlanRejected('MOD_WORKSPACE_UNSAFE')
                        replacements.append({key: item.get(key) for key in ('id', 'display_name', 'version_raw', 'release_id')}
                                            | dict(comparison=compare_versions(incoming['version_raw'], item['version_raw'])))
                return dict(installation_id=installation_id, release_id=release_id, manifest_hash=manifest_hash,
                    incoming={key: incoming.get(key) for key in ('id', 'display_name', 'version_raw', 'package_id')},
                    snapshot_id=view['snapshot_id'], replacements=replacements,
                    already_enabled=incoming['id'] in view['enabled_ids'],
                    message='替换会停用所列旧版本、清理其独有文件并启用此版本；旧版本仍留在包库和原方案中。')

    def apply(self, installation_id, release_id, manifest_hash, snapshot_id, replaced_ids, idempotency_key, *,
              check=lambda: None, commit=None, progress=lambda *args: None):
        preview = self.preview(installation_id, release_id, manifest_hash, check=check)
        if preview['snapshot_id'] != snapshot_id or sorted(replaced_ids) != sorted(row['id'] for row in preview['replacements']):
            raise PlanRejected('MOD_WORKSPACE_STALE')
        view = self.workspace.list(installation_id, check=check)
        enabled = [value for value in view['enabled_ids'] if value not in replaced_ids and value != preview['incoming']['id']]
        enabled.append(preview['incoming']['id'])
        return self.workspace.save(dict(installation_id=installation_id, snapshot_id=snapshot_id,
            enabled_ids=enabled, idempotency_key=idempotency_key), check=check, commit=commit, progress=progress)
