"""Publish a checked CI artifact using GitHub's short-lived workflow token.

Create a draft, upload both assets, then publish. An existing release (including
an interrupted draft) is never modified or overwritten by a rerun.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


def checked_assets(directory, tag):
    if not re.fullmatch(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', tag):
        raise ValueError('Invalid version tag')
    directory = Path(directory)
    name = f'PSModManager-{tag}-windows-x64.zip'
    if {path.name for path in directory.iterdir()} != {name, 'SHA256SUMS.txt'}:
        raise ValueError('Expected exactly the portable ZIP and SHA256SUMS.txt')
    archive, checksums = directory/name, directory/'SHA256SUMS.txt'
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if checksums.read_text('utf-8') != f'{digest}  {name}\n':
        raise ValueError('Release ZIP checksum mismatch')
    with zipfile.ZipFile(archive) as stream:
        manifest = json.loads(stream.read('PSModManager/build-manifest.json'))
        if (manifest['version'] != tag[1:] or manifest['product'] != 'PSModManager'
                or manifest.get('distribution_profile') != 'public-cpp2il-only'):
            raise ValueError('Release archive identity/profile mismatch')
    return archive, checksums


def version_changes(tag, path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[1]/'docs/user/release-notes.md'
    text = path.read_text('utf-8')
    headings = list(re.finditer(r'^# (\d+\.\d+\.\d+)(?:[^\r\n]*)\r?$', text, re.M))
    for index, heading in enumerate(headings):
        if 'v'+heading[1] == tag:
            changes = text[heading.end():headings[index+1].start() if index+1 < len(headings) else len(text)].strip()
            if changes:
                return changes
    raise ValueError('Missing release notes for '+tag)


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository) or not token:
            raise ValueError('GITHUB_REPOSITORY and workflow GITHUB_TOKEN are required')
        self.base = 'https://api.github.com/repos/'+repository
        self.token = token

    def request(self, method, path, body=None, *, binary=None):
        url = self.base+path if path.startswith('/') else path
        if not url.startswith((self.base+'/', 'https://uploads.github.com/repos/')):
            raise ValueError('Unexpected GitHub API endpoint')
        headers = {'Authorization': 'Bearer '+self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'PSModManager-release'}
        data = json.dumps(body).encode() if body is not None else binary
        if data is not None:
            headers['Content-Type'] = 'application/octet-stream' if binary is not None else 'application/json'
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=180) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404 and method == 'GET':
                return None
            raise RuntimeError(f'GitHub {method} failed (HTTP {error.code}); release draft, if any, is retained') from None


def publish(api, directory, tag, commit):
    assets = checked_assets(directory, tag)
    changes = version_changes(tag)
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Release commit must be a full Git commit ID')
    reference = api.request('GET', '/git/ref/tags/'+quote(tag, safe=''))
    if reference is None:
        raise ValueError('Version tag must already exist; this workflow does not create tags')
    target = reference['object']
    for _ in range(8):
        if target['type'] != 'tag':
            break
        target = api.request('GET', '/git/tags/'+target['sha'])['object']
    if target['type'] != 'commit' or target['sha'] != commit:
        raise ValueError('Version tag no longer points to the built commit')
    if api.request('GET', '/releases/tags/'+quote(tag, safe='')) is not None:
        raise ValueError('Release already exists; no assets were overwritten. Use a new version, or inspect an interrupted draft.')
    notes = (f'## PSModManager {tag}\n\n'
        + changes + '\n\n## 下载与使用\n\n'
        '下载下方 Windows x64 ZIP，完整解压后运行 PSModManager.exe。\n\n'
        '公开版内置 Cpp2IL 和配套插件的离线恢复资源；Unity 运行库由 MelonLoader '
        '在首次生成或游戏更新后联网下载。\n\n'
        '需要 Windows 10/11 x64、.NET Framework 4.8+ 和 WebView2 Runtime。'
        '软件未签名，系统可能显示安全提示。\n\n'
        f'源码提交：`{commit}`。`SHA256SUMS.txt` 可用于核对下载文件。\n\n'
        '已通过构建和包体检查；真实游戏、所有 Mod 兼容性及干净机器体验仍需按实际环境确认。')
    release = api.request('POST', '/releases', dict(tag_name=tag, target_commitish=commit,
        name='PSModManager '+tag, body=notes, draft=True, prerelease=False))
    upload = release['upload_url'].split('{', 1)[0]
    for asset in assets:
        uploaded = api.request('POST', upload+'?name='+quote(asset.name, safe=''), binary=asset.read_bytes())
        if uploaded.get('state') != 'uploaded' or uploaded.get('size') != asset.stat().st_size:
            raise RuntimeError('Incomplete release asset; draft retained for inspection')
    published = api.request('PATCH', '/releases/'+str(release['id']), {'draft': False})
    return published['html_url']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    args = parser.parse_args()
    api = GitHub(os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GITHUB_TOKEN', ''))
    print(publish(api, args.assets, os.environ.get('RELEASE_TAG', ''), os.environ.get('RELEASE_COMMIT', '')))
