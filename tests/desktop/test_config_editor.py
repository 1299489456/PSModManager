"""CASE-035 selected configuration's actual Vue/bridge/native-file consumer."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case035_actual_webview_save_and_new_process_reopen(tmp_path):
    for phase in ('first', 'reopen'):
        command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/config_editor.py'),
                   '--evidence-root', str(tmp_path), '--phase', phase]
        result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=100,
            stdout_path=tmp_path / (phase + '-stdout.log'), stderr_path=tmp_path / (phase + '-stderr.log'))
        (tmp_path / (phase + '-process.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['exit_code'] == 0, f'Inspect {tmp_path}/{phase}-config-editor.json'
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        report = json.loads((tmp_path / (phase + '-config-editor.json')).read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['app_exit_code'] == 0
