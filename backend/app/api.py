"""The sole desktop bridge: a closed command table, never object reflection."""
import json
import sqlite3
import threading
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError
from typing import Literal

from .identity import APP_NAME, APP_VERSION, KEYRING_SERVICE, UPDATE_SOURCE
from backend.contracts.plans import ModAction, PlanProposal, PlanPage, PlanIdentity, PlanApply, RollbackProposal
from backend.deployments.history import HistoryPage, IssueRecheck
from backend.deployments.mod_workspace import WorkspaceQuery, WorkspaceDetail, WorkspaceSave, MESSAGES as WORKSPACE_MESSAGES


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class WindowPayload(Empty):
    op: Literal['state', 'minimize', 'toggle_maximize', 'close', 'drag', 'resize_n', 'resize_s', 'resize_e', 'resize_w',
                'resize_ne', 'resize_nw', 'resize_se', 'resize_sw']


class ThemePayload(Empty):
    theme_id: Literal['obsidian-cyan', 'graphite-ash', 'win11-mica']


class StorageTarget(Empty):
    target: str = Field(min_length=1, max_length=4096)


class AISettingsPayload(Empty):
    provider: Literal['openai_compatible']
    base_url: str = Field(min_length=1,max_length=2048)
    model: str = Field(min_length=1,max_length=200)
    api_key: str | None = Field(default=None,max_length=4096)
    remember: StrictBool = True


class AISessionPayload(Empty):
    session_id: UUID


class AISendPayload(Empty):
    question: str = Field(min_length=1,max_length=2000)
    selected_text: str = Field(default='',max_length=4000)
    selected_label: str = Field(default='',max_length=120)
    session_id: UUID | None = None
    installation_id: UUID | None = None
    selected_evidence_id: UUID | None = None


class AILogPagePayload(Empty):
    installation_id: UUID
    after: StrictInt = Field(default=0, ge=0, le=2**53-1)
    limit: StrictInt = Field(default=20, ge=1, le=20)


class AISuggestionPayload(AISessionPayload):
    suggestion_id: UUID


class TaskPayload(Empty):
    task_id: UUID


class SubscribePayload(TaskPayload):
    after_sequence: StrictInt = Field(default=0, ge=0, le=2**53-1)


class SubscriptionPayload(Empty):
    subscription_id: UUID


class EventsPayload(SubscriptionPayload):
    after_sequence: StrictInt = Field(default=0, ge=0, le=2**53-1)


class ChooseInstallationPayload(Empty):
    kind: Literal['steam', 'manual']


class DiscoverInstallationPayload(Empty):
    source_handles: list[UUID] = Field(min_length=1, max_length=8)


class SelectInstallationPayload(Empty):
    candidate_id: UUID


class UseInstallationPayload(Empty):
    installation_id: UUID


class LoaderLocalPayload(UseInstallationPayload):
    source_handle: UUID


class DeploymentStatusPayload(UseInstallationPayload):
    plan_id: UUID | None = None


class ConfigReadPayload(UseInstallationPayload):
    adapter_id: Literal['pswallpaper']


class ConfigSavePayload(ConfigReadPayload):
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    changes: dict[str, StrictBool | StrictInt | str] = Field(min_length=1, max_length=3)
    backup: StrictBool = True


class InspectMetadataPayload(UseInstallationPayload):
    files: list[str] = Field(min_length=1,max_length=8)


class ScanInventoryPayload(UseInstallationPayload):
    force: StrictBool = False


class PackagePagePayload(Empty):
    offset: StrictInt = Field(default=0, ge=0, le=2**53-1)
    limit: StrictInt = Field(default=20, ge=1, le=20)


class AdoptionPagePayload(PackagePagePayload):
    snapshot_id: UUID


class PackageDetailPayload(PackagePagePayload):
    release_id: UUID
    manifest_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class ModPagePayload(PackagePagePayload):
    installation_id: UUID


class AdoptPackagePayload(Empty):
    snapshot_id: UUID
    manifest_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    paths: list[str] = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    version_raw: str | None = Field(default=None, min_length=1, max_length=200)
    package_id: UUID | None = None


class ChooseImportPayload(Empty):
    kind: Literal['dll', 'directory', 'zip']


class ImportPreviewPayload(Empty):
    source_handle: UUID


class ImportFilesPayload(PackagePagePayload):
    preview_id: UUID


class ImportCommitPayload(Empty):
    preview_id: UUID
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    display_name: str = Field(min_length=1, max_length=200)
    version_raw: str | None = Field(default=None, min_length=1, max_length=200)


class ProfileIdentity(Empty):
    profile_id: UUID


class ProfileRevision(ProfileIdentity):
    expected_revision: StrictInt = Field(ge=0, le=2**53-1)


class ProfileCreate(UseInstallationPayload):
    name: str = Field(min_length=1, max_length=120)


class ProfileClone(ProfileRevision):
    name: str = Field(min_length=1, max_length=120)


class ProfileEdit(ProfileRevision):
    # The service validates the discriminated, closed action schema as well.
    action: dict


class ProfileImport(ImportPreviewPayload):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    installation_id: UUID
    name: str = Field(min_length=1, max_length=120)


class ProfilePreview(ImportPreviewPayload):
    installation_id: UUID


class CompatibilityPayload(ProfileRevision):
    profile_kind: Literal['draft', 'committed'] = 'draft'
    enable_release_ids: list[UUID] = Field(default_factory=list, max_length=200)
    disable_release_ids: list[UUID] = Field(default_factory=list, max_length=200)


class ListInventoryPayload(UseInstallationPayload):
    generation: StrictInt | None = Field(default=None, ge=1)
    limit: StrictInt = Field(default=50, ge=1, le=50)
    offset: StrictInt = Field(default=0, ge=0, le=2**53-1)


class DetailInventoryPayload(UseInstallationPayload):
    entry_id: UUID
    generation: StrictInt | None = Field(default=None, ge=1)


class ContentInventoryPayload(DetailInventoryPayload):
    offset: StrictInt = Field(default=0, ge=0, le=2**53-1)
    limit: StrictInt = Field(default=20, ge=1, le=20)


class GraphInventoryPayload(UseInstallationPayload):
    generation: StrictInt | None = Field(default=None, ge=1)
    offset: StrictInt = Field(default=0, ge=0, le=2**53-1)
    limit: StrictInt = Field(default=20, ge=1, le=20)


