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
from tools.check_release_version import check
from tools.publish_release import checked_assets, publish, version_changes

ROOT = Path(__file__).resolve().parents[2]
COMMIT = '1'*40


def write(root, name, value):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return path


@pytest.mark.parametrize('tag', ['v999999.0.0', '0.1.0', 'v01.1.0', 'v0.1.0;echo bad', 'main', 'v0.1.0-beta'])
def test_release_tag_must_match_committed_product_version(tag):
    with pytest.raises(ValueError):
        check(tag)


def test_current_tag_matches_all_version_sources():
    from backend.app.identity import APP_VERSION
    assert check('v'+APP_VERSION) == packaging.VERSION
    assert check() == APP_VERSION


@pytest.mark.parametrize('notes', [
    '# 0.0.1 · 2026-10-19\n\nOld notes only',
    '# 0.1.1 · 2026-10-19\n\n# 0.1.0 便携版\n\nOld notes',
    'Missing a version heading',
])
def test_release_check_rejects_stale_or_empty_current_notes(tmp_path, notes):
    write(tmp_path, 'pyproject.toml', b'[project]\nversion = "0.1.1"\n')
    write(tmp_path, 'backend/app/identity.py', b'APP_VERSION = "0.1.1"\n')
    write(tmp_path, 'uv.lock', b'[[package]]\nname = "psmodmanager"\nversion = "0.1.1"\n')
    write(tmp_path, 'docs/user/release-notes.md', notes.encode('utf-8'))
    with pytest.raises(ValueError, match='Release notes'):
        check(root=tmp_path)


def test_release_check_accepts_current_notes_and_preserves_history(tmp_path):
    write(tmp_path, 'pyproject.toml', b'[project]\nversion = "0.1.1"\n')
    write(tmp_path, 'backend/app/identity.py', b'APP_VERSION = "0.1.1"\n')
    write(tmp_path, 'uv.lock', b'[[package]]\nname = "psmodmanager"\nversion = "0.1.1"\n')
    write(tmp_path, 'docs/user/release-notes.md',
          '# 0.1.1 · 2026-10-19\r\n\r\n- 修复首次查询\r\n\r\n# 0.1.0 便携版\r\n历史记录'.encode('utf-8'))
    assert check(root=tmp_path) == '0.1.1'


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
            version='0.1.0', product='PSModManager', distribution_profile='public-native-mono')))
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
    assert version_changes('v0.1.0') in api.calls[2][2]['body']
    assert '无需 MelonLoader' in api.calls[2][2]['body']
    assert '内置 Cpp2IL' not in api.calls[2][2]['body']


@pytest.mark.parametrize('profile', ['public-cpp2il-only', 'local-full', 'local-native-mono', None])
def test_release_rejects_legacy_or_local_distribution_profile(assets, profile):
    archive = assets/'PSModManager-v0.1.0-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('PSModManager/build-manifest.json', json.dumps(dict(
            version='0.1.0', product='PSModManager', distribution_profile=profile)))
    (assets/'SHA256SUMS.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()
        +'  '+archive.name+'\n', encoding='utf-8')
    with pytest.raises(ValueError, match='identity/profile'):
        checked_assets(assets, 'v0.1.0')


def test_release_notes_select_only_the_published_version(tmp_path):
    notes = write(tmp_path, 'notes.md', '# 0.1.1 · 2026-10-19\r\n\r\n## 修复\r\n- 查询超时\r\n\r\n# 0.1.0 便携版\r\n旧记录'.encode())
    assert version_changes('v0.1.1', notes) == '## 修复\n- 查询超时'
    assert version_changes('v0.1.0', notes) == '旧记录'
    with pytest.raises(ValueError, match='Missing release notes'):
        version_changes('v9.0.0', notes)
    notes.write_text('# 0.1.1\n\n# 0.1.0\n旧记录', encoding='utf-8')
    with pytest.raises(ValueError, match='Missing release notes'):
        version_changes('v0.1.1', notes)


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
    assert 'prepare_public_toolchain.py' not in text and '--offline-resources' not in text
    assert 'nativeWorkspaceRuntime.test.mjs' in text and 'nativeModel.test.mjs' in text
    assert 'tests/native_mods' in text and 'tests/workshop' in text


def test_helper_sdk_pin_rejects_uninstalled_version(monkeypatch):
    spec = importlib.util.spec_from_file_location('release_helper_build', ROOT/'tools/PSModManager.Metadata/build.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setenv('PSMM_DOTNET_SDK_VERSION', '10.0.99999')
    with pytest.raises(RuntimeError, match='10.0.99999'):
        helper.toolchain()
