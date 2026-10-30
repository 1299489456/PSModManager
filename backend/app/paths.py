"""Resolve and validate owned data paths before any startup write."""
from dataclasses import dataclass
import os
from pathlib import Path
import stat

REFERENCE_ROOT = Path('E:/WorkSpace/GitHub/RimCrow')
KNOWN_GAME_ROOT = Path('E:/Entertainment/Steam/steamapps/common/Probably Stolen Demo')
FORMAL_GAME_ROOT = Path('E:/Entertainment/Steam/steamapps/common/Probably Stolen')
FORMAL_WORKSHOP_ROOT = Path('E:/Entertainment/Steam/steamapps/workshop/content/4348910')


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def reject_reparse(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('数据路径不允许符号链接或目录联接')


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def config(self): return self.root / 'config.json'

    @property
    def database(self): return self.root / 'psmodmanager.sqlite3'

    @property
    def log(self): return self.root / 'logs' / 'psmodmanager.log'

    @property
    def webview(self): return self.root / 'webview2'

    def check_owned_paths(self):
        for path in (self.root, self.config, self.database, self.log, self.webview):
            reject_reparse(path)
            if path.is_file() and path.stat().st_nlink > 1:
                raise ValueError('数据文件不允许硬链接')


def resolve_paths(value=None, protected_roots=(), *, environ=None):
    env = os.environ if environ is None else environ
    local = env.get('LOCALAPPDATA')
    if value is None and not local:
        raise ValueError('LOCALAPPDATA 不可用；请显式指定 --psmm-data-root')
    candidate = Path(value) if value is not None else Path(local) / 'PSModManager'
    if not candidate.is_absolute() or str(candidate).startswith(('\\\\', '//')):
        raise ValueError('数据目录必须是本地绝对路径')
    if any(p.endswith((' ', '.')) or ':' in p for p in candidate.parts[1:]):
        raise ValueError('数据目录包含不明确的 Windows 路径段')
    candidate = Path(os.path.abspath(candidate))
    reject_reparse(candidate)
    # Expand Windows short-name aliases before protection/identity checks. The
    # deployment path contract deliberately rejects ambiguous ~1 spellings.
    candidate = candidate.resolve()
    forbidden = [REFERENCE_ROOT, KNOWN_GAME_ROOT, FORMAL_GAME_ROOT, FORMAL_WORKSHOP_ROOT, *map(Path, protected_roots)]
    if env.get('USERPROFILE'):
        forbidden.append(Path(env['USERPROFILE']) / 'AppData/LocalLow/Questing Goose Studio/Probably Stolen')
    workspace = Path(__file__).resolve().parents[2]
    forbidden += [workspace / name for name in ('data', 'mods', 'toolmods', 'updates')]
    for base in (env.get('LOCALAPPDATA'), env.get('APPDATA')):
        if base:
            forbidden += [Path(base) / name for name in ('RimCrow', 'RimModManager')]
    for path in forbidden:
        if not path.is_absolute():
            raise ValueError('保护目录必须是绝对路径')
        if overlaps(candidate, Path(os.path.abspath(path))) or overlaps(candidate, path.resolve()):
            raise ValueError('数据目录与游戏、包来源或 RimCrow 目录重叠')
    if candidate == workspace or candidate in workspace.parents or candidate == Path(candidate.anchor):
        raise ValueError('不能使用源码根目录或磁盘根目录作为数据目录')
    for ancestor in (candidate, *candidate.parents):
        if ancestor == Path(ancestor.anchor):
            break
        if any((ancestor / name).exists() for name in ('Probably Stolen.exe', 'Probably Stolen_Data',
                'MelonLoader', 'GameAssembly.dll', 'il2cpp_data')):
            raise ValueError('数据目录位于游戏安装目录内')
    paths = AppPaths(candidate)
    paths.check_owned_paths()
    return paths
