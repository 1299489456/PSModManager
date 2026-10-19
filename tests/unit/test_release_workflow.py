"""Release contracts use owned bytes and fake APIs, never credentials or games."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

import pack_pyinstaller as packaging
from backend.loaders.melonloader.offline import CPP2IL, SCRS, UNITY, OfflineError, OfflineToolchain
from tools.check_release_version import check
from tools.prepare_public_toolchain import build
from tools.publish_release import checked_assets, publish

ROOT = Path(__file__).resolve().parents[2]
COMMIT = '1'*40


def write(root, name, value):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return path


@pytest.fixture
def public_resources(tmp_path):
    original = tmp_path/'original'
    source = tmp_path/'source'
    rows = []
    for name, local in ((CPP2IL, 'Cpp2IL.exe'), (SCRS, 'Cpp2IL.Plugin.StrippedCodeRegSupport.dll')):
        raw = b'MZ inert fixture '+local.encode()
        write(source, local, raw)
        rows.append(dict(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    manifest = dict(schema_version=1, loader_version='0.7.3', dumper_version='2022.1.0-pre-release.21',
        unity_version='2021.3.45', archive='toolchain.zip', files=rows,
        sources=[dict(component=name, url='https://github.com/SamboyCoding/Cpp2IL/releases/download/fixture/'+name)
                 for name in ('Cpp2IL', 'Cpp2IL.Plugin.StrippedCodeRegSupport')])
    write(original, 'manifest.json', manifest)
    write(original, 'licenses/Cpp2IL-LICENSE.txt', b'MIT License fixture')
    output = build(tmp_path/'public', source=source, resources=original)
    return output, original, source


def test_public_preparation_leaves_local_resources_and_omits_unity(public_resources):
    output, original, source = public_resources
    packaging.validate_public_resources(output)
    assert not (original/'toolchain.zip').exists()
    assert 'profile' not in json.loads((original/'manifest.json').read_text())
    with zipfile.ZipFile(output/'toolchain.zip') as archive:
        assert set(archive.namelist()) == {CPP2IL, SCRS}
        assert archive.read(CPP2IL) == (source/'Cpp2IL.exe').read_bytes()


def test_public_preparation_rejects_unverified_upstream_bytes(public_resources, tmp_path):
    _, original, source = public_resources
    (source/'Cpp2IL.exe').write_bytes(b'MZ wrong content')
    output = tmp_path/'failed'
    with pytest.raises(ValueError, match='verification failed'):
        build(output, source=source, resources=original)
    assert not output.exists()


@pytest.mark.parametrize('mutation', ['full', 'unity', 'extra', 'corrupt'])
def test_public_packaging_rejects_full_or_unexpected_resources(public_resources, mutation):
    output, _, _ = public_resources
    manifest_path = output/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if mutation == 'full':
        manifest.pop('profile')
    elif mutation == 'unity':
        manifest['files'][1]['path'] = UNITY+'UnityEngine.dll'
    elif mutation == 'extra':
        write(output, 'unexpected.dll', b'MZ extra')
    else:
        (output/'toolchain.zip').write_bytes(b'broken zip')
    write(output, 'manifest.json', manifest)
    with pytest.raises((RuntimeError, OfflineError)):
        packaging.validate_public_resources(output)


def test_public_runtime_restores_only_cpp2il_and_reports_unity_download(public_resources, tmp_path):
    output, _, _ = public_resources
    toolchain = OfflineToolchain(output)
    game = tmp_path/'owned-game'
    game.mkdir()
    state = toolchain.assess(game, loader_version='0.7.3', current_unity='2021.3.45f2')
    assert state['can_repair'] and set(state['missing']) == {CPP2IL, SCRS}
    toolchain.extract(game, state['missing'])
    state = toolchain.assess(game, loader_version='0.7.3', current_unity='2021.3.45f2')
    assert state['ready'] and not state['offline_complete'] and state['unity_download_required']
    assert '联网下载' in state['message']


def test_bundle_resource_override_does_not_copy_local_full_pack(public_resources, tmp_path, monkeypatch):
    output, _, _ = public_resources
    root = tmp_path/'repo'
    write(root, packaging.OFFLINE_RESOURCES+'/toolchain.zip', b'not public')
    write(root, 'backend/app/resources/psmm-icon.ico', b'icon')
    helper = tmp_path/'helper'
    write(helper, 'helper.dll', b'helper')
    monkeypatch.setenv('PSMM_OFFLINE_RESOURCES', str(output))
    rows = packaging.bundle_data(root, helper)
    archives = [(source, target) for source, target in rows if Path(source).name == 'toolchain.zip']
    assert archives == [(str(output/'toolchain.zip'), str(Path(packaging.OFFLINE_RESOURCES)))]


@pytest.mark.parametrize('tag', ['v999999.0.0', '0.1.0', 'v01.1.0', 'v0.1.0;echo bad', 'main', 'v0.1.0-beta'])
def test_release_tag_must_match_committed_product_version(tag):
    with pytest.raises(ValueError):
        check(tag)


def test_current_tag_matches_all_version_sources():
    from backend.app.identity import APP_VERSION
    assert check('v'+APP_VERSION) == packaging.VERSION


def test_packager_can_be_loaded_by_isolated_pyinstaller_spec(tmp_path):
    code = ('import importlib.util; '
        f'spec=importlib.util.spec_from_file_location("owned_packager", {str(ROOT/"pack_pyinstaller.py")!r}); '
        'module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); print(module.VERSION)')
    result = subprocess.run([sys.executable, '-I', '-c', code], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == packaging.VERSION


@pytest.fixture
def assets(tmp_path):
    directory = tmp_path/'assets'
    directory.mkdir()
    archive = directory/'PSModManager-v0.1.0-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('PSModManager/build-manifest.json', json.dumps(dict(
            version='0.1.0', product='PSModManager', distribution_profile='public-cpp2il-only')))
    (directory/'SHA256SUMS.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()
        +'  '+archive.name+'\n', encoding='utf-8')
    return directory


class FakeGitHub:
    def __init__(self, *, existing=False, broken_upload=False, target=COMMIT):
        self.existing, self.broken_upload, self.target = existing, broken_upload, target
        self.calls = []

    def request(self, method, path, body=None, *, binary=None):
        self.calls.append((method, path, body))
        if path.startswith('/git/ref/'):
            return {'object': {'type': 'commit', 'sha': self.target}}
        if path.startswith('/releases/tags/'):
            return {'id': 1} if self.existing else None
        if method == 'POST' and path == '/releases':
            assert body['draft'] is True
            return {'id': 2, 'upload_url': 'https://uploads.github.com/repos/test/repo/releases/2/assets{?name,label}'}
        if method == 'POST':
            return {'state': 'starter' if self.broken_upload else 'uploaded', 'size': len(binary)}
        return {'html_url': 'https://github.com/test/repo/releases/tag/v0.1.0'}


def test_release_publishes_only_after_both_assets_upload(assets):
    api = FakeGitHub()
    assert publish(api, assets, 'v0.1.0', COMMIT).endswith('/v0.1.0')
    assert [method for method, _, _ in api.calls] == ['GET', 'GET', 'POST', 'POST', 'POST', 'PATCH']
    assert api.calls[-1][2] == {'draft': False}


@pytest.mark.parametrize('case', ['existing', 'moved-tag', 'failed-upload', 'bad-checksum'])
def test_failed_release_never_overwrites_or_publishes(assets, case):
    api = FakeGitHub(existing=case == 'existing', broken_upload=case == 'failed-upload',
                     target='2'*40 if case == 'moved-tag' else COMMIT)
    if case == 'bad-checksum':
        (assets/'SHA256SUMS.txt').write_text('wrong')
    with pytest.raises((ValueError, RuntimeError)):
        publish(api, assets, 'v0.1.0', COMMIT)
    assert not any(method in ('PATCH', 'DELETE') for method, _, _ in api.calls)
    if case != 'failed-upload':
        assert not any(method == 'POST' for method, _, _ in api.calls)


def test_workflow_permissions_and_publish_gates_are_explicit():
    text = (ROOT/'.github/workflows/release.yml').read_text('utf-8')
    assert 'branches: [main]' in text and "tags: ['v*']" in text
    assert text.count('contents: write') == 1 and 'contents: read' in text
    assert 'needs: build' in text and 'persist-credentials: false' in text
    assert 'PSMM_PUBLIC_RELEASE' in text and '--public-release' in text
    assert '--locked' in text and 'npm --prefix frontend ci' in text


def test_helper_sdk_pin_rejects_uninstalled_version(monkeypatch):
    spec = importlib.util.spec_from_file_location('release_helper_build', ROOT/'tools/PSModManager.Metadata/build.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setenv('PSMM_DOTNET_SDK_VERSION', '10.0.99999')
    with pytest.raises(RuntimeError, match='10.0.99999'):
        helper.toolchain()
