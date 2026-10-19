"""CASE-037 ordinary buttons, physical files and four actual desktop starts."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case037_actual_webview_direct_actions_and_reopening(tmp_path):
    phases = ('first', 'reopen_enabled', 'reopen_disabled', 'reopen_uninstalled')
    for phase in phases:
        result = run_command([sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/deployment_workbench.py'),
            '--evidence-root', str(tmp_path), '--phase', phase], cwd=ROOT, env=os.environ.copy(), timeout=150,
            stdout_path=tmp_path / (phase + '-stdout.log'), stderr_path=tmp_path / (phase + '-stderr.log'))
        (tmp_path / (phase + '-process.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['exit_code'] == 0, f'Inspect {tmp_path}/{phase}-direct-mods.json'
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        report = json.loads((tmp_path / (phase + '-direct-mods.json')).read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['app_exit_code'] == 0
        assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
