"""Current engine metadata and Steam builds must not masquerade as game versions."""
import os

import pytest

from backend.games.probably_stolen import fingerprint
from backend.games.probably_stolen.update_state import current_build_id, game_files_modified_at
from tests.unit.test_installation_layout import make_installation, manifest


@pytest.mark.parametrize('value,expected', [
    ('2021.3.45f2 (88f88f591b2e)', None), ('2021.3.45.8976527', None),
    ('6000.0.40f1', None), ('0.46D', '0.46D'), ('1.2.0', '1.2.0'), (None, None),
])
def test_game_version_excludes_unity_pe_resources(value, expected):
    assert fingerprint.game_version_from_pe(value) == expected


def test_complete_fingerprint_separates_unity_and_game(tmp_path, monkeypatch):
    root = make_installation(tmp_path / 'game')
    (root / 'UnityPlayer.dll').write_bytes(b'not executed')
    monkeypatch.setattr(fingerprint, 'file_version', lambda p: '0.7.3' if p.name == 'MelonLoader.dll' else '2021.3.45f2')
    from backend.games.probably_stolen.layout import inspect
    paths, _ = inspect(root, tmp_path / 'data')
    result, issues = fingerprint.collect(root, paths, fingerprint.Budget())
    assert not issues
    assert result['game_version_raw'] is None
    assert result['unity_version'] == '2021.3.45f2'
    assert result['loader_version'] == '0.7.3'
    assert result['coverage'] == 'complete'


def test_current_versions_reject_drift_and_do_not_fall_back_to_logs(tmp_path, monkeypatch):
    root = make_installation(tmp_path / 'game')
    player = root / 'UnityPlayer.dll'
    player.write_bytes(b'old')
    def changed(path):
        if path == player:
            player.write_bytes(b'changed file')
        return '2021.3.45f2'
    monkeypatch.setattr(fingerprint, 'file_version', changed)
    value = fingerprint.current_versions(root)
    assert value['unity_version'] is None and value['game_version'] is None


def test_current_steam_build_refreshes_without_selecting_again(tmp_path):
    root = make_installation(tmp_path / 'steam/steamapps/common/Demo')
    library = tmp_path / 'steam'
    manifest(library, '4349200', 'Demo', '100')
    assert current_build_id(root, '4349200') == '100'
    manifest(library, '4349200', 'Demo', '101')
    assert current_build_id(root, '4349200') == '101'
    manifest(library, '4349200', 'DifferentGame', '102')
    assert current_build_id(root, '4349200') is None
    assert current_build_id(root, '../private') is None
    assert current_build_id(tmp_path, '4349200') is None


def test_game_file_time_uses_game_binaries_not_mods_or_logs(tmp_path):
    root = make_installation(tmp_path / 'game')
    for path in root.rglob('*'):
        if path.is_file():
            os.utime(path, (1000, 1000))
    os.utime(root / 'GameAssembly.dll', (2000, 2000))
    (root / 'Mods').mkdir(exist_ok=True)
    (root / 'Mods/Recent.dll').write_bytes(b'new mod')
    assert game_files_modified_at(root) == 2000
    assert game_files_modified_at(tmp_path / 'missing') is None
