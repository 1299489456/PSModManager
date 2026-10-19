"""Generate owned, original CLR fixtures with an already installed compiler only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys

WORKSPACE = Path(__file__).resolve().parents[1]
SOURCE_ROOT = WORKSPACE / 'tests' / 'metadata' / 'fixtures'
TEXT_ROOT = WORKSPACE / 'tests' / 'fixtures' / 'synthetic'
OWNER = 'PSModManager.synthetic-fixtures.v1'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def validate_output_root(value: Path) -> Path:
    root = Path(value)
    if not root.is_absolute() or str(root).startswith(('\\\\', '//')):
        raise ValueError('fixture root must be a local absolute path')
    if any(part in ('..', '.') or part.endswith((' ', '.')) or ':' in part for part in root.parts[1:]):
        raise ValueError('ambiguous fixture root')
    root = Path(os.path.abspath(root))
    if root == WORKSPACE or WORKSPACE not in root.parents:
        raise ValueError('fixture root must be strictly inside this workspace')
    for part in (root, *root.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('fixture path may not contain reparse points')
        if part != WORKSPACE and WORKSPACE in part.parents:
            if any((part / name).exists() for name in ('MelonLoader', 'GameAssembly.dll', 'il2cpp_data')):
                raise ValueError('fixture root may not be inside a game installation')
    if root.exists():
        raise ValueError('fixture root already exists; choose a fresh isolated directory')
    return root


def _version(path: Path) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in path.name.split('.'))
    except ValueError:
        return ()


def compiler_inputs() -> tuple[Path, Path, list[Path]]:
    dotnet = shutil.which('dotnet')
    if not dotnet:
        raise RuntimeError('missing dependency: local dotnet host, SDK Roslyn and .NET reference pack; no installation attempted')
    host = Path(dotnet).resolve()
    sdks = sorted((host.parent / 'sdk').glob('*'), key=_version, reverse=True)
    compiler = next((p / 'Roslyn/bincore/csc.dll' for p in sdks if (p / 'Roslyn/bincore/csc.dll').is_file()), None)
    packs = sorted((host.parent / 'packs/Microsoft.NETCore.App.Ref').glob('*'), key=_version, reverse=True)
    reference_dir = next((p for pack in packs for p in sorted((pack / 'ref').glob('net*'), reverse=True)
                          if (p / 'System.Runtime.dll').is_file()), None)
    if compiler is None or reference_dir is None:
        raise RuntimeError('missing dependency: installed SDK Roslyn/reference pack; no restore attempted')
    return host, compiler, sorted(reference_dir.glob('*.dll'))


def clr_info(path: Path) -> dict:
    """Read PE/CLI directory and metadata signature without loading assembly code."""
    data = path.read_bytes()
    if data[:2] != b'MZ' or len(data) < 64:
        raise ValueError('not a PE file')
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('invalid PE signature')
    sections, optional_size = struct.unpack_from('<H', data, pe + 6)[0], struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    directory = optional + (96 if magic == 0x10B else 112 if magic == 0x20B else 0)
    if directory == optional:
        raise ValueError('invalid optional header')
    cli_rva, cli_size = struct.unpack_from('<II', data, directory + 14 * 8)
    table = optional + optional_size

    def offset(rva: int) -> int:
        for index in range(sections):
            size, va, raw_size, raw = struct.unpack_from('<IIII', data, table + index * 40 + 8)
            if va <= rva < va + max(size, raw_size):
                return raw + rva - va
        raise ValueError('RVA outside sections')

    if cli_size < 72 or not cli_rva:
        raise ValueError('missing CLR header')
    cli = offset(cli_rva)
    metadata_rva, metadata_size = struct.unpack_from('<II', data, cli + 8)
    if data[offset(metadata_rva):offset(metadata_rva) + 4] != b'BSJB':
        raise ValueError('missing CLR metadata root')
    resource_rva, resource_size = struct.unpack_from('<II', data, cli + 24)
    resources = []
    if resource_size:
        pos, end = offset(resource_rva), offset(resource_rva) + resource_size
        while pos < end:
            length = struct.unpack_from('<I', data, pos)[0]
            if pos + 4 + length > end:
                raise ValueError('resource outside CLR resource directory')
            resources.append(data[pos + 4:pos + 4 + length])
            pos = (pos + 4 + length + 7) & ~7
    return {'cli_header_size': cli_size, 'metadata_size': metadata_size, 'resources': resources}


def build(output_root: Path) -> dict:
    root = validate_output_root(output_root)
    host, compiler, references = compiler_inputs()
    source_paths = sorted(SOURCE_ROOT.glob('*.cs')) + sorted(SOURCE_ROOT.glob('*.json')) + [TEXT_ROOT / name for name in ('loader.log', 'settings.cfg', 'README.md')]
    if len(source_paths) != 9 or not all(path.is_file() for path in source_paths):
        raise RuntimeError('missing original fixture source')
    # All preflight checks precede creation. Never adopt or clean an existing tree.
    root.mkdir(parents=True, exist_ok=False)
    (root / '.fixture-owner').write_text(OWNER + '\n', encoding='utf-8')
    for name in ('sources', 'dependencies', 'install/Mods', 'install/UserData', 'install/MelonLoader', 'temp'):
        (root / name).mkdir(parents=True, exist_ok=False)
    for path in source_paths:
        shutil.copyfile(path, root / 'sources' / path.name)
    env = dict(os.environ, DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_CLI_TELEMETRY_OPTOUT='1',
               DOTNET_CLI_HOME=str(root / 'temp'), TEMP=str(root / 'temp'), TMP=str(root / 'temp'))
    commands = []
    targets = [('MelonAttributes.cs', 'dependencies/MelonAttributes.dll'),
               ('SyntheticMod.cs', 'install/Mods/SyntheticMod.dll'),
               ('ItemForge.cs', 'install/Mods/ItemForge.dll'), ('Helper.cs', 'install/Mods/Helper.dll')]
    report = {'schema_version': 1, 'commands': commands}
    for source, target in targets:
        command = [str(host), str(compiler), '/nologo', '/target:library', '/deterministic+', '/optimize+', '/debug-', '/nostdlib+',
                   '/pathmap:' + str(root) + '=/synthetic', '/out:' + str(root / target)]
        command += ['/reference:' + str(p) for p in references]
        if source in ('SyntheticMod.cs', 'ItemForge.cs'):
            command.append('/reference:' + str(root / 'dependencies/MelonAttributes.dll'))
        if source == 'ItemForge.cs':
            command += ['/resource:' + str(root / 'sources' / name) + ',Synthetic.ItemForge.' + name for name in ('items.json', 'recipes.json')]
        command.append(str(root / 'sources' / source))
        try:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=60, check=False)
        except subprocess.TimeoutExpired as error:
            commands.append({'command': command, 'exit_code': None, 'timed_out': True,
                             'stdout': str(error.stdout or ''), 'stderr': str(error.stderr or '')})
            _json(root / 'build-report.json', report)
            raise RuntimeError('fixture compiler timeout; partial outputs retained') from error
        commands.append({'command': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        _json(root / 'build-report.json', report)
        if result.returncode:
            raise RuntimeError('fixture compiler failed; see build-report.json')
        clr_info(root / target)
    shutil.copyfile(root / 'sources/loader.log', root / 'install/MelonLoader/Latest.log')
    shutil.copyfile(root / 'sources/settings.cfg', root / 'install/UserData/Synthetic.cfg')
    _json(root / 'install/SYNTHETIC-INSTALL.json', {'schema_version': 1, 'synthetic': True, 'launchable': False})
    files = sorted(path for path in root.rglob('*') if path.is_file() and path.name != 'build-report.json' and 'temp' not in path.relative_to(root).parts)
    manifest = {
        'schema_version': 1, 'owner': OWNER, 'origin': 'Original PSModManager synthetic source; no real game or third-party binary',
        'execution_prohibited': True, 'deterministic': True,
        'toolchain': {'compiler_sha256': sha256(compiler), 'reference_hashes': {p.name: sha256(p) for p in references}},
        'sources': {p.relative_to(WORKSPACE).as_posix(): sha256(p) for p in source_paths},
        'artifacts': {p.relative_to(root).as_posix(): sha256(p) for p in files},
        'counts': {'mod_dlls': 3, 'attribute_dependency_dlls': 1, 'melon_info_mods': 2, 'embedded_resources': 2},
        'roles': {'SyntheticMod.dll': ['melon_mod'], 'ItemForge.dll': ['melon_mod', 'content_items', 'content_recipes'], 'Helper.dll': ['dependency']},
    }
    _json(root / 'manifest.json', manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = build(args.output_root)
    except (ValueError, RuntimeError, OSError, struct.error) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({'status': 'PASS', 'manifest': str(args.output_root / 'manifest.json'), 'counts': manifest['counts']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
