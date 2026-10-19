"""Offline toolchain recovery in real, isolated Windows installation guards.

The fixture tools are inert bytes; no real game, loader or generated tool is
executed. Official loader extraction is checked separately using its release ZIP.
"""
import hashlib
import io
import json
import os
import shutil
import zipfile

import pytest

from backend.deployments.paths import GuardRejected
from backend.deployments.process_guard import ProcessGuard
from backend.games.probably_stolen.layout import EXE
from backend.loaders.melonloader import service as loader_module
from backend.loaders.melonloader.offline import CPP2IL, SCRS, UNITY, UNITY_ARCHIVE, OfflineError, OfflineToolchain
from backend.loaders.melonloader.service import CORE_FILES, LoaderError, MelonLoaderService
from tests.integration.harness.transaction_recovery import ROOT, TransactionFixture, tree

OFFICIAL_ARCHIVE = ROOT / 'docs/development/evidence/20261015T055229Z-melonloader/MelonLoader.x64.zip'
UNITY_DLL = UNITY + 'UnityEngine.CoreModule.dll'


@pytest.fixture
def app(tmp_path, monkeypatch):
    fixture = TransactionFixture(tmp_path / 'offline')
    executable = fixture.game / EXE
    pe = bytearray(executable.read_bytes())
    pe[132:134] = (0x8664).to_bytes(2, 'little')
    executable.write_bytes(pe)
    shutil.rmtree(fixture.game / 'MelonLoader')
    with zipfile.ZipFile(OFFICIAL_ARCHIVE) as archive:
        for name in CORE_FILES:
            target = fixture.game / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    (fixture.game / 'UnityPlayer.dll').write_bytes(b'inert Unity version-resource fixture')
    resources = tmp_path / 'resources'
    resources.mkdir()
    fixture.tool_bytes = {
        CPP2IL: b'inert Cpp2IL fixture: must never execute',
        SCRS: b'inert stripped code registration plugin',
        UNITY_DLL: b'inert version-matched Unity dependency',
    }
    managed = io.BytesIO()
    with zipfile.ZipFile(managed, 'w') as archive:
        archive.writestr('UnityEngine.CoreModule.dll', fixture.tool_bytes[UNITY_DLL])
    fixture.tool_bytes[UNITY_ARCHIVE] = managed.getvalue()
    manifest = dict(schema_version=1, loader_version='0.7.3',
        dumper_version='2022.1.0-pre-release.21', unity_version='2021.3.45',
        archive='toolchain.zip', sources=[], files=[dict(path=name, size=len(content),
            sha256=hashlib.sha256(content).hexdigest()) for name, content in fixture.tool_bytes.items()])
    (resources / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with zipfile.ZipFile(resources / 'toolchain.zip', 'w') as archive:
        for name, content in fixture.tool_bytes.items():
            archive.writestr(name, content)
    fixture.context.loader = MelonLoaderService(fixture.context.paths,
        fixture.context.deployment_guards, offline_resources=resources)
    fixture.loader_version, fixture.unity_version = '0.7.3', '2021.3.45f2'
    monkeypatch.setattr(loader_module, 'file_version', lambda path:
        fixture.unity_version if path.name == 'UnityPlayer.dll' else fixture.loader_version)
    def no_network(*args, **kwargs):
        raise AssertionError('Offline recovery must never access the network')
    monkeypatch.setattr(loader_module, 'urlopen', no_network)
    yield fixture
    fixture.close()


def repair(app, **kwargs):
    return app.context.loader.repair(app.installation, **kwargs)


def generation(app):
    return app.context.loader.status(app.installation)['generation_dependencies']


def seed_tools(app):
    for name, content in app.tool_bytes.items():
        path = app.game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def launch_prepare(app):
    with app.context.deployment_guards.operation(app.installation, 'launch') as session:
        return app.context.loader.prepare_launch(session)


def test_missing_dependencies_are_visible_and_read_only(app):
    before = tree(app.game)
    state = generation(app)
    assert state['supported'] and state['can_repair'] and not state['ready']
    assert set(state['missing']) == set(app.tool_bytes)
    assert tree(app.game) == before


def test_repair_is_offline_and_preserves_config_generated_state_and_foreign_files(app):
    configuration = app.game / 'UserData/Loader.cfg'
    configuration.write_text('[loader]\ndisable = false\n[unityengine]\nforce_offline_generation = false\n', encoding='utf-8')
    generator_config = app.game / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Config.cfg'
    generator_config.write_bytes(b'\xef\xbb\xbf[Il2CppAssemblyGenerator]\n# retain this comment\nGameAssemblyHash = "keep"\nOldFiles = ["keep.dll"]\nDumperVersion = "historical"\nUnityVersion = "2020.1.1"\n')
    foreign = app.game / UNITY / 'foreign.txt'
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_bytes(b'user owned file')
    before = {name: path.read_bytes() for name, path in (
        ('configuration', configuration), ('generator', generator_config), ('foreign', foreign))}
    value = repair(app)
    assert value['repaired'] and value['files_written'] == len(app.tool_bytes)
    assert value['status']['generation_dependencies']['ready']
    for name, content in app.tool_bytes.items():
        assert (app.game / name).read_bytes() == content
    assert configuration.read_bytes() == before['configuration']
    assert generator_config.read_bytes() == before['generator']
    assert foreign.read_bytes() == before['foreign']
    assert repair(app)['files_written'] == 0


def test_first_install_without_generator_record_can_process_unity_without_download(app):
    config = app.game / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Config.cfg'
    assert not config.exists()
    value = repair(app)
    state = value['generation_dependencies']
    assert state['ready'] and state['unity_archive_required']
    assert not config.exists(), 'The official generator owns its version record'
    cache = app.game / UNITY_ARCHIVE
    assert cache.read_bytes() == app.tool_bytes[UNITY_ARCHIVE]
    # Official PackageBase.Setup downloads only if ShouldSetup is true AND
    # FilePath is absent. Unity's FilePath is the version-specific archive.
    should_setup = state['recorded_unity_version'] != state['unity_version']
    assert should_setup and not (should_setup and not cache.is_file())


def test_official_zero_version_sentinel_does_not_disable_default_recovery(app):
    configuration = app.game / 'UserData/Loader.cfg'
    content = b'[unityengine]\nforce_il2cpp_dumper_version = "0.0.0.0"\n'
    configuration.write_bytes(content)
    assert generation(app)['can_repair']
    assert repair(app)['generation_dependencies']['ready']
    assert configuration.read_bytes() == content


@pytest.mark.parametrize('override', ['2020.1.1', '2022.3.1f1', 'not-a-unity-version', '2021.3.45f-invalid'])
def test_different_unity_override_is_preserved_without_automatic_replacement(app, override):
    configuration = app.game / 'UserData/Loader.cfg'
    configuration.write_text(f'[unityengine]\nversion_override = "{override}"\n', encoding='utf-8')
    before = tree(app.game)
    state = generation(app)
    assert not state['supported'] and not state['can_repair'] and state['status'] == 'unknown'
    assert state['unity_version_override'] == override and 'version_override' in state['message']
    with pytest.raises(LoaderError, match='version_override'):
        repair(app)
    with pytest.raises(LoaderError, match='version_override'):
        launch_prepare(app)
    assert tree(app.game) == before
    seed_tools(app)
    before = tree(app.game)
    assert launch_prepare(app)['skipped'] == 'custom_toolchain'
    assert tree(app.game) == before


@pytest.mark.parametrize('override', ['2021.3.45', '2021.3.45f2'])
def test_same_normalized_unity_override_allows_offline_recovery(app, override):
    configuration = app.game / 'UserData/Loader.cfg'
    content = f'[unityengine]\nversion_override = "{override}"\n'.encode()
    configuration.write_bytes(content)
    assert generation(app)['can_repair']
    assert repair(app)['generation_dependencies']['ready']
    assert configuration.read_bytes() == content


@pytest.mark.parametrize('value', ['123', 'true', '[]'])
def test_invalid_unity_override_type_is_reported_as_invalid_configuration(app, value):
    configuration = app.game / 'UserData/Loader.cfg'
    configuration.write_text(f'[unityengine]\nversion_override = {value}\n', encoding='utf-8')
    before = tree(app.game)
    state = generation(app)
    assert state['status'] == 'unknown' and not state['can_repair'] and not state['ready']
    assert 'Loader.cfg' in state['message']
    with pytest.raises(LoaderError, match='Loader.cfg'):
        repair(app)
    assert tree(app.game) == before


def test_officially_saved_unity_record_makes_consumed_cache_optional(app):
    repair(app)
    cache = app.game / UNITY_ARCHIVE
    cache.unlink()  # Official FileHandler.Process removes this after extraction.
    config = app.game / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Config.cfg'
    content = b'[Il2CppAssemblyGenerator]\nUnityVersion = "2021.3.45"\nGameAssemblyHash = "keep"\nOldFiles = ["keep.dll"]\n'
    config.write_bytes(content)
    state = generation(app)
    assert state['ready'] and not state['unity_archive_required']
    assert UNITY_ARCHIVE not in state['missing']
    assert repair(app)['files_written'] == 0
    assert not cache.exists() and config.read_bytes() == content


def test_bad_generator_record_is_visible_and_never_overwritten(app):
    config = app.game / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Config.cfg'
    config.write_bytes(b'[Il2CppAssemblyGenerator]\nUnityVersion = [')
    before = tree(app.game)
    state = generation(app)
    assert not state['ready'] and not state['can_repair'] and state['status'] == 'unknown'
    with pytest.raises(LoaderError, match='Config.cfg'):
        repair(app)
    assert tree(app.game) == before


def test_real_bundled_resources_restore_isolated_game_without_network_or_execution(app):
    app.context.loader.offline = OfflineToolchain()
    manifest = app.context.loader.offline.manifest()
    config = app.game / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Config.cfg'
    result = repair(app)
    assert result['repaired'] and result['files_written'] == len(manifest['files'])
    assert result['generation_dependencies']['ready']
    assert not config.exists()
    cache = app.game / UNITY_ARCHIVE
    # Check the actual upstream cache is usable for the exact staged DLLs,
    # rather than merely placing any non-empty ZIP at the expected path.
    with zipfile.ZipFile(cache) as archive:
        unity_names = {row['path'][len(UNITY):] for row in manifest['files'] if row['path'].startswith(UNITY)}
        assert {item.filename for item in archive.infolist() if not item.is_dir()} == unity_names
        for name in unity_names:
            assert archive.read(name) == (app.game / UNITY / name).read_bytes()
    config.write_bytes(b'[Il2CppAssemblyGenerator]\nUnityVersion = "2021.3.45"\nGameAssemblyHash = "do-not-touch"\nOldFiles = ["keep.dll"]\n')
    content = config.read_bytes()
    cache.unlink()
    assert generation(app)['ready'], 'Normal upstream consumption must not trigger cache reinstallation'
    assert repair(app)['files_written'] == 0
    assert config.read_bytes() == content and not cache.exists()


def test_modified_target_invalidates_prior_verification_even_with_preserved_mtime(app):
    seed_tools(app)
    assert generation(app)['ready']
    path = app.game / CPP2IL
    stamp = path.stat()
    path.write_bytes(b'x' * len(app.tool_bytes[CPP2IL]))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    state = generation(app)
    assert CPP2IL in state['damaged'] and not state['ready']
    value = repair(app)
    assert value['files_written'] == 1
    assert path.read_bytes() == app.tool_bytes[CPP2IL]


def test_launch_restores_within_existing_guard_without_nested_lease(app, monkeypatch):
    with app.context.deployment_guards.operation(app.installation, 'launch') as session:
        def nested(*args, **kwargs):
            raise AssertionError('Must reuse the active launch session')
        monkeypatch.setattr(app.context.deployment_guards, 'operation', nested)
        result = app.context.loader.prepare_launch(session)
    assert result['repaired'] and result['generation_dependencies']['ready']


def test_ready_launch_uses_one_read_only_dependency_inspection(app, monkeypatch):
    seed_tools(app)
    original = app.context.loader._generation_state
    calls = []
    def inspect(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(app.context.loader, '_generation_state', inspect)
    before = tree(app.game)
    result = launch_prepare(app)
    assert not result['repaired'] and result['files_written'] == 0
    assert len(calls) == 1 and tree(app.game) == before


@pytest.mark.parametrize('mode', ['no_loader', 'no_loader_proxy', 'disabled'])
def test_launch_does_not_modify_unloaded_or_disabled_game(app, mode):
    if mode == 'no_loader':
        shutil.rmtree(app.game / 'MelonLoader')
    elif mode == 'no_loader_proxy':
        (app.game / 'version.dll').unlink()
    else:
        (app.game / 'UserData/Loader.cfg').write_text('[loader]\ndisable = true\n', encoding='utf-8')
    before = tree(app.game)
    assert launch_prepare(app)['skipped'] == mode
    assert tree(app.game) == before


@pytest.mark.parametrize('kind', ['custom_dumper', 'custom_loader', 'unknown_unity', 'different_unity', 'invalid_config'])
def test_unsupported_selection_never_uses_historical_config_or_overwrites_tools(app, kind):
    if kind == 'custom_dumper':
        (app.game / 'UserData/Loader.cfg').write_text(
            '[unityengine]\nforce_il2cpp_dumper_version = "custom-v3"\n', encoding='utf-8')
    elif kind == 'custom_loader':
        app.loader_version = '0.7.4'
    elif kind == 'unknown_unity':
        app.unity_version = None
    elif kind == 'different_unity':
        app.unity_version = '2022.3.1f1'
    else:
        (app.game / 'UserData/Loader.cfg').write_text('invalid = [', encoding='utf-8')
    historical = app.game / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Config.cfg'
    historical.write_text('DumperVersion = "2022.1.0-pre-release.21"\nUnityVersion = "2021.3.45"\n', encoding='utf-8')
    (app.game / 'MelonLoader/Latest.log').write_text('Unity Version: 2021.3.45f2\n', encoding='utf-8')
    before = tree(app.game)
    state = generation(app)
    assert not state['supported'] and not state['can_repair']
    with pytest.raises(LoaderError, match=state['message']):
        repair(app)
    with pytest.raises(LoaderError):
        launch_prepare(app)
    assert tree(app.game) == before
    # Custom environments which already have their own tools keep the old
    # launch contract; only automatic recovery is unsupported.
    seed_tools(app)
    before = tree(app.game)
    assert launch_prepare(app)['skipped'] == 'custom_toolchain'
    assert tree(app.game) == before


def test_running_game_rejects_repair_before_any_write(app, monkeypatch):
    monkeypatch.setattr(ProcessGuard, 'inspect', lambda _: dict(status='running', pids=[1], unknown_pids=[]))
    before = tree(app.game)
    with pytest.raises(GuardRejected) as rejected:
        repair(app)
    assert rejected.value.code == 'GUARD_PROCESS_RUNNING'
    assert tree(app.game) == before


def test_failure_after_publication_rolls_back_every_dependency(app):
    before = tree(app.game)
    def fail_after_first(done, total, *args):
        if done == 1:
            raise OSError('isolated publication failure')
    with pytest.raises(OSError, match='isolated publication failure'):
        repair(app, progress=fail_after_first)
    assert tree(app.game) == before


def test_failure_during_replacement_restores_corrupt_original_and_config(app):
    seed_tools(app)
    original = b'previous broken tool'
    (app.game / CPP2IL).write_bytes(original)
    before = tree(app.game)
    def cancel_commit(action):
        raise RuntimeError('cancel at commit boundary')
    with pytest.raises(RuntimeError, match='cancel at commit boundary'):
        repair(app, commit=cancel_commit)
    assert tree(app.game) == before


def test_native_file_lock_preserves_original_and_retry_can_restore(app):
    import win32con
    import win32file
    seed_tools(app)
    target = app.game / CPP2IL
    target.write_bytes(b'corrupt but owned previous executable')
    before = tree(app.game)
    handle = win32file.CreateFile(str(target), win32con.GENERIC_READ,
        win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING, 0, None)
    try:
        with pytest.raises(GuardRejected) as rejected:
            repair(app)
        assert rejected.value.code == 'GUARD_PATH_UNAVAILABLE'
        assert tree(app.game) == before
    finally:
        handle.Close()
    assert repair(app)['generation_dependencies']['ready']


def test_corrupt_bundled_archive_is_rejected_before_game_write(app):
    archive = app.context.loader.offline.resources / 'toolchain.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        for name, content in app.tool_bytes.items():
            stream.writestr(name, content if name != CPP2IL else b'x' * len(content))
    before = tree(app.game)
    with pytest.raises(LoaderError) as rejected:
        repair(app)
    assert rejected.value.code == 'LOADER_OFFLINE_PACKAGE_INVALID'
    assert tree(app.game) == before


def test_guard_loss_during_archive_read_keeps_its_actionable_guard_code(app):
    stage = app.root / 'staging-only'
    stage.mkdir()
    checks = 0
    def lose_guard():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise GuardRejected('GUARD_PROCESS_RUNNING')
    before = tree(app.game)
    with pytest.raises(GuardRejected) as rejected:
        app.context.loader.offline.extract(stage, list(app.tool_bytes), lose_guard)
    assert rejected.value.code == 'GUARD_PROCESS_RUNNING'
    assert tree(app.game) == before


@pytest.mark.parametrize('path', ['../escape.dll', 'Mods/foreign.dll', UNITY+'nested/tool.dll', 'UserData/Loader.cfg'])
def test_manifest_cannot_authorize_other_game_targets(app, path):
    manifest_path = app.context.loader.offline.resources / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'][2]['path'] = path
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    before = tree(app.game)
    with pytest.raises(OfflineError):
        app.context.loader.offline.manifest()
    assert not generation(app)['can_repair']
    assert tree(app.game) == before


def test_official_loader_zip_stays_valid_and_install_adds_offline_dependencies(app):
    shutil.rmtree(app.game / 'MelonLoader')
    (app.game / 'version.dll').unlink()
    handle = app.context.loader.grant(app.installation, OFFICIAL_ARCHIVE)['source_handle']
    value = app.context.loader.install(app.installation, handle)
    assert value['installed'] and value['files_written'] == 164 + len(app.tool_bytes)
    assert value['status']['generation_dependencies']['ready']
    with zipfile.ZipFile(OFFICIAL_ARCHIVE) as archive:
        for entry in archive.infolist():
            if not entry.is_dir():
                assert (app.game / entry.filename).read_bytes() == archive.read(entry)


def test_official_install_and_toolchain_roll_back_together(app):
    shutil.rmtree(app.game / 'MelonLoader')
    (app.game / 'version.dll').unlink()
    handle = app.context.loader.grant(app.installation, OFFICIAL_ARCHIVE)['source_handle']
    before = tree(app.game)
    def reject_commit(action):
        raise RuntimeError('cancel official installation')
    with pytest.raises(RuntimeError, match='cancel official installation'):
        app.context.loader.install(app.installation, handle, commit=reject_commit)
    assert tree(app.game) == before


def test_combined_install_publishes_bootstrap_only_after_offline_dependencies(app, monkeypatch):
    shutil.rmtree(app.game / 'MelonLoader')
    (app.game / 'version.dll').unlink()
    handle = app.context.loader.grant(app.installation, OFFICIAL_ARCHIVE)['source_handle']
    before = tree(app.game)
    def inspect_publication(root, stage, files, *args):
        assert files[-1] == 'version.dll'
        assert set(app.tool_bytes) <= set(files)
        raise RuntimeError('publication order checked before writes')
    monkeypatch.setattr(app.context.loader, '_publish', inspect_publication)
    with pytest.raises(RuntimeError, match='publication order checked before writes'):
        app.context.loader.install(app.installation, handle)
    assert tree(app.game) == before
