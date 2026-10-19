"""CASE-006 read-only domain checks; all binaries are inert self-authored bytes."""
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from backend.contracts.models import RuntimeFingerprint
from backend.games.probably_stolen.discovery import discover, parse_vdf
from backend.games.probably_stolen.fingerprint import Budget, DiscoveryCancelled, read_file
from backend.games.probably_stolen.layout import EXE, ASSEMBLY, METADATA


def make_installation(root):
    root.mkdir(parents=True)
    pe = bytearray(256)
    pe[:2] = b'MZ'
    pe[60:64] = (128).to_bytes(4, 'little')
    pe[128:132] = b'PE\0\0'
    for name in (EXE, ASSEMBLY, 'MelonLoader/net6/MelonLoader.dll'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pe)
    path = root / METADATA
    path.parent.mkdir(parents=True)
    path.write_bytes(b'\xaf\x1b\xb1\xfa' + (29).to_bytes(4, 'little') + b'synthetic')
    return root


def manifest(library, app_id, name, build='123'):
    (library / 'steamapps').mkdir(parents=True, exist_ok=True)
    (library / f'steamapps/appmanifest_{app_id}.acf').write_text(
        f'"AppState" {{ "appid" "{app_id}" "installdir" "{name}" "buildid" "{build}" }}', encoding='utf-8')


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_manual_unknown_complete_readonly_and_history(tmp_path):
    game = make_installation(tmp_path / '中文 Demo 0.46D')
    (game / 'MelonLoader/Latest.log').write_text('Game Version: 0.46D\nUnity Version: 2021.3.45f2\nMelonLoader 0.7.3 Open-Beta\n')
    before = hashes(game)
    found = discover([], [game], data_root=tmp_path / 'data')[0]
    assert found['valid'] and found['channel'] == 'unknown' and found['app_id'] is None
    assert found['historical_observation']['game_version_raw'] == '0.46D'
    assert found['fingerprint']['game_version_raw'] is None
    assert found['fingerprint']['loader_version'] is None
    RuntimeFingerprint.model_validate(found['fingerprint'])
    assert found['fingerprint']['coverage'] == 'complete'
    assert hashes(game) == before


@pytest.mark.parametrize('missing', [EXE, ASSEMBLY, METADATA, 'MelonLoader/net6/MelonLoader.dll'])
def test_missing_files_never_valid(tmp_path, missing):
    game = make_installation(tmp_path / 'game')
    (game / missing).unlink()
    found = discover([], [game], data_root=tmp_path / 'data')[0]
    assert not found['valid'] and found['issues']
    if missing != 'MelonLoader/net6/MelonLoader.dll':
        assert found['fingerprint']['coverage'] == 'partial'


@pytest.mark.parametrize('broken', [EXE, ASSEMBLY, METADATA, 'MelonLoader/net6/MelonLoader.dll'])
def test_corrupt_binary_not_accepted(tmp_path, broken):
    game = make_installation(tmp_path / 'game')
    (game / broken).write_bytes(b'not a binary')
    found = discover([], [game], data_root=tmp_path / 'data')[0]
    assert not found['valid'] and found['fingerprint']['coverage'] == 'partial'


def test_two_libraries_channel_and_manual_manifest(tmp_path):
    steam = tmp_path / 'Steam'
    other = tmp_path / '第二库'
    demo = make_installation(steam / 'steamapps/common/renamed')
    retail = make_installation(other / 'steamapps/common/正式')
    manifest(steam, '4349200', 'renamed')
    manifest(other, '4348910', '正式', '987')
    (steam / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" {"0" {"path" "' + str(steam).replace('\\', '\\\\') + '"} "1" {"path" "' + str(other).replace('\\', '\\\\') + '"}}', encoding='utf-8')
    before = hashes(tmp_path)
    found = discover([steam], [demo], data_root=tmp_path / 'data')
    assert len(found) == 2 and all(x['valid'] for x in found)
    assert {(x['channel'], x['app_id'], x['build_id']) for x in found} == {('demo', '4349200', '123'), ('retail', '4348910', '987')}
    assert discover([], [retail], data_root=tmp_path / 'data')[0]['channel'] == 'retail'
    assert hashes(tmp_path) == before


@pytest.mark.parametrize('vdf', ['"a" {', '"a" "b" "A" "c"', '"a" }', 'a b', '}', '"a" {"b" "c"'])
def test_malformed_vdf_rejected(vdf):
    with pytest.raises(ValueError):
        parse_vdf(vdf)


def test_cancel_and_byte_limit(tmp_path):
    game = make_installation(tmp_path / 'game')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DiscoveryCancelled):
        discover([], [game], data_root=tmp_path / 'data', cancel=cancel)
    with pytest.raises(ValueError, match='read_size_limit'):
        read_file(game, EXE, Budget(), limit=4)


