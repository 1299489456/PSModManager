"""Owned fixtures exercise complete delivery assembly without running build tools."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import zipfile

import pytest

import pack_pyinstaller as packaging


def write(root, name, value):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
    return path


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path/'checkout'
    baseline = dict(python_version='.'.join(map(str, sys.version_info[:3])),
        helper_runtime_version='10.0.11',
        dependencies=[dict(name='demo-package', version='1.2.3', role='bundled_dependency')],
        frontend_packages={'vue': '3.5.40'})
    for name in [*packaging.DELIVERY_DOCUMENTS, 'docs/user/quick-start.md',
                 'docs/user/data-and-reinstall.md', 'frontend/dist/index.html',
                 'backend/app/resources/psmm-icon.ico']:
        write(root, name, 'owned fixture '+name)
    write(root, 'frontend/package-lock.json', {'packages': {'node_modules/vue': {'version': '3.5.40'}}})
    write(root, 'frontend/node_modules/vue/package.json', {'version': '3.5.40'})
    write(root, packaging.NOTICE_ROOT+'/build-baseline.json', baseline)
    material = b'Owned third-party license fixture\n'
    row = dict(path='demo/LICENSE.txt', bytes=len(material), sha256=hashlib.sha256(material).hexdigest())
    catalog = dict(schema_version=1, components_count=1, materials_count=1,
        distribution_relative_root='licenses', components=[dict(name='demo-package', version='1.2.3',
            role='bundled_dependency', source='https://example.invalid/demo',
            license_paths=[row['path']], materials=[row])])
    write(root, packaging.NOTICE_ROOT+'/distribution-materials.json', catalog)
    write(root, packaging.NOTICE_ROOT+'/'+row['path'], material)
    monkeypatch.setattr(packaging.metadata, 'version', lambda name: '1.2.3')
    monkeypatch.setattr(packaging, 'ROOT', root)
    return root, baseline


def test_delivery_copies_current_documents_and_catalog_materials(checkout, tmp_path):
    root, baseline = checkout
    actual_baseline, catalog, materials = packaging.preflight(root)
    assert actual_baseline == baseline and catalog['materials_count'] == 1
    bundle = tmp_path/'bundle'
    copied = packaging.delivery_materials(root, bundle, materials)
    for name in packaging.delivery_documents(root):
        assert (bundle/name).read_bytes() == (root/name).read_bytes()
    for name in [*materials, 'distribution-materials.json']:
        assert (bundle/'licenses'/name).read_bytes() == (root/packaging.NOTICE_ROOT/name).read_bytes()
        assert 'licenses/'+name in copied
    readme = (bundle/'README-PORTABLE.txt').read_text('utf-8-sig')
    assert all(text in readme for text in ('完整解压', 'WebView2', 'LocalAppData', '数据位置'))
    assert '无需安装 MelonLoader' in readme and 'Steam 创意工坊' in readme
    assert f'核对实际运行版本 {packaging.VERSION}' in readme
    assert '解压新包不会更新仍打开的旧窗口' in readme
    assert '顶部独立“模组检查”页' in readme
    assert 'AI 与 Nexus' not in readme
    assert not (bundle/'licenses/build-baseline.json').exists()


@pytest.mark.parametrize('missing', ['frontend/dist/index.html', 'backend/app/resources/psmm-icon.ico',
    packaging.NOTICE_ROOT+'/distribution-materials.json', packaging.NOTICE_ROOT+'/build-baseline.json',
    'LICENSE', 'docs/user/quick-start.md',
    'docs/development/workshop-recovery-scope-fix-2026-10-29.md',
    'docs/development/mod-security-implementation-2026-10-30.md',
    'docs/development/interaction-reliability-fix-2026-10-30.md',
    'docs/development/manual-mod-check-2026-10-30.md',
    'docs/development/standalone-mod-check-2026-10-29.md'])
def test_missing_resource_stops_before_output_or_build(checkout, monkeypatch, missing):
    root, _ = checkout
    (root/missing).unlink()
    monkeypatch.setattr(packaging, 'run', lambda *args: pytest.fail('Build must not start'))
    output = root/'new-output'
    with pytest.raises(RuntimeError, match='Required build resources') as error:
        packaging.build(output)
    assert missing in str(error.value)
    assert 'docs/development/README.md' in str(error.value)
    assert not output.exists()


@pytest.mark.parametrize('public_release', [False, True])
def test_native_preflight_needs_no_legacy_resources(checkout, public_release):
    root, baseline = checkout
    assert not (root/'backend/loaders').exists()
    assert packaging.preflight(root, public_release=public_release)[0] == baseline


def test_legacy_resource_argument_fails_before_build_with_removal_instruction(checkout, monkeypatch):
    root, _ = checkout
    monkeypatch.setattr(packaging, 'run', lambda *args: pytest.fail('Build must not start'))
    with pytest.raises(ValueError, match='Remove this argument'):
        packaging.build(root/'new-output', offline_resources=root/'old-toolchain')
    assert not (root/'new-output').exists()


def test_bundle_data_ignores_legacy_resources_and_environment(checkout, tmp_path, monkeypatch):
    root, _ = checkout
    legacy = root/'backend/loaders/melonloader/resources'
    write(legacy, 'toolchain.zip', b'old archive')
    override = tmp_path/'old-override'
    write(override, 'toolchain.zip', b'old override')
    helper = tmp_path/'helper'
    write(helper, 'PSModManager.Metadata.dll', b'owned metadata helper')
    write(root, 'backend/diagnostics/rules/definitions.json', '{}')
    monkeypatch.setenv('PSMM_OFFLINE_RESOURCES', str(override))
    rows = packaging.bundle_data(root, helper)
    names = {Path(source).name for source, _ in rows}
    assert names == {'index.html', 'psmm-icon.ico', 'definitions.json', 'PSModManager.Metadata.dll'}
    assert any(target == str(Path('tools/PSModManager.Metadata/bin')) for _, target in rows)


@pytest.mark.parametrize('relative', [
    '_internal/backend/loaders/melonloader/resources/toolchain.zip',
    '_internal/Cpp2IL/Cpp2IL.exe', 'MelonLoader/MelonLoader.dll',
    'UnityDependencies/UnityEngine.dll', '_internal/tools/Cpp2IL.exe',
    'licenses/Cpp2IL/Cpp2IL.exe', 'licenses/UnityDependencies/UnityEngine.dll',
])
def test_package_inventory_rejects_legacy_loader_payload(tmp_path, relative):
    write(tmp_path, relative, b'owned legacy fixture')
    with pytest.raises(ValueError, match='Forbidden release content'):
        packaging.package_files(tmp_path)


@pytest.mark.parametrize('relative', [
    '_internal/steam_api64.dll', '_internal/steam_api.dll',
    '_internal/steamclient.dll', '_internal/steamclient64.dll',
    'steam_appid.txt',
])
def test_package_inventory_rejects_external_steam_runtime(tmp_path, relative):
    write(tmp_path, relative, b'owned external runtime fixture')
    with pytest.raises(ValueError, match='Forbidden release content'):
        packaging.package_files(tmp_path)


def test_package_inventory_rejects_private_translation_cache(tmp_path):
    write(tmp_path, 'ai-translations.json', '{"owned": "private cached text"}')
    with pytest.raises(ValueError, match='Forbidden release content'):
        packaging.package_files(tmp_path)


@pytest.mark.parametrize('relative', ['mod-security/installation/report.json',
                                     '_internal/mod-security/installation/dispositions.json',
                                     'mod-security-analysis/fixture-cache.json'])
def test_package_inventory_rejects_private_security_reports_and_dispositions(tmp_path, relative):
    write(tmp_path, relative, '{"owned": "private security history"}')
    with pytest.raises(ValueError, match='Forbidden release content'):
        packaging.package_files(tmp_path)


def test_historical_notice_exception_is_limited_to_exact_text_paths(tmp_path):
    for name in packaging.HISTORICAL_NOTICE_TEXTS:
        write(tmp_path, name, b'owned historical notice text')
    assert {row['path'] for row in packaging.package_files(tmp_path)} == packaging.HISTORICAL_NOTICE_TEXTS


def test_desktop_initializes_without_frozen_excluded_loader_or_nexus(tmp_path):
    """Catch startup imports hidden by a source checkout but absent in the EXE."""
    root = Path(__file__).resolve().parents[2]
    spec = ast.parse((root/'PSModManager.spec').read_text('utf-8'))
    excluded = next(ast.literal_eval(node.value) for node in spec.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == 'excluded' for target in node.targets))
    retired = [name for name in excluded if name.startswith(('backend.loaders.', 'backend.online.'))]
    assert 'backend.loaders.melonloader' in retired and 'backend.online.nexus' in retired
    code = f'''
import importlib.abc
import sys
sys.path.insert(0, {str(root)!r})
retired = {retired!r}
class FrozenExclusions(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in retired):
            raise ModuleNotFoundError('Excluded from native portable: ' + fullname, name=fullname)
sys.meta_path.insert(0, FrozenExclusions())
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
context = DesktopContext(resolve_paths({str(tmp_path/'isolated-desktop')!r}))
try:
    context.initialize()
    assert context.native_mods is not None and context.workshop is not None
    print('native desktop initialized without retired modules')
finally:
    context.close()
'''
    result = subprocess.run([sys.executable, '-I', '-c', code], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'native desktop initialized without retired modules' in result.stdout


@pytest.mark.parametrize('field,value', [
    ('python_version', '3.12.0'), ('helper_runtime_version', '10.0.12'),
    ('packages', []),
    ('packages', [dict(name='demo-package', version='2.0.0', role='bundled_dependency')]),
    ('packages', [dict(name='demo-package', version='1.2.3', role='build')]),
    ('packages', [dict(name='demo-package', version='1.2.3', role='bundled_dependency'),
                  dict(name='new-package', version='1.0', role='bundled_dependency')]),
    ('frontend_packages', {'vue': '3.6.0'}),
])
def test_notice_baseline_rejects_changed_versions_or_dependency_set(checkout, field, value):
    _, baseline = checkout
    with pytest.raises(RuntimeError, match='Update the notice texts'):
        packaging.check_notice_baseline(baseline, **{field: value})


def test_notice_baseline_accepts_canonical_python_names(checkout):
    _, baseline = checkout
    packaging.check_notice_baseline(baseline, python_version=baseline['python_version'],
        helper_runtime_version='10.0.11', frontend_packages={'vue': '3.5.40'},
        packages=[dict(name='Demo_Package', version='1.2.3', role='bundled_dependency')])


def test_preflight_rejects_installed_python_version_drift(checkout, monkeypatch):
    root, _ = checkout
    monkeypatch.setattr(packaging.metadata, 'version', lambda name: '9.9.9')
    with pytest.raises(RuntimeError, match='demo-package'):
        packaging.preflight(root)


def test_preflight_rejects_frontend_install_lock_disagreement(checkout):
    root, _ = checkout
    write(root, 'frontend/node_modules/vue/package.json', {'version': '3.6.0'})
    with pytest.raises(RuntimeError, match='installed 3.6.0, locked 3.5.40'):
        packaging.preflight(root)


def test_preflight_reports_missing_notice_text_before_build(checkout, monkeypatch):
    root, _ = checkout
    (root/packaging.NOTICE_ROOT/'demo/LICENSE.txt').unlink()
    monkeypatch.setattr(packaging, 'run', lambda *args: pytest.fail('Build must not start'))
    output = root/'new-output'
    with pytest.raises(RuntimeError, match='Restore or update third_party/notices'):
        packaging.build(output)
    assert not output.exists()


@pytest.mark.parametrize('mutation', ['bytes', 'count', 'escape', 'license_paths'])
def test_notice_inventory_rejects_invalid_materials(checkout, mutation):
    root, _ = checkout
    path = root/packaging.NOTICE_ROOT/'distribution-materials.json'
    catalog = json.loads(path.read_text('utf-8'))
    component = catalog['components'][0]
    if mutation == 'bytes':
        write(root, packaging.NOTICE_ROOT+'/demo/LICENSE.txt', 'changed bytes')
    elif mutation == 'count':
        catalog['materials_count'] = 2
    elif mutation == 'escape':
        component['materials'][0]['path'] = '../outside.txt'
        component['license_paths'] = ['../outside.txt']
    else:
        component['license_paths'] = ['missing.txt']
    write(root, packaging.NOTICE_ROOT+'/distribution-materials.json', catalog)
    with pytest.raises(ValueError):
        packaging.notice_materials(root)


@pytest.mark.parametrize('public_release', [False, True])
def test_build_assembles_final_delivery_before_manifest_and_archive(checkout, monkeypatch, public_release):
    root, baseline = checkout
    # Build inputs are tiny owned files. Neither helper nor PyInstaller executes.
    for name in ('main.py', 'PSModManager.spec', 'pack_pyinstaller.py', 'pyproject.toml', 'uv.lock',
                 'backend/scanning/worker_host.py', 'backend/scanning/worker_client.py',
                 'tools/PSModManager.Metadata/build.py', 'tools/PSModManager.Metadata/Program.cs',
                 'tools/PSModManager.Metadata/SecurityAnalysis.cs',
                 'tools/PSModManager.Metadata/ApprovalAnalysis.cs',
                 'tools/PSModManager.Metadata/PSModManager.Metadata.csproj',
                 'tools/PSModManager.Metadata/console_host_main.py'):
        write(root, name, 'owned build input')
    output = root/'new-output'
    calls = []
    def fake_run(command, log, env):
        calls.append(command)
        assert 'PSMM_OFFLINE_RESOURCES' not in env
        if len(calls) == 1:
            write(output, 'helper/helper-build.json', {'runtime_version': '10.0.11'})
        else:
            write(output, 'dist/PSModManager/PSModManager.exe', b'owned fake exe')
    monkeypatch.setattr(packaging, 'run', fake_run)
    monkeypatch.setattr(packaging, 'dependencies', lambda work: baseline['dependencies'])
    monkeypatch.setenv('PSMM_OFFLINE_RESOURCES', str(root/'unused-legacy-tools'))
    result = packaging.build(output, public_release=public_release)
    assert len(calls) == 2
    bundle = Path(result['bundle'])
    actual = packaging.validate_bundle(bundle)
    manifest = json.loads((bundle/'build-manifest.json').read_text('utf-8'))
    assert manifest['distribution_profile'] == ('public-native-mono' if public_release else 'local-native-mono')
    declared = {row['path'] for row in manifest['files']}
    assert {*packaging.delivery_documents(root), 'README-PORTABLE.txt',
        'licenses/demo/LICENSE.txt', 'licenses/distribution-materials.json'} <= declared
    assert 'licenses/distribution-materials.json' in manifest['licenses']
    assert 'tools/PSModManager.Metadata/SecurityAnalysis.cs' in {row['path'] for row in manifest['inputs']}
    assert 'tools/PSModManager.Metadata/ApprovalAnalysis.cs' in {row['path'] for row in manifest['inputs']}
    with zipfile.ZipFile(result['archive']) as archive:
        assert set(archive.namelist()) == {'PSModManager/'+name for name in actual}
        for name in actual:
            assert archive.read('PSModManager/'+name) == (bundle/name).read_bytes()
