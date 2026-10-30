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
# PyInstaller loads this module from its spec under python -I, before the
# application import path exists. Read the literal without importing the app.
VERSION = next(ast.literal_eval(node.value) for node in
    ast.parse((ROOT/'backend/app/identity.py').read_text('utf-8')).body
    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
        and target.id == 'APP_VERSION' for target in node.targets))
EXCLUDED_MANIFEST = ['build-manifest.json', 'SHA256SUMS.txt']
NOTICE_ROOT = 'third_party/notices'
DELIVERY_DOCUMENTS = ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'docs/development/known-limitations.md',
                      'docs/development/workshop-usability-2026-10-28.md',
                      'docs/development/workshop-performance-settings-2026-10-29.md',
                      'docs/development/workshop-recovery-scope-fix-2026-10-29.md',
                      'docs/development/mod-security-implementation-2026-10-30.md',
                      'docs/development/interaction-reliability-fix-2026-10-30.md',
                      'docs/development/manual-mod-check-2026-10-30.md',
                      'docs/development/standalone-mod-check-2026-10-29.md']
HISTORICAL_NOTICE_TEXTS = {'licenses/Cpp2IL/Cpp2IL-LICENSE.txt',
                           'licenses/UnityDependencies/UnityDependencies-README.md'}


def delivery_documents(root):
    return DELIVERY_DOCUMENTS + [path.relative_to(root).as_posix()
        for path in sorted((root/'docs/user').glob('*.md'))]


