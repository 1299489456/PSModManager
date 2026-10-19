"""Offline Windows x64 desktop build; never scans legacy user/resource trees."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
PRODUCT = 'PSModManager'
VERSION = '0.1.0'
EXCLUDED_MANIFEST = ['build-manifest.json', 'SHA256SUMS.txt']


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def bundle_data(root, helper):
    """Explicit resource roots, not the workspace or imported game content."""
    paths = list((root/'frontend/dist').rglob('*'))
    paths += list((root/'backend/diagnostics/rules').glob('*.json'))
    paths += list(helper.iterdir())
    output = []
    for path in paths:
        if path.is_symlink() or path.lstat().st_file_attributes & 0x400:
            raise ValueError('Redirected bundle resource')
        if not path.is_file():
            continue
        if path.is_relative_to(helper):
            relative = Path('tools/PSModManager.Metadata/bin')/path.name
        else:
            relative = path.relative_to(root)
        output.append((str(path), str(relative.parent)))
    return output


def forbidden(relative):
    # Public vendor trust roots are runtime resources, not private credentials.
    if relative == '_internal/certifi/cacert.pem':
        return False
    path = PurePosixPath(relative)
    lowered = {part.lower() for part in path.parts}
    return (bool(lowered & {'mods', 'toolmods', 'saves', 'userdata', 'updates', '.venv',
                           '.git', 'node_modules', 'tests', '.preview', 'credentials'})
            or path.suffix.lower() in {'.sqlite', '.sqlite3', '.db', '.key', '.pem', '.pfx'}
            or path.name.lower() in {'config.json', 'credentials.json', '.env', 'gameassembly.dll',
                                    'assembly-csharp.dll', 'probably stolen.exe', 'settings.py'}
            or any(part.lower().startswith('.env.') for part in path.parts))


def package_files(root):
    output = []
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink() or path.lstat().st_file_attributes & 0x400:
            raise ValueError('Redirected package path')
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if forbidden(relative):
                raise ValueError('Forbidden release content: '+relative)
            output.append(dict(path=relative, size=path.stat().st_size, sha256=sha256(path)))
    return output


def validate_bundle(root):
    root = Path(root)
    actual = {r['path']:r for r in package_files(root)}
    manifest = json.loads((root/'build-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('product') != PRODUCT or manifest.get('target') != 'windows-x64':
        raise ValueError('Package identity mismatch')
    claimed = manifest.get('files', [])
    names = [r['path'] for r in claimed]
    if len(set(names))!=len(names) or set(names)!=set(actual)-set(EXCLUDED_MANIFEST):
        raise ValueError('Package inventory mismatch')
    for row in claimed:
        if actual.get(row['path']) != row:
            raise ValueError('Package file changed: '+row['path'])
    hashes = {}
    for line in (root/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, relative = line.split('  ', 1)
        if relative in hashes or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Invalid hash inventory')
        hashes[relative] = digest
    if set(hashes)!=set(actual)-{'SHA256SUMS.txt'} or any(actual[k]['sha256']!=v for k,v in hashes.items()):
        raise ValueError('Hash inventory mismatch')
    return actual


def dependencies(work):
    names = {'pyinstaller','pyinstaller-hooks-contrib','pywebview','pythonnet','clr-loader',
             'cffi','pycparser','pydantic','pydantic-core','annotated-types','typing-extensions',
             'typing-inspection','proxy-tools','bottle','pywin32'}
    mapping = metadata.packages_distributions()
    def visit(value):
        if not isinstance(value, (tuple, list)):
            return
        if (len(value)==3 and isinstance(value[0],str) and isinstance(value[1],str)
                and '/site-packages/' in value[1].replace('\\','/')):
            names.update(mapping.get(value[0].split('.')[0], []))
        for item in value:
            visit(item)
    # Material names come from both real analysis graphs, not all installed
    # development packages or the legacy application's lockfile requirements.
    for path in sorted(work.glob('*.toc')):
        if path.name.startswith(('Analysis-', 'PYZ-')):
            visit(ast.literal_eval(path.read_text(encoding='utf-8')))
    canonical = {}
    for name in names:
        distribution = metadata.distribution(name)
        canonical_name = distribution.metadata['Name']
        key = re.sub('[-_.]+', '-', canonical_name).lower()
        canonical[key] = dict(name=canonical_name, version=distribution.version,
            role='build' if key in {'pyinstaller','pyinstaller-hooks-contrib'} else 'bundled_dependency')
    return [canonical[key] for key in sorted(canonical)]


def licenses(bundle, packages):
    copied = []
    python_license = Path(sys._base_executable).parent/'LICENSE.txt'
    if python_license.is_file():
        destination = bundle/'licenses/CPython/LICENSE.txt'
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(python_license.read_bytes())
        copied.append(destination.relative_to(bundle).as_posix())
    for package in packages:
        distribution = metadata.distribution(package['name'])
        for relative in distribution.files or []:
            if relative.name.lower().startswith(('license','copying','notice')):
                source = Path(distribution.locate_file(relative))
                if source.is_file():
                    destination = bundle/'licenses'/package['name']/relative.name
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    destination.write_bytes(source.read_bytes())
                    copied.append(destination.relative_to(bundle).as_posix())
    return copied


def run(command, output, env):
    with output.open('w',encoding='utf-8') as log:
        result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
            stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f'Build exit {result.returncode}; inspect {output}')


def build(output):
    if sys.platform!='win32' or struct.calcsize('P')!=8:
        raise RuntimeError('Installed Windows x64 Python is required')
    output = output.resolve()
    if not output.is_relative_to(ROOT) or output==ROOT or output.exists():
        raise ValueError('Use a new output directory inside this workspace')
    if not (ROOT/'frontend/dist/index.html').is_file():
        raise RuntimeError('Existing frontend build is required; no automatic dependency install')
    output.mkdir(parents=True)
    (output/'.psmm-build-owner.json').write_text(json.dumps(dict(product=PRODUCT,output=str(output))),encoding='utf-8')
    env = os.environ.copy()
    env.update(PSMM_HELPER_OUTPUT=str(output/'helper'), PYTHONUTF8='1', PYTHONHASHSEED='0',
        SETUPTOOLS_USE_DISTUTILS='stdlib', PYINSTALLER_CONFIG_DIR=str(output/'pyi-cache'),
        SOURCE_DATE_EPOCH='1791859200')
    run([sys.executable,ROOT/'tools/PSModManager.Metadata/build.py','--self-contained',
         '--output-dir',output/'helper'], output/'helper.log',env)
    run([sys.executable,'-I','-m','PyInstaller','--noconfirm','--distpath',output/'dist',
         '--workpath',output/'pyi','--log-level','INFO',ROOT/'PSModManager.spec'],output/'pyinstaller.log',env)
    bundle = output/'dist'/PRODUCT
    packages = dependencies(output/'pyi/PSModManager')
    license_paths = licenses(bundle, packages)
    (bundle/'README-PORTABLE.txt').write_text(
        'PSModManager 0.1.0 - Windows x64\n\n'
        'Extract the whole PSModManager folder; run PSModManager.exe.\n'
        'Required: Windows 10/11 x64, Microsoft .NET Framework 4.8 or later,\n'
        'and Microsoft Edge WebView2 Runtime. Missing runtimes are reported;\n'
        'nothing is installed automatically. Python, Node and dotnet SDK are not required.\n'
        'The metadata helper includes its own .NET runtime. Keep _internal and\n'
        'PSModManager.WorkerHost.exe beside the application.\n\n'
        'Data: %LOCALAPPDATA%\\PSModManager (separate from program files).\n'
        'No RimCrow data migration, original update channel, real games, Mods,\n'
        'saves or credentials are included. AI is optional and configured by the user.\n'
        'Uninstalling the portable folder does not remove LocalAppData.\n',encoding='utf-8')
    helper = json.loads((output/'helper/helper-build.json').read_text(encoding='utf-8'))
    inputs = ['main.py','PSModManager.spec','pack_pyinstaller.py','pyproject.toml','uv.lock',
              'frontend/package-lock.json','backend/scanning/worker_host.py','backend/scanning/worker_client.py',
              'tools/PSModManager.Metadata/build.py','tools/PSModManager.Metadata/Program.cs',
              'tools/PSModManager.Metadata/PSModManager.Metadata.csproj',
              'tools/PSModManager.Metadata/console_host_main.py','frontend/dist/index.html']
    manifest = dict(schema_version=1,product=PRODUCT,version=VERSION,target='windows-x64',
        build_utc=datetime.now(timezone.utc).isoformat(), packaging='PyInstaller onedir; shared _internal',
        requirements=['Windows 10/11 x64','.NET Framework 4.8+','WebView2 Runtime'],
        tools=dict(python=sys.version,pyinstaller=metadata.version('pyinstaller')),
        dependencies=packages,licenses=license_paths,helper=helper,
        inputs=[dict(path=p,sha256=sha256(ROOT/p)) for p in inputs],
        self_excluded=EXCLUDED_MANIFEST,files=package_files(bundle))
    (bundle/'build-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (bundle/'SHA256SUMS.txt').write_text(''.join(r['sha256']+'  '+r['path']+'\n'
        for r in package_files(bundle) if r['path']!='SHA256SUMS.txt'),encoding='utf-8')
    validate_bundle(bundle)
    archive = output/f'{PRODUCT}-v{VERSION}-windows-x64.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as stream:
        for row in package_files(bundle):
            # Fixed ZIP timestamps/order; build metadata time is an intentional difference.
            info=zipfile.ZipInfo(PRODUCT+'/'+row['path'],(2026,10,13,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            stream.writestr(info,(bundle/row['path']).read_bytes())
    result=dict(bundle=str(bundle),archive=str(archive),archive_sha256=sha256(archive),
        archive_bytes=archive.stat().st_size,files=len(package_files(bundle)),
        installed_bytes=sum(r['size'] for r in package_files(bundle)))
    (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Offline PSModManager Windows x64 portable build')
    parser.add_argument('--output-root',type=Path,required=True)
    print(json.dumps(build(parser.parse_args().output_root),indent=2))
