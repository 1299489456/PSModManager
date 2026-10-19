"""CASE-005 failures use actual pytest processes, not fabricated runner success."""
import json
import os
from pathlib import Path
import sys

import pytest

from scripts.verification import runner
from scripts.verification.processes import run_command


@pytest.mark.parametrize(('source', 'expected_code', 'status', 'passed', 'skipped'), [
    ('def test_ok(): assert 2 + 2 == 4', 0, 'PASS', 1, 0),
    ('def test_bad(): assert False', 1, 'FAIL', 0, 0),
    ('value = 1', 5, 'FAIL', 0, 0),
    ('import pytest\n@pytest.mark.skip(reason="required unavailable")\ndef test_skip(): pass', 0, 'FAIL', 0, 1),
    ('def test_broken(: pass', 2, 'FAIL', 0, 0),
])
def test_actual_pytest_outcomes(tmp_path, source, expected_code, status, passed, skipped):
    script = tmp_path / 'test_probe.py'
    script.write_text(source, encoding='utf-8')
    report = tmp_path / 'junit.xml'
    argv = [sys.executable, '-B', '-m', 'pytest', str(script), '-q', '-p', 'no:cacheprovider', '--junitxml', str(report)]
    env = os.environ.copy()
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    result = run_command(argv, cwd=runner.ROOT, env=env, timeout=30,
                         stdout_path=tmp_path / 'stdout.log', stderr_path=tmp_path / 'stderr.log')
    (tmp_path / 'process-result.json').write_text(json.dumps(result), encoding='utf-8')
    assert result['exit_code'] == expected_code
    actual, code, counts, rows, problems = runner.assess(result, report, ['test_probe.py'])
    assert actual == status and (code == 0) == (status == 'PASS')
    assert counts['passed'] == passed and counts['skipped'] == skipped
    assert result['cleanup']['remaining_processes'] == 0


def test_actual_timeout_never_passes(tmp_path):
    result = run_command([sys.executable, '-c', 'import time; time.sleep(30)'], cwd=runner.ROOT,
                         env=os.environ.copy(), timeout=1, stdout_path=tmp_path / 'out', stderr_path=tmp_path / 'err')
    status, code, *_ = runner.assess(result, tmp_path / 'missing.xml')
    assert status == 'FAIL' and code == 124 and result['exit_code'] is None
    records = list(tmp_path.glob('process-status-*.json'))
    assert len(records) == 1  # Even an early timeout retains its bootstrap evidence.


@pytest.mark.parametrize(('task', 'profile'), [('unknown', 'synthetic'), ('PS-00-05', 'unknown'), ('PS-03-01', 'desktop-local'), ('PS-03-03', 'desktop-local'), ('PS-03-04', 'desktop-local'), ('PS-00-05', 'desktop-local')])
def test_unknown_and_unimplemented_routes(task, profile):
    with pytest.raises(ValueError):
        runner.select_route(runner.ROOT, task, profile)


def test_case029_route_includes_real_user_ai_plans_and_native_preview_consumers():
    route = runner.select_route(runner.ROOT, 'PS-03-02', 'synthetic')
    assert route['case'] == 'CASE-029'
    assert 'tests/unit/test_action_plans.py' in route['tests']
    assert 'tests/desktop/test_action_plan_preview.py' in route['tests']
    assert 'frontend/tests/planPreview.test.mjs' in route['node_tests']
    assert route['case_tests']['CASE-029']


def test_executor_route_requires_durable_product_consumers_and_every_case():
    route = runner.select_route(runner.ROOT,'PS-03-03','synthetic')
    assert set(route['case_tests'])=={'CASE-029','CASE-030','CASE-031','CASE-032','CASE-033'}
    assert 'tests/integration/test_transaction_recovery.py' in route['tests']
    assert 'tests/desktop/test_deployment_execution.py' in route['tests']
    assert 'frontend/tests/deploymentExecution.test.mjs' in route['node_tests']
    assert set(route['node_case_tests']) == {'CASE-029', 'CASE-030', 'CASE-032'}


