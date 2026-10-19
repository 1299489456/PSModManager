"""CASE-036 desktop-local actual button/bridge/native process and app reopen."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case036_actual_webview_launch_exit_error_and_reopen(tmp_path):
    for phase in ('first', 'reopen'):
        result = run_command([sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/game_launch.py'),
            '--evidence-root', str(tmp_path), '--phase', phase], cwd=ROOT, env=os.environ.copy(), timeout=100,
            stdout_path=tmp_path / (phase + '-stdout.log'), stderr_path=tmp_path / (phase + '-stderr.log'))
        (tmp_path / (phase + '-process.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['exit_code'] == 0, f'Inspect {tmp_path}/{phase}-game-launch.json'
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        report = json.loads((tmp_path / (phase + '-game-launch.json')).read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['app_exit_code'] == 0
