"""Fixed projections of existing local services, never bridge reflection."""
import json
import time
from pathlib import Path

from .gateway import AIError
from .redaction import redact
from .tool_policy import ToolScope, parameters


# A cold library/file query may legitimately take more than ten seconds. The
# whole request's deadline and cancellation still bound every nested operation.
TOOL_SECONDS = 30
MAX_OUTPUT_BYTES = 12000


class ReadOnlyTools:
    def __init__(self, installations, packages, mods, logs, configs, *, workspace=None):
        self.installations, self.packages, self.mods = installations, packages, mods
        self.logs, self.configs = logs, configs
        self.workspace = workspace
        self._handlers = {
            'get_installation_summary': self._installation,
            'search_mods': self._search,
            'get_package': self._package,
            'read_selected_log': self._log,
            'get_installed_mods': self._installed,
            'get_mod_workspace': self._workspace,
            'get_mod_config': self._config,
        }

    def validate_scope(self, scope):
        if not isinstance(scope, ToolScope):
            raise AIError('AI_TOOL_SCOPE')
        if scope.installation_id is None:
            if scope.selected_evidence_id is not None:
                raise AIError('AI_TOOL_SCOPE')
            return
        result = self.installations.list()
        if result['selected_installation_id'] != scope.installation_id or not any(
                row['installation_id'] == scope.installation_id for row in result['installations']):
            raise AIError('AI_TOOL_SCOPE')
        if scope.selected_evidence_id is not None:
            try:
                self.logs.read(scope.installation_id, scope.selected_evidence_id, limit=1)
            except (ValueError, OSError, KeyError, RuntimeError):
                raise AIError('AI_TOOL_SCOPE') from None

    def execute(self, name, arguments, scope, check, secrets=()):
        deadline = time.monotonic() + TOOL_SECONDS
        def bounded():
            check()
            if time.monotonic() > deadline:
                raise AIError('AI_TOOL_TIMEOUT')
        bounded()
        data = parameters(name, arguments, scope)
        try:
            self.validate_scope(scope)  # Repeat at execution, not just at send.
            bounded()
            result = self._handlers[name](scope, data, bounded)
            bounded()
            def clean(value, field=None):
                if isinstance(value,str):
                    return redact(value,secrets,partial=field=='text' and name=='read_selected_log' and result['truncated'])
                if isinstance(value,list):
                    return [clean(item) for item in value]
                if isinstance(value,dict):
                    return {key: clean(item,key) for key,item in value.items()}
                return value
            output = json.dumps(clean(dict(ok=True, data=result)), ensure_ascii=False, allow_nan=False)
            if len(output.encode('utf-8')) > MAX_OUTPUT_BYTES:
                raise AIError('AI_TOOL_OUTPUT_LIMIT')
            return output
        except AIError:
            raise
        except Exception as exc:
            # Job cancellation must reach the job manager, never become a tool error.
            from backend.runtime.jobs import _Cancelled
            if isinstance(exc, (_Cancelled, InterruptedError)):
                raise
            raise AIError('AI_TOOL_UNAVAILABLE') from None

    def _installation(self, scope, data, check):
        from backend.games.probably_stolen.fingerprint import current_versions
        from backend.games.probably_stolen.layout import identity
        from backend.games.probably_stolen.update_state import current_build_id
        installation, _ = self.configs.guards._facts(scope.installation_id)
        check()
        root = Path(installation.canonical_root)
        if identity(root) != installation.file_identity:
            raise AIError('AI_TOOL_UNAVAILABLE')
        versions = current_versions(root)
        build = current_build_id(root, installation.app_id)
        check()
        return dict(installation_id=scope.installation_id, game='Probably Stolen', channel=installation.channel,
                    build_id=build, **versions,
                    freshness='current_files', runtime_verified=False)

    def _search(self, scope, data, check):
        return self.packages.search(**data, check=check)

    def _package(self, scope, data, check):
        page = self.packages.detail(**data, check=check)
        return {key: page[key] for key in ('release_id', 'manifest_hash', 'display_name', 'version_raw',
                'roles', 'integrity', 'total', 'offset', 'next_offset')} | dict(files=[
            {key: row[key] for key in ('target_root_kind', 'relative_path', 'size', 'roles')}
            for row in page['files']], installed_state='query_get_installed_mods')

    def _log(self, scope, data, check):
        row = self.logs.read(scope.installation_id, data['evidence_id'], limit=4096)
        check()
        return dict(evidence_id=data['evidence_id'], text=row['text'], bytes_total=row['total'],
                    truncated=row['next_offset'] is not None, freshness='selected_stored_fragment',
                    runtime_verified=False)

    def _installed(self, scope, data, check):
        # Wait cooperatively for the existing planner lock, then reuse its real state query.
        lock = self.mods.plans._lock
        while not lock.acquire(timeout=.05):
            check()
        try:
            check()
            return self.mods.list(scope.installation_id, **data, check=check)
        finally:
            lock.release()

    def _workspace(self, scope, data, check):
        value = self.workspace_view(scope, check)
        selected = value['items'][data['offset']:data['offset']+data['limit']]
        fields = ('id','release_id','manifest_hash','display_name','version_raw','state','source','manageable','message')
        return dict(items=[{key: row[key] for key in fields if key in row} for row in selected],
            snapshot_id=value['snapshot_id'],total=len(value['items']),offset=data['offset'],
            next_offset=data['offset']+len(selected) if data['offset']+len(selected)<len(value['items']) else None,
            order_policy=value['order_policy'],order_message=value['order_message'],runtime_verified=False)

    def workspace_view(self, scope, check):
        if self.workspace is None:
            raise AIError('AI_TOOL_UNAVAILABLE')
        lock = self.mods.plans._lock
        while not lock.acquire(timeout=.05):
            check()
        try:
            return self.workspace.list(scope.installation_id,check=check)
        finally:
            lock.release()

    def _config(self, scope, data, check):
        page = self.configs.read(scope.installation_id, data['adapter_id'], check=check)
        return dict(adapter_id=page['adapter_id'], supported=page['editable'], fields=[
            {key: row[key] for key in ('key', 'label', 'value')} for row in page['fields']])
