"""Versioned evidence and explicit task routes; never restores dependencies."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET

from .processes import run_command

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {'synthetic', 'desktop-local', 'real-readonly', 'real-game', 'ai-live', 'release'}
AUTH_FIELDS = {'task_id', 'profile', 'resources', 'operations', 'network', 'upload', 'budget', 'valid_until', 'excluded_data'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root, path):
    """Reject links before resolve, including intermediate Windows junctions."""
    root = root.absolute()
    path = path.absolute()
    if str(path).startswith(('\\\\', '//')) or any(
            part == '..' or part.endswith((' ', '.')) or ':' in part for part in path.parts[1:]):
        raise ValueError('Ambiguous path refused')
    if not path.is_relative_to(root):
        raise ValueError('Path outside controlled project root')
    for candidate in (root, *reversed(path.parents), path):
        if candidate.exists() and (candidate.is_symlink() or
                getattr(candidate.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400):
            raise ValueError(f'Reparse point refused: {candidate}')
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise ValueError('Hard-linked file refused')
    return path


def artifact_files(run):
    # Negative tests intentionally leave links. Describe them without traversing
    # or opening their targets; regular hardlinks are read-only fixture evidence.
    files, links = [], []
    for directory, dirs, names in os.walk(run, followlinks=False):
        for name in list(dirs) + names:
            path = Path(directory) / name
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                links.append({'path': path.relative_to(run).as_posix(), 'kind': 'reparse', 'target_not_read': True})
                if name in dirs:
                    dirs.remove(name)
            elif path.is_file() and '__pycache__' not in path.parts and path.name != 'artifacts.sha256':
                files.append(path)
                if info.st_nlink > 1:
                    links.append({'path': path.relative_to(run).as_posix(), 'kind': 'hardlink', 'links': info.st_nlink})
    return files, links


def authorization(path, task, profile):
    if profile in {'synthetic', 'desktop-local'}:
        return {'required': False, 'scope': 'project fixtures and isolated data only'}
    if not path:
        raise ValueError('Authorization configuration missing')
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not AUTH_FIELDS <= value.keys() or value['task_id'] != task or value['profile'] != profile:
        raise ValueError('Authorization does not cover task/profile and required boundaries')
    if not value['resources'] or not value['operations']:
        raise ValueError('Authorization resources/operations empty')
    expiry = datetime.fromisoformat(value['valid_until'].replace('Z', '+00:00'))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('Authorization expired or not timezone-qualified')
    if task == 'PS-01-07' and profile == 'real-readonly':
        from .readonly import validate_authorization
        validate_authorization(value)
    # This configuration records existing user consent; it never grants authority.
    return {'required': True, 'configuration_sha256': digest(Path(path))}


def select_route(root, task, profile):
    if profile not in PROFILES:
        raise ValueError('Unknown profile')
    ledger = json.loads((root / 'docs/development/task-register.json').read_text(encoding='utf-8'))
    if task not in ledger['execution_order']:
        raise ValueError('Unknown task')
    config = json.loads((root / 'docs/development/verification-profiles.json').read_text(encoding='utf-8'))
    if config.get('schema_version') != 1:
        raise ValueError('Unsupported route schema')
    route = config['routes'].get(task, {}).get(profile)
    if not route:
        raise ValueError('Required test entry missing or unsupported task/profile')
    if not route.get('tests') or not re.fullmatch(r'CASE-\d{3}', route.get('case', '')):
        raise ValueError('Empty test/case mapping')
    if 'case_tests' in route:
        expected = next(item['cases'] for item in ledger['tasks'] if item['id'] == task)
        mapping = route['case_tests']
        if (not isinstance(mapping,dict) or set(mapping) != set(expected)
                or any(not isinstance(parts,list) or not parts or any(not isinstance(p,str) or not p for p in parts) for parts in mapping.values())):
            raise ValueError('Incomplete required CASE scenario mapping')
    if 'node_case_tests' in route:
        mapping = route['node_case_tests']
        expected = set(route.get('case_tests', {route['case']: None}))
        if (not route.get('node_tests') or not isinstance(mapping, dict) or not mapping
                or not set(mapping).issubset(expected)
                or any(not isinstance(parts, list) or not parts
                       or any(not isinstance(p, str) or not p for p in parts) for parts in mapping.values())):
            raise ValueError('Invalid Node CASE scenario mapping')
    for entry in route['tests'] + route.get('node_tests', []):
        if not safe_path(root, root / entry).is_file():
            raise ValueError(f'Required test entry missing: {entry}')
    return route


def read_junit(path, required_tests=()):
    document = ET.parse(path).getroot()
    cases = list(document.iter('testcase'))
    rows = []
    for case in cases:
        status = 'PASS'
        if case.find('failure') is not None or case.find('error') is not None:
            status = 'FAIL'
        elif case.find('skipped') is not None:
            status = 'BLOCKED'
        rows.append({'name': case.get('name'), 'class': case.get('classname', ''), 'status': status})
    missing = [entry for entry in required_tests if not any(
        Path(entry).stem in row['class'].split('.') for row in rows)]
    counts = {'discovered': len(rows), 'executed': sum(r['status'] != 'BLOCKED' for r in rows),
              'passed': sum(r['status'] == 'PASS' for r in rows),
              'failed': sum(r['status'] == 'FAIL' for r in rows),
              'skipped': sum(r['status'] == 'BLOCKED' for r in rows)}
    return counts, rows, missing


def assess(result, junit, required_tests=()):
    counts = dict.fromkeys(('discovered', 'executed', 'passed', 'failed', 'skipped'), 0)
    rows, problems = [], []
    if not result['started']:
        return 'BLOCKED', 2, counts, rows, [result.get('error') or 'Command not started']
    if result['timed_out']:
        problems.append('Command timed out')
    if result.get('error'):
        problems.append(result['error'])
    if result['cleanup'].get('remaining_processes') != 0 or result['cleanup'].get('error'):
        problems.append('Process cleanup not proven')
    try:
        counts, rows, missing = read_junit(junit, required_tests)
        if missing:
            problems.append('Required test files not discovered: ' + ', '.join(missing))
        if not counts['discovered'] or not counts['executed']:
            problems.append('Zero required tests executed')
        if counts['skipped']:
            problems.append('Required tests skipped')
        if counts['failed']:
            problems.append('Test assertions/errors failed')
    except (OSError, ET.ParseError) as exc:
        problems.append(f'Test report unavailable: {exc}')
    if result['exit_code'] != 0:
        problems.append(f'Actual process exit: {result["exit_code"]}')
    code = 124 if result['timed_out'] else (result['exit_code'] or (1 if problems else 0))
    return ('FAIL' if problems else 'PASS'), code, counts, rows, problems


def isolated_environment(run, profile):
    env = os.environ.copy()
    for key in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS'):
        env.pop(key, None)
    for name in ('TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'USERPROFILE'):
        target = safe_path(ROOT, run / 'artifacts/isolation' / name.lower())
        target.mkdir(parents=True, exist_ok=True)
        env[name] = str(target)
    # Inherited product overrides must never point at a real installation/data root.
    for key in list(env):
        if key.startswith(('PSMODMANAGER_', 'PSMM_')):
            del env[key]
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PSMM_VERIFY_RUN_ROOT=str(run), PSMM_VERIFY_PROFILE=profile)
    return env


def snapshot_inputs(root):
    paths = []
    for folder in ('backend/app', 'backend/contracts', 'backend/storage', 'backend/runtime', 'backend/games', 'backend/scanning', 'backend/loaders', 'backend/catalog', 'backend/diagnostics', 'backend/profiles', 'backend/deployments', 'tools/PSModManager.Metadata',
                   'scripts/verification', 'tests', 'frontend/src/app', 'frontend/src/features/installations',
                   'frontend/src/features/catalog', 'frontend/src/features/diagnostics', 'frontend/src/features/imports', 'frontend/src/features/profiles', 'frontend/src/features/deployments', 'frontend/src/shared/components/desktop', 'frontend/tests', 'frontend/dist'):
        paths.extend((root / folder).rglob('*'))
    paths += [root / value for value in ('main.py', 'pyproject.toml', 'uv.lock', 'scripts/verify.ps1',
              'scripts/build-fixtures.ps1', 'scripts/build_fixtures.py',
              'docs/ProbablyStolen_ModManager_Analysis.zip', 'docs/ProbablyStolen_RimCrow_Analysis.zip',
              'docs/development/verification-profiles.json', 'docs/development/verification-summary.schema.json')]
    return sorted({p for p in paths if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'})


def execute(task, profile, authorization_path=None):
    started = utc()
    task_folder = task if re.fullmatch(r'PS-\d{2}-\d{2}', task) else 'invalid-task'
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    run = safe_path(ROOT, ROOT / 'docs/development/evidence' / task_folder / run_id)
    run.mkdir(parents=True, exist_ok=False)
    (run / 'artifacts').mkdir()
    command = [sys.executable, '-B', '-m', 'scripts.verification.runner', '--task', task, '--profile', profile]
    (run / 'command.txt').write_text(json.dumps(command) + '\ncwd=' + str(ROOT), encoding='utf-8')
    inputs = {}
    summary = {'schema_version': 1, 'task_id': task, 'profile': profile, 'run_id': run_id,
               'verification_status': 'BLOCKED', 'started_at': started, 'ended_at': None,
               'command': command, 'exit_code': None, 'runner_exit_code': 2, 'reference_commit':
               'a7e875112a175a914bb4706a82589d96d62adc70', 'git_commit': None,
               'counts': dict.fromkeys(('discovered', 'executed', 'passed', 'failed', 'skipped'), 0),
               'required_cases': [], 'blockers': [], 'gaps': (['No game launch, saves, deployment, provider or release verification; real reads limited to authorized scope']
                   if profile == 'real-readonly' else ['No real-game, real resources, provider or release verification']),
               'evidence': ['environment.json', 'cases.json', 'command.txt', 'stdout.log', 'stderr.log', 'inputs.sha256', 'artifacts.sha256'], 'commands': []}
    cases = []
    environment = {'schema_version': 1, 'os': platform.platform(), 'architecture': platform.machine(),
                   'python': sys.version, 'git': 'NOT_INITIALIZED' if not (ROOT / '.git').exists() else 'UNRESOLVED',
                   'code_fingerprint': hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
                   'isolation_root': str(run / 'artifacts'), 'dependencies': {},
                   'constraints': 'No dependency restore; synthetic inputs only; no game or save operations. This runner is not an OS network sandbox.'}
    try:
        environment['ledger_sha256_at_start'] = digest(ROOT / 'docs/development/task-register.json')
        inputs = {p.relative_to(ROOT).as_posix(): digest(safe_path(ROOT, p)) for p in snapshot_inputs(ROOT)}
        environment['code_fingerprint'] = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        (run / 'inputs.sha256').write_text(''.join(f'{h}  {p}\n' for p, h in inputs.items()), encoding='utf-8')
        route = select_route(ROOT, task, profile)
        summary['required_cases'] = list(route.get('case_tests', {route['case']:None}))
        environment['authorization'] = authorization(authorization_path, task, profile)
        for module, distribution in [('pytest', 'pytest'), ('psutil', 'psutil'), ('win32job', 'pywin32'), ('pydantic', 'pydantic'), ('jsonschema', 'jsonschema')]:
            if importlib.util.find_spec(module) is None:
                raise ValueError(f'Missing dependency: {distribution}; no automatic install')
            environment['dependencies'][distribution] = importlib.metadata.version(distribution)
        if profile == 'desktop-local':
            environment['dependencies']['pywebview'] = importlib.metadata.version('pywebview')
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') as key:
                environment['dependencies']['webview2'] = winreg.QueryValueEx(key, 'pv')[0]
        env = isolated_environment(run, profile)
        if task == 'PS-01-07' and profile == 'real-readonly':
            env['PSMM_READONLY_AUTHORIZATION_FILE'] = str(Path(authorization_path).absolute())
            environment['constraints'] = 'Explicit authorized read-only resources; isolated output; no game launch, saves, network or dependency restore.'
        if route.get('fixtures'):
            from scripts.build_fixtures import compiler_inputs
            compiler_inputs()  # dependency preflight before any compiler is launched
            fixture_result = run_command([sys.executable, '-B', '-m', 'scripts.build_fixtures', '--output-root', str(run / 'artifacts/fixtures')],
                cwd=ROOT, env=env, timeout=90, stdout_path=run / 'artifacts/fixture-stdout.log', stderr_path=run / 'artifacts/fixture-stderr.log')
            summary['commands'].append(fixture_result)
            summary['exit_code'] = fixture_result['exit_code']
            if (not fixture_result['started'] or fixture_result['exit_code'] != 0 or fixture_result['error'] or
                    fixture_result['timed_out'] or fixture_result['cleanup'].get('remaining_processes') != 0 or fixture_result['cleanup'].get('error')):
                raise RuntimeError('Fixture build failed; see artifacts/fixture-stdout.log and fixture-stderr.log')
        jobs = [([sys.executable, '-B', '-m', 'pytest', *route['tests'], '-q', '-p', 'no:cacheprovider',
                  '-o', 'addopts=',
                  '-W', 'error::pytest.PytestUnhandledThreadExceptionWarning',
                  '--basetemp', str(run / 'artifacts/tests'), '--junitxml', str(run / 'artifacts/pytest.xml')],
                 run / 'artifacts/pytest.xml', route['tests'])]
        if route.get('node_tests'):
            node = shutil.which('node')
            if not node:
                raise ValueError('Missing dependency: Node.js; no automatic install')
            node_version = run_command([node, '--version'], cwd=ROOT, env=env, timeout=10,
                stdout_path=run / 'artifacts/node-version-stdout.log', stderr_path=run / 'artifacts/node-version-stderr.log')
            if node_version['exit_code'] != 0 or node_version.get('error'):
                raise ValueError('Node version preflight failed')
            environment['dependencies']['node'] = (run / 'artifacts/node-version-stdout.log').read_text().strip()
            jobs.append(([node, '--test', '--test-reporter=junit', *route['node_tests']], run / 'artifacts/job-1-stdout.log', []))
        summary['verification_status'] = 'PASS'
        summary['runner_exit_code'] = 0
        for index, (argv, report, required) in enumerate(jobs):
            stdout, stderr = run / f'artifacts/job-{index}-stdout.log', run / f'artifacts/job-{index}-stderr.log'
            if required:
                env['PSMM_VERIFY_COLLECTION_FILE'] = str(run / 'artifacts/collection.json')
            else:
                env.pop('PSMM_VERIFY_COLLECTION_FILE', None)
            print(json.dumps(argv), flush=True)
            result = run_command(argv, cwd=ROOT, env=env, timeout=route['timeout'], stdout_path=stdout, stderr_path=stderr)
            summary['commands'].append(result)
            summary['exit_code'] = result['exit_code']
            status, code, counts, rows, problems = assess(result, report, required)
            if status == 'PASS' and required:
                from collections import Counter
                collected = json.loads((run / 'artifacts/collection.json').read_text())['nodeids']
                if Counter(item.rsplit('::', 1)[-1] for item in collected) != Counter(row['name'] for row in rows):
                    status, code = 'FAIL', 1
                    problems.append('Discovered collection differs from executed JUnit scenarios')
            for name in counts:
                summary['counts'][name] += counts[name]
            mapping = route.get('case_tests', {route['case']: None})
            if not required:
                mapping = route.get('node_case_tests', mapping)
            for case_id, patterns in mapping.items():
                selected = rows if patterns is None else [row for row in rows if any(pattern in row['class']+'.'+row['name'] for pattern in patterns)]
                if not selected:
                    status, code = 'FAIL', 1
                    problems.append('Required CASE has no executed scenarios: '+case_id)
                case_counts = dict(discovered=len(selected),executed=sum(row['status']!='BLOCKED' for row in selected),
                                   passed=sum(row['status']=='PASS' for row in selected),failed=sum(row['status']=='FAIL' for row in selected),
                                   skipped=sum(row['status']=='BLOCKED' for row in selected))
                cases.append({'case_id': case_id, 'profile': profile, 'job': index, 'status': status,
                          'expected': 'All explicitly selected scenarios execute and pass without skips; owned processes cleaned',
                          'actual': case_counts, 'scenarios': selected, 'failure_reasons': list(problems),
                          'input_hashes': 'inputs.sha256', 'artifact_hashes': 'artifacts.sha256',
                          'output': [str(stdout.relative_to(run)), str(stderr.relative_to(run))]})
            if status != 'PASS':
                summary['verification_status'], summary['runner_exit_code'] = status, code
                summary['blockers'].extend(problems)
                break
        # A changed input during execution invalidates this run, even if tests passed.
        current_inputs = {p.relative_to(ROOT).as_posix(): digest(safe_path(ROOT, p)) for p in snapshot_inputs(ROOT)}
        if current_inputs != inputs:
            summary['verification_status'], summary['runner_exit_code'] = 'FAIL', 1
            summary['blockers'].append('Code/input changed during verification')
    except Exception as exc:
        did_start = any(item['started'] for item in summary['commands'])
        summary['verification_status'] = 'FAIL' if did_start else 'BLOCKED'
        summary['runner_exit_code'] = (summary['exit_code'] or 1) if did_start else 2
        summary['blockers'].append(f'{type(exc).__name__}: {exc}')
    if not cases:
        cases = [{'case_id': case, 'profile': profile, 'status': 'BLOCKED', 'failure_reasons': summary['blockers']} for case in summary['required_cases']]
    summary['ended_at'] = utc()
    try:
        import jsonschema
        schema = json.loads((ROOT / 'docs/development/verification-summary.schema.json').read_text(encoding='utf-8'))
        jsonschema.Draft202012Validator(schema).validate(summary)
        jsonschema.Draft202012Validator(schema['$defs']['environment']).validate(environment)
        jsonschema.Draft202012Validator(schema['$defs']['cases']).validate({'schema_version': 1, 'cases': cases})
    except ImportError:
        summary['verification_status'], summary['runner_exit_code'] = 'BLOCKED', 2
        summary['blockers'].append('Missing jsonschema: evidence schema validation unavailable')
    except Exception as exc:
        summary['verification_status'], summary['runner_exit_code'] = 'FAIL', 1
        summary['blockers'].append(f'Evidence schema validation failed: {exc}')
    write_json(run / 'environment.json', environment)
    write_json(run / 'cases.json', {'schema_version': 1, 'cases': cases})
    write_json(run / 'summary.json', summary)
    for stream in ('stdout', 'stderr'):
        with (run / f'{stream}.log').open('wb') as target:
            for source in sorted((run / 'artifacts').glob(f'job-*-{stream}.log')):
                target.write(source.read_bytes())
            for source in sorted((run / 'artifacts').glob(f'fixture-{stream}.log')):
                target.write(source.read_bytes())
            if stream == 'stderr' and summary['blockers']:
                target.write(('\n' + '\n'.join(summary['blockers'])).encode('utf-8'))
    try:
        artifacts, links = artifact_files(run)
        write_json(run / 'artifact-links.json', links)
        artifacts.append(run / 'artifact-links.json')
        (run / 'artifacts.sha256').write_text(''.join(f'{digest(p)}  {p.relative_to(run).as_posix()}\n' for p in sorted(artifacts)), encoding='utf-8')
    except Exception as exc:
        summary['verification_status'], summary['runner_exit_code'] = 'FAIL', 1
        summary['blockers'].append(f'Evidence sealing failed: {type(exc).__name__}: {exc}')
        write_json(run / 'summary.json', summary)
    print(f'{summary["verification_status"]}: {run / "summary.json"}', flush=True)
    return summary['runner_exit_code']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--authorization')
    args = parser.parse_args()
    return execute(args.task, args.profile, args.authorization)


if __name__ == '__main__':
    raise SystemExit(main())
