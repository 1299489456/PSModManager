"""Closed, read-only AI query parameters; scope comes from the user request."""
from dataclasses import dataclass
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from .gateway import AIError


UUID_PATTERN = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'


@dataclass(frozen=True)
class ToolScope:
    installation_id: str | None = None
    selected_evidence_id: str | None = None


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Page(Empty):
    offset: StrictInt = Field(default=0, ge=0, le=2000)
    limit: StrictInt = Field(default=10, ge=1, le=20)


class Search(Page):
    query: str = Field(default='', max_length=100)


class Package(Page):
    release_id: str = Field(pattern=UUID_PATTERN)
    manifest_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class Log(Empty):
    evidence_id: str = Field(pattern=UUID_PATTERN)


class Config(Empty):
    adapter_id: Literal['pswallpaper'] = 'pswallpaper'


class NativeMod(Empty):
    # Manifest IDs are case-sensitive and are not catalog release UUIDs.
    mod_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$')


class NativeModPage(NativeMod, Page):
    pass


REGISTRY = {
    'get_installation_summary': (Empty, '当前游戏的已登记版本摘要，不证明游戏运行正常。'),
    'search_mods': (Search, '搜索当前私有包库的名称/版本，不只检查第一页面；按 next_offset 游标继续，最多20项。'),
    'get_package': (Package, '查看已登记且 manifest_hash 匹配的包的名称、版本与有界文件摘要。'),
    'read_selected_log': (Log, '只读用户所选 evidence_id 的前4096字节，不能改选日志。'),
    'get_installed_mods': (Page, '查询当前游戏的Mod文件状态；enabled仅表示安装文件匹配。'),
    'get_mod_workspace': (Page, '查询当前 Mod 列表，包含外部发现尚未纳管的 Mod、包库版本、启停状态和 snapshot_id；外部 Mod 可用 suggest_workspace_action 建议启停。'),
    'get_mod_config': (Config, '只读 PSWallpaper 已支持的三个配置字段，不能读取其他配置或存档。'),
}


NATIVE_REGISTRY = {
    'get_installation_summary': (Empty, '当前原生 Mono 游戏登记摘要与实时进程状态；运行加载结果须查询本次启动对账。'),
    'search_mods': (Search, '搜索当前游戏真实 manifest ID、名称与版本；只包含已安装原生包，不查询旧 DLL 包库。'),
    'read_selected_log': REGISTRY['read_selected_log'],
    'get_installed_mods': (Page, '分页查询原生库存：安装、目标启用、游戏批准和最近 Player.log 加载证据分别返回。'),
    'get_mod_workspace': (Page, '查询原生三栏工作区当前保存顺序、revision 和各模组状态；保存顺序不保证 DLL 初始化顺序。'),
    'get_mod_detail': (NativeModPage, '用真实 manifest mod_id 查询原生包元数据、声明权限、历史批准、文件摘要；不执行或扫描 DLL。'),
    'get_mod_config': (NativeModPage, '分页读取所选原生 ModConfig 的原始字符串键值，类型保持 unknown；凭据隐藏，不读取存档。'),
    'get_mod_dependencies': (NativeMod, '查询真实 manifest 前置依赖、反向依赖，以及当前保存列表的依赖校验；不更改排序。'),
    'get_runtime_reconciliation': (Page, '对账目标启用、游戏批准与最近 Player.log 运行证据；日志证据不等于进程仍在运行。'),
}


def schemas(scope, *, native=False):
    if scope.installation_id is None:
        return []
    return [dict(type='function', function=dict(name=name, description=description,
        parameters=model.model_json_schema())) for name, (model, description) in (NATIVE_REGISTRY if native else REGISTRY).items()
        if name != 'read_selected_log' or scope.selected_evidence_id is not None]


def validate_arguments(arguments, model):
    if not isinstance(arguments, str) or len(arguments.encode('utf-8')) > 4096:
        raise AIError('AI_TOOL_ARGUMENTS')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        value = json.loads(arguments, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return model.model_validate(value).model_dump()
    except (ValueError, TypeError, ValidationError, RecursionError):
        raise AIError('AI_TOOL_ARGUMENTS') from None


def parameters(name, arguments, scope, *, native=False):
    registry = NATIVE_REGISTRY if native else REGISTRY
    if name not in registry:
        raise AIError('AI_TOOL_UNKNOWN')
    if scope.installation_id is None:
        raise AIError('AI_TOOL_SCOPE')
    data = validate_arguments(arguments, registry[name][0])
    if name == 'read_selected_log' and (scope.selected_evidence_id is None
            or data['evidence_id'] != scope.selected_evidence_id):
        raise AIError('AI_TOOL_SCOPE')
    return data