def notice_materials(root):
    """Read the checked-in, version-bound license inventory; never fetch notices."""
    source = root/NOTICE_ROOT
    catalog = json.loads((source/'distribution-materials.json').read_text(encoding='utf-8'))
    materials = {}
    for component in catalog['components']:
        if set(component['license_paths']) != {item['path'] for item in component['materials']}:
            raise ValueError('Notice license_paths mismatch: '+component['name'])
        for item in component['materials']:
            relative = item['path']
            path = PurePosixPath(relative)
            if (not relative or '\\' in relative or ':' in relative or path.is_absolute()
                    or any(part in ('', '.', '..') for part in relative.split('/'))):
                raise ValueError('Invalid notice material path: '+relative)
            target = source/relative
            if not target.resolve().is_relative_to(source.resolve()):
                raise ValueError('Redirected notice material: '+relative)
            for entry in (target, *target.parents):
                if entry == source.parent:
                    break
                if entry.is_symlink() or getattr(entry.lstat(), 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Redirected notice material: '+relative)
            if target.stat().st_size != item['bytes'] or sha256(target) != item['sha256']:
                raise ValueError('Notice material changed: '+relative)
            if relative in materials and materials[relative] != item:
                raise ValueError('Conflicting notice material: '+relative)
            materials[relative] = item
    if (not materials or catalog['components_count'] != len(catalog['components'])
            or catalog['materials_count'] != len(materials)):
        raise ValueError('Notice inventory count mismatch')
    return catalog, materials


def dependency_versions(packages):
    return {re.sub('[-_.]+', '-', item['name']).lower(): (item['version'], item['role'])
            for item in packages}


def check_notice_baseline(baseline, *, python_version=None, packages=None,
                          helper_runtime_version=None, frontend_packages=None):
    differences = []
    if python_version is not None and python_version != baseline['python_version']:
        differences.append(f'Python {python_version} (expected {baseline["python_version"]})')
    if packages is not None:
        actual, expected = dependency_versions(packages), dependency_versions(baseline['dependencies'])
        for name in sorted(actual.keys() | expected.keys()):
            if actual.get(name) != expected.get(name):
                differences.append(f'{name}: {actual.get(name)} (expected {expected.get(name)})')
    if helper_runtime_version is not None and helper_runtime_version != baseline['helper_runtime_version']:
        differences.append(f'helper runtime {helper_runtime_version} '
                           f'(expected {baseline["helper_runtime_version"]})')
    if frontend_packages is not None and frontend_packages != baseline['frontend_packages']:
        for name in sorted(frontend_packages.keys() | baseline['frontend_packages'].keys()):
            actual, expected = frontend_packages.get(name), baseline['frontend_packages'].get(name)
            if actual != expected:
                differences.append(f'frontend {name}: {actual} (expected {expected})')
    if differences:
        raise RuntimeError('Build differs from third_party/notices/build-baseline.json: '
            + '; '.join(differences) + '. Update the notice texts, distribution-materials.json '
            'and build-baseline.json for these versions before packaging; '
            'see docs/development/README.md.')


def preflight(root, *, offline_resources=None, public_release=False):
    """Reject incomplete public checkouts before creating output or running builds."""
    if offline_resources is not None:
        raise ValueError('--offline-resources is retired for the native Mono build. '
                         'Remove this argument; no MelonLoader/Cpp2IL resources are used.')
    required = ['frontend/dist/index.html', 'frontend/package-lock.json',
                'backend/app/resources/psmm-icon.ico',
                NOTICE_ROOT+'/distribution-materials.json', NOTICE_ROOT+'/build-baseline.json',
                'docs/user/quick-start.md', *delivery_documents(root)]
    missing = [name for name in required if not (root/name).is_file() or (root/name).stat().st_size == 0]
    if missing:
        raise RuntimeError('Required build resources are missing: '+', '.join(sorted(set(missing)))
            + '. Prepare the frontend dist and checked-in delivery materials first; see '
            'docs/development/README.md. No download or installation was attempted.')
    try:
        catalog, materials = notice_materials(root)
        baseline = json.loads((root/NOTICE_ROOT/'build-baseline.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('Third-party notice materials are missing or invalid: '+str(exc)
            + '. Restore or update third_party/notices; see docs/development/README.md.') from exc
    packages = []
    for package in baseline['dependencies']:
        try:
            version = metadata.version(package['name'])
        except metadata.PackageNotFoundError:
            version = None
        packages.append(dict(package, version=version))
    lock = json.loads((root/'frontend/package-lock.json').read_text(encoding='utf-8'))['packages']
    frontend = {}
    for name in baseline['frontend_packages']:
        path = root/'frontend/node_modules'/name/'package.json'
        installed = json.loads(path.read_text(encoding='utf-8'))['version'] if path.is_file() else None
        locked = lock.get('node_modules/'+name, {}).get('version')
        if installed != locked:
            raise RuntimeError(f'Frontend {name}: installed {installed}, locked {locked}; '
                'prepare the locked frontend dependencies and rebuild dist before packaging. '
                'See docs/development/README.md; no installation was attempted.')
        frontend[name] = installed
    check_notice_baseline(baseline, python_version='.'.join(map(str, sys.version_info[:3])),
                          packages=packages, frontend_packages=frontend)
    return baseline, catalog, materials


def delivery_materials(root, bundle, materials, *, public_release=False):
    """Include distribution documents before binding the manifest and ZIP."""
    for name in delivery_documents(root):
        target = bundle/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root/name).read_bytes())
    names = [*sorted(materials), 'distribution-materials.json']
    for name in names:
        target = bundle/'licenses'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root/NOTICE_ROOT/name).read_bytes())
    (bundle/'README-PORTABLE.txt').write_text(
        f'PSModManager {VERSION} — Windows x64 便携版\n\n'
        '完整解压 PSModManager 文件夹，再运行 PSModManager.exe。\n'
        '请保留同目录的 _internal 和 PSModManager.WorkerHost.exe，不要只复制 EXE。\n\n'
        '升级时先保留旧窗口的未保存选择并正常退出，再从本目录启动。\n'
        f'在“应用信息”核对实际运行版本 {VERSION}；解压新包不会更新仍打开的旧窗口。\n'
        '普通保存和启动不自动异常扫描；手动检查在顶部独立“模组检查”页。\n\n'
        '运行环境：Windows 10/11 x64、Microsoft .NET Framework 4.8+、\n'
        'Microsoft Edge WebView2 Runtime。缺少时会提示，不会自动安装。\n'
        '无需安装 Python、Node 或 .NET SDK；元数据 helper 带独立 .NET 运行时。\n\n'
        '首次启动选择数据位置；默认 %LOCALAPPDATA%\\PSModManager（LocalAppData）。\n'
        '以后可在“设置 → 数据位置”迁移到其他本地目录。删除程序目录不会清除数据或游戏 Mod。\n'
        '面向 Probably Stolen 正式版原生 Mono 模组；不包含游戏、Mod、存档或凭据。\n'
        '无需安装 MelonLoader；不包含 Cpp2IL 或 Unity 生成依赖。\n'
        'Steam 创意工坊订阅与更新使用本机已登录客户端；会话不可用时可打开官方页面。\n'
        '管理器自动更新模组默认关闭；这不能关闭 Steam 客户端自己的更新。\n'
        '不分发 Steam API DLL；AI 服务在设置页按分析/翻译分别选择。\n\n'
        '使用说明：docs/user/quick-start.md\n'
        '备份与重装：docs/user/data-and-reinstall.md\n'
        '已知限制：docs/development/known-limitations.md\n'
        '许可与第三方材料：LICENSE、THIRD_PARTY_NOTICES.md、licenses/\n',
        encoding='utf-8-sig')
    return ['licenses/'+name for name in names]


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
    paths += [root/'backend/app/resources/psmm-icon.ico']
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
    # Version-bound notice texts remain distributable; this does not admit the
    # retired tool binaries or resource archive. notice_materials validates bytes.
    if relative in HISTORICAL_NOTICE_TEXTS:
        return False
    # Public vendor trust roots are runtime resources, not private credentials.
    if relative == '_internal/certifi/cacert.pem':
        return False
    path = PurePosixPath(relative)
    lowered = {part.lower() for part in path.parts}
    return (bool(lowered & {'mods', 'toolmods', 'saves', 'userdata', 'updates', '.venv',
                           'melonloader', 'cpp2il', 'unitydependencies',
                           '.git', 'node_modules', 'tests', '.preview', 'credentials',
                           'mod-security', 'mod-security-analysis'})
            or path.suffix.lower() in {'.sqlite', '.sqlite3', '.db', '.key', '.pem', '.pfx'}
            or path.name.lower() in {'config.json', 'credentials.json', 'ai-translations.json', '.env', 'gameassembly.dll',
                                    'assembly-csharp.dll', 'probably stolen.exe', 'settings.py',
                                    'melonloader.dll', 'cpp2il.exe', 'toolchain.zip',
                                    'steam_api.dll', 'steam_api64.dll', 'steamclient.dll',
                                    'steamclient64.dll', 'steam_appid.txt'}
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


def run(command, output, env):
    with output.open('w',encoding='utf-8') as log:
        result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
            stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f'Build exit {result.returncode}; inspect {output}')


