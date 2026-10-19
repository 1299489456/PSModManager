"""PS-00-01 only: fixed Git blobs -> local source; never import application code.

Uses the standard library. No checkout, dependency installation or application launch.
Existing destination files must match the fixed blobs; conflicts are never overwritten.
"""
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('E:/WorkSpace/GitHub/RimCrow')
COMMIT = 'a7e875112a175a914bb4706a82589d96d62adc70'
RUN = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
OUT = ROOT / 'docs/development/evidence/PS-00-01' / RUN
COMMANDS = []


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def command(args, cwd=ROOT, required=True):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, timeout=90)
        code, stdout, stderr = p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, stdout, stderr = None, b'', str(exc).encode()
    entry = dict(command=subprocess.list2cmdline([str(a) for a in args]), cwd=str(cwd), exit_code=code,
                 stdout=stdout.decode('utf-8', 'replace'), stderr=stderr.decode('utf-8', 'replace'))
    COMMANDS.append(entry)
    if required and code != 0:
        raise RuntimeError(f'Command failed: {entry}')
    return stdout


def git(*args):
    return command(['git', '--no-optional-locks', '-C', str(SOURCE), *args])


def snapshot(paths):
    return {p: digest((SOURCE / p).read_bytes()) if (SOURCE / p).is_file() else None for p in paths}


