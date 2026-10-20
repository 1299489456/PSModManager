"""Read-only GameAssembly comparison against MelonLoader 0.7.3's saved record."""
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
import hashlib
from pathlib import Path
import re
import threading
import tomllib

from backend.deployments.paths import pinned_file
from backend.games.probably_stolen.layout import ASSEMBLY, safe_path
from .offline import GENERATOR
from .v073 import supports_version

CONFIG = GENERATOR + 'Config.cfg'
BLOCK_SIZE = 1024 * 1024


class _Unavailable(ValueError):
    pass


def _access(action):
    """Convert read failures only; caller cancellation must keep its own error."""
    import pywintypes
    try:
        return action()
    except FileNotFoundError:
        raise
    except (OSError, ValueError, pywintypes.error) as exc:
        raise _Unavailable() from exc


@contextmanager
def _opened(root, relative):
    path = _access(lambda: safe_path(root, relative))
    with ExitStack() as stack:
        handle = _access(lambda: stack.enter_context(pinned_file(path)))
        yield handle


def _signature(handle):
    import win32file
    info = _access(lambda: win32file.GetFileInformationByHandle(handle))
    if info[0] & (0x400 | 0x10) or info[7] != 1:
        raise _Unavailable()
    # Volume/file identity, size, last-write time and creation time. The handle
    # denies writers/deleters; checking again also rejects an unstable read.
    return (info[4], info[8], info[9], (info[5] << 32) | info[6], info[3], info[1])


def _blocks(handle, size, check):
    import win32file
    while size:
        check()
        _, block = _access(lambda: win32file.ReadFile(handle, min(BLOCK_SIZE, size)))
        if not block:
            raise _Unavailable()
        size -= len(block)
        yield block
    check()


class AssemblyGeneration:
    def __init__(self):
        self._cache = OrderedDict()
        self._lock = threading.RLock()

    def _hash(self, root, check):
        # This is an advisory UI cache, not installation/deployment integrity
        # evidence. Never persist it or reuse it for guards or repair decisions.
        key = str(Path(root) / ASSEMBLY).casefold()
        with self._lock, _opened(root, ASSEMBLY) as handle:
            check()
            before = _signature(handle)
            cached = self._cache.pop(key, None)
            if cached and cached[0] == before:
                digest = cached[1]
            else:
                # Official Core.Run compares ComputeSimpleSHA512Hash's full
                # file digest, using uppercase hexadecimal and case sensitivity.
                value = hashlib.sha512()
                for block in _blocks(handle, before[3], check):
                    value.update(block)
                digest = value.hexdigest().upper()
            check()
            if _signature(handle) != before:
                raise _Unavailable()
            self._cache[key] = (before, digest)
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
            return digest

    @staticmethod
    def _record(root, check):
        with _opened(root, CONFIG) as handle:
            before = _signature(handle)
            if before[3] > 1024 * 1024:
                raise _Unavailable()
            data = b''.join(_blocks(handle, before[3], check))
            if _signature(handle) != before:
                raise _Unavailable()
        try:
            record = tomllib.loads(data.decode('utf-8-sig')).get('Il2CppAssemblyGenerator', {})
            if not isinstance(record, dict):
                raise ValueError()
            value = record.get('GameAssemblyHash', '')
            if not isinstance(value, str) or value and not re.fullmatch('[0-9A-Fa-f]{128}', value):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise _Unavailable() from None

    def assess(self, root, loader_version, *, check=lambda: None):
        check()
        if not supports_version(loader_version):
            return dict(status='unknown', message='当前加载器版本的程序集生成记录暂无法确认。')
        try:
            try:
                recorded = self._record(root, check)
            except FileNotFoundError:
                recorded = ''
            if not recorded:
                return dict(status='not_generated', message='尚无成功生成记录；下次启动游戏时由 MelonLoader 生成程序集，可能需要较长时间。')
            if self._hash(root, check) != recorded:
                return dict(status='pending', message='GameAssembly.dll 与上次生成记录不同，待重新生成程序集；下次启动游戏时由 MelonLoader 重建，可能需要较长时间。')
            return dict(status='matched', message='生成记录与当前 GameAssembly.dll 匹配。')
        except (FileNotFoundError, _Unavailable):
            return dict(status='unknown', message='无法确认程序集生成状态：生成器 Config.cfg 或 GameAssembly.dll 无法安全读取、记录无效或读取时发生变化。')
