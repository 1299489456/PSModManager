"""Build the fixed data-only IL2CPP recovery pack from official downloaded assets.

Usage: .venv/Scripts/python.exe tools/prepare_melonloader_offline.py --source <folder>
No network, game reads, or executable invocation occurs in this build step.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'backend/loaders/melonloader/resources'
BASE = 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/'
VERSION = '2022.1.0-pre-release.21'
UNITY = '2021.3.45'


def build(source):
    assets = {
        BASE + 'Cpp2IL/Cpp2IL.exe': (source / 'Cpp2IL.exe').read_bytes(),
        BASE + 'Cpp2IL/Plugins/Cpp2IL.Plugin.StrippedCodeRegSupport.dll':
            (source / 'Cpp2IL.Plugin.StrippedCodeRegSupport.dll').read_bytes(),
    }
    for body in assets.values():
        assert body[:2] == b'MZ', 'Expected official Windows PE binary'
    managed = (source / 'Managed.zip').read_bytes()
    with zipfile.ZipFile(BytesIO(managed)) as archive:
        for member in archive.infolist():
            assert '/' not in member.filename and '\\' not in member.filename
            assert member.filename.startswith('UnityEngine') and member.filename.endswith('.dll')
            body = archive.read(member)
            assert body[:2] == b'MZ'
            assets[BASE + 'UnityDependencies/' + member.filename] = body
    # MelonLoader consumes this local archive when Config.cfg has no matching
    # UnityVersion, then removes it. Preserve its own configuration/save logic.
    assets[BASE + f'UnityDependencies_{UNITY}.zip'] = managed
    sources = [
        {'component': 'Cpp2IL', 'version': VERSION, 'url': f'https://github.com/SamboyCoding/Cpp2IL/releases/download/{VERSION}/Cpp2IL-{VERSION}-Windows.exe'},
        {'component': 'Cpp2IL.Plugin.StrippedCodeRegSupport', 'version': VERSION, 'url': f'https://github.com/SamboyCoding/Cpp2IL/releases/download/{VERSION}/Cpp2IL.Plugin.StrippedCodeRegSupport.dll'},
        {'component': 'UnityDependencies', 'version': UNITY, 'url': f'https://github.com/LavaGang/MelonLoader.UnityDependencies/releases/download/{UNITY}/Managed.zip'},
    ]
    manifest = dict(schema_version=1, loader_version='0.7.3', dumper_version=VERSION,
        unity_version=UNITY, archive='toolchain.zip', sources=sources,
        files=[dict(path=name, size=len(body), sha256=hashlib.sha256(body).hexdigest())
               for name, body in sorted(assets.items())])
    DESTINATION.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(DESTINATION / 'toolchain.zip', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, body in sorted(assets.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
    (DESTINATION / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    licenses = DESTINATION / 'licenses'
    licenses.mkdir(exist_ok=True)
    for name in ('Cpp2IL-LICENSE.txt', 'UnityDependencies-README.md'):
        (licenses / name).write_bytes((source / name).read_bytes())
    print(json.dumps(dict(files=len(assets), archive_bytes=(DESTINATION / 'toolchain.zip').stat().st_size)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    build(parser.parse_args().source)