def disposition(path):
    forbidden = ('steam', 'workshop', 'texture', 'multiplayer', 'mgr_update', 'mgr_rules', 'mgr_github', 'mgr_download', 'mgr_sub_browser', 'browser_runtime')
    if any(s in path.lower() for s in forbidden):
        return 'remove-from-active-entry', 'PS-00-02/PS-00-04', 'Old online/game-specific behavior; reference only, do not execute'
    if path.startswith(('backend/scanner/', 'backend/load_order/', 'backend/managers/', 'backend/paths/', 'backend/profile/', 'backend/migrations/')):
        return 'rewrite', 'PS-00-02 and responsible domain tasks in 02-architecture.md', 'RimWorld semantics and filesystem side effects cannot be reused as-is'
    if path in ('main.py', 'backend/settings.py', 'backend/api.py') or path.startswith(('backend/database/', 'backend/ai/', 'frontend/src/app/', 'frontend/src/features/')):
        return 'rewrite', 'PS-00-02/03/04 and domain tasks in 02-architecture.md', 'Retain fixed source for selective migration, not a safe PSModManager runtime'
    return 'retain-for-review', 'Responsible task in 02-architecture.md', 'Reusable mechanism or baseline material; runtime and redistribution not approved'


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    save('plan.json', {'task_id': 'PS-00-01', 'steps': ['snapshot source and existing workspace', 'import fixed blobs without overwriting docs', 'hash and static-parse actual consumer files', 'inventory tools, dependencies, modules and licenses'], 'prohibited': ['application execution', 'dependency installation', 'source writes', 'game access']})
    cases = []
    status, error = 'FAIL', None
    try:
        # Preserve a file-level fingerprint of everything already in the destination.
        before = {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in ROOT.rglob('*') if p.is_file() and OUT not in p.parents}
        save('workspace-before.json', before)
        ledger = json.loads((ROOT / 'docs/development/task-register.json').read_text(encoding='utf-8-sig'))
        task = next(t for t in ledger['tasks'] if t['id'] == 'PS-00-01')
        assert task['depends_on'] == [] and task['required_gate'] is None
        head = git('rev-parse', 'HEAD').decode().strip()
        assert head == COMMIT, 'Reference HEAD drift'
        state = git('status', '--porcelain=v1', '--untracked-files=all').decode()
        subs = git('submodule', 'status').decode()
        tree = git('ls-tree', '-rz', COMMIT)
        entries = []
        for raw in tree.split(b'\0'):
            if raw:
                meta, path = raw.split(b'\t', 1)
                mode, kind, oid = meta.decode().split()
                entries.append((path.decode('utf-8'), mode, kind, oid))
        source_before = snapshot([p for p, _, k, _ in entries if k == 'blob'])
        save('source-before.json', dict(head=head, status=state, submodules=subs, files=source_before))
        roots = {'main.py', 'pyproject.toml', 'uv.lock', '.python-version', 'LICENSE'}
        suffixes = {'.py', '.js', '.mjs', '.vue', '.json', '.toml', '.md', '.html', '.css', '.scss', '.svg'}
        selected, excluded, manifest = [], [], []
        for path, mode, kind, oid in entries:
            allowed = path in roots or (path.startswith(('backend/', 'frontend/', 'tests/', 'scripts/')) and (Path(path).suffix in suffixes or Path(path).name == '.gitignore'))
            if kind != 'blob' or mode != '100644' or not allowed:
                excluded.append(dict(path=path, git_object=oid, reason='Outside source-only task whitelist; no submodules, binary/brand images, root packaging tools, upstream docs or runtime data'))
                continue
            content = git('cat-file', 'blob', oid)
            dest = ROOT / path
            if dest.exists():
                assert dest.is_file() and dest.read_bytes() == content, f'Existing file conflict: {path}'
            selected.append((path, oid, content))
        # All conflicts are checked before the first import write.
        for path, oid, content in selected:
            dest = ROOT / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(content)
            action, owner, reason = disposition(path)
            manifest.append(dict(path=path, git_blob=oid, source_sha256=digest(content), destination_sha256=digest(dest.read_bytes()), bytes=len(content), disposition=action, owner=owner, reason=reason))
        save('import-manifest.json', manifest)
        save('excluded.json', {'tracked': excluded, 'never_copied': ['.git/', '.codegraph/', '.venv/', 'node_modules/', 'dist/', 'build/', 'user data/config/database/credentials', 'game/mod binaries'], 'method': 'git cat-file fixed committed blobs only; no working-tree copy'})
        assert manifest and all(m['source_sha256'] == m['destination_sha256'] for m in manifest)
        cases.append(dict(subcase='fixed-commit-import', status='PASS', count=len(manifest), expected='Exact fixed blobs at actual product paths', actual='All SHA256 matched; no source edits'))
        parses = []
        test_inventory = []
        for m in manifest:
            path = m['path']
            p = ROOT / path
            kind = p.suffix
            if kind in ('.py', '.json', '.toml') or p.name == 'uv.lock':
                text = p.read_text(encoding='utf-8-sig')
                if kind == '.py':
                    parsed = ast.parse(text, filename=path)
                    compile(text, path, 'exec', dont_inherit=True)  # In-memory only; no execution or pyc.
                    if path.startswith('tests/'):
                        test_inventory.append(dict(path=path, test_functions=sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_') for n in ast.walk(parsed)), status='NOT_RUN', exit_code=None, reason='Legacy imports may initialize settings and real paths; project .venv missing'))
                elif kind == '.json':
                    json.loads(text)
                else:
                    tomllib.loads(text)
                parses.append(dict(path=path, kind=kind, status='PASS'))
            elif kind in ('.js', '.mjs'):
                command(['node', '--check', str(p)])
                parses.append(dict(path=path, kind=kind, status='PASS'))
            if (path.startswith('frontend/tests/') or path.startswith('tests/')) and kind == '.mjs':
                test_inventory.append(dict(path=path, status='NOT_RUN', exit_code=None, reason='Legacy suite not executed; node_modules missing; loader mocks are not product acceptance'))
        save('static-parsing.json', parses)
        save('legacy-tests.json', test_inventory)
        cases.append(dict(subcase='static-source-availability', status='PASS', count=len(parses), expected='Parse without importing old settings', actual='Python compile/AST, JSON/TOML and node --check succeeded; Vue SFC compiler not run'))
        pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        lock = tomllib.loads((ROOT / 'uv.lock').read_text())
        packages = json.loads((ROOT / 'frontend/package-lock.json').read_text())['packages']
        dependencies = []
        for requirement in pyproject['project']['dependencies']:
            name = re.split(r'[<>=!~;\[]', requirement)[0].strip()
            try:
                installed = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                installed = None
            dependencies.append(dict(requirement=requirement, global_interpreter_version=installed, project_environment='MISSING', locked_versions=[p['version'] for p in lock['package'] if p['name'] == name and 'version' in p]))
        licenses = {'upstream': {'path': 'LICENSE', 'license': 'MIT', 'copyright': 'Copyright (c) 2026 RimCrow contributors', 'sha256': digest((ROOT / 'LICENSE').read_bytes())},
                    'python_packages': [dict(name=p['name'], version=p.get('version'), source=p.get('source'), license_status='NOT_AUDITED: lock does not establish redistribution rights') for p in lock['package']],
                    'npm_packages': [dict(path=k, version=v.get('version'), license=v.get('license', 'UNKNOWN'), source=v.get('resolved'), integrity=v.get('integrity')) for k, v in packages.items() if k],
                    'assets': [dict(path=m['path'], source_commit=COMMIT, sha256=m['source_sha256'], license_status='upstream repository provenance only; third-party/brand redistribution review pending PS-05-04') for m in manifest if m['path'].endswith('.svg')],
                    'excluded_submodule': dict(name='SteamworksPy', commit='c021d1c186084d22a21817fc4fe5fad1e1c20a7e', source='https://github.com/philippj/SteamworksPy.git', imported=False)}
        save('third-party-sources.json', licenses)
        versions = {}
        for name, args in [('python', [sys.executable, '--version']), ('python_inventory', ['py', '-0p']), ('node', ['node', '--version']), ('npm', ['npm.cmd', '--version']), ('uv', ['uv', '--version']), ('dotnet', ['dotnet', '--list-sdks']), ('git', ['git', '--version']), ('pwsh', ['pwsh', '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()'])]:
            versions[name] = command(args, required=False).decode('utf-8', 'replace').strip()
        webview = command(['pwsh', '-NoProfile', '-Command', "Get-ChildItem 'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients','HKCU:\\Software\\Microsoft\\EdgeUpdate\\Clients' -ErrorAction SilentlyContinue | Get-ItemProperty | Where-Object { $_.name -like '*WebView*' } | Select-Object name,pv | ConvertTo-Json"], required=False).decode('utf-8', 'replace').strip()
        save('environment.json', dict(os=platform.platform(), architecture=platform.machine(), interpreter=sys.executable, versions=versions, python_pin='3.11', project_venv_exists=(ROOT / '.venv/Scripts/python.exe').exists(), node_modules_exists=(ROOT / 'frontend/node_modules').exists(), dependencies=dependencies, vite=packages.get('node_modules/vite'), webview_registry=webview, source_commit=COMMIT, source_status=state, workspace_git='NOT_INITIALIZED', workspace_fingerprint=digest(json.dumps(before, sort_keys=True).encode()), isolated_data_root='NOT_USED: no application or game executed', constraints=['no dependency install', 'no game access', 'source read-only', 'no app imports']))
        cases.append(dict(subcase='provenance-licenses-and-dependency-inventory', status='PASS', count=len(manifest), expected='Every imported file has disposition; dependencies and third-party origins recorded', actual='Manifest, third-party-sources, environment and legacy-tests populated; runtime remains NOT_RUN'))
        assert all((ROOT / p).is_file() and digest((ROOT / p).read_bytes()) == h for p, h in before.items()), 'Existing workspace content changed'
        after = dict(head=git('rev-parse', 'HEAD').decode().strip(), status=git('status', '--porcelain=v1', '--untracked-files=all').decode(), submodules=git('submodule', 'status').decode(), files=snapshot(source_before))
        save('source-after.json', after)
        assert after == dict(head=head, status=state, submodules=subs, files=source_before), 'Source changed'
        cases.append(dict(subcase='source-and-existing-docs-preserved', status='PASS', count=len(before), expected='Source tracked content/Git state and existing workspace bytes unchanged', actual='Before/after equality checked; no working-tree data copied'))
        status = 'PASS'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        cases.append(dict(subcase='execution-error', status='FAIL', expected='Complete baseline verification', actual=error))
    finally:
        save('commands.json', COMMANDS)
        (OUT / 'command.txt').write_text('\n'.join(f"cwd={c['cwd']}\n{c['command']}\nexit_code={c['exit_code']}" for c in COMMANDS), encoding='utf-8')
        (OUT / 'stdout.log').write_text('\n'.join(f"$ {c['command']}\n{c['stdout']}" for c in COMMANDS), encoding='utf-8')
        (OUT / 'stderr.log').write_text('\n'.join(f"$ {c['command']}\n{c['stderr']}" for c in COMMANDS if c['stderr']) + (error or ''), encoding='utf-8')
        save('cases.json', [dict(case_id='CASE-001', task_id='PS-00-01', profile='synthetic', status=status, subcases=cases, evidence=['import-manifest.json', 'static-parsing.json', 'source-before.json', 'source-after.json', 'third-party-sources.json'], remaining_owner='PS-00-02: remove old active entry paths; NOT_RUN')])
        save('summary.json', dict(schema_version=1, task_id='PS-00-01', profile='synthetic', run_id=RUN, started_at=started, ended_at=datetime.now(timezone.utc).isoformat(), command='python -B scripts/reference_baseline.py', exit_code=0 if status == 'PASS' else 1, reference_commit=COMMIT, verification_status=status, verification_kind='reference_import_integrity', discovered=4, executed=len(cases), passed=sum(c['status'] == 'PASS' for c in cases), failed=sum(c['status'] == 'FAIL' for c in cases), skipped=0, required_cases=['CASE-001/PS-00-01'], blockers=[error] if error else [], limitations=['No legacy tests/app/build executed', 'Python 3.11 and project .venv absent; frontend node_modules absent', 'Vue SFC compilation NOT_RUN', 'Independent startup belongs to PS-00-02; G0 NOT_RUN', 'Third-party redistribution NOT_AUDITED', 'Source preservation covers tracked file bytes and Git status, not ignored user data reads'], artifacts=[p.name for p in OUT.iterdir() if p.is_file()]))
        inputs = [(p.relative_to(ROOT).as_posix(), digest(p.read_bytes())) for p in [Path(__file__), ROOT / 'docs/development/tasks/PS-00-01.md', ROOT / 'docs/development/02-architecture.md']]
        if (OUT / 'import-manifest.json').exists():
            inputs += [(m['path'], m['source_sha256']) for m in json.loads((OUT / 'import-manifest.json').read_text(encoding='utf-8'))]
        (OUT / 'inputs.sha256').write_text(''.join(f'{h}  {p}\n' for p, h in inputs), encoding='utf-8')
        (OUT / 'artifacts.sha256').write_text(''.join(f'{digest(p.read_bytes())}  {p.name}\n' for p in sorted(OUT.iterdir()) if p.is_file() and p.name != 'artifacts.sha256'), encoding='utf-8')
    print(json.dumps(dict(run=RUN, status=status, error=error), ensure_ascii=False))
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