def test_rollback_route_requires_actual_history_files_and_desktop_consumers():
    route = runner.select_route(runner.ROOT,'PS-03-04','synthetic')
    # Finish trusted desktop input before the long unattended backend matrix.
    assert route['tests'][0] == 'tests/desktop/test_rollback_history.py'
    assert set(route['case_tests']) == {'CASE-022','CASE-034'}
    assert {'tests/integration/test_rollback_drift.py','tests/integration/test_deployment_history.py',
            'tests/desktop/test_rollback_history.py'}.issubset(route['tests'])
    assert 'frontend/tests/deploymentHistory.test.mjs' in route['node_tests']
    assert set(route['node_case_tests']) == {'CASE-034'}
    assert runner.ROOT/'frontend/src/features/deployments/DeploymentHistory.vue' in runner.snapshot_inputs(runner.ROOT)


@pytest.mark.parametrize('mapping', [{}, {'CASE-003': ['unknown']}, {'CASE-001': []}, {'CASE-001': ['']}])
def test_node_case_mapping_cannot_be_empty_or_claim_foreign_responsibilities(tmp_path, mapping):
    docs = tmp_path / 'docs/development'; docs.mkdir(parents=True)
    (docs / 'task-register.json').write_text(json.dumps({'execution_order': ['PS-00-05'],
        'tasks': [{'id': 'PS-00-05', 'cases': ['CASE-001', 'CASE-002']}]}))
    (docs / 'verification-profiles.json').write_text(json.dumps({'schema_version': 1, 'routes': {
        'PS-00-05': {'synthetic': {'case': 'CASE-001', 'case_tests': {'CASE-001': ['alpha'], 'CASE-002': ['beta']},
            'node_case_tests': mapping, 'tests': ['absent.py'], 'node_tests': ['absent.mjs']}}}}))
    with pytest.raises(ValueError, match='Node CASE scenario mapping'):
        runner.select_route(tmp_path, 'PS-00-05', 'synthetic')


def test_actual_python_and_node_jobs_report_their_own_case_responsibilities(tmp_path, monkeypatch):
    import jsonschema
    docs = tmp_path / 'docs/development'; docs.mkdir(parents=True)
    schema_path = Path(__file__).resolve().parents[2] / 'docs/development/verification-summary.schema.json'
    (docs / 'verification-summary.schema.json').write_bytes(schema_path.read_bytes())
    tests = tmp_path / 'tests'; tests.mkdir()
    (tests / 'test_probe.py').write_text('def test_alpha(): assert 1 == 1\ndef test_beta(): assert 2 == 2\n')
    (tests / 'conftest.py').write_text(
        'import json, os\nfrom pathlib import Path\n'
        'def pytest_collection_modifyitems(items):\n'
        '    Path(os.environ["PSMM_VERIFY_COLLECTION_FILE"]).write_text(json.dumps({"nodeids": [item.nodeid for item in items]}))\n')
    (tests / 'probe.mjs').write_text("import test from 'node:test'; import assert from 'node:assert/strict';\n"
        "test('alpha node component', () => assert.equal(1, 1));\n")
    (docs / 'task-register.json').write_text(json.dumps({'execution_order': ['PS-00-05'],
        'tasks': [{'id': 'PS-00-05', 'cases': ['CASE-001', 'CASE-002']}]}))
    (docs / 'verification-profiles.json').write_text(json.dumps({'schema_version': 1, 'routes': {
        'PS-00-05': {'synthetic': {'case': 'CASE-001', 'case_tests': {'CASE-001': ['alpha'], 'CASE-002': ['beta']},
            'node_case_tests': {'CASE-001': ['alpha']}, 'tests': ['tests/test_probe.py'], 'node_tests': ['tests/probe.mjs'], 'timeout': 30}}}}))
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    assert runner.execute('PS-00-05', 'synthetic') == 0
    run = next((docs / 'evidence/PS-00-05').iterdir())
    summary = json.loads((run / 'summary.json').read_text())
    schema = json.loads(schema_path.read_text())
    jsonschema.validate(summary, schema)
    assert summary['counts'] == {'discovered': 3, 'executed': 3, 'passed': 3, 'failed': 0, 'skipped': 0}
    assert summary['required_cases'] == ['CASE-001', 'CASE-002']
    cases = json.loads((run / 'cases.json').read_text())['cases']
    assert [(item['job'], item['case_id'], item['actual']['passed']) for item in cases] == [(0, 'CASE-001', 1), (0, 'CASE-002', 1), (1, 'CASE-001', 1)]
    assert all(item['cleanup']['remaining_processes'] == 0 for item in summary['commands'])


