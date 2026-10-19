"""Small, read-only checks for the runtime used by the selected loader."""
import os
from pathlib import Path
import re

DOTNET_HELP = 'https://dotnet.microsoft.com/en-us/download/dotnet/6.0/runtime'
VC_HELP = 'https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist'


def pe_machine(path):
    """Read the PE machine field; never load or hash the executable."""
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            return None
        offset = int.from_bytes(header[60:64], 'little')
        if not 64 <= offset <= 1024 * 1024:
            return None
        stream.seek(offset)
        data = stream.read(6)
        return int.from_bytes(data[4:6], 'little') if data[:4] == b'PE\0\0' else None


def _registry_values(key, values):
    if os.name != 'nt':
        return {}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
            result = {}
            for name in values:
                try: result[name] = winreg.QueryValueEx(handle, name)[0]
                except OSError: pass
            return result
    except OSError:
        return {}


def _runtime_roots(game, config, environ):
    override = config.get('hostfxr_path_override')
    if isinstance(override, str) and override.strip():
        path = Path(override)
        if not path.is_absolute(): path = game / path
        # Official overrides accept either a dotnet root or hostfxr.dll.
        yield path.parents[3] if path.name.casefold() == 'hostfxr.dll' and len(path.parents) >= 4 else path
    yield game / 'dotnet'
    yield game / 'MelonLoader/Dependencies/dotnet'
    registered = _registry_values(r'SOFTWARE\dotnet\Setup\InstalledVersions\x64', ['InstallLocation']).get('InstallLocation')
    if isinstance(registered, str): yield Path(registered)
    for name in ('DOTNET_ROOT_X64', 'DOTNET_ROOT'):
        if environ.get(name): yield Path(environ[name])
    if environ.get('ProgramW6432') or environ.get('ProgramFiles'):
        yield Path(environ.get('ProgramW6432') or environ['ProgramFiles']) / 'dotnet'


def environment_checks(game, config, major=6, *, environ=None):
    env = os.environ if environ is None else environ
    runtime = None
    seen = set()
    for root in _runtime_roots(Path(game), config, env):
        if not root.is_absolute() or str(root).startswith(('\\\\', '//')) or root in seen:
            continue
        seen.add(root)
        try:
            shared = root / 'shared/Microsoft.NETCore.App'
            for version in sorted(shared.iterdir(), reverse=True):
                if (re.fullmatch(rf'{major}\.\d+\.\d+', version.name)
                        and (version / 'System.Private.CoreLib.dll').is_file()
                        and any(pe_machine(host) == 0x8664 for host in (root / 'host/fxr').glob('*/hostfxr.dll'))):
                    runtime = version.name
                    break
        except OSError:
            continue
        if runtime: break
    dotnet = dict(id='dotnet', label=f'.NET {major} x64 运行时', status='ok' if runtime else 'missing',
        detail=f'Microsoft.NETCore.App {runtime}' if runtime else f'未找到 .NET {major} x64 运行时，可安装 Core 或 Desktop Runtime。',
        help_url=DOTNET_HELP if major == 6 else f'https://dotnet.microsoft.com/en-us/download/dotnet/{major}.0/runtime')
    if not runtime and config.get('hostfxr_path_override'):
        dotnet.update(detail='自定义 hostfxr_path_override 中未找到所需运行时，请检查 Loader.cfg 的运行时位置。')
    vc = _registry_values(r'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', ['Installed', 'Version'])
    present = vc.get('Installed') == 1
    if not present:
        for root in (Path(env.get('SystemRoot', 'C:/Windows')) / 'System32', Path(game)):
            try:
                if all(pe_machine(root / name) == 0x8664 for name in ('vcruntime140.dll', 'msvcp140.dll')):
                    present = True
                    break
            except OSError:
                pass
    return [dotnet, dict(id='vc_runtime', label='VC++ 2015–2022 x64 运行库', status='ok' if present else 'missing',
        detail=str(vc.get('Version') or '已安装兼容运行库') if present else '未找到兼容的 VC++ x64 运行库。', help_url=VC_HELP)]
