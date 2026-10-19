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


REGISTRY = {
    'get_installation_summary': (Empty, '当前游戏的已登记版本摘要，不证明游戏运行正常。'),
    'search_mods': (Search, '搜索当前私有包库的名称/版本，不只检查第一页面；按 next_offset 游标继续，最多20项。'),
    'get_package': (Package, '查看已登记且 manifest_hash 匹配的包的名称、版本与有界文件摘要。'),
    'read_selected_log': (Log, '只读用户所选 evidence_id 的前4096字节，不能改选日志。'),
    'get_installed_mods': (Page, '查询当前游戏的Mod文件状态；enabled仅表示安装文件匹配。'),
    'get_mod_workspace': (Page, '查询当前 Mod 列表，包含外部发现尚未纳管的 Mod、包库版本、启停状态和 snapshot_id；外部 Mod 可用 suggest_workspace_action 建议启停。'),
    'get_mod_config': (Config, '只读 PSWallpaper 已支持的三个配置字段，不能读取其他配置或存档。'),
}


def schemas(scope):
    if scope.installation_id is None:
        return []
    return [dict(type='function', function=dict(name=name, description=description,
        parameters=model.model_json_schema())) for name, (model, description) in REGISTRY.items()
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


def parameters(name, arguments, scope):
    if name not in REGISTRY:
        raise AIError('AI_TOOL_UNKNOWN')
    if scope.installation_id is None:
        raise AIError('AI_TOOL_SCOPE')
    data = validate_arguments(arguments, REGISTRY[name][0])
    if name == 'read_selected_log' and (scope.selected_evidence_id is None
            or data['evidence_id'] != scope.selected_evidence_id):
        raise AIError('AI_TOOL_SCOPE')
    return data
