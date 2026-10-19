"""Version-bound, data-only verification of the bundled IL2CPP toolchain.

Nothing here downloads or executes a tool. The manifest is part of the product,
not an import format, and its hashes bind each permitted destination to the
official bytes shipped with that build.
"""
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile

from backend.catalog.archive_policy import canonical_name, locked_path
from backend.deployments.paths import GuardRejected
from backend.games.probably_stolen.layout import safe_path, signature
from .v073 import supports_version

RESOURCE_ROOT = Path(__file__).parent / 'resources'
GENERATOR = 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/'
CPP2IL = GENERATOR + 'Cpp2IL/Cpp2IL.exe'
SCRS = GENERATOR + 'Cpp2IL/Plugins/Cpp2IL.Plugin.StrippedCodeRegSupport.dll'
UNITY = GENERATOR + 'UnityDependencies/'
UNITY_ARCHIVE = GENERATOR + 'UnityDependencies_2021.3.45.zip'
DUMPER_VERSION = '2022.1.0-pre-release.21'
MAX_BYTES = 64 * 1024 * 1024


class OfflineError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.code = 'LOADER_OFFLINE_PACKAGE_INVALID'


def unity_version(value):
    """Unity PE ProductVersion includes the channel suffix (for example f2)."""
    found = re.match(r'^(\d+)\.(\d+)\.(\d+)(?=[abfp.\s]|$)', value or '')
    return '.'.join(found.groups()) if found else None


