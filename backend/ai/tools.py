"""Fixed projections of existing local services, never bridge reflection."""
import json
import re
import time
from pathlib import Path

from backend.runtime.events import RuntimePolicyError
from .gateway import AIError
from .redaction import HIDDEN, redact
from .tool_policy import ToolScope, parameters


# A cold library/file query may legitimately take more than ten seconds. The
# whole request's deadline and cancellation still bound every nested operation.
TOOL_SECONDS = 30
MAX_OUTPUT_BYTES = 12000


class ReadOnlyTools:
    def __init__(self, installations, packages, mods, logs, configs, *, workspace=None,
                 native=None, installation_resolver=None):
        self.installations, self.packages, self.mods = installations, packages, mods
        self.logs, self.configs = logs, configs
        self.workspace = workspace
        self.native = native
        self.installation_resolver = installation_resolver
        self._handlers = {
            'get_installation_summary': self._installation,
            'search_mods': self._search,
            'get_package': self._package,
            'read_selected_log': self._log,
            'get_installed_mods': self._installed,
            'get_mod_workspace': self._workspace,
            'get_mod_config': self._config,
            'get_mod_detail': self._native_detail,
            'get_mod_dependencies': self._native_dependencies,
            'get_runtime_reconciliation': self._native_reconciliation,
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
            except (ValueError, OSError, KeyError, RuntimeError, RuntimePolicyError):
                raise AIError('AI_TOOL_SCOPE') from None

    def execute(self, name, arguments, scope, check, secrets=()):
        deadline = time.monotonic() + TOOL_SECONDS
        def bounded():
            check()
            if time.monotonic() > deadline:
                raise AIError('AI_TOOL_TIMEOUT')
        bounded()
        data = parameters(name, arguments, scope, native=self.native is not None)
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
        if self.native is not None:
            from backend.games.probably_stolen.native_identity import observe
            installation = self.native_installation(scope)
            snapshot = self.native_view(scope, check, installation=installation)
            persistent = snapshot['paths'].get('persistent_root')
            versions = observe(installation, persistent) if persistent else {'runtime': 'unknown'}
            versions['runtime_kind'] = versions.pop('runtime')
            check()
            fields = ('installation_id', 'channel', 'app_id')
            return {key: installation[key] for key in fields if key in installation} | dict(
                installation_id=scope.installation_id, game='Probably Stolen',
                inventory_kind='native_manifest', revision=snapshot['revision'],
                ready=snapshot.get('ready', True), issues=self._issues(snapshot.get('issues', [])),
                runtime=self._runtime(snapshot), freshness='current_native_snapshot',
                runtime_verified=False, **versions)
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
        if self.native is not None:
            snapshot = self.native_view(scope, check)
            query = data['query'].casefold()
            rows = [row for row in snapshot['mods'] if query in ' '.join(
                str(row.get(key) or '') for key in ('id', 'name', 'version')).casefold()]
            return self._native_page(snapshot, rows, data)
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
        text = row['text']
        if self.native is not None:
            from .log_diagnostics import clean_log_text
            text = clean_log_text(text, manager_root=self.packages.paths.root)
        return dict(evidence_id=data['evidence_id'], text=text, bytes_total=row['total'],
                    truncated=row['next_offset'] is not None, freshness='selected_stored_fragment',
                    runtime_verified=False)

    def _installed(self, scope, data, check):
        if self.native is not None:
            return self._workspace(scope, data, check)
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
        if self.native is not None:
            value = self.native_view(scope, check)
            return self._native_page(value, value['mods'], data)
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
        if self.native is not None:
            installation = self.native_installation(scope)
            snapshot = self.native_view(scope, check, installation=installation)
            if not any(row['id'] == data['mod_id'] for row in snapshot['mods']):
                raise AIError('AI_TOOL_ARGUMENTS')
            value = self._native_read('config_read', installation, check, data['mod_id'])
            rows = value['entries']
            selected = rows[data['offset']:data['offset'] + data['limit']]
            entries = []
            for row in selected:
                # Configs store arbitrary string keys; redacting each value alone
                # would leak a password whose label is in the adjacent key field.
                label = re.sub(r'[^a-z0-9]', '', str(row['key']).casefold())
                sensitive = any(word in label for word in ('password', 'passphrase', 'apikey',
                    'token', 'secret', 'credential', 'authorization', 'cookie', 'steamlogin',
                    'sessionkey', 'sessionid', 'authkey', 'privatekey')) or label == 'key'
                entries.append(dict(key=row['key'], value=HIDDEN if sensitive else row['value'],
                                    type='unknown', redacted=sensitive))
            return dict(id=value['id'], revision=value['revision'], exists=value['exists'],
                        entries=entries, **self._pagination(rows, selected, data),
                        freshness='current_config_file', runtime_verified=False)
        page = self.configs.read(scope.installation_id, data['adapter_id'], check=check)
        return dict(adapter_id=page['adapter_id'], supported=page['editable'], fields=[
            {key: row[key] for key in ('key', 'label', 'value')} for row in page['fields']])

    def native_installation(self, scope):
        """Resolve only the selected registration, never a provider-supplied path."""
        if self.native is None or self.installation_resolver is None:
            raise AIError('AI_TOOL_UNAVAILABLE')
        self.validate_scope(scope)
        installation = self.installation_resolver(scope.installation_id)
        if not isinstance(installation, dict) or str(installation.get('installation_id')) != scope.installation_id:
            raise AIError('AI_TOOL_SCOPE')
        return installation

    def native_view(self, scope, check, *, installation=None):
        return self._native_read('snapshot', installation or self.native_installation(scope), check)

    def _native_read(self, method, installation, check, *args):
        # The service uses a reentrant lock. Wait cooperatively instead of
        # blocking AI cancellation behind an import/save already in progress.
        check()
        lock = getattr(self.native, '_lock', None)
        if lock is not None:
            while not lock.acquire(timeout=.05):
                check()
        try:
            check()
            value = getattr(self.native, method)(installation, *args)
            check()
            return value
        except InterruptedError:
            raise
        except (OSError, RuntimePolicyError):
            raise AIError('AI_TOOL_UNAVAILABLE') from None
        finally:
            if lock is not None:
                lock.release()

    @staticmethod
    def _runtime(snapshot):
        value = snapshot.get('runtime', {})
        return {key: value[key] for key in ('state', 'reason', 'checked_at', 'observed_at', 'writable', 'blocking_reason') if key in value}

    @staticmethod
    def _issues(rows):
        return [{key: row[key] for key in ('code', 'mod_id', 'severity', 'dependency',
            'before', 'after', 'cycle', 'other_id') if key in row} for row in rows]

    @staticmethod
    def _pagination(rows, selected, data):
        offset = data['offset'] + len(selected)
        return dict(total=len(rows), offset=data['offset'], next_offset=offset if offset < len(rows) else None)

    @staticmethod
    def _native_row(row):
        fields = ('id', 'name', 'version', 'installed', 'target_enabled', 'source', 'workshop_id')
        result = {key: row[key] for key in fields if key in row}
        approval = row.get('approval', {})
        result['approval'] = {key: approval[key] for key in
            ('state', 'scan_status', 'approved_at', 'updated_at') if key in approval}
        run = row.get('run', {})
        result['run'] = {key: run[key] for key in ('state', 'runtime_state', 'running',
            'restart_pending', 'audit_line', 'audit_lines', 'observed_entry_count',
            'running_entry_count', 'error_entry_count') if key in run}
        result['run']['events'] = [{key: event[key] for key in ('kind', 'line') if key in event}
            for event in run.get('events', []) if event.get('kind') in
            {'startup_audit', 'enabled_for_restart', 'disabled_for_restart'}]
        return result

    def _native_page(self, snapshot, rows, data):
        selected = rows[data['offset']:data['offset'] + data['limit']]
        return dict(items=[self._native_row(row) for row in selected],
            revision=snapshot['revision'], enabled_ids=snapshot['enabled_ids'],
            ready=snapshot.get('ready', True), issues=self._issues(snapshot.get('issues', [])),
            **self._pagination(rows, selected, data), runtime=self._runtime(snapshot),
            order_policy='saved_order_not_initialization_order',
            order_message='当前游戏先加载 DLL 再应用保存顺序；顺序不保证 Init、Harmony 或 Hook 初始化先后。',
            freshness='current_native_snapshot', runtime_verified=False)

    def _native_detail(self, scope, data, check):
        value = self._native_read('detail', self.native_installation(scope), check, data['mod_id'])
        row = value['mod']
        mod = self._native_row(row)
        mod.update({key: row[key] for key in ('author', 'description', 'game_versions',
            'prerequisites', 'capabilities', 'size_bytes', 'updated_at', 'assemblies') if key in row})
        mod['issues'] = self._issues(row.get('issues', []))
        approval = row.get('approval', {})
        mod['approval'].update({key: approval[key] for key in ('declared', 'detected') if key in approval})
        files = row.get('files', [])
        selected = files[data['offset']:data['offset'] + data['limit']]
        mod['files'] = [{key: item[key] for key in ('relative_path', 'path', 'size', 'size_bytes', 'kind')
                         if key in item and (key != 'path' or not Path(str(item[key])).is_absolute())}
                        if isinstance(item, dict) else str(item) for item in selected]
        # Absolute game/user paths and trust fingerprints stay local.
        mod['sources'] = [{key: item[key] for key in ('source', 'workshop_id', 'active') if key in item}
                          for item in row.get('sources', [])]
        return dict(revision=value['revision'], mod=mod, files_page=self._pagination(files, selected, data),
                    permissions_meaning='权限是声明与历史扫描资料，不是沙箱；批准只能由游戏完成。',
                    runtime_verified=False)

    def _native_dependencies(self, scope, data, check):
        installation = self.native_installation(scope)
        snapshot = self.native_view(scope, check, installation=installation)
        mod = next((row for row in snapshot['mods'] if row['id'] == data['mod_id']), None)
        if mod is None:
            raise AIError('AI_TOOL_ARGUMENTS')
        validation = self._native_read('validate', installation, check, snapshot['enabled_ids'])
        validation['issues'] = self._issues(validation.get('issues', []))
        return dict(id=mod['id'], revision=snapshot['revision'],
            prerequisites=mod.get('prerequisites', []),
            required_by=[row['id'] for row in snapshot['mods'] if mod['id'] in row.get('prerequisites', [])],
            validation={key: validation[key] for key in
                ('enabled_ids', 'sorted_ids', 'issues', 'blocking', 'approval_required_ids') if key in validation},
            applied=False, order_policy='saved_order_not_initialization_order')

    def _native_reconciliation(self, scope, data, check):
        snapshot = self.native_view(scope, check)
        result = self._native_page(snapshot, snapshot['mods'], data)
        log = snapshot.get('runtime_reconciliation') or snapshot.get('log', {})
        result['log'] = {key: log[key] for key in
            ('source', 'scope', 'status', 'game_version', 'unity_version', 'audit_line',
             'configured_count', 'running_count', 'not_running_count', 'failed_count',
             'partial_count', 'unknown_count', 'pending_restart_count', 'coverage',
             'source_modified_at', 'process_verified') if key in log}
        result['evidence_meaning'] = 'run 是最近 Player.log 的加载证据；runtime 才是实时进程状态。保存或批准不等于运行成功。'
        return result
