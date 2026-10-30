"""Suggestion cards; native cards navigate to review and never mutate game state."""
import json
import sqlite3
import time
from typing import Literal
from uuid import uuid4

from pydantic import Field

from backend.app.identity import DATABASE_APPLICATION_ID
from backend.contracts import ModRelease
from .gateway import AIError
from .redaction import redact
from .tool_policy import Empty, NativeMod, UUID_PATTERN, validate_arguments
from .tools import TOOL_SECONDS


TOOL_NAME = 'suggest_mod_action'
WORKSPACE_TOOL_NAME = 'suggest_workspace_action'
NATIVE_TOOL_NAME = 'suggest_native_mod_action'
MAX_SUGGESTIONS = 4
SUGGESTION_SECONDS = 600
PUBLIC_FIELDS = ('suggestion_id','installation_id','release_id','manifest_hash','display_name',
                 'version_raw','action','reason')
WORKSPACE_FIELDS = ('kind','suggestion_id','installation_id','workspace_id','snapshot_id',
                    'display_name','version_raw','action','reason')
NATIVE_FIELDS = ('kind','suggestion_id','installation_id','mod_id','revision',
                 'display_name','version_raw','action','reason')


class Suggestion(Empty):
    release_id: str = Field(pattern=UUID_PATTERN)
    manifest_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['install','enable','disable']
    reason: str = Field(min_length=1,max_length=300)


class WorkspaceSuggestion(Empty):
    workspace_id: str = Field(pattern=UUID_PATTERN)
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['enable','disable']
    reason: str = Field(min_length=1,max_length=300)


class NativeSuggestion(NativeMod):
    revision: str = Field(min_length=1, max_length=128)
    action: Literal['enable', 'disable']
    reason: str = Field(min_length=1, max_length=300)


def schema(scope, *, workspace=False, native=False):
    if scope.installation_id is None:
        return []
    if native:
        return [dict(type='function', function=dict(name=NATIVE_TOOL_NAME,
            description='对原生库存的真实 mod_id 与 revision 生成启用/停用建议。'
                        '卡片按钮只打开模组列表供用户确认草稿；不启停、不保存、不批准。',
            parameters=NativeSuggestion.model_json_schema()))]
    result = [dict(type='function',function=dict(name=TOOL_NAME,
        description='生成当前游戏的安装/启用/停用建议卡，只包含已登记的Mod版本与简短理由。'
                    '不执行操作，必须用户点击卡片按钮；不能提供路径、命令或批准字段。',
        parameters=Suggestion.model_json_schema()))]
    if workspace:
        result.append(dict(type='function',function=dict(name=WORKSPACE_TOOL_NAME,
            description='对 get_mod_workspace 返回的具体 Mod 生成启用/停用建议卡，含外部发现的 Mod。'
                        '必须使用查询返回的 id 和 snapshot_id；不执行写入，须用户点击，其他 Mod 保持原状。',
            parameters=WorkspaceSuggestion.model_json_schema())))
    return result


