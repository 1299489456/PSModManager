"""Current-task real WebView executor and startup-recovery consumer evidence."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case030_case031_case032_actual_apply_cancel_and_startup_recovery(tmp_path):
    assert sys.platform == 'win32', 'BLOCKED: Windows and installed WebView2 required'
    for phase, expected in [('first', 73), ('reopen', 0)]:
        command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/deployment_execution.py'), '--evidence-root', str(tmp_path), '--phase', phase]
        (tmp_path / (phase + '-command.json')).write_text(json.dumps(command), encoding='utf-8')
        result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=240,
            stdout_path=tmp_path / (phase + '-stdout.log'), stderr_path=tmp_path / (phase + '-stderr.log'))
        (tmp_path / (phase + '-process.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        assert result['exit_code'] == expected, f'Inspect {tmp_path}/{phase}-process.json'
    first = json.loads((tmp_path / 'first-deployment-execution.json').read_text(encoding='utf-8'))
    reopened = json.loads((tmp_path / 'reopen-deployment-execution.json').read_text(encoding='utf-8'))
    assert first['status'] == 'CRASH_EXPECTED' and reopened['status'] == 'PASS' and reopened['app_exit_code'] == 0
    assert first['protected_before'] == first['protected_after'] and reopened['protected_before'] == reopened['protected_after']
    assert first['archives_before'] == first['archives_after'] and reopened['archives_before'] == reopened['archives_after']
    assert reopened['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
    assert len(first['screenshots']) == 4 and len(reopened['screenshots']) == 1
    assert all(row['trusted'] for row in first['trusted_clicks'] + reopened['trusted_clicks'])
    assert len(first['native_confirmations']) == 3 and all(row['accepted'] for row in first['native_confirmations'])
    assert first['cancel_boundary']['game'] != first['committed_disk']
    assert first['crash_boundary']['game'] != first['committed_disk']
    assert first['cancelled_status']['transaction']['state'] == 'rolled_back'
    assert reopened['startup_status']['startup_recovery']['status'] == 'recovered'
    assert first['committed_status']['current_generation'] == reopened['startup_status']['current_generation']
    assert first['committed_status']['verification_status'] == reopened['startup_status']['verification_status'] == 'not_run'
