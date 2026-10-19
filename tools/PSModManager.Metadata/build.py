"""Build with installed tools and cached runtime materials; never download."""
import json
import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def toolchain():
    dotnet = shutil.which('dotnet')
    if not dotnet:
        raise RuntimeError('Installed dotnet SDK is required; no automatic installation')
    root = Path(dotnet).resolve().parent
    version = lambda p: tuple(int(x) for x in p.name.split('.')[:3])
    sdks = sorted((root / 'sdk').glob('10.*'), key=version)
    refs = sorted((root / 'packs/Microsoft.NETCore.App.Ref').glob('10.*'), key=version)
    if not sdks or not refs:
        raise RuntimeError('Installed net10 SDK and reference pack are required')
    return dotnet, sdks[-1] / 'Roslyn/bincore/csc.dll', refs[-1] / 'ref/net10.0'


def compile_source(source, output, *, library=False, resources=(), platform='anycpu'):
    dotnet, compiler, references = toolchain()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [dotnet, str(compiler), '/nologo', '/noconfig', '/nostdlib+', '/deterministic+',
            '/nullable:enable', '/langversion:latest', f'/target:{"library" if library else "exe"}',
            f'/platform:{platform}', f'/out:{output}']
    args += [f'/reference:{p}' for p in sorted(references.glob('*.dll'))]
    args += [f'/resource:{p},{name}' for p, name in resources]
    result = subprocess.run(args + [str(source)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    if not library:
        output.with_suffix('.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {
            'tfm': 'net10.0', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '10.0.0'},
            'rollForward': 'LatestPatch'}}), encoding='utf-8')
    return output


