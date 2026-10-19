"""Owned fixtures exercise complete delivery assembly without running build tools."""
import hashlib
import json
from pathlib import Path
import sys
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
                 'backend/app/resources/psmm-icon.ico',
                 *[packaging.OFFLINE_RESOURCES+'/'+name for name in ('toolchain.zip',
                    'manifest.json', 'licenses/Cpp2IL-LICENSE.txt', 'licenses/UnityDependencies-README.md')]]:
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
    assert not (bundle/'licenses/build-baseline.json').exists()


@pytest.mark.parametrize('missing', ['frontend/dist/index.html', 'backend/app/resources/psmm-icon.ico',
    packaging.OFFLINE_RESOURCES+'/toolchain.zip', packaging.OFFLINE_RESOURCES+'/manifest.json',
    packaging.OFFLINE_RESOURCES+'/licenses/Cpp2IL-LICENSE.txt',
    packaging.OFFLINE_RESOURCES+'/licenses/UnityDependencies-README.md',
    packaging.NOTICE_ROOT+'/distribution-materials.json', packaging.NOTICE_ROOT+'/build-baseline.json',
    'LICENSE', 'docs/user/quick-start.md'])
def test_missing_resource_stops_before_output_or_build(checkout, monkeypatch, missing):
    root, _ = checkout
    (root/missing).unlink()
    monkeypatch.setattr(packaging, 'run', lambda *args: pytest.fail('Build must not start'))
    output = root/'new-output'
    with pytest.raises(RuntimeError, match='tools/prepare_melonloader_offline.py') as error:
        packaging.build(output)
    assert missing in str(error.value)
    assert 'docs/development/README.md' in str(error.value)
    assert not output.exists()


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


def test_build_assembles_final_delivery_before_manifest_and_archive(checkout, monkeypatch):
    root, baseline = checkout
    # Build inputs are tiny owned files. Neither helper nor PyInstaller executes.
    for name in ('main.py', 'PSModManager.spec', 'pack_pyinstaller.py', 'pyproject.toml', 'uv.lock',
                 'backend/scanning/worker_host.py', 'backend/scanning/worker_client.py',
                 'tools/PSModManager.Metadata/build.py', 'tools/PSModManager.Metadata/Program.cs',
                 'tools/PSModManager.Metadata/PSModManager.Metadata.csproj',
                 'tools/PSModManager.Metadata/console_host_main.py'):
        write(root, name, 'owned build input')
    output = root/'new-output'
    calls = []
    def fake_run(command, log, env):
        calls.append(command)
        if len(calls) == 1:
            write(output, 'helper/helper-build.json', {'runtime_version': '10.0.11'})
        else:
            write(output, 'dist/PSModManager/PSModManager.exe', b'owned fake exe')
    monkeypatch.setattr(packaging, 'run', fake_run)
    monkeypatch.setattr(packaging, 'dependencies', lambda work: baseline['dependencies'])
    result = packaging.build(output)
    assert len(calls) == 2
    bundle = Path(result['bundle'])
    actual = packaging.validate_bundle(bundle)
    manifest = json.loads((bundle/'build-manifest.json').read_text('utf-8'))
    declared = {row['path'] for row in manifest['files']}
    assert {*packaging.delivery_documents(root), 'README-PORTABLE.txt',
        'licenses/demo/LICENSE.txt', 'licenses/distribution-materials.json'} <= declared
    assert 'licenses/distribution-materials.json' in manifest['licenses']
    with zipfile.ZipFile(result['archive']) as archive:
        assert set(archive.namelist()) == {'PSModManager/'+name for name in actual}
        for name in actual:
            assert archive.read('PSModManager/'+name) == (bundle/name).read_bytes()