class PlanProposals:
    def __init__(self, tools):
        self.tools = tools
        self.paths = tools.packages.paths

    @staticmethod
    def view(row, secrets=()):
        output={}
        source=row.get('public',row)
        fields = NATIVE_FIELDS if source.get('kind') == 'native_mod' else (
            WORKSPACE_FIELDS if source.get('kind') == 'workspace' else PUBLIC_FIELDS)
        for key in fields:
            value=source[key]
            if isinstance(value,str):
                value=redact(value,secrets)
                limit={'display_name':200,'version_raw':200,'reason':300}.get(key)
                if limit is not None:
                    value=redact(value[:limit],secrets,partial=True)
            output[key]=value
        return output

    def prepare(self, arguments, scope, pending, check, secrets=(), *, name=TOOL_NAME):
        if getattr(self.tools, 'native', None) is not None:
            if name != NATIVE_TOOL_NAME:
                raise AIError('AI_TOOL_UNKNOWN')
            return self._prepare_native(arguments, scope, pending, check, secrets)
        if name == NATIVE_TOOL_NAME:
            raise AIError('AI_TOOL_UNAVAILABLE')
        if name==WORKSPACE_TOOL_NAME:
            return self._prepare_workspace(arguments,scope,pending,check,secrets)
        deadline = time.monotonic()+TOOL_SECONDS
        def bounded():
            check()
            if time.monotonic()>deadline:
                raise AIError('AI_TOOL_TIMEOUT')
        bounded()
        data = validate_arguments(arguments, Suggestion)
        if not data['reason'].strip():
            raise AIError('AI_TOOL_ARGUMENTS')
        self.tools.validate_scope(scope)
        if scope.installation_id is None:
            raise AIError('AI_TOOL_SCOPE')
        bounded()
        try:
            package = self.tools.packages.detail(data['release_id'],data['manifest_hash'],limit=1,check=bounded)
        except (ValueError,OSError,KeyError) as exc:
            if isinstance(exc,(AIError,InterruptedError)):
                raise
            raise AIError('AI_SUGGESTION_INVALID') from None
        bounded()
        match = next((row for row in pending if row.get('kind')!='workspace' and all(row[key]==data[key] for key in ('release_id','manifest_hash','action'))),None)
        if match is None:
            if len(pending)>=MAX_SUGGESTIONS:
                raise AIError('AI_SUGGESTION_LIMIT')
            match = dict(suggestion_id=str(uuid4()),installation_id=scope.installation_id,
                release_id=data['release_id'],manifest_hash=data['manifest_hash'],action=data['action'],
                display_name=package['display_name'],version_raw=package['version_raw'],
                reason=redact(data['reason'].strip(),secrets),idempotency_key=str(uuid4()),
                expires_at=time.monotonic()+SUGGESTION_SECONDS)
            # Clearing credentials must never reveal a key from old metadata.
            match['public']=self.view(match,secrets)
            match['display_name']=match['public']['display_name']
            match['version_raw']=match['public']['version_raw']
            pending.append(match)
        return json.dumps(dict(ok=True,data=dict(suggestion=self.view(match,secrets),executed=False)),ensure_ascii=False)

    def _workspace(self, scope, snapshot_id, workspace_id, action, check):
        self.tools.validate_scope(scope)
        if self.tools.workspace is None or scope.installation_id is None:
            raise AIError('AI_TOOL_UNAVAILABLE')
        try:
            value=self.tools.workspace_view(scope,check)
        except (ValueError,OSError) as exc:
            if isinstance(exc,AIError):
                raise
            raise AIError('AI_SUGGESTION_STALE') from None
        if value['snapshot_id']!=snapshot_id:
            raise AIError('AI_SUGGESTION_STALE')
        item=next((row for row in value['items'] if row['id']==workspace_id),None)
        if (item is None or not item.get('manageable',False) or item['state'] not in {'enabled','disabled','not_installed'}
                or (action=='disable') != (workspace_id in value['enabled_ids'])):
            raise AIError('AI_SUGGESTION_INVALID')
        return value,item

    def _prepare_workspace(self, arguments, scope, pending, check, secrets):
        data=validate_arguments(arguments,WorkspaceSuggestion)
        if not data['reason'].strip():
            raise AIError('AI_TOOL_ARGUMENTS')
        check()
        _,item=self._workspace(scope,data['snapshot_id'],data['workspace_id'],data['action'],check)
        check()
        row=next((row for row in pending if row.get('kind')=='workspace' and all(row[key]==data[key]
            for key in ('workspace_id','snapshot_id','action'))),None)
        if row is None:
            if len(pending)>=MAX_SUGGESTIONS:
                raise AIError('AI_SUGGESTION_LIMIT')
            row=dict(kind='workspace',suggestion_id=str(uuid4()),installation_id=scope.installation_id,
                **data,display_name=item['display_name'],version_raw=item['version_raw'],
                idempotency_key=str(uuid4()),expires_at=time.monotonic()+SUGGESTION_SECONDS)
            row['public']=self.view(row,secrets)
            pending.append(row)
        return json.dumps(dict(ok=True,data=dict(suggestion=self.view(row,secrets),executed=False)),ensure_ascii=False)

    def _native(self, scope, revision, mod_id, action, check):
        self.tools.validate_scope(scope)
        value = self.tools.native_view(scope, check)
        if not value.get('ready', True):
            raise AIError('AI_TOOL_UNAVAILABLE')
        if value['revision'] != revision:
            raise AIError('AI_SUGGESTION_STALE')
        item = next((row for row in value['mods'] if row['id'] == mod_id), None)
        if item is None or not item.get('installed') or (action == 'disable') != (mod_id in value['enabled_ids']):
            raise AIError('AI_SUGGESTION_INVALID')
        return value, item

    def _prepare_native(self, arguments, scope, pending, check, secrets):
        deadline = time.monotonic() + TOOL_SECONDS
        def bounded():
            check()
            if time.monotonic() > deadline:
                raise AIError('AI_TOOL_TIMEOUT')
        data = validate_arguments(arguments, NativeSuggestion)
        if not data['reason'].strip():
            raise AIError('AI_TOOL_ARGUMENTS')
        bounded()
        _, item = self._native(scope, data['revision'], data['mod_id'], data['action'], bounded)
        row = next((row for row in pending if row.get('kind') == 'native_mod' and all(
            row[key] == data[key] for key in ('mod_id', 'revision', 'action'))), None)
        if row is None:
            if len(pending) >= MAX_SUGGESTIONS:
                raise AIError('AI_SUGGESTION_LIMIT')
            row = dict(kind='native_mod', suggestion_id=str(uuid4()), installation_id=scope.installation_id,
                       **data, display_name=item['name'], version_raw=item.get('version', ''),
                       expires_at=time.monotonic() + SUGGESTION_SECONDS)
            row['public'] = self.view(row, secrets)
            pending.append(row)
        return json.dumps(dict(ok=True, data=dict(suggestion=self.view(row, secrets), executed=False)), ensure_ascii=False)

    def resolve(self, row, scope):
        """Metadata freshness only; mod.action still validates actual bytes/targets."""
        self.tools.validate_scope(scope)
        if (row['installation_id']!=scope.installation_id or time.monotonic()>row['expires_at']):
            raise AIError('AI_SUGGESTION_STALE')
        if getattr(self.tools, 'native', None) is not None:
            if row.get('kind') != 'native_mod':
                # Old conversation cards must never enter the physical-file executor.
                raise AIError('AI_SUGGESTION_STALE')
            deadline = time.monotonic() + TOOL_SECONDS
            def check():
                if time.monotonic() > deadline:
                    raise AIError('AI_TOOL_TIMEOUT')
            self._native(scope, row['revision'], row['mod_id'], row['action'], check)
            return dict(kind='native_mod', installation_id=scope.installation_id,
                        mod_id=row['mod_id'], action=row['action'], revision=row['revision'],
                        navigation=dict(page='mods', mod_id=row['mod_id']))
        if row.get('kind') == 'native_mod':
            raise AIError('AI_SUGGESTION_STALE')
        if row.get('kind')=='workspace':
            value,_=self._workspace(scope,row['snapshot_id'],row['workspace_id'],row['action'],lambda:None)
            enabled=[identity for identity in value['enabled_ids'] if identity!=row['workspace_id']]
            if row['action']=='enable':
                enabled.append(row['workspace_id'])
            return dict(kind='workspace',request=dict(installation_id=scope.installation_id,
                snapshot_id=row['snapshot_id'],enabled_ids=enabled,idempotency_key=row['idempotency_key']))
        self.paths.check_owned_paths()
        db = sqlite3.connect(self.paths.database.as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            deadline=time.monotonic()+2
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            db.execute('BEGIN')
            if db.execute('PRAGMA application_id').fetchone()[0]!=DATABASE_APPLICATION_ID:
                raise AIError('AI_SUGGESTION_STALE')
            payload=db.execute('SELECT payload FROM mod_releases WHERE release_id=?',(row['release_id'],)).fetchone()
            if payload is None or ModRelease.model_validate_json(payload[0]).manifest_hash!=row['manifest_hash']:
                raise AIError('AI_SUGGESTION_STALE')
            offset=db.execute('SELECT count(*) FROM mod_releases WHERE release_id<?',(row['release_id'],)).fetchone()[0]
            return dict(request={key:row[key] for key in ('installation_id','release_id','manifest_hash','action','idempotency_key')},offset=offset)
        except (sqlite3.Error,ValueError) as exc:
            if isinstance(exc,AIError):
                raise
            raise AIError('AI_SUGGESTION_STALE') from None
        finally:
            db.close()