def self_contained(output):
    """Publish only from installed SDK packs and a verified local runtime nupkg.

    The caller must supply a new output directory. All mutable CLI, NuGet and
    MSBuild state lives inside that owned directory; failures retain diagnostics.
    """
    if os.name != 'nt':
        raise RuntimeError('The portable metadata helper requires Windows x64')
    dotnet, compiler, _ = toolchain()
    sdk = compiler.parents[2]
    dotnet_root = sdk.parents[1]
    bundled = ET.parse(sdk / 'Microsoft.NETCoreSdk.BundledVersions.props').getroot()
    framework = next((item for item in bundled.iter('KnownFrameworkReference')
                      if item.get('Include') == 'Microsoft.NETCore.App'
                      and item.get('TargetFramework') == 'net10.0'), None)
    if framework is None:
        raise RuntimeError('Installed SDK has no net10 runtime definition')
    runtime_version = framework.get('LatestRuntimeFrameworkVersion')
    ref_version = framework.get('TargetingPackVersion')
    reference = dotnet_root / 'packs/Microsoft.NETCore.App.Ref' / ref_version
    host = dotnet_root / 'packs/Microsoft.NETCore.App.Host.win-x64' / runtime_version
    cache = Path(os.environ.get('NUGET_PACKAGES', str(Path.home() / '.nuget/packages')))
    runtime = cache / 'microsoft.netcore.app.runtime.win-x64' / runtime_version
    package = runtime / f'microsoft.netcore.app.runtime.win-x64.{runtime_version}.nupkg'
    required = [reference / 'ref/net10.0/System.Runtime.dll',
                host / 'runtimes/win-x64/native/apphost.exe', package,
                runtime / 'LICENSE.TXT', runtime / 'THIRD-PARTY-NOTICES.TXT']
    if any(not path.is_file() for path in required):
        raise RuntimeError(f'Offline win-x64 runtime/reference/host materials for {runtime_version} '
                           'are missing; no download or dependency installation was attempted')
    base = Path(__file__).resolve().parent
    output = Path(output).absolute()
    if output == base or base in output.parents:
        raise RuntimeError('Self-contained output must be outside the helper source directory')
    for parent in (output, *output.parents):
        if parent.exists() and (parent.is_symlink() or
                getattr(parent.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise RuntimeError('Redirected self-contained output directory')
    output.mkdir(parents=True, exist_ok=False)
    work = output / '.build'
    source = work / 'source'
    source.mkdir(parents=True)
    feed = work / 'feed'
    feed.mkdir()
    shutil.copyfile(base / 'Program.cs', source / 'Program.cs')
    shutil.copyfile(base / 'PSModManager.Metadata.csproj', source / 'PSModManager.Metadata.csproj')
    shutil.copyfile(package, feed / package.name)
    (source / 'global.json').write_text(json.dumps({'sdk': {
        'version': sdk.name, 'rollForward': 'disable'}}), encoding='utf-8')
    config = ET.Element('configuration')
    feeds = ET.SubElement(config, 'packageSources')
    ET.SubElement(feeds, 'clear')
    ET.SubElement(feeds, 'add', key='owned-offline-runtime', value=str(feed))
    config_path = work / 'NuGet.Config'
    ET.ElementTree(config).write(config_path, encoding='utf-8', xml_declaration=True)
    env = dict(os.environ, DOTNET_CLI_HOME=str(work / 'cli'),
               DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
               DOTNET_GENERATE_ASPNET_CERTIFICATE='false', DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false',
               DOTNET_NOLOGO='1', DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE='true',
               MSBuildEnableWorkloadResolver='false', NUGET_CERT_REVOCATION_MODE='offline',
               NUGET_PACKAGES=str(work / 'packages'), NUGET_HTTP_CACHE_PATH=str(work / 'http-cache'),
               NUGET_PLUGINS_CACHE_PATH=str(work / 'plugins-cache'))
    project = source / 'PSModManager.Metadata.csproj'
    properties = [f'-p:RuntimeFrameworkVersion={runtime_version}', '-p:NuGetAudit=false',
                  '-p:UseSharedCompilation=false', '-nr:false']
    commands = [
        [dotnet, 'restore', str(project), '-r', 'win-x64', '--configfile', str(config_path),
         '--source', str(feed), '--packages', str(work / 'packages'), *properties],
        [dotnet, 'publish', str(project), '-c', 'Release', '-r', 'win-x64',
         '--self-contained', 'true', '--no-restore', '-o', str(output), *properties,
         f'-p:PathMap={source}=/_/PSModManager.Metadata']]
    logs = []
    log_path = output / 'helper-build.log'
    for command in commands:
        print(f'Offline helper: {command[1]} with SDK {sdk.name}, runtime {runtime_version}', flush=True)
        result = subprocess.run(command, cwd=source, env=env, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=180)
        logs.append(' '.join(command) + '\n' + result.stdout + result.stderr +
                    f'\nexit_code={result.returncode}\n')
        log_path.write_text('\n'.join(logs).replace(str(work), '[owned-build-root]')
                           .replace(str(output), '[owned-output-root]'), encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'Offline helper {command[1]} failed with exit {result.returncode}; '
                               f'diagnostics retained in {log_path}')
    for name in ('PSModManager.Metadata.exe', 'PSModManager.Metadata.dll',
                 'PSModManager.Metadata.deps.json', 'PSModManager.Metadata.runtimeconfig.json',
                 'hostfxr.dll', 'hostpolicy.dll', 'coreclr.dll', 'System.Private.CoreLib.dll'):
        if not (output / name).is_file():
            raise RuntimeError(f'Incomplete self-contained helper: {name}')
    shutil.copyfile(runtime / 'LICENSE.TXT', output / 'LICENSE-dotnet.txt')
    shutil.copyfile(runtime / 'THIRD-PARTY-NOTICES.TXT', output / 'THIRD-PARTY-NOTICES-dotnet.txt')
    sha256 = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    (output / 'helper-build.json').write_text(json.dumps({
        'schema_version': 1, 'target': 'win-x64', 'framework': 'net10.0',
        'sdk_version': sdk.name, 'runtime_version': runtime_version, 'reference_version': ref_version,
        'self_contained': True, 'single_file': False, 'trimmed': False,
        'offline_feed': 'caller-owned copy of cached Microsoft.NETCore.App.Runtime.win-x64',
        'runtime_package_sha256': sha256(package),
        'source_sha256': sha256(base / 'Program.cs'),
        'project_sha256': sha256(base / 'PSModManager.Metadata.csproj'),
        'commands_exit_codes': [0, 0]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Delete only the .build tree created above, never a caller-supplied path.
    if work.resolve().parent != output.resolve():
        raise RuntimeError('Unsafe helper build cleanup target')
    for item in [work, *work.rglob('*')]:
        if item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise RuntimeError('Redirected helper build cleanup target')
    shutil.rmtree(work)
    return output / 'PSModManager.Metadata.exe'


if __name__ == '__main__':
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--self-contained', action='store_true')
    options = parser.parse_args()
    if options.self_contained:
        if options.output_dir is None:
            parser.error('--self-contained requires a new --output-dir outside the source directory')
        print(self_contained(options.output_dir))
    else:
        print(compile_source(base / 'Program.cs', (options.output_dir or base / 'bin') / 'PSModManager.Metadata.dll'))
