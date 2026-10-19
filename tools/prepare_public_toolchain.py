"""Prepare only MIT-licensed Cpp2IL recovery assets for a public release.

Downloads are explicit (--download) and bound to the source manifest's size and
SHA-256. --source instead consumes pre-downloaded files without network access.
The local full toolchain in backend/ is never modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT/'backend/loaders/melonloader/resources'
BASE = 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Cpp2IL/'
FILES = {
    'Cpp2IL': (BASE+'Cpp2IL.exe', 'Cpp2IL.exe'),
    'Cpp2IL.Plugin.StrippedCodeRegSupport': (
        BASE+'Plugins/Cpp2IL.Plugin.StrippedCodeRegSupport.dll',
        'Cpp2IL.Plugin.StrippedCodeRegSupport.dll'),
}


def build(output, *, source=None, download=False, resources=RESOURCES):
    if (source is None) == (not download):
        raise ValueError('Choose exactly one of --source or --download')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new public resource output directory')
    original = json.loads((resources/'manifest.json').read_text('utf-8'))
    expected = {row['path']: row for row in original['files']}
    sources = {row['component']: row for row in original['sources']}
    assets = {}
    for component, (name, local_name) in FILES.items():
        row = expected[name]
        if download:
            url = sources[component]['url']
            if not url.startswith('https://github.com/SamboyCoding/Cpp2IL/releases/download/'):
                raise ValueError('Unexpected public toolchain upstream')
            request = Request(url, headers={'User-Agent': 'PSModManager-release-build'})
            with urlopen(request, timeout=60) as response:
                body = response.read(row['size']+1)
        else:
            body = (Path(source)/local_name).read_bytes()
        if (len(body) != row['size'] or hashlib.sha256(body).hexdigest() != row['sha256']
                or not body.startswith(b'MZ')):
            raise ValueError('Official asset verification failed: '+local_name)
        assets[name] = body
    manifest = dict(original, profile='cpp2il-only',
        sources=[sources[key] for key in FILES], files=[expected[name] for name in assets])
    output.mkdir(parents=True)
    with zipfile.ZipFile(output/'toolchain.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in sorted(assets.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    (output/'licenses').mkdir()
    (output/'licenses/Cpp2IL-LICENSE.txt').write_bytes((resources/'licenses/Cpp2IL-LICENSE.txt').read_bytes())
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--source', type=Path)
    choice.add_argument('--download', action='store_true')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.output_dir, source=args.source, download=args.download))