def build(output, *, offline_resources=None, public_release=False):
    if sys.platform!='win32' or struct.calcsize('P')!=8:
        raise RuntimeError('Installed Windows x64 Python is required')
    output = output.resolve()
    if not output.is_relative_to(ROOT) or output==ROOT or output.exists():
        raise ValueError('Use a new output directory inside this workspace')
    baseline, _, materials = preflight(ROOT, offline_resources=offline_resources,
                                       public_release=public_release)
    output.mkdir(parents=True)
    (output/'.psmm-build-owner.json').write_text(json.dumps(dict(product=PRODUCT,output=str(output))),encoding='utf-8')
    env = os.environ.copy()
    env.update(PSMM_HELPER_OUTPUT=str(output/'helper'), PYTHONUTF8='1', PYTHONHASHSEED='0',
        SETUPTOOLS_USE_DISTUTILS='stdlib', PYINSTALLER_CONFIG_DIR=str(output/'pyi-cache'),
        SOURCE_DATE_EPOCH='1791859200')
    env.pop('PSMM_OFFLINE_RESOURCES', None)
    run([sys.executable,ROOT/'tools/PSModManager.Metadata/build.py','--self-contained',
         '--output-dir',output/'helper'], output/'helper.log',env)
    helper = json.loads((output/'helper/helper-build.json').read_text(encoding='utf-8'))
    check_notice_baseline(baseline, helper_runtime_version=helper['runtime_version'])
    run([sys.executable,'-I','-m','PyInstaller','--noconfirm','--distpath',output/'dist',
         '--workpath',output/'pyi','--log-level','INFO',ROOT/'PSModManager.spec'],output/'pyinstaller.log',env)
    bundle = output/'dist'/PRODUCT
    packages = dependencies(output/'pyi/PSModManager')
    check_notice_baseline(baseline, packages=packages)
    license_paths = delivery_materials(ROOT, bundle, materials, public_release=public_release)
    inputs = ['main.py','PSModManager.spec','pack_pyinstaller.py','pyproject.toml','uv.lock',
              'frontend/package-lock.json','backend/scanning/worker_host.py','backend/scanning/worker_client.py',
              'tools/PSModManager.Metadata/build.py','tools/PSModManager.Metadata/Program.cs',
              'tools/PSModManager.Metadata/SecurityAnalysis.cs',
              'tools/PSModManager.Metadata/ApprovalAnalysis.cs',
              'tools/PSModManager.Metadata/PSModManager.Metadata.csproj',
              'tools/PSModManager.Metadata/console_host_main.py','frontend/dist/index.html',
              NOTICE_ROOT+'/build-baseline.json', NOTICE_ROOT+'/distribution-materials.json']
    manifest = dict(schema_version=1,product=PRODUCT,version=VERSION,target='windows-x64',
        build_utc=datetime.now(timezone.utc).isoformat(), packaging='PyInstaller onedir; shared _internal',
        distribution_profile='public-native-mono' if public_release else 'local-native-mono',
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
    checksum = output/'SHA256SUMS.txt'
    checksum.write_text(result['archive_sha256']+'  '+archive.name+'\n', encoding='utf-8')
    result['checksum'] = str(checksum)
    (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Offline PSModManager Windows x64 portable build')
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--offline-resources', type=Path,
        help='Retired: native Mono builds reject this legacy argument')
    parser.add_argument('--public-release', action='store_true',
        help='Mark the native Mono portable as a public-release candidate')
    args = parser.parse_args()
    print(json.dumps(build(args.output_root, offline_resources=args.offline_resources,
                           public_release=args.public_release),indent=2))
