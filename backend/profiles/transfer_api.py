"""Closed desktop commands for portable profiles and ordinary application."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Capture(Empty):
    installation_id: UUID
    name: str = Field(min_length=1, max_length=120)
    profile_id: UUID | None = None
    expected_revision: StrictInt | None = Field(default=None, ge=0)


class Identity(Empty):
    profile_id: UUID


class Export(Identity):
    kind: Literal['manifest', 'bundle']


class Preview(Empty):
    source_handle: UUID
    installation_id: UUID


class Import(Preview):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    name: str = Field(min_length=1, max_length=120)


class Apply(Identity):
    expected_revision: StrictInt = Field(ge=0)
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key: UUID


def job(context, request_id, action):
    def run(task):
        from backend.profiles.serialization import ProfileError
        from backend.catalog.archive_policy import ImportRejected
        from backend.deployments.policy import PlanRejected
        from backend.deployments.paths import GuardRejected
        try:
            return action(task)
        except (ProfileError, ImportRejected, PlanRejected, GuardRejected) as error:
            messages = {
                'PROFILE_CAPTURE_UNSAFE': '当前列表有缺失、外部改动或不可管理的 Mod，请先检查后再保存方案。',
                'PROFILE_BUNDLE_UNSAFE': '整合包包含游戏文件、配置凭据、脚本或不支持的 Mod 目标，已拒绝。',
                'PROFILE_BUNDLE_TAMPERED': '整合包文件或版本清单已被修改，请重新获取完整分享包。',
                'PROFILE_BUNDLE_CONFLICT': '本地存在同一版本标识但内容不同，未覆盖本地版本。',
                'PROFILE_EXPORT_REFUSED': '请选择游戏和管理器数据目录之外的新文件名；不会覆盖已有文件。',
                'PROFILE_BUNDLE_LIMIT': '整合包超过 4000 个文件或 1 GiB 容量。',
                'PROFILE_UNRESOLVED': '方案缺少匹配版本，请导入完整整合包或先补齐 Mod。',
                'PROFILE_UNSAVED': '请先保存方案修改，再应用。',
                'MOD_VERSION_CONFLICT': '同一个 Mod 只能启用一个版本，请停用旧版后重试。',
                'MOD_WORKSPACE_STALE': 'Mod 列表已改变，请刷新并重新确认。',
                'PLAN_UNMANAGED_CONFLICT': '目标有未接管文件，已保留原文件。请先核对该 Mod。',
                'PLAN_MANAGED_DRIFT': '旧版本文件已在外部修改，已保留修改并停止替换。',
            }
            issues = getattr(error, 'issues', [])
            message = messages.get(error.code, '操作未完成，请刷新并重新核对。')
            if issues:
                reasons = {'credential_file': '凭据文件', 'script': '安装脚本或程序', 'nested_archive': '嵌套压缩包',
                    'unsafe_path': '不安全路径', 'game_or_unsupported_target': '游戏或非 Mod 目标', 'unsupported_lifecycle': '未适配的可变文件'}
                item = issues[0]
                message = f"无法分享「{item.get('mod', 'Mod')}」中的 {item['relative_path']}：{reasons.get(item['reason'], item['reason'])}。其他文件未被修改。"
            return dict(error_code=error.code, message=message, issues=issues)
    return context.jobs.start(request_id, run)


def commands(context):
    def capture(request_id, payload):
        return job(context, request_id, lambda task: context.profile_sharing.capture(
            str(payload.installation_id), payload.name, str(payload.profile_id) if payload.profile_id else None,
            payload.expected_revision, check=task.check_cancelled, commit=task.commit, progress=task.progress))
    def export(request_id, payload):
        selected = context.choose_profile_share_destination(payload.kind)
        if payload.kind == 'bundle':
            return job(context, request_id, lambda task: context.profile_sharing.export_bundle(str(payload.profile_id), selected,
                check=task.check_cancelled))
        from backend.app.profile_export import save_manifest
        return save_manifest(context.paths, selected, context.profiles.export(str(payload.profile_id)))
    return {
        'profile.capture': (Capture, capture),
        'profile.share_export': (Export, export),
        'profile.share_choose': (Empty, lambda _,p: context.profile_sharing.grant(context.choose_profile_share_source())),
        'profile.share_preview': (Preview, lambda rid,p: job(context, rid, lambda task: context.profile_sharing.preview(
            str(p.source_handle), str(p.installation_id), check=task.check_cancelled))),
        'profile.share_import': (Import, lambda rid,p: job(context, rid, lambda task: context.profile_sharing.import_bundle(
            str(p.source_handle), p.preview_hash, str(p.installation_id), p.name, check=task.check_cancelled))),
        'profile.apply_saved': (Apply, lambda rid,p: job(context, rid, lambda task: context.profile_sharing.apply(
            str(p.profile_id), p.expected_revision, p.snapshot_id, str(p.idempotency_key), check=task.check_cancelled,
            commit=task.commit, progress=task.progress))),
    }