class WatchLogsPayload(UseInstallationPayload):
    user_logs: list[str] = Field(default_factory=list,max_length=8)


class DiagnosticPayload(GraphInventoryPayload):
    session_id: UUID | None = None
    snapshot_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')


class SessionListPayload(UseInstallationPayload):
    after: StrictInt = Field(default=0,ge=0,le=2**53-1)
    limit: StrictInt = Field(default=20,ge=1,le=20)


class EvidenceListPayload(SessionListPayload):
    session_id: UUID | None = None
    group_id: UUID | None = None


class EvidenceReadPayload(UseInstallationPayload):
    evidence_id: UUID
    offset: StrictInt = Field(default=0,ge=0,le=2**53-1)
    limit: StrictInt = Field(default=4096,ge=1,le=8192)


MESSAGES = {
    'LOADER_GENERATION_UNSUPPORTED': 'IL2CPP 生成依赖尚未就绪，未启动游戏。请打开 MelonLoader 环境详情，检查版本并修复依赖。',
    'LOADER_OFFLINE_PACKAGE_INVALID': '管理器内置生成依赖包缺失或损坏，请重新完整解压最新版管理器，再修复依赖。',
    'LOADER_REPAIR_REQUIRED': '依赖修复未完成，请关闭占用程序后在 MelonLoader 环境详情中重新修复。',
    'GAME_LAUNCH_FAILED': '无法启动游戏，请检查可执行文件、权限和所需运行环境。',
    'GAME_IMAGE_CHANGED': '游戏程序已变化，未执行新进程；请重新选择安装。',
    'GAME_ALREADY_RUNNING': '游戏已在运行，未重复启动。',
    'GAME_PROCESS_UNKNOWN': '暂无法检查游戏进程或程序权限，请检查权限后重试。',
    'CONFIG_UNSUPPORTED': '此配置暂不支持可靠编辑，请使用外部编辑器；原文件未改动。',
    'CONFIG_MISSING': '没有找到壁纸 Mod 配置。请先由该 Mod 生成配置文件。',
    'CONFIG_CHANGED': '配置文件已被其他程序修改，请重新读取后再保存。',
    'CONFIG_VALUE_INVALID': '配置值无效，请检查所选字段。',
    'CONFIG_IO': '配置未能保存或读取。请检查文件占用、权限和可用空间；必要时使用原文件备份。',
    'GUARD_PROCESS_RUNNING': '游戏正在运行，请先关闭游戏后再执行此操作。',
    'GUARD_PROCESS_UNKNOWN': '暂时无法确认游戏是否已关闭，请关闭游戏后重试。',
    'GUARD_LEASE_BUSY': '此安装正在处理其他操作，请稍后重试。',
    'GUARD_RECOVERY_REQUIRED': '此安装仍有未完成的文件操作，请先恢复或处理它。',
    'GUARD_INSTALLATION_CHANGED': '所选游戏安装已经变化，请重新选择。',
    'GUARD_FINGERPRINT_INCOMPLETE': '所选游戏安装不完整，请重新选择。',
    'GUARD_REPARSE_POINT': '配置路径包含链接，暂不支持直接编辑。',
    'GUARD_HARDLINK': '配置文件包含硬链接，暂不支持直接编辑。',
    'COMPATIBILITY_INPUT_INVALID': '检查参数无效。',
    'COMPATIBILITY_PROFILE_NOT_FOUND': '配置不存在，请刷新后重试。',
    'COMPATIBILITY_STALE': '配置或检查依据已改变，请重新检查。',
    'COMPATIBILITY_REVISION_CONFLICT': '配置或检查依据已改变，请重新检查。',
    'COMPATIBILITY_CHANGED': '配置或检查依据已改变，请重新检查。',
    'COMPATIBILITY_LIMIT': '检查内容超过限额，请缩小配置范围。',
    'COMPATIBILITY_NOT_FOUND': '配置或安装不存在，请刷新后重试。',
    'COMPATIBILITY_INVALID_REQUEST': '检查参数无效。',
    'COMPATIBILITY_UNRESOLVED': '版本缺失或包库校验失败，无法完成检查。',
    'PROFILE_INVALID_REQUEST': '配置编辑内容无效。',
    'PROFILE_REVISION_CONFLICT': '配置已在其他窗口修改，请刷新后重试。',
    'PROFILE_NOT_FOUND': '配置不存在，请刷新列表。',
    'PROFILE_UNRESOLVED': '部分版本缺失或校验失败，请先补齐包库。',
    'PROFILE_MANIFEST_INVALID': '配置清单格式不受支持。',
    'PROFILE_MANIFEST_UNSAFE': '清单含有不适合导出的字段内容，请检查后重试。',
    'PROFILE_SOURCE_EXPIRED': '文件选择已过期，请重新选择。',
    'PROFILE_SOURCE_CHANGED': '清单文件已改变，请重新预览。',
    'PROFILE_PREVIEW_CHANGED': '预览已失效，请重新选择文件。',
    'PROFILE_CAPACITY': '配置内容达到大小或数量限制。',
    'PROFILE_EXPORT_REFUSED': '请选择受保护目录之外的新 JSON 文件。',
    'PROFILE_CLOSED': '工作空间已关闭。',
    'PROFILE_HISTORY_EMPTY': '当前没有可撤销或重做的编辑。',
    'INVALID_REQUEST': '请求格式或参数无效。',
    'UNSUPPORTED_COMMAND': '此桌面入口未开放该命令。',
    'INTERNAL_ERROR': '操作失败，请重试或查看本地诊断。',
    'SUBSCRIPTION_EXPIRED': '任务订阅已失效，请重新连接。',
    'TASK_NOT_FOUND': '任务不存在或已不在保留范围内。',
    'INVALID_CURSOR': '事件位置无效，请重新获取任务快照。',
    'CAPACITY_EXCEEDED': '当前任务或订阅已达上限，请稍后重试。',
    'RUNTIME_CLOSED': '工作空间已关闭。',
    'INSTALLATION_BUSY': '安装检查尚未结束，请等待或取消。',
    'SOURCE_EXPIRED': '目录授权已失效，请重新选择目录。',
    'CANDIDATE_EXPIRED': '候选安装已失效，请重新发现。',
    'INSTALLATION_INCOMPLETE': '安装文件不完整，不能选择。',
    'INSTALLATION_NOT_FOUND': '已登记安装不存在。',
    'INVENTORY_SCAN_NOT_FOUND': '库存扫描代次不存在。',
    'INVENTORY_ENTRY_NOT_FOUND': '该扫描代次中没有此库存条目。',
    'EVIDENCE_NOT_FOUND': '此安装中没有该日志证据。',
    'DIAGNOSTIC_SESSION_NOT_FOUND': '此安装中没有该日志会话。',
    'DIAGNOSTIC_SNAPSHOT_CHANGED': '诊断依据已变化，请刷新首页。',
    'DIAGNOSTIC_QUERY_LIMIT': '诊断读取达到时间限额，请选择具体会话重试。',
    'DIAGNOSTIC_RESULT_LIMIT': '诊断输出超过窗口限额。',
    'LOG_WATCH_OPTIONS_CHANGED': '日志监视正在运行；请先取消原任务，再更改日志选择。',
    'INVALID_INSTALLATION_SOURCE': '安装来源记录无效，请重新选择目录。',
}


