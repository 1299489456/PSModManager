"""Check the actual portable payload; synthetic cases exercise the checker only.

Set PSMM_PORTABLE_ROOT to an unpacked PSModManager directory and optionally
PSMM_PORTABLE_ZIP to its archive. No product build or dependency restore runs
implicitly from this module.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
from urllib.parse import unquote, urlsplit
import zipfile

import pytest


REPO = Path(__file__).resolve().parents[2]
FORBIDDEN_DIRECTORIES = {
    '.git', '.venv', 'node_modules', '__pycache__', 'userdata', 'saves',
    'mods', 'plugins', 'melonloader', 'cpp2il', 'unitydependencies', 'data', 'private-data',
    'synthetic-game', 'tests', 'settings', 'mod-security', 'mod-security-analysis',
}
FORBIDDEN_FILENAMES = {
    '.env', 'config.json', 'settings.json', 'credentials.json', 'secrets.json', 'ai-translations.json',
    'api_key.txt', 'probably stolen.exe', 'gameassembly.dll', 'melonloader.dll',
    'main.py', 'settings.py', 'cpp2il.exe', 'toolchain.zip',
    'steam_api.dll', 'steam_api64.dll', 'steamclient.dll', 'steamclient64.dll',
    'steam_appid.txt',
}
FORBIDDEN_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.key', '.pem', '.pfx', '.p12'}
HISTORICAL_NOTICE_TEXTS = {'licenses/Cpp2IL/Cpp2IL-LICENSE.txt',
                           'licenses/UnityDependencies/UnityDependencies-README.md'}


def _safe_relative(value):
    """Portable manifests use POSIX paths without redirects or drive syntax."""
    assert isinstance(value, str) and value, 'Empty or non-string payload path'
    path = PurePosixPath(value)
    assert '\\' not in value and ':' not in value, f'Invalid payload path: {value}'
    assert not path.is_absolute() and all(part not in ('', '.', '..')
                                         for part in value.split('/')), value
    assert str(path) == value, f'Non-canonical payload path: {value}'
    return path


def _files(root):
    result = {}
    assert root.is_dir(), f'Portable directory missing: {root}'
    for item in [root, *root.rglob('*')]:
        info = item.lstat()
        assert not item.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400, (
            f'Redirected payload path: {item}')
        if item.is_file():
            relative = item.relative_to(root).as_posix()
            _safe_relative(relative)
            result[relative] = item
    return result


def _assert_no_private_or_game_files(root):
    files = _files(root)
    for relative in files:
        path = PurePosixPath(relative)
        directories = set(part.casefold() for part in path.parts[:-1]) & FORBIDDEN_DIRECTORIES
        if relative in HISTORICAL_NOTICE_TEXTS:
            # These exact text paths are additionally bound to the license catalog
            # in the delivery material check; no binary/resource exception exists.
            directories -= {'cpp2il', 'unitydependencies'}
        assert not directories, (
            f'Private, game, or development directory in payload: {relative}')
        assert path.name.casefold() not in FORBIDDEN_FILENAMES, f'Forbidden payload: {relative}'
        if relative == '_internal/certifi/cacert.pem':
            # certifi's public CA bundle is a required dependency, not a private key.
            import certifi

            trusted_bundle = Path(certifi.where())
            assert hashlib.sha256(files[relative].read_bytes()).digest() == hashlib.sha256(
                trusted_bundle.read_bytes()).digest(), 'Packaged public CA bundle differs from certifi'
            continue
        assert path.suffix.casefold() not in FORBIDDEN_SUFFIXES, f'Private payload: {relative}'
    return files


def _checksums(root):
    manifest = root / 'SHA256SUMS.txt'
    assert manifest.is_file(), 'SHA256SUMS.txt missing'
    checksums = {}
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        assert match, f'Invalid SHA256SUMS.txt line: {line!r}'
        digest, relative = match.groups()
        _safe_relative(relative)
        assert relative not in checksums, f'Duplicate checksum path: {relative}'
        assert relative != 'SHA256SUMS.txt', 'Checksum file cannot hash itself'
        checksums[relative] = digest
    assert checksums, 'Empty checksum file'
    return checksums


def _assert_checksums(root):
    checksums = _checksums(root)
    payload = _files(root)
    assert set(checksums) == set(payload) - {'SHA256SUMS.txt'}, (
        f'Checksum coverage mismatch; unlisted={set(payload) - set(checksums) - {"SHA256SUMS.txt"}}, '
        f'missing={set(checksums) - set(payload)}')
    for relative, digest in checksums.items():
        assert hashlib.sha256(payload[relative].read_bytes()).hexdigest() == digest, (
            f'Payload hash mismatch: {relative}')
    return checksums


def _assert_pe(path, subsystem):
    raw = path.read_bytes()
    assert raw[:2] == b'MZ' and len(raw) >= 64, f'Not a PE executable: {path.name}'
    offset = struct.unpack_from('<I', raw, 0x3c)[0]
    assert offset + 94 <= len(raw) and raw[offset:offset + 4] == b'PE\0\0', path.name
    assert struct.unpack_from('<H', raw, offset + 4)[0] == 0x8664, (
        f'Executable is not AMD64: {path.name}')
    optional = offset + 24
    assert struct.unpack_from('<H', raw, optional)[0] == 0x20b, path.name
    assert struct.unpack_from('<H', raw, optional + 68)[0] == subsystem, (
        f'Unexpected PE subsystem: {path.name}')


@pytest.fixture(scope='module')
def portable_root():
    configured = os.environ.get('PSMM_PORTABLE_ROOT')
    if not configured:
        pytest.skip('Set PSMM_PORTABLE_ROOT to the actual built/unpacked Windows x64 product')
    root = Path(configured).absolute()
    if not root.is_dir():
        pytest.skip(f'Actual portable directory is not available: {root}')
    return root


def test_portable_executables_have_product_names_and_x64_subsystems(portable_root):
    _assert_pe(portable_root / 'PSModManager.exe', 2)  # Windows GUI.
    _assert_pe(portable_root / 'PSModManager.WorkerHost.exe', 3)  # Worker pipe host.
    assert not (portable_root / 'RimCrow.exe').exists()


def test_portable_python_archive_excludes_legacy_entry_modules(portable_root):
    # Use the installed build tool's reader; never execute archived product code.
    from PyInstaller.archive.readers import CArchiveReader

    forbidden = ('settings', 'api', 'backend.managers', 'backend.routers', 'backend.legacy',
                 'backend.loaders.melonloader', 'backend.online.nexus',
                 'backend.online.api', 'backend.online.nxm_protocol')
    for executable in ('PSModManager.exe', 'PSModManager.WorkerHost.exe'):
        archive = CArchiveReader(str(portable_root / executable))
        embedded = [name for name, entry in archive.toc.items() if entry[-1] == 'z']
        assert embedded, f'Missing Python archive: {executable}'
        modules = set()
        for name in embedded:
            modules.update(archive.open_embedded_archive(name).toc)
        expected = ('backend.app.identity' if executable == 'PSModManager.exe'
                    else 'backend.scanning.worker_sandbox')
        assert expected in modules, f'Missing current entry dependency: {executable}: {expected}'
        if executable == 'PSModManager.exe':
            native_dependencies = {'backend.native_mods.service', 'backend.native_mods.packages',
                                   'backend.native_mods.state', 'backend.workshop.service',
                                   'backend.workshop.remote', 'backend.workshop.cache', 'backend.workshop.steam_session',
                                   'backend.workshop.steam_native', 'backend.workshop.signature',
                                   'backend.ai.providers', 'backend.ai.translation_cache',
                                   'backend.ai.security_report', 'backend.app.security_ai_api',
                                   'backend.mod_security.service', 'backend.mod_security.storage',
                                   'backend.mod_security.evidence', 'backend.mod_security.rules',
                                   'backend.mod_security.errors', 'backend.mod_security.defender',
                                   'backend.scanning.worker_client',
                                   'httpx', 'httpcore', 'html.parser'}
            assert native_dependencies <= modules, (
                f'Missing native/Workshop dependencies: {native_dependencies - modules}')
        for prefix in forbidden:
            assert not any(name == prefix or name.startswith(prefix + '.') for name in modules), (
                f'Legacy module {prefix} included in {executable}')


def test_portable_frontend_assets_and_rules_are_complete(portable_root):
    frontend = portable_root / '_internal/frontend/dist'
    html = (frontend / 'index.html').read_text(encoding='utf-8')
    assert '<title>PSModManager</title>' in html
    references = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', html)
    suffixes = set()
    for reference in references:
        parsed = urlsplit(reference)
        assert not parsed.scheme and not parsed.netloc, f'External frontend resource: {reference}'
        relative = unquote(parsed.path)
        if relative.startswith('./'):
            relative = relative[2:]
        _safe_relative(relative)
        assert (frontend / relative).is_file(), f'Missing frontend resource: {reference}'
        suffixes.add(Path(relative).suffix)
    assert {'.js', '.css'} <= suffixes
    assert 'connect-src \'none\'' in html
    rules_path = Path('backend/diagnostics/rules/definitions.json')
    packaged = json.loads((portable_root / '_internal' / rules_path).read_text(encoding='utf-8'))
    source = json.loads((REPO / rules_path).read_text(encoding='utf-8'))
    assert packaged == source, 'Packaged diagnostic rules differ from current product rules'
    assert packaged['defaults']['automatic_actions'] == []
    assert len(packaged['rules']) >= 10


def test_portable_is_native_mono_without_loader_generation_resources(portable_root):
    manifest = json.loads((portable_root/'build-manifest.json').read_text('utf-8'))
    expected = 'public-native-mono' if os.environ.get('PSMM_PUBLIC_RELEASE') == '1' else 'local-native-mono'
    assert manifest['distribution_profile'] == expected
    _assert_no_private_or_game_files(portable_root)
    assert not (portable_root/'_internal/backend/loaders/melonloader').exists()


def test_portable_metadata_helper_contains_its_runtime(portable_root):
    helper = portable_root / '_internal/tools/PSModManager.Metadata/bin'
    _assert_pe(helper / 'PSModManager.Metadata.exe', 3)
    for name in ('PSModManager.Metadata.dll', 'PSModManager.Metadata.deps.json',
                 'hostfxr.dll', 'hostpolicy.dll', 'coreclr.dll', 'System.Private.CoreLib.dll',
                 'LICENSE-dotnet.txt', 'THIRD-PARTY-NOTICES-dotnet.txt'):
        assert (helper / name).is_file(), f'Missing self-contained helper file: {name}'
    runtime = json.loads((helper / 'PSModManager.Metadata.runtimeconfig.json').read_text('utf-8'))
    options = runtime['runtimeOptions']
    assert options['tfm'] == 'net10.0'
    assert 'framework' not in options and 'frameworks' not in options
    assert any(item['name'] == 'Microsoft.NETCore.App' for item in options['includedFrameworks'])
    dependency = json.loads((helper / 'PSModManager.Metadata.deps.json').read_text('utf-8'))
    assert dependency['runtimeTarget']['name'].endswith('/win-x64')
    build = json.loads((helper / 'helper-build.json').read_text('utf-8'))
    assert build['self_contained'] is True and build['target'] == 'win-x64'
    assert build['commands_exit_codes'] == [0, 0]
    assert re.fullmatch(r'[0-9a-f]{64}', build['runtime_package_sha256'])
    assert re.fullmatch(r'[0-9a-f]{64}', build['security_source_sha256'])
    assert re.fullmatch(r'[0-9a-f]{64}', build['approval_source_sha256'])
    manifest = json.loads((portable_root / 'build-manifest.json').read_text('utf-8'))
    inputs = {row['path']: row['sha256'] for row in manifest['inputs']}
    assert build['security_source_sha256'] == inputs['tools/PSModManager.Metadata/SecurityAnalysis.cs']
    assert build['approval_source_sha256'] == inputs['tools/PSModManager.Metadata/ApprovalAnalysis.cs']


def test_portable_has_no_private_game_or_legacy_migration_payload(portable_root):
    _assert_no_private_or_game_files(portable_root)
    readme = (portable_root / 'README-PORTABLE.txt').read_text(encoding='utf-8-sig')
    assert 'PSModManager' in readme and 'WebView2' in readme
    assert 'LocalAppData' in readme


def test_portable_checksum_file_covers_actual_bytes(portable_root):
    _assert_checksums(portable_root)


def test_local_delivery_includes_current_docs_and_license_materials(portable_root):
    if not os.environ.get('PSMM_FINAL_DELIVERY'):
        pytest.skip('Final delivery material check is opt-in; raw builds have no delivery docs')
    documents = ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'docs/development/known-limitations.md',
                 'docs/development/workshop-usability-2026-10-28.md',
                 'docs/development/workshop-performance-settings-2026-10-29.md',
                 'docs/development/workshop-recovery-scope-fix-2026-10-29.md',
                 'docs/development/mod-security-implementation-2026-10-30.md',
                 'docs/development/interaction-reliability-fix-2026-10-30.md',
                 'docs/development/manual-mod-check-2026-10-30.md',
                 'docs/development/standalone-mod-check-2026-10-29.md']
    documents += [path.relative_to(REPO).as_posix() for path in (REPO / 'docs/user').glob('*.md')]
    for name in documents:
        assert (portable_root / name).read_bytes() == (REPO / name).read_bytes(), name
    record = json.loads((portable_root / 'licenses/distribution-materials.json').read_text('utf-8'))
    paths = set()
    for component in record['components']:
        for material in component['materials']:
            relative = _safe_relative(material['path'])
            raw = (portable_root / 'licenses' / relative).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == material['sha256'], relative
            paths.add(str(relative))
    assert len(record['components']) == record['components_count']
    assert len(paths) == record['materials_count']
    for name in ['THIRD_PARTY_NOTICES.md', 'docs/development/known-limitations.md',
                 'docs/development/workshop-usability-2026-10-28.md',
                 'docs/development/workshop-performance-settings-2026-10-29.md',
                 'docs/development/workshop-recovery-scope-fix-2026-10-29.md',
                 'docs/development/mod-security-implementation-2026-10-30.md',
                 'docs/development/interaction-reliability-fix-2026-10-30.md',
                 'docs/development/manual-mod-check-2026-10-30.md',
                 'docs/development/standalone-mod-check-2026-10-29.md',
                 *[p.relative_to(REPO).as_posix() for p in (REPO / 'docs/user').glob('*.md')]]:
        for link in re.findall(r'\]\(([^)]+)\)', (portable_root / name).read_text('utf-8')):
            if urlsplit(link).scheme or link.startswith('#'):
                continue
            target = ((portable_root / name).parent / unquote(urlsplit(link).path)).resolve()
            assert target.is_relative_to(portable_root.resolve()) and target.exists(), (name, link)


def test_portable_build_manifest_binds_payload_and_dependency_materials(portable_root):
    manifest = json.loads((portable_root / 'build-manifest.json').read_text('utf-8'))
    assert manifest['schema_version'] == 1
    assert manifest['product'] == 'PSModManager'
    from backend.app.identity import APP_VERSION
    assert manifest['version'] == APP_VERSION
    assert manifest['target'] == 'windows-x64'
    assert manifest['self_excluded'] == ['build-manifest.json', 'SHA256SUMS.txt']
    files = _files(portable_root)
    declared = {}
    for entry in manifest['files']:
        relative = entry['path']
        _safe_relative(relative)
        assert relative not in declared, f'Duplicate build-manifest path: {relative}'
        declared[relative] = entry
    assert set(declared) == set(files) - set(manifest['self_excluded'])
    for relative, entry in declared.items():
        assert entry['size'] == files[relative].stat().st_size, f'Wrong size: {relative}'
        assert re.fullmatch(r'[0-9a-f]{64}', entry['sha256']), relative
        assert entry['sha256'] == hashlib.sha256(files[relative].read_bytes()).hexdigest(), relative
    for key in ('requirements', 'tools', 'inputs', 'dependencies', 'helper'):
        assert manifest[key], f'Missing dependency/build material information: {key}'


def test_portable_archive_matches_directory(portable_root):
    configured = os.environ.get('PSMM_PORTABLE_ZIP')
    if not configured:
        pytest.skip('Set PSMM_PORTABLE_ZIP to the actual portable archive')
    archive_path = Path(configured)
    assert archive_path.is_file(), f'Portable archive missing: {archive_path}'
    payload = _files(portable_root)
    with zipfile.ZipFile(archive_path) as archive:
        all_entries = archive.infolist()
        all_names = [item.filename for item in all_entries]
        assert len(all_names) == len(set(all_names)), 'Duplicate ZIP entry'
        for entry in all_entries:
            path = _safe_relative(entry.filename.rstrip('/'))
            assert path.parts[0] == 'PSModManager', f'Unexpected ZIP root: {entry.filename}'
            assert (entry.external_attr >> 16) & 0o170000 != 0o120000, entry.filename
        entries = [item for item in all_entries if not item.is_dir()]
        names = [item.filename for item in entries]
        expected = {f'PSModManager/{relative}' for relative in payload}
        assert set(names) == expected, 'Portable ZIP does not match the product directory'
        for entry in entries:
            relative = entry.filename.removeprefix('PSModManager/')
            assert hashlib.sha256(archive.read(entry)).digest() == hashlib.sha256(
                payload[relative].read_bytes()).digest(), f'Archive byte mismatch: {relative}'


def _small_payload(tmp_path):
    (tmp_path / 'README-PORTABLE.txt').write_bytes(b'owned checksum negative sample')
    digest = hashlib.sha256((tmp_path / 'README-PORTABLE.txt').read_bytes()).hexdigest()
    (tmp_path / 'SHA256SUMS.txt').write_text(f'{digest}  README-PORTABLE.txt\n', encoding='utf-8')
    return tmp_path


@pytest.mark.parametrize('mutation', ['changed', 'missing', 'unlisted', 'escape', 'duplicate'])
def test_checksum_checker_rejects_owned_negative_samples(tmp_path, mutation):
    root = _small_payload(tmp_path)
    _assert_checksums(root)
    if mutation == 'changed':
        (root / 'README-PORTABLE.txt').write_bytes(b'changed owned bytes')
    elif mutation == 'missing':
        (root / 'README-PORTABLE.txt').unlink()
    elif mutation == 'unlisted':
        (root / 'unlisted.txt').write_bytes(b'owned unlisted file')
    elif mutation == 'escape':
        (root / 'SHA256SUMS.txt').write_text('0' * 64 + '  ../outside.txt\n', encoding='utf-8')
    elif mutation == 'duplicate':
        checksum = root / 'SHA256SUMS.txt'
        checksum.write_text(checksum.read_text('utf-8') * 2, encoding='utf-8')
    with pytest.raises(AssertionError):
        _assert_checksums(root)


@pytest.mark.parametrize('relative', [
    'game/Probably Stolen.exe', 'Mods/owned-fixture.dll', 'UserData/save.bin',
    'catalog.db', 'credentials.json', 'ai-translations.json', '.env', 'settings/main.py', 'other/cacert.pem',
    'mod-security/installation/report.json', '_internal/mod-security/installation/dispositions.json',
    'mod-security-analysis/fixture-cache.json',
    '_internal/backend/loaders/melonloader/resources/toolchain.zip',
    'Cpp2IL/Cpp2IL.exe', 'UnityDependencies/UnityEngine.dll',
    '_internal/steam_api64.dll', '_internal/steam_api.dll',
    '_internal/steamclient.dll', '_internal/steamclient64.dll', 'steam_appid.txt',
])
def test_payload_checker_rejects_owned_private_game_and_legacy_samples(tmp_path, relative):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'owned forbidden sample, no real user content')
    with pytest.raises(AssertionError):
        _assert_no_private_or_game_files(tmp_path)


def test_payload_checker_binds_exact_public_ca_exception_to_installed_certifi(tmp_path):
    import certifi

    target = tmp_path / '_internal/certifi/cacert.pem'
    target.parent.mkdir(parents=True)
    target.write_bytes(Path(certifi.where()).read_bytes())
    _assert_no_private_or_game_files(tmp_path)
    target.write_bytes(b'owned unexpected bytes in public CA exception')
    with pytest.raises(AssertionError, match='public CA bundle differs'):
        _assert_no_private_or_game_files(tmp_path)