def test_missing_test_entry(tmp_path):
    docs = tmp_path / 'docs/development'
    docs.mkdir(parents=True)
    (docs / 'task-register.json').write_text(json.dumps({'execution_order': ['PS-00-05']}))
    (docs / 'verification-profiles.json').write_text(json.dumps({'schema_version': 1, 'routes': {
        'PS-00-05': {'synthetic': {'case': 'CASE-005', 'tests': ['missing.py']}}}}))
    with pytest.raises(ValueError, match='missing'):
        runner.select_route(tmp_path, 'PS-00-05', 'synthetic')


@pytest.mark.parametrize('mapping',[{}, {'CASE-007':[]}, {'CASE-007':['fixture']}])
def test_required_multi_case_mapping_cannot_omit_responsibilities(tmp_path,mapping):
    docs=tmp_path/'docs/development';docs.mkdir(parents=True)
    (docs/'task-register.json').write_text(json.dumps({'execution_order':['PS-01-02'],
        'tasks':[{'id':'PS-01-02','cases':['CASE-007','CASE-010','CASE-024']}]}))
    (docs/'verification-profiles.json').write_text(json.dumps({'schema_version':1,'routes':{
        'PS-01-02':{'synthetic':{'case':'CASE-007','case_tests':mapping,'tests':['missing.py']}}}}))
    with pytest.raises(ValueError,match='CASE scenario mapping'):
        runner.select_route(tmp_path,'PS-01-02','synthetic')


@pytest.mark.parametrize('profile', ['real-readonly', 'real-game', 'ai-live', 'release'])
def test_missing_authorization(profile):
    with pytest.raises(ValueError, match='Authorization'):
        runner.authorization(None, 'PS-00-05', profile)


def test_expired_authorization(tmp_path):
    path = tmp_path / 'authorization.json'
    value = dict.fromkeys(runner.AUTH_FIELDS, [])
    value.update(task_id='PS-00-05', profile='real-game', resources=['synthetic-only'], operations=['read'], valid_until='2020-01-01T00:00:00Z')
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='expired'):
        runner.authorization(path, 'PS-00-05', 'real-game')


def test_workbench_readonly_route_requires_exact_read_scope(tmp_path):
    from scripts.verification.readonly import REQUIRED_FILES, TREES, EVIDENCE_FIELDS
    value = dict(task_id='PS-01-07', profile='real-readonly',
        resources=dict(installation_root=str(tmp_path / 'synthetic'), trees=list(TREES),
                       files=list(REQUIRED_FILES), logs=[], evidence_fields=list(EVIDENCE_FIELDS)),
        operations=['read'], network=False, upload=False,
        budget=dict(max_files=5000, max_bytes=1024**3, max_seconds=120),
        valid_until='2099-01-01T00:00:00Z', excluded_data=['saves', 'credentials', 'personal_data'])
    path = tmp_path / 'authorization.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    assert runner.authorization(path, 'PS-01-07', 'real-readonly')['required'] is True
    value['operations'].append('write')
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='NOT_ALLOWED'):
        runner.authorization(path, 'PS-01-07', 'real-readonly')