from backend.ai.desktop_runtime import MESSAGES as AI_MESSAGES
MESSAGES.update(AI_MESSAGES)
MESSAGES.update(WORKSPACE_MESSAGES)
from backend.online.nexus import MESSAGES as NEXUS_MESSAGES
MESSAGES.update(NEXUS_MESSAGES)
from .storage_location import MESSAGES as STORAGE_MESSAGES
MESSAGES.update(STORAGE_MESSAGES)


class DesktopApi:
    def __init__(self, context, *, test_commands=None):
        self._context = context
        self._owner = str(uuid4())
        self._lock = threading.RLock()
        self._closed = False
        self._latest_check_task_id = None
        self._commands = {
            'ai.settings.get': (Empty, lambda _,p:context.ai.settings()),
            'ai.settings.save': (AISettingsPayload, lambda _,p:context.ai.save(
                dict(provider=p.provider,base_url=p.base_url,model=p.model),p.api_key,p.remember)),
            'ai.credentials.clear': (Empty, lambda _,p:context.ai.clear_key()),
            'ai.test': (Empty, lambda request_id,p:context.ai.test(request_id)),
            'ai.chat.send': (AISendPayload, lambda request_id,p:context.ai.send(request_id,p.question,
                p.selected_text,p.selected_label,str(p.session_id) if p.session_id else None,
                str(p.installation_id) if p.installation_id else None,
                str(p.selected_evidence_id) if p.selected_evidence_id else None)),
            'ai.chat.get': (AISessionPayload, lambda _,p:context.ai.get(str(p.session_id))),
            'ai.chat.clear': (AISessionPayload, lambda _,p:context.ai.clear(str(p.session_id))),
            'ai.chat.export': (AISessionPayload, lambda _,p:context.ai.export(str(p.session_id))),
            'ai.logs.list': (AILogPagePayload, lambda _,p:context.ai.log_previews(str(p.installation_id),p.after,p.limit)),
            'ai.suggestion.resolve': (AISuggestionPayload, lambda _,p:context.ai.resolve_suggestion(str(p.session_id),str(p.suggestion_id))),
            'mod.list': (ModPagePayload, self._mod_list),
            'mod.action': (ModAction, self._mod_action),
            'mod.workspace': (WorkspaceQuery, lambda rid,p: self._mod_job(rid, lambda task: context.mod_workspace.list(str(p.installation_id), check=task.check_cancelled))),
            'mod.workspace.detail': (WorkspaceDetail, lambda rid,p: self._mod_job(rid, lambda task: context.mod_workspace.detail(str(p.installation_id),str(p.id),p.offset,p.limit,check=task.check_cancelled))),
            'mod.workspace.save': (WorkspaceSave, lambda rid,p: self._mod_job(rid, lambda task: context.mod_workspace.save(p.model_dump(),check=task.check_cancelled,commit=task.commit,progress=task.progress))),
            'game.launch': (UseInstallationPayload, lambda _, p: context.launcher.launch(str(p.installation_id))),
            'game.status': (UseInstallationPayload, lambda _, p: context.launcher.status(str(p.installation_id))),
            'config.read': (ConfigReadPayload, lambda _, p: context.mod_configs.read(str(p.installation_id), p.adapter_id)),
            'config.save': (ConfigSavePayload, lambda _, p: context.mod_configs.save(
                str(p.installation_id), p.adapter_id, p.expected_revision, p.changes, p.backup)),
            'plan.create': (PlanProposal, self._plan_create),
            'plan.inspect': (PlanPage, self._plan_inspect),
            'plan.approve': (PlanIdentity, self._plan_approve),
            'plan.apply': (PlanApply, self._plan_apply),
            'assistant.apply_plan': (PlanApply, self._plan_apply),
            'deployment.status': (DeploymentStatusPayload, lambda _, p: context.deployments.status(p.model_dump(mode='json'))),
            'deployment.recover': (UseInstallationPayload, self._deployment_recover),
            'deployment.history': (HistoryPage, self._deployment_history),
            'deployment.rollback.create': (RollbackProposal, self._rollback_create),
            'deployment.rollback': (PlanApply, self._plan_apply),
            'deployment.conflicts.recheck': (IssueRecheck, self._conflicts_recheck),
            'assistant.propose_plan': (PlanProposal, self._assistant_propose_plan),
            'assistant.inspect_plan': (PlanPage, self._assistant_inspect_plan),
            'app.info': (Empty, lambda request_id, payload: self._app_info()),
            'app.storage.status': (Empty, lambda _,p: context.storage.status()),
            'app.storage.choose': (Empty, lambda _,p: {'target': context.choose_storage_folder()}),
            'app.storage.accept': (Empty, lambda _,p: context.storage.accept()),
            'app.storage.start': (StorageTarget, lambda _,p: context.storage.start(p.target)),
            'app.storage.restart': (Empty, lambda _,p: context.storage.restart()),
            'app.theme.get': (Empty, lambda _, p: context.preferences.theme()),
            'window.control': (WindowPayload, lambda _, p: self._window_control(p.op)),
            'app.theme.set': (ThemePayload, lambda _, p: context.preferences.set_theme(p.theme_id)),
            'app.check': (Empty, self._check),
            'installation.choose': (ChooseInstallationPayload, self._installation_choose),
            'installation.discover': (DiscoverInstallationPayload, lambda request_id, p: context.installations.discover(self._jobs(), request_id, [str(value) for value in p.source_handles])),
            'installation.auto_discover': (Empty, lambda request_id, p: context.installations.auto_discover(self._jobs(), request_id)),
            'installation.select': (SelectInstallationPayload, lambda request_id, p: context.installations.select(self._jobs(), request_id, str(p.candidate_id))),
            'installation.use': (UseInstallationPayload, lambda request_id, p: context.installations.use(self._jobs(), request_id, str(p.installation_id))),
            'installation.list': (Empty, lambda _, p: context.installations.list()),
            'deployment.guard_status': (UseInstallationPayload, lambda _, p: context.deployment_guards.status(str(p.installation_id))),
            'loader.status': (UseInstallationPayload, lambda rid,p: self._loader_job(rid, lambda task: context.loader.status(str(p.installation_id), check=task.check_cancelled))),
            'loader.choose': (UseInstallationPayload, lambda _,p: self._loader_choose(str(p.installation_id))),
            'loader.install': (UseInstallationPayload, lambda rid,p: self._loader_install(rid,p)),
            'loader.install_local': (LoaderLocalPayload, lambda rid,p: self._loader_install(rid,p)),
            'loader.repair': (UseInstallationPayload, lambda rid,p: self._loader_job(rid, lambda task: context.loader.repair(
                str(p.installation_id), check=task.check_cancelled, progress=task.progress, commit=task.commit))),
            'package.adopt_preview': (UseInstallationPayload, self._package_preview),
            'package.adopt_files': (AdoptionPagePayload, lambda _,p: context.packages.preview_page(str(p.snapshot_id),p.offset,p.limit)),
            'package.adopt': (AdoptPackagePayload, self._package_adopt),
            'package.list': (PackagePagePayload, self._package_list),
            'package.detail': (PackageDetailPayload, self._package_detail),
            'profile.list': (UseInstallationPayload, lambda _,p: context.profiles.list(str(p.installation_id))),
            'profile.create': (ProfileCreate, lambda _,p: context.profiles.create(str(p.installation_id),p.name)),
            'profile.get': (ProfileIdentity, lambda _,p: context.profiles.get(str(p.profile_id))),
            'profile.select': (ProfileIdentity, lambda _,p: context.profiles.select(str(p.profile_id))),
            'profile.edit': (ProfileEdit, lambda _,p: context.profiles.edit(str(p.profile_id),p.expected_revision,p.action)),
            'profile.clone': (ProfileClone, lambda _,p: context.profiles.clone(str(p.profile_id),p.expected_revision,p.name)),
            'profile.delete': (ProfileRevision, lambda _,p: context.profiles.delete(str(p.profile_id),p.expected_revision)),
            'profile.commit': (ProfileRevision, self._profile_commit),
            'compatibility.check': (CompatibilityPayload, self._compatibility_check),
            'profile.export': (ProfileIdentity, lambda _,p: {'manifest':context.profiles.export(str(p.profile_id))}),
            'profile.save_export': (ProfileIdentity, self._profile_save_export),
            'profile.choose': (Empty, lambda _,p: context.profiles.grant(context.choose_profile_source())),
            'profile.import_preview': (ProfilePreview, lambda _,p: context.profiles.import_preview(str(p.source_handle),str(p.installation_id))),
            'profile.import_commit': (ProfileImport, lambda _,p: context.profiles.import_commit(str(p.source_handle),p.preview_hash,str(p.installation_id),p.name)),
            'package.choose': (ChooseImportPayload, lambda _,p: self._import_result(lambda: context.imports.grant(p.kind, context.choose_import_source(p.kind)))),
            'package.import_preview': (ImportPreviewPayload, self._import_preview),
            'package.import_files': (ImportFilesPayload, lambda _,p: context.imports.page(str(p.preview_id),p.offset,p.limit)),
            'package.import_commit': (ImportCommitPayload, self._import_commit),
            'inventory.inspect': (InspectMetadataPayload, lambda request_id,p: context.metadata.inspect(self._jobs(),request_id,str(p.installation_id),p.files)),
            'inventory.scan': (ScanInventoryPayload, lambda request_id,p: context.inventory.scan(self._jobs(),request_id,str(p.installation_id),p.force)),
            'inventory.list': (ListInventoryPayload, lambda _,p: context.inventory.list(str(p.installation_id),p.generation,p.limit,p.offset)),
            'inventory.detail': (DetailInventoryPayload, lambda _,p: context.catalog.detail(str(p.installation_id),str(p.entry_id),p.generation)),
            'inventory.contents': (ContentInventoryPayload, lambda _,p: context.catalog.contents(str(p.installation_id),str(p.entry_id),p.generation,p.offset,p.limit)),
            'inventory.graph': (GraphInventoryPayload, lambda _,p: context.catalog.graph(str(p.installation_id),p.generation,p.offset,p.limit)),
            'logs.watch': (WatchLogsPayload, lambda request_id,p: context.logs.watch(self._jobs(),request_id,str(p.installation_id),p.user_logs)),
            'logs.sources': (UseInstallationPayload, lambda _,p: context.logs.sources(str(p.installation_id))),
            'evidence.list': (EvidenceListPayload, lambda _,p: context.logs.evidence(str(p.installation_id),p.after,p.limit,str(p.session_id) if p.session_id else None,str(p.group_id) if p.group_id else None)),
            'evidence.read': (EvidenceReadPayload, lambda _,p: context.logs.read(str(p.installation_id),str(p.evidence_id),p.offset,p.limit)),
            'session.list': (SessionListPayload, lambda _,p: context.logs.sessions(str(p.installation_id),p.after,p.limit)),
            'diagnostics.explain': (DiagnosticPayload, lambda _,p: context.diagnostics.explain(str(p.installation_id),p.generation,str(p.session_id) if p.session_id else None,p.offset,p.limit,p.snapshot_id)),
            'task.snapshot': (TaskPayload, lambda _, p: self._jobs().snapshot(str(p.task_id))),
            'task.cancel': (TaskPayload, lambda _, p: self._jobs().cancel(str(p.task_id))),
            'task.subscribe': (SubscribePayload, lambda _, p: self._jobs().subscribe(str(p.task_id), p.after_sequence, self._owner)),
            'task.events': (EventsPayload, lambda _, p: self._jobs().events(str(p.subscription_id), p.after_sequence, self._owner)),
            'task.unsubscribe': (SubscriptionPayload, self._unsubscribe),
        }
        from backend.ai.diagnostics_api import diagnostic_commands
        self._commands.update(diagnostic_commands(context))
        from backend.profiles.transfer_api import commands as profile_transfer_commands
        from backend.catalog.update_api import commands as package_update_commands
        from backend.online.api import commands as nexus_commands
        self._commands.update(profile_transfer_commands(context))
        self._commands.update(package_update_commands(context))
        self._commands.update(nexus_commands(context))
        # This is constructor injection for the real desktop test harness only.
        # It has no request flag, environment toggle or command to enable it.
        for name, definition in (test_commands or {}).items():
            if not isinstance(name, str) or not name.startswith('test.') or len(name) > 64:
                raise ValueError('Invalid test command name')
            self._commands[name] = definition

    def dispatch(self, request):
        from backend.contracts import ErrorDTO
        from backend.runtime.jobs import RuntimePolicyError
        request_id = request.get('request_id') if isinstance(request, dict) else None
        valid_id = isinstance(request_id, str) and 0 < len(request_id) <= 128 and bool(request_id.strip()) and all(32 <= ord(c) < 127 for c in request_id)
        def failure(code):
            # Never echo invalid payloads, exception text, credentials or paths.
            public_code = code if code in MESSAGES else 'INTERNAL_ERROR'
            return {'request_id': request_id if valid_id else None, 'ok': False,
                    'error': ErrorDTO(code=public_code, message=MESSAGES[public_code], retryable=False,
                                      details={}, evidence_ids=[]).model_dump(mode='json', exclude={'schema_version'})}
        try:
            if (not isinstance(request, dict) or set(request) != {'schema_version', 'request_id', 'command', 'payload'}
                    or type(request['schema_version']) is not int or request['schema_version'] != 1
                    or not valid_id or not isinstance(request['command'], str) or type(request['payload']) is not dict
                    or len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')) > (32768 if request.get('command')=='ai.chat.send' else 16384)):
                return failure('INVALID_REQUEST')
            with self._lock:
                if self._closed:
                    return failure('RUNTIME_CLOSED')
                storage = getattr(self._context, 'storage', None)
                if (storage is not None and storage.blocked and request['command'] not in
                        {'app.storage.status', 'app.storage.restart', 'window.control'}):
                    return failure('STORAGE_BUSY')
                if request['command'] not in self._commands:
                    return failure('UNSUPPORTED_COMMAND')
                schema, handler = self._commands[request['command']]
                payload = schema.model_validate(request['payload'])
                data = handler(request_id, payload)
                response = {'request_id': request_id, 'ok': True, 'data': data}
                if request['command'] in ('compatibility.check', 'profile.commit', 'package.import_preview', 'package.import_commit', 'package.list', 'package.detail', 'package.adopt_preview', 'package.adopt', 'app.check', 'installation.discover', 'installation.auto_discover', 'installation.select', 'installation.use', 'inventory.inspect', 'inventory.scan', 'logs.watch', 'assistant.propose_plan', 'assistant.inspect_plan') or request['command'].startswith(('test.', 'plan.', 'loader.', 'profile.', 'package.update_', 'package.import_version', 'nexus.')):
                    if isinstance(data, dict) and 'task_id' in data:
                        response['task_id'] = data['task_id']
                return response
        except (ValidationError, TypeError, ValueError, OverflowError) as exc:
            return failure(getattr(exc, 'code', 'INVALID_REQUEST'))
        except RuntimePolicyError as exc:
            code = {'TASK_LIMIT': 'CAPACITY_EXCEEDED', 'SUBSCRIPTION_LIMIT': 'CAPACITY_EXCEEDED',
                    'INVALID_SEQUENCE': 'INVALID_CURSOR', 'SUBSCRIPTION_NOT_FOUND': 'SUBSCRIPTION_EXPIRED'}.get(exc.code, exc.code)
            return failure(code)
        except Exception:
            return failure('INTERNAL_ERROR')

    def _window_control(self, operation):
        frame = getattr(self._context, 'window_frame', None)
        if frame is None:
            raise RuntimeError('window controls unavailable')
        return frame.control(operation)

    def _jobs(self):
        if self._context.jobs is None:
            from backend.runtime.jobs import RuntimePolicyError
            raise RuntimePolicyError('RUNTIME_CLOSED')
        return self._context.jobs

    def _plan_job(self, request_id, action):
        def work(task):
            from backend.deployments.policy import PlanRejected
            from backend.deployments.paths import GuardRejected
            from backend.profiles.compatibility import CompatibilityError
            try:
                return action(task)
            except (PlanRejected, GuardRejected, CompatibilityError) as exc:
                messages = {
                    'PLAN_UNMANAGED_CONFLICT':'目标文件尚未接管，请先确认接管后重新预览。',
                    'PLAN_MANAGED_DRIFT':'已托管文件与记录不符，请保留当前文件并重新核查。',
                    'PLAN_SPACE_INSUFFICIENT':'部署、暂存与备份的合并空间不足。',
                    'PLAN_COMPATIBILITY_BLOCKED':'方案存在硬依赖、互斥或文件目标冲突，无法生成批准计划。',
                    'PLAN_CONFIG_UNSUPPORTED':'此方案包含未适配的配置变更，当前不能生成部署计划。',
                    'PLAN_PROFILE_DIRTY':'请先保存当前方案，再预览部署。',
                    'PLAN_STAGING_UNAVAILABLE':'当前数据目录无法提供目标同卷且扫描树外的暂存位置。',
                    'ROLLBACK_DRIFT':'文件存在外部改动或未知占位，已记录恢复问题；请查看历史中的保全结果与差异。',
                    'ROLLBACK_SUPERSEDED':'后续部署已改变此事务的文件，请先回退后续部署。',
                    'ROLLBACK_BACKUP_MISSING':'原事务备份缺失，不能自动还原；已保留恢复问题。',
                    'ROLLBACK_BACKUP_CORRUPT':'原事务备份校验失败，不能自动还原；已保留恢复问题。',
                    'ROLLBACK_SOURCE_CHANGED':'回退来源或备份身份已改变，请重新生成预览并批准。',
                    'ROLLBACK_GENERATION_STALE':'当前部署代次已改变，请刷新历史后重新预览。',
                }
                return {'error_code': exc.code, 'message': messages.get(exc.code,'计划未获批准，请刷新方案与安装事实后重新预览。'),
                        'conflicts':getattr(exc,'conflicts',[])}
            except (ValueError, OSError, KeyError, sqlite3.Error):
                return {'error_code': 'PLAN_INPUT_INVALID', 'message': '计划输入无法完整校验，请重新保存方案并预览。'}
        return self._jobs().start(request_id, work)

    def _loader_choose(self, installation_id):
        from backend.loaders.melonloader.service import LoaderError
        try:
            return self._context.loader.grant(installation_id, self._context.choose_loader_source())
        except LoaderError as exc:
            return {'error_code': exc.code, 'message': str(exc)}

    def _loader_install(self, request_id, payload):
        return self._loader_job(request_id, lambda task: self._context.loader.install(
            str(payload.installation_id), str(payload.source_handle) if isinstance(payload, LoaderLocalPayload) else None,
            check=task.check_cancelled, progress=task.progress, commit=task.commit))

    def _loader_job(self, request_id, action):
        def work(task):
            from backend.loaders.melonloader.service import LoaderError
            from backend.deployments.paths import GuardRejected
            try:
                return action(task)
            except LoaderError as exc:
                return {'error_code': exc.code, 'message': str(exc)}
            except GuardRejected as exc:
                return {'error_code': exc.code, 'message': '暂时无法操作此游戏目录，请关闭游戏和其他占用程序后重试。'}
            except (OSError, ValueError, KeyError) as exc:
                reason = getattr(exc, 'winerror', None)
                message = '加载器文件无法读写，请检查目录权限和占用后重试。'
                if reason in (32,33): message = '加载器文件被占用，请关闭游戏或占用程序后重试。'
                return {'error_code': 'LOADER_FILE_OPERATION_FAILED', 'message': message}
        return self._jobs().start(request_id, work)

    def _mod_job(self, request_id, action):
        def work(task):
            try:
                result = action(task)
                if result.get('error_code'):
                    reason = result.get('io_error', {}).get('kind')
                    result['message'] = {'file_busy':'文件被占用，请关闭游戏或占用文件的程序后重试。',
                        'permission_denied':'无法修改文件，请检查目录权限或文件只读属性后重试。',
                        'disk_full':'磁盘空间不足，请腾出空间后重试。'}.get(reason,
                            {'EXEC_STAGE_INVALID':'暂存文件或事务目录已变化，已停止操作。',
                             'PLAN_MANAGED_DRIFT':'安装文件已被外部修改，已保留；请检查修改后重试。',
                             'PLAN_UNMANAGED_CONFLICT':'目标已有其他文件，已保留；请检查文件冲突。',
                             'PLAN_SPACE_INSUFFICIENT':'磁盘空间不足，请腾出空间后重试。',
                             'EXEC_EXTERNAL_CONFLICT':'操作期间文件被外部修改，已保留；请检查这些文件。'}
                            .get(result['error_code'], '文件操作未完成；请检查目录权限、占用和包内容后重试。'))
                    if result.get('recovery_required'):
                        result['message'] += ' 本次变更尚未恢复完成，请关闭游戏后重新打开管理器。'
                return result
            except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
                code = getattr(exc, 'code', 'MOD_ACTION_FAILED')
                messages = {
                    'PLAN_UNMANAGED_CONFLICT': '目标已有未安装到本工具的文件，已保留；请换一个安装位置或确认原文件。',
                    'PLAN_MANAGED_DRIFT': '安装文件已被外部修改，已保留；请检查修改后重试。',
                    'PLAN_TARGET_CONFLICT': '两个 Mod 使用相同文件位置但内容不同，请先停用冲突的 Mod。',
                    'PLAN_TARGET_NOT_ALLOWED': '包中包含不支持的目标，只能安装到 Mods、Plugins 或 UserLibs。',
                    'PLAN_SPACE_INSUFFICIENT': '磁盘空间不足，请腾出空间后重试。',
                    'PLAN_STAGING_UNAVAILABLE': '无法建立游戏同卷的安全事务目录，请检查游戏所在磁盘及父目录权限。',
                    'EXEC_STAGE_INVALID': '暂存文件或事务目录已变化，已停止操作。',
                    'MOD_NOT_INSTALLED': '此 Mod 尚未安装，请先加入加载列表并保存。',
                    'MOD_STATE_CHANGED': '安装状态已经变化，请刷新后重试。',
                    'GUARD_PROCESS_RUNNING': '游戏正在运行，请关闭游戏后重试。',
                    'GUARD_LEASE_BUSY': '正在处理此游戏的其他操作，请稍后重试。',
                    'GUARD_RECOVERY_REQUIRED': '上次文件操作尚未完成，请重新打开管理器或重试恢复。',
                }
                native = getattr(exc, 'native_error', None) or getattr(exc, 'winerror', None)
                io_message = {32:'文件被占用，请关闭游戏或占用程序后重试。',
                    33:'文件被占用，请关闭占用程序后重试。', 5:'没有文件修改权限，请检查目录权限或只读属性。',
                    112:'磁盘空间不足，请腾出空间后重试。'}.get(native)
                return dict(error_code=code, message=io_message or messages.get(code, MESSAGES.get(code,
                    '无法完成文件操作；请检查包内容、目录权限和占用，再重试。')))
        return self._jobs().start(request_id, work)

    def _mod_list(self, request_id, payload):
        return self._mod_job(request_id, lambda task: self._context.mods.list(str(payload.installation_id),
            payload.offset, payload.limit, check=task.check_cancelled))

    def _mod_action(self, request_id, payload):
        return self._mod_job(request_id, lambda task: self._context.mods.action(payload.model_dump(),
            check=task.check_cancelled, commit=task.commit, progress=task.progress))

    def _plan_create(self, request_id, payload):
        return self._plan_job(request_id, lambda task: self._context.plans.create(payload.model_dump(),
            check=task.check_cancelled, commit=task.commit))

    def _plan_inspect(self, request_id, payload):
        return self._plan_job(request_id, lambda task: self._context.plans.inspect(payload.model_dump(),
            check=task.check_cancelled))

    def _plan_approve(self, request_id, payload):
        return self._plan_job(request_id, lambda task: self._context.plans.approve(payload.model_dump(),
            confirm=self._context.confirm_plan, check=task.check_cancelled, commit=task.commit))

    def _plan_apply(self, request_id, payload):
        return self._plan_job(request_id, lambda task: self._context.deployments.apply(payload.model_dump(),
            check=task.check_cancelled, commit=task.commit, progress=task.progress))

    def _deployment_recover(self, request_id, payload):
        # Once restoration starts it owns a bounded recovery budget, not an
        # interruptible sequence of independent file writes.
        return self._plan_job(request_id, lambda task: self._context.recovery.recover(payload.model_dump()))

    def _deployment_history(self, request_id, payload):
        return self._plan_job(request_id,lambda task:self._context.history.list(payload.model_dump(),check=task.check_cancelled))

    def _rollback_create(self, request_id, payload):
        return self._plan_job(request_id,lambda task:self._context.rollbacks.create(payload.model_dump(),
            check=task.check_cancelled,commit=task.commit))

    def _conflicts_recheck(self, request_id, payload):
        # Private recovery bookkeeping is completed as one bounded operation.
        return self._plan_job(request_id,lambda task:self._context.history.recheck(payload.model_dump()))

    def _assistant_propose_plan(self, request_id, payload):
        return self._plan_job(request_id, lambda task: self._context.plan_proposals.preview(payload.model_dump(),
            check=task.check_cancelled,commit=task.commit))

    def _assistant_inspect_plan(self, request_id, payload):
        return self._plan_job(request_id, lambda task: self._context.plan_proposals.inspect(payload.model_dump(),
            check=task.check_cancelled))

    def _package_preview(self, request_id, payload):
        def work(task):
            from backend.catalog.packages import AdoptionError
            try:
                return self._context.packages.preview(str(payload.installation_id), cancel=task.check_cancelled, progress=task.progress)
            except AdoptionError as exc:
                return {'error_code': exc.code, 'issues': exc.issues}
            except (ValueError, OSError):
                return {'error_code': 'ADOPTION_READ_FAILED'}
        return self._jobs().start(request_id, work)

    def _package_list(self, request_id, payload):
        return self._jobs().start(request_id, lambda task: self._context.packages.list(
            payload.offset, payload.limit, check=task.check_cancelled))

    def _package_detail(self, request_id, payload):
        def work(task):
            from backend.catalog.packages import PackageDetailError
            try:
                return self._context.packages.detail(str(payload.release_id), payload.manifest_hash,
                    payload.offset, payload.limit, check=task.check_cancelled)
            except PackageDetailError as exc:
                messages = {
                    'PACKAGE_DETAIL_INVALID_REQUEST': '详情请求无效，请重新选择版本。',
                    'PACKAGE_DETAIL_LOCK_CHANGED': '版本锁不一致，请刷新包库后重新选择。',
                    'PACKAGE_DETAIL_NOT_FOUND': '本地版本不存在，请重新导入原文件。',
                    'PACKAGE_DETAIL_CORRUPT': '包库文件缺失或校验失败，请重新导入原文件。',
                    'PACKAGE_DETAIL_LIMIT': '文件或归属记录超过检查限制，请减少包内容后重试。',
                }
                return {'error_code': exc.code, 'message': messages.get(exc.code, '详情读取失败，请刷新包库。')}
        return self._jobs().start(request_id, work)

    def _profile_commit(self, request_id, payload):
        def work(task):
            from backend.profiles.service import ProfileError
            try:
                result = self._context.profiles.commit(str(payload.profile_id), payload.expected_revision,
                    check=task.check_cancelled, commit=task.commit)
                return {key:result[key] for key in ('profile_id','revision','committed_revision','dirty')}
            except ProfileError as exc:
                return {'error_code':exc.code, 'message':MESSAGES.get(exc.code, '配置未保存。')}
        return self._jobs().start(request_id, work)

    def _compatibility_check(self, request_id, payload):
        def work(task):
            from backend.profiles.compatibility import CompatibilityError
            try:
                return self._context.compatibility.check(str(payload.profile_id), payload.expected_revision,
                    profile_kind=payload.profile_kind,
                    enable_release_ids=[str(value) for value in payload.enable_release_ids],
                    disable_release_ids=[str(value) for value in payload.disable_release_ids],
                    check=task.check_cancelled)
            except CompatibilityError as exc:
                return {'error_code':exc.code, 'message':MESSAGES.get(exc.code, '检查未完成，请刷新配置后重试。')}
        return self._jobs().start(request_id, work)

    def _profile_save_export(self, _, payload):
        from .profile_export import save_manifest
        manifest = self._context.profiles.export(str(payload.profile_id))
        return save_manifest(self._context.paths, self._context.choose_profile_destination(), manifest)

    @staticmethod
    def _import_result(action):
        from backend.catalog.archive_policy import ImportRejected
        import zipfile
        try:
            return action()
        except (ImportRejected, OSError, zipfile.BadZipFile) as exc:
            code = exc.code if isinstance(exc, ImportRejected) else 'IMPORT_IO_FAILED'
            messages = {
                'IMPORT_SCRIPT_REFUSED': '文件包含安装脚本或可执行程序，无法导入。',
                'IMPORT_NESTED_ARCHIVE': '不支持嵌套压缩包，请先整理文件。',
                'IMPORT_METADATA_REJECTED': 'DLL 未通过隔离检查，未加入包库。',
                'IMPORT_SOURCE_EXPIRED': '文件选择已过期，请重新选择。',
                'IMPORT_PREVIEW_EXPIRED': '预览已过期，请重新选择文件。',
                'IMPORT_NAME_CONFLICT': '文件名称或目标位置存在冲突，无法导入。',
                'IMPORT_SOURCE_PROTECTED': '所选来源位于受保护目录内，或包含受保护目录。请选择独立存放的模组文件。',
                'IMPORT_SOURCE_PROTECTED_LIBRARY': '所选来源位于管理器的数据目录内，或包含该目录。已有包请在包库中选择；重新导入请使用独立存放的原始文件。',
                'IMPORT_SOURCE_PROTECTED_REFERENCE': '所选来源位于受保护的参考源码目录内，或包含该目录。请选择独立存放的模组 DLL、目录或 ZIP。',
                'IMPORT_FORMAT_UNSUPPORTED': '仅支持本地 DLL、目录和 ZIP 文件。',
                'IMPORT_LINK_REFUSED': '来源包含链接或重解析点，无法安全导入。',
                'IMPORT_PATH_INVALID': '文件路径包含不支持或不明确的名称，请检查文件名及 ZIP 内的相对路径。',
                'IMPORT_PATH_UNAVAILABLE': '无法读取所选文件。请确认文件仍存在、未被其他程序占用，并且有读取权限。',
                'IMPORT_ARCHIVE_INVALID': 'ZIP 文件损坏或使用了不支持的格式。请使用未加密、非分卷的标准 ZIP 文件。',
                'IMPORT_ARCHIVE_LIMIT': 'ZIP 文件超过 64 MiB，请拆分为较小的模组包后重试。',
                'IMPORT_ENTRY_LIMIT': '目录或 ZIP 条目过多。单次最多支持 4000 个文件、8000 个文件和目录条目。',
                'IMPORT_FILE_LIMIT': '单个文件超过 64 MiB，无法导入。',
                'IMPORT_TOTAL_LIMIT': '文件数量或展开大小超过限制。单次最多支持 4000 个文件、展开后共 256 MiB。',
                'IMPORT_RATIO_LIMIT': 'ZIP 的压缩比超过安全限制（100 倍）。请使用较低压缩率重新打包。',
                'IMPORT_TIME_LIMIT': '读取文件超时（120 秒）。请拆分为较小的模组包后重试。',
                'IMPORT_SIZE_MISMATCH': '文件实际内容与声明的大小或校验值不一致，请重新获取文件后重试。',
                'IMPORT_EMPTY': '所选来源没有可导入的文件。',
                'IMPORT_SOURCE_CHANGED': '所选文件在读取前发生了变化，请重新选择。',
                'IMPORT_PREVIEW_CHANGED': '预览内容已发生变化，请重新选择文件并核对。',
                'IMPORT_CAPACITY': '当前保留的预览较多，请关闭导入窗口后重试。',
                'IMPORT_BUSY': '正在处理导入，请等待完成或取消当前任务。',
            }
            return {'can_commit': False, 'error_code': code,
                    'message': messages.get(code, '导入未完成：文件不符合安全限制，或暂存空间不可用。')}

    def _import_preview(self, request_id, payload):
        return self._jobs().start(request_id, lambda task: self._import_result(lambda:
            self._context.imports.preview(str(payload.source_handle), check=task.check_cancelled)))

    def _import_commit(self, request_id, payload):
        return self._jobs().start(request_id, lambda task: self._import_result(lambda:
            self._context.imports.commit(str(payload.preview_id), payload.preview_hash, payload.display_name,
                payload.version_raw, check=task.check_cancelled, commit=task.commit)))

    def _package_adopt(self, request_id, payload):
        def work(task):
            try:
                result = self._context.packages.adopt(str(payload.snapshot_id), payload.manifest_hash, payload.paths,
                    payload.display_name, payload.version_raw, payload.package_id, cancel=task.check_cancelled, commit=task.commit)
            except (ValueError, OSError):
                return {'error_code': 'ADOPTION_COMMIT_FAILED'}
            return {'release': result['release'], 'snapshot_id': result['snapshot_id'], 'deployment_generation': None}
        return self._jobs().start(request_id, work)

    def _installation_choose(self, request_id, payload):
        selected = self._context.choose_installation_folder(payload.kind)
        return self._context.installations.grant(payload.kind, selected)

    def _app_info(self):
        paths = self._context.paths
        return dict(name=APP_NAME, version=APP_VERSION, data_root=str(paths.root),
                    storage_first_run=self._context.storage.status()['first_run'],
                    config_path=str(paths.config), database_path=str(paths.database), log_path=str(paths.log),
                    keyring_service=KEYRING_SERVICE, update_source=UPDATE_SOURCE,
                    latest_check_task_id=self._latest_check_task_id)

    def _check(self, request_id, payload):
        snapshot = self._jobs().start(request_id, self._workspace_check)
        self._latest_check_task_id = snapshot['task_id']
        return snapshot

    def _workspace_check(self, task):
        # Each worker owns its own read-only connection; never reuse the UI
        # thread's Store connection from a pywebview dispatch/background thread.
        from backend.storage.schema import CURRENT_SCHEMA, ENTITY_TABLES
        from .identity import DATABASE_APPLICATION_ID
        paths = self._context.paths
        paths.check_owned_paths()
        task.check_cancelled()
        task.progress(0, 3, 'database', 'CHECK_DATABASE')
        db = sqlite3.connect(paths.database.as_uri() + '?mode=ro', uri=True, timeout=2)
        db.set_progress_handler(lambda: int(task.cancelled), 1000)
        try:
            if db.execute('PRAGMA application_id').fetchone()[0] != DATABASE_APPLICATION_ID:
                raise RuntimeError('Unexpected database identity')
            if db.execute('PRAGMA user_version').fetchone()[0] != CURRENT_SCHEMA:
                raise RuntimeError('Unexpected database schema')
            task.check_cancelled()
            task.progress(1, 3, 'integrity', 'CHECK_INTEGRITY')
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise RuntimeError('Database integrity failed')
            task.check_cancelled()
            task.progress(2, 3, 'references', 'CHECK_REFERENCES')
            if db.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('Database references failed')
            count = sum(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table, _ in ENTITY_TABLES.values())
            task.check_cancelled()
            task.progress(3, 3, 'complete', 'CHECK_COMPLETE')
            return {'database_schema': CURRENT_SCHEMA, 'entity_count': count}
        finally:
            db.close()

    def _unsubscribe(self, request_id, payload):
        self._jobs().unsubscribe(str(payload.subscription_id), self._owner)
        return {}

    def _page_loading(self):
        with self._lock:
            if self._context.jobs is not None:
                self._context.jobs.close_owner(self._owner)
            self._owner = str(uuid4())

    def _close(self):
        with self._lock:
            self._closed = True
            if self._context.jobs is not None:
                self._context.jobs.close_owner(self._owner)
