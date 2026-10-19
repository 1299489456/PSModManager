"""CASE-005 original reproducible inputs and output boundary checks."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import build_fixtures as fixtures


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp('fixture-regeneration')
    first, second = root / 'first', root / 'second'
    one, two = fixtures.build(first), fixtures.build(second)
    return first, second, one, two


def test_payload_and_manifest_reproduce_byte_for_byte(generated):
    first, second, one, two = generated
    assert one == two
    assert (first / 'manifest.json').read_bytes() == (second / 'manifest.json').read_bytes()
    assert len(one['artifacts']) >= 17
    for relative, digest in one['artifacts'].items():
        assert fixtures.sha256(first / relative) == fixtures.sha256(second / relative) == digest
    for source, digest in one['sources'].items():
        assert fixtures.sha256(fixtures.WORKSPACE / source) == digest


def test_real_clr_metadata_and_embedded_content(generated):
    first, _, manifest, _ = generated
    dlls = sorted(first.rglob('*.dll'))
    assert len(dlls) == 4
    for dll in dlls:
        info = fixtures.clr_info(dll)
        assert info['cli_header_size'] >= 72 and info['metadata_size'] > 0
    itemforge = first / 'install/Mods/ItemForge.dll'
    resources = fixtures.clr_info(itemforge)['resources']
    assert len(resources) == manifest['counts']['embedded_resources'] == 2
    assert resources == [(first / 'sources' / name).read_bytes() for name in ('items.json', 'recipes.json')]
    assert [json.loads(blob)['synthetic'] for blob in resources] == [True, True]
    assert b'MelonInfoAttribute' in itemforge.read_bytes()
    assert b'Synthetic.ItemForge.items.json' in itemforge.read_bytes()
    assert b'Synthetic.ItemForge.recipes.json' in itemforge.read_bytes()
    assert b'MelonInfoAttribute' not in (first / 'install/Mods/Helper.dll').read_bytes()


def test_compiler_success_no_restore_or_execution(generated):
    first, _, manifest, _ = generated
    commands = json.loads((first / 'build-report.json').read_text())['commands']
    assert len(commands) == 4 and all(command['exit_code'] == 0 for command in commands)
    for command in commands:
        assert command['command'][1].endswith('csc.dll')
        assert '/deterministic+' in command['command']
        assert not {'restore', 'install', 'build', 'run'} & set(command['command'])
    assert manifest['execution_prohibited'] is True
    assert not list((first / 'install').rglob('*.exe'))
    assert 'SYNTHETIC' in (first / 'install/MelonLoader/Latest.log').read_text()
    assert 'UnknownFutureKey' in (first / 'install/UserData/Synthetic.cfg').read_text()


@pytest.mark.parametrize('target', ['relative', '..', str(fixtures.WORKSPACE), str(fixtures.WORKSPACE.parent / 'escape'),
                                    'E:/WorkSpace/GitHub/RimCrow/fixture', 'E:/Entertainment/Steam/steamapps/common/Probably Stolen Demo/fixture'])
def test_disallowed_roots(target):
    with pytest.raises(ValueError):
        fixtures.validate_output_root(Path(target))


def test_existing_root_untouched(tmp_path):
    root = tmp_path / 'owned-by-someone-else'
    root.mkdir()
    sentinel = root / 'keep.txt'
    sentinel.write_bytes(b'unchanged')
    with pytest.raises(ValueError, match='already exists'):
        fixtures.build(root)
    assert list(root.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b'unchanged'


def test_game_ancestor_rejected(tmp_path):
    root = tmp_path / 'synthetic-game'
    (root / 'MelonLoader').mkdir(parents=True)
    with pytest.raises(ValueError, match='game installation'):
        fixtures.build(root / 'new')
    assert not (root / 'new').exists()


def test_missing_compiler_does_not_create_output(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='missing dependency'):
        fixtures.build(tmp_path / 'absent')
    assert not (tmp_path / 'absent').exists()


def test_missing_fixture_source_does_not_create_output(tmp_path, monkeypatch):
    empty = tmp_path / 'empty-sources'
    empty.mkdir()
    monkeypatch.setattr(fixtures, 'SOURCE_ROOT', empty)
    with pytest.raises(RuntimeError, match='missing original fixture source'):
        fixtures.build(tmp_path / 'absent-fixture')
    assert not (tmp_path / 'absent-fixture').exists()


def test_plain_mz_is_not_a_clr_fixture(tmp_path):
    file = tmp_path / 'fake.dll'
    file.write_bytes(b'MZ' + bytes(200))
    with pytest.raises(ValueError, match='PE signature'):
        fixtures.clr_info(file)


def test_reparse_parent_rejected(tmp_path):
    target, junction = tmp_path / 'target', tmp_path / 'junction'
    target.mkdir()
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(target)], capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        junction.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match='reparse points'):
            fixtures.build(junction / 'new')
        assert not (target / 'new').exists()
    finally:
        os.rmdir(junction) if os.name == 'nt' else junction.unlink()
