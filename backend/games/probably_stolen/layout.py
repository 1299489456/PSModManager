"""Bounded, read-only installation layout inspection."""
from __future__ import annotations

import re
import stat
from pathlib import Path, PureWindowsPath

EXE = 'Probably Stolen.exe'
DATA = 'Probably Stolen_Data'
ASSEMBLY = 'GameAssembly.dll'
METADATA = DATA + '/il2cpp_data/Metadata/global-metadata.dat'
LOADERS = ('MelonLoader/net6/MelonLoader.dll', 'MelonLoader/MelonLoader.dll')


def approved_root(value) -> Path:
    raw = str(value)
    path = Path(raw)
    if not path.is_absolute() or raw.startswith(('\\\\', '//')):
        raise ValueError('local_absolute_root_required')
    win = PureWindowsPath(raw)
    parts = win.parts[1:] if win.drive else path.parts[1:]
    if any(part in ('.', '..') or part.rstrip(' .') != part or ':' in part or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part) or any(ord(c) < 32 or c in '<>"|?*' for c in part) for part in parts):
        raise ValueError('ambiguous_root')
    return path.resolve()


def identity(path: Path) -> str:
    info = path.stat()
    return f'{info.st_dev:x}:{info.st_ino:x}'


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def safe_path(root: Path, relative: str, *, directory=False) -> Path:
    """Root may be a user-approved alias; descendants must not redirect reads."""
    target = root / relative
    current = root
    for part in Path(relative).parts:
        if part in ('..', '.'):
            raise ValueError('ambiguous_path')
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('reparse_point')
    info = target.stat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('not_directory')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('not_regular_or_hardlinked')
    return target


def inspect(root: Path, data_root: Path) -> tuple[dict, list[str]]:
    issues = []
    paths = {}
    if root == data_root or root in data_root.parents or data_root in root.parents:
        return paths, ['data_root_conflict']
    for relative, directory in ((EXE, False), (DATA, True), (ASSEMBLY, False), (METADATA, False)):
        try:
            paths[relative] = safe_path(root, relative, directory=directory)
        except (OSError, ValueError) as exc:
            issues.append(f'{relative}: {type(exc).__name__}: {exc}')
    for relative in LOADERS:
        try:
            paths['loader'] = safe_path(root, relative)
            break
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            issues.append(f'{relative}: {type(exc).__name__}: {exc}')
    if 'loader' not in paths:
        issues.append('loader_missing: MelonLoader/net6/MelonLoader.dll or MelonLoader/MelonLoader.dll')
    return paths, issues


def inspect_selection(root: Path, data_root: Path) -> list[str]:
    """Check a usable game directory without reading or fingerprinting binaries."""
    if root == data_root or root in data_root.parents or data_root in root.parents:
        return ['data_root_conflict']
    try:
        if not root.is_dir():
            return ['installation_directory_missing']
        # Access is checked here; binary contents and optional loader/data files
        # belong to their actual consumers, not directory selection.
        target = safe_path(root, EXE)
        with target.open('rb'):
            pass
    except (OSError, ValueError) as exc:
        return [f'{EXE}: {type(exc).__name__}: {exc}']
    return []
