"""Current-task real WebView rollback/history and preserved drift consumers."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case022_case034_actual_history_rollback_and_manual_drift_recheck(tmp_path_factory):
    assert sys.platform == 'win32', 'BLOCKED: Windows and installed WebView2 required'
    tmp_path = tmp_path_factory.mktemp('rh')
    (tmp_path / 'case-nodeid.json').write_text(json.dumps({'test': 'case022_case034_actual_history_rollback_and_manual_drift_recheck', 'fixture': str(tmp_path)}), encoding='utf-8')
    command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/rollback_history.py'), '--evidence-root', str(tmp_path)]
    (tmp_path / 'command.json').write_text(json.dumps(command), encoding='utf-8')
    result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=360,
        stdout_path=tmp_path / 'stdout.log', stderr_path=tmp_path / 'stderr.log')
    (tmp_path / 'process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['started'] and not result['timed_out'] and not result['error'], result
    assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
    assert result['exit_code'] == 0, f'Inspect {tmp_path}/process.json'
    report = json.loads((tmp_path / 'rollback-history.json').read_text(encoding='utf-8'))
    assert report['status'] == 'PASS' and report['app_exit_code'] == 0
    assert report['protected_before'] == report['protected_after'] and report['archives_before'] == report['archives_after']
    assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
    assert len(report['screenshots']) == 6 and len(report['checks']) == 9
    assert report['trusted_clicks'] and all(event['trusted'] for event in report['trusted_clicks'])
    assert report['trusted_keys'] and all(event['trusted'] for event in report['trusted_keys'])
    assert len(report['native_confirmations']) == 5 and all(event['accepted'] for event in report['native_confirmations'])
    assert report['drift_history']['recovery_required'] and not report['rechecked_history']['recovery_required']
    assert len(report['final_database']['entities']['deployment_generations']) == 5
    assert report['first_rollback']['history']['records'][0]['kind'] == report['final_history']['records'][0]['kind'] == 'rollback'
    assert report['final_history']['verification_status'] == 'not_run' and not report['final_history']['cleanup_available']
    commands = [row['command'] for row in report['requests']]
    assert commands.count('deployment.rollback') == 2 and commands.count('deployment.rollback.create') == 3
    assert commands.count('deployment.conflicts.recheck') == 2
