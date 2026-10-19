"""Only the runtime used by MelonLoader is a prerequisite for Mod loading."""
from pathlib import Path

import pytest

from backend.loaders.melonloader import environment


def runtime(root, version='6.0.36'):
    shared = root / 'shared/Microsoft.NETCore.App' / version
    shared.mkdir(parents=True)
    (shared / 'System.Private.CoreLib.dll').write_bytes(b'owned runtime marker')
    fxr = root / 'host/fxr' / version / 'hostfxr.dll'
    fxr.parent.mkdir(parents=True)
    fxr.write_bytes(pe_bytes())
    return fxr


def pe_bytes(machine=0x8664):
    pe = bytearray(256)
    pe[:2] = b'MZ'
    pe[60:64] = (128).to_bytes(4, 'little')
    pe[128:132] = b'PE\0\0'
    pe[132:134] = machine.to_bytes(2, 'little')
    return pe


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, '_registry_values', lambda *args: {})
    game = tmp_path / 'game'
    game.mkdir()
    return game, {'ProgramW6432': str(tmp_path / 'Program Files'),
                  'SystemRoot': str(tmp_path / 'Windows')}


def dotnet(game, config, env):
    return next(check for check in environment.environment_checks(game, config, environ=env)
                if check['id'] == 'dotnet')


def test_core_runtime_suffices_without_windows_desktop_runtime(isolated):
    game, env = isolated
    root = Path(env['ProgramW6432']) / 'dotnet'
    runtime(root)
    assert not (root / 'shared/Microsoft.WindowsDesktop.App').exists()
    result = dotnet(game, {}, env)
    assert result['status'] == 'ok' and '6.0.36' in result['detail']


def test_other_major_runtime_does_not_satisfy_net6_requirement(isolated):
    game, env = isolated
    runtime(Path(env['ProgramW6432']) / 'dotnet', '10.0.11')
    result = dotnet(game, {}, env)
    assert result['status'] == 'missing' and '.NET 6' in result['detail']


@pytest.mark.parametrize('kind', ['root', 'hostfxr_dll'])
def test_explicit_hostfxr_override_uses_that_runtime(isolated, kind):
    game, env = isolated
    root = game / 'custom-dotnet'
    fxr = runtime(root)
    result = dotnet(game, {'hostfxr_path_override': str(root if kind == 'root' else fxr)}, env)
    assert result['status'] == 'ok', result


def test_bad_explicit_override_continues_to_system_runtime_like_loader(isolated):
    game, env = isolated
    runtime(Path(env['ProgramW6432']) / 'dotnet')
    result = dotnet(game, {'hostfxr_path_override': 'missing-runtime'}, env)
    assert result['status'] == 'ok' and '6.0.36' in result['detail']


def test_bad_override_and_missing_system_runtime_explains_configured_path(isolated):
    game, env = isolated
    result = dotnet(game, {'hostfxr_path_override': 'missing-runtime'}, env)
    assert result['status'] == 'missing' and 'hostfxr_path_override' in result['detail']


def test_x86_hostfxr_does_not_satisfy_x64_runtime_requirement(isolated):
    game, env = isolated
    fxr = runtime(Path(env['ProgramW6432']) / 'dotnet')
    fxr.write_bytes(pe_bytes(0x14c))
    assert dotnet(game, {}, env)['status'] == 'missing'


def test_local_vc_runtime_is_detected_without_registry_registration(isolated):
    game, env = isolated
    for name in ('vcruntime140.dll', 'msvcp140.dll'):
        (game / name).write_bytes(pe_bytes())
    result = next(check for check in environment.environment_checks(game, {}, environ=env)
                  if check['id'] == 'vc_runtime')
    assert result['status'] == 'ok', result