def test_data_root_overlap_rejected_before_hash(tmp_path):
    game = make_installation(tmp_path / 'game')
    result = discover([], [game], data_root=game / 'manager')[0]
    assert result['issues'] == ['data_root_conflict']
    assert not result['valid'] and result['fingerprint']['coverage'] == 'partial'


def test_alias_deduplicated(tmp_path):
    game = make_installation(tmp_path / 'game')
    found = discover([], [game, game / '.', str(game).upper()], data_root=tmp_path / 'data')
    assert len(found) == 1


def test_hardlink_rejected(tmp_path):
    game = make_installation(tmp_path / 'game')
    (tmp_path / 'alias.dll').hardlink_to(game / ASSEMBLY)
    found = discover([], [game], data_root=tmp_path / 'data')[0]
    assert not found['valid'] and any('hardlinked' in issue for issue in found['issues'])


def test_manifest_missing_and_mismatch(tmp_path):
    steam = tmp_path / 'steam'
    (steam / 'steamapps').mkdir(parents=True)
    results = discover([steam], [], data_root=tmp_path / 'data')
    assert all(not x['valid'] for x in results)
    assert any('supported_appmanifest_missing' in i for x in results for i in x['issues'])
    manifest(steam, '4349200', 'Demo')
    path = steam / 'steamapps/appmanifest_4349200.acf'
    path.write_text(path.read_text().replace('"appid" "4349200"', '"appid" "4348910"'))
    results = discover([steam], [], data_root=tmp_path / 'data')
    assert any('manifest_app_id_mismatch' in i for x in results for i in x['issues'])


@pytest.mark.parametrize('root', ['relative', r'\\server\share\game', r'\\?\C:\game', 'C:/game/../other', 'C:/game/file:stream', 'C:/game.'])
def test_ambiguous_or_remote_roots_rejected(tmp_path, root):
    with pytest.raises(ValueError):
        discover([], [root], data_root=tmp_path / 'data')


def test_historical_oversized_log_does_not_invalidate_current_files(tmp_path):
    game = make_installation(tmp_path / 'game')
    (game / 'MelonLoader/Latest.log').write_bytes(b'x' * (1024 * 1024 + 1))
    result = discover([], [game], data_root=tmp_path / 'data')[0]
    assert result['valid'] and result['warnings'] and result['historical_observation'] is None


def test_mid_read_file_mutation_rejected(tmp_path):
    game = make_installation(tmp_path / 'game')
    calls = 0
    def mutate():
        nonlocal calls
        calls += 1
        if calls == 2:
            with (game / EXE).open('ab') as stream:
                stream.write(b'drift')
        return False
    with pytest.raises(ValueError, match='file_drift'):
        read_file(game, EXE, Budget(mutate))


def test_elapsed_budget_and_candidate_limit(tmp_path):
    game = make_installation(tmp_path / 'game')
    budget = Budget()
    budget.deadline = 0
    with pytest.raises(ValueError, match='read_time_limit'):
        read_file(game, EXE, budget)
    with pytest.raises(ValueError, match='candidate_limit'):
        discover([], [game] * 17, data_root=tmp_path / 'data')


def test_root_junction_deduplicates_and_internal_junction_refuses(tmp_path):
    game = make_installation(tmp_path / 'game')
    alias = tmp_path / 'alias'
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(game)], check=True, capture_output=True)
    assert len(discover([], [game, alias], data_root=tmp_path / 'data')) == 1
    data = game / 'Probably Stolen_Data'
    moved = tmp_path / 'external-data'
    data.rename(moved)
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(data), str(moved)], check=True, capture_output=True)
    result = discover([], [game], data_root=tmp_path / 'data')[0]
    assert not result['valid'] and any('reparse_point' in x for x in result['issues'])


def test_current_pe_resource_preserves_raw_version(tmp_path):
    from scripts.build_fixtures import compiler_inputs
    game = make_installation(tmp_path / 'game')
    source = tmp_path / 'OriginalVersion.cs'
    source.write_text('using System.Reflection; [assembly: AssemblyInformationalVersion("0.46D")] public class OriginalVersion {}')
    host, compiler, references = compiler_inputs()
    command = [str(host), str(compiler), '/nologo', '/target:library', '/deterministic+', '/nostdlib+', '/out:' + str(game / EXE)]
    command.extend('/reference:' + str(p) for p in references)
    command.append(str(source))
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, env=dict(os.environ, DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_CLI_HOME=str(tmp_path)))
    (tmp_path / 'compiler.json').write_text(json.dumps({'command': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}))
    assert result.returncode == 0, result.stdout + result.stderr
    found = discover([], [game], data_root=tmp_path / 'data')[0]
    assert found['valid'] and found['fingerprint']['game_version_raw'] == '0.46D'
    assert found['historical_observation'] is None