def test_environment_isolation_and_override_removal(tmp_path, monkeypatch):
    monkeypatch.setenv('PSMODMANAGER_DATA_ROOT', r'C:\real-save')
    monkeypatch.setenv('PSMM_GAME_ROOT', r'C:\real-game')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nonexistent')
    monkeypatch.setenv('NODE_OPTIONS', '--require untrusted.js')
    env = runner.isolated_environment(tmp_path, 'synthetic')
    for key in ('TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'):
        assert Path(env[key]).is_relative_to(tmp_path) and Path(env[key]).is_dir()
    assert 'PSMODMANAGER_DATA_ROOT' not in env and 'PSMM_GAME_ROOT' not in env
    assert 'PYTEST_ADDOPTS' not in env and 'NODE_OPTIONS' not in env


def test_outside_root_refused(tmp_path):
    with pytest.raises(ValueError):
        runner.safe_path(tmp_path, tmp_path.parent / 'outside')
    with pytest.raises(ValueError):
        runner.safe_path(tmp_path, tmp_path / '..' / 'outside')


def test_required_file_omission_never_passes(tmp_path):
    report = tmp_path / 'junit.xml'
    report.write_text('<testsuite><testcase name="ok" classname="other"/></testsuite>')
    result = {'started': True, 'exit_code': 0, 'timed_out': False, 'cleanup': {'remaining_processes': 0}}
    status, code, *_ = runner.assess(result, report, ['required.py'])
    assert status == 'FAIL' and code != 0


def test_missing_executable_exit_is_null(tmp_path):
    result = run_command([str(tmp_path / 'absent.exe')], cwd=runner.ROOT, env=os.environ.copy(), timeout=5,
                         stdout_path=tmp_path / 'out', stderr_path=tmp_path / 'err')
    status, code, *_ = runner.assess(result, tmp_path / 'missing.xml')
    assert status == 'BLOCKED' and code != 0 and result['exit_code'] is None


def test_real_cli_unknown_profile_preserves_blocked_evidence(tmp_path):
    result = run_command([sys.executable, '-B', '-m', 'scripts.verification.runner', '--task', 'PS-00-05', '--profile', 'unknown'],
                         cwd=runner.ROOT, env=os.environ.copy(), timeout=15, stdout_path=tmp_path / 'out', stderr_path=tmp_path / 'err')
    assert result['exit_code'] == 2
    summary_path = Path((tmp_path / 'out').read_text().strip().split('BLOCKED: ')[-1])
    summary = json.loads(summary_path.read_text())
    assert summary['verification_status'] == 'BLOCKED' and summary['exit_code'] is None
    assert summary['counts']['executed'] == 0 and summary['runner_exit_code'] != 0
    assert 'Unknown profile' in summary['blockers'][0]
    import jsonschema
    schema = json.loads((runner.ROOT / 'docs/development/verification-summary.schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(summary)


def test_powershell_missing_python_retains_schema_valid_evidence(tmp_path):
    import shutil
    import jsonschema
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(runner.ROOT / 'scripts/verify.ps1', scripts / 'verify.ps1')
    result = run_command(['pwsh', '-NoProfile', '-File', str(scripts / 'verify.ps1'), '-Task', 'PS-00-05', '-Profile', 'synthetic'],
                         cwd=tmp_path, env=os.environ.copy(), timeout=15, stdout_path=tmp_path / 'out', stderr_path=tmp_path / 'err')
    assert result['exit_code'] == 2
    reports = list((tmp_path / 'docs/development/evidence/PS-00-05').glob('*/summary.json'))
    assert len(reports) == 1
    summary = json.loads(reports[0].read_text())
    assert summary['exit_code'] is None and summary['counts']['executed'] == 0
    assert summary['verification_status'] == 'BLOCKED'
    schema = json.loads((runner.ROOT / 'docs/development/verification-summary.schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(summary)
    environment = json.loads(reports[0].with_name('environment.json').read_text())
    jsonschema.Draft202012Validator(schema['$defs']['environment']).validate(environment)
    for line in reports[0].with_name('artifacts.sha256').read_text().splitlines():
        expected, relative = line.split('  ', 1)
        assert runner.digest(reports[0].parent / relative) == expected
