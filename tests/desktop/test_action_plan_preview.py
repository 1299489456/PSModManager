"""CASE-029 current-task actual WebView preview and native approval consumer."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case029_actual_preview_pages_and_native_approval(tmp_path):
    assert sys.platform == 'win32', 'BLOCKED: Windows and installed WebView2 required'
    command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/action_plan_preview.py'), '--evidence-root', str(tmp_path)]
    (tmp_path / 'command.json').write_text(json.dumps(command), encoding='utf-8')
    result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=240,
        stdout_path=tmp_path / 'stdout.log', stderr_path=tmp_path / 'stderr.log')
    (tmp_path / 'process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['started'] and not result['timed_out'] and not result['error'], result
    assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
    assert result['exit_code'] == 0, f'Inspect {tmp_path}'
    report = json.loads((tmp_path / 'action-plan-preview.json').read_text(encoding='utf-8'))
    assert report['status'] == 'PASS' and report['app_exit_code'] == 0, report
    assert report['source_before'] == report['source_after']
    assert report['archive_before'] == report['archive_after']
    assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
    assert len(report['screenshots']) == 5
    assert [row['accepted'] for row in report['native_confirmations']] == [False, True]
    assert report['after_cancel']['approvals'] == 0 and report['after_approve']['approvals'] == 1
    assert report['after_approve']['deployment_generations'] == report['after_approve']['transactions'] == 0
    assert len(set(report['first_page'] + report['second_page'])) == 24
    assert report['command_counts']['plan.create'] == 3
    assert report['command_counts']['plan.inspect'] == 1
    assert report['command_counts']['plan.approve'] == 2
    assert 'PLAN_UNMANAGED_CONFLICT' in report['conflict_error']
    assert report['after_conflict']['action_plans'] == 2 and report['after_conflict']['approvals'] == 1
    assert all(row['trusted'] for row in report['trusted_clicks'])