class OfflineToolchain:
    def __init__(self, resources=None):
        self.resources = Path(resources) if resources is not None else RESOURCE_ROOT

    def manifest(self):
        try:
            path = safe_path(self.resources, 'manifest.json')
            if path.stat().st_size > 256 * 1024:
                raise ValueError()
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            profile = value.get('profile', 'full')
            if (type(value['schema_version']) is not int or value['schema_version'] != 1 or value['loader_version'] != '0.7.3'
                    or value['dumper_version'] != DUMPER_VERSION
                    or value['unity_version'] != '2021.3.45'
                    or value['archive'] != 'toolchain.zip'
                    or profile not in ('full', 'cpp2il-only')
                    or not isinstance(value['files'], list)
                    or not (2 <= len(value['files']) <= 256)):
                raise ValueError()
            seen, total = set(), 0
            for item in value['files']:
                name = canonical_name(item['path'])
                if (name != item['path'] or name.casefold() in seen
                        or not (name in (CPP2IL, SCRS, UNITY_ARCHIVE) or name.startswith(UNITY)
                            and '/' not in name[len(UNITY):] and name.endswith('.dll'))
                        or type(item['size']) is not int or not 0 < item['size'] <= MAX_BYTES
                        or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
                    raise ValueError()
                seen.add(name.casefold())
                total += item['size']
            required = {CPP2IL.casefold(), SCRS.casefold()}
            if profile == 'full':
                required.add(UNITY_ARCHIVE.casefold())
            if (not required <= seen or total > MAX_BYTES
                    or profile == 'cpp2il-only' and seen != required
                    or profile == 'full' and len(seen) < 4):
                raise ValueError()
            return value
        except (OSError, ValueError, KeyError, TypeError):
            raise OfflineError('离线生成依赖包缺失或清单无效，请重新完整解压最新版管理器。') from None

    @staticmethod
    def _present(root, name):
        try:
            return safe_path(root, name).stat().st_size > 0
        except (OSError, ValueError):
            return False

    def _file_state(self, root, item, check):
        """Check target bytes, including equal-size edits preserving mtime.

        Windows stat ctime can be creation time, so metadata alone cannot safely
        cache a positive result for a mutable game-directory executable.
        """
        try:
            path = safe_path(root, item['path'])
            before = signature(path.stat())
            if before[2] != item['size']:
                return 'damaged'
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                while block := stream.read(256 * 1024):
                    check()
                    digest.update(block)
            if signature(safe_path(root, item['path']).stat()) != before:
                return 'unknown'
            if digest.hexdigest() != item['sha256']:
                return 'damaged'
            return 'ok'
        except FileNotFoundError:
            return 'missing'
        except GuardRejected:
            raise
        except (OSError, ValueError):
            return 'unknown'

    def assess(self, root, *, loader_version, current_unity, forced_dumper='', config_valid=True,
               recorded_unity='', generator_config_valid=True, unity_override='', check=lambda: None):
        root = Path(root)
        current_unity = unity_version(current_unity)
        override_version = (unity_version(unity_override)
            if re.fullmatch(r'\d+\.\d+\.\d+(?:[abfp]\d+)?', unity_override) else None)
        # The official constructor treats 0.0.0.0 like an unset override.
        dumper = DUMPER_VERSION if forced_dumper in ('', '0.0.0.0') else forced_dumper
        result = dict(ready=False, supported=False, can_repair=False,
            loader_version=loader_version, dumper_version=dumper,
            unity_version=current_unity, unity_version_override=unity_override, recorded_unity_version=recorded_unity,
            unity_archive_required=recorded_unity != current_unity,
            files=[], missing=[], damaged=[])
        # Presence is useful for custom installations, but is not compatibility
        # evidence. Their own loader remains responsible for their toolchain.
        unity_present = False
        try:
            directory = safe_path(root, UNITY.rstrip('/'), directory=True)
            unity_present = any(p.suffix.lower() == '.dll' and self._present(root, UNITY+p.name)
                                for p in directory.iterdir())
        except (OSError, ValueError):
            pass
        result['existing_tools'] = self._present(root, CPP2IL) and self._present(root, SCRS) and unity_present
        reason = None
        if not supports_version(loader_version):
            reason = '当前 MelonLoader 版本不在离线依赖支持范围内；请安装 0.7.3，或按该版本要求手动准备依赖。'
        elif not config_valid:
            reason = '无法读取 Loader.cfg，请修正配置后再修复生成依赖。'
        elif not generator_config_valid:
            reason = '生成器 Config.cfg 格式无效或无法读取；请备份并修复该配置后重试，不会覆盖现有生成记录。'
        elif dumper != DUMPER_VERSION:
            reason = 'Loader.cfg 指定了自定义 Cpp2IL 版本；请手动准备该版本工具，或取消 force_il2cpp_dumper_version 后修复。'
        elif unity_override and (current_unity is None or override_version != current_unity):
            reason = 'Loader.cfg 的 version_override 与当前 UnityPlayer 版本不一致；请手动准备对应依赖，或取消该覆盖后修复，不会替换自定义工具链。'
        elif current_unity != '2021.3.45':
            reason = ('无法从当前 UnityPlayer.dll 确认 Unity 版本；请验证游戏文件后重试。'
                      if current_unity is None else
                      f'当前 Unity {current_unity} 暂无配套离线依赖；请为该版本准备 UnityDependencies，不会覆盖现有工具。')
        if reason:
            result.update(message=reason, status='unknown')
            return result
        result['supported'] = True
        try:
            manifest = self.manifest()
        except OfflineError as exc:
            result.update(message=str(exc), status='unknown')
            return result
        for item in manifest['files']:
            # Official Setup compares Config.UnityVersion before deciding to
            # process Unity libraries. Its FilePath is this ZIP, not a DLL.
            # On success it saves the version and deletes the ZIP; do not
            # restore that normal, no-longer-needed cache on every launch.
            if item['path'] == UNITY_ARCHIVE and not result['unity_archive_required']:
                continue
            check()
            state = self._file_state(root, item, check)
            result['files'].append(dict(path=item['path'], status=state))
            if state != 'ok':
                result['damaged' if state == 'damaged' else 'missing'].append(item['path'])
        result['ready'] = not result['missing'] and not result['damaged']
        result['can_repair'] = not result['ready']
        result['status'] = 'ok' if result['ready'] else 'missing'
        result['message'] = ('Cpp2IL、配套插件和 Unity 依赖已校验，可离线生成程序集。' if result['ready'] else
                             'IL2CPP 生成依赖缺失或损坏，可从管理器内置资源离线修复。')
        if manifest.get('profile') == 'cpp2il-only':
            # Public builds restore the MIT-licensed tools only. Unity libraries
            # remain owned by MelonLoader's normal setup/download path; absence
            # must not block launching the game to complete that setup.
            result['offline_complete'] = False
            result['unity_download_required'] = not unity_present or result['unity_archive_required']
            result['message'] = ('Cpp2IL 和配套插件已校验。' if result['ready'] else
                                 'Cpp2IL 或配套插件缺失、损坏，可离线修复。')
            result['message'] += ('公开版不内置 Unity 库，首次生成或游戏更新后由 MelonLoader 联网下载。'
                                  if result['unity_download_required'] else 'Unity 库由 MelonLoader 管理。')
        return result

    def extract(self, stage, needed, check=lambda: None):
        """Verify the entire bundled archive before making any game writes."""
        manifest = self.manifest()
        expected = {item['path']: item for item in manifest['files']}
        if not set(needed) <= set(expected):
            raise OfflineError('离线依赖修复目标无效，请重新打开环境页。')
        try:
            archive_path = safe_path(self.resources, 'toolchain.zip')
            if archive_path.stat().st_size > MAX_BYTES:
                raise ValueError()
            with locked_path(archive_path), zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) != len(expected):
                    raise ValueError()
                seen = set()
                for member in members:
                    check()
                    name = canonical_name(member.filename)
                    item = expected.get(name)
                    if (item is None or name in seen or member.is_dir() or member.flag_bits & 1
                            or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                            or member.file_size != item['size']):
                        raise ValueError()
                    seen.add(name)
                    digest, count = hashlib.sha256(), 0
                    target = stage / name
                    if name in needed:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        destination = target.open('xb')
                    else:
                        destination = None
                    try:
                        with archive.open(member) as source:
                            while block := source.read(256 * 1024):
                                check()
                                count += len(block)
                                if count > item['size']:
                                    raise ValueError()
                                digest.update(block)
                                if destination is not None:
                                    destination.write(block)
                    finally:
                        if destination is not None:
                            destination.close()
                    if count != item['size'] or digest.hexdigest() != item['sha256']:
                        raise ValueError()
            return sorted(needed)
        except GuardRejected:
            raise
        except (OSError, ValueError, zipfile.BadZipFile):
            raise OfflineError('离线生成依赖包损坏或不完整，请重新完整解压最新版管理器。') from None
