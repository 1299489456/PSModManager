"""CASE-006 actual bundled selector -> command bridge -> SQLite consumer."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_real_installation_selector(tmp_path):
    assert sys.platform == 'win32', 'BLOCKED: Windows/WebView2 required'
    command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/installation_smoke.py'),
               '--evidence-root', str(tmp_path)]
    (tmp_path / 'desktop-command.json').write_text(json.dumps(command), encoding='utf-8')
    result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=100,
                         stdout_path=tmp_path / 'desktop-stdout.log', stderr_path=tmp_path / 'desktop-stderr.log')
    (tmp_path / 'desktop-process-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['started'] and not result['timed_out'] and not result['error'], result
    assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
    assert result['exit_code'] == 0, f'inspect {tmp_path}'
    report = json.loads((tmp_path / 'installation-desktop.json').read_text(encoding='utf-8'))
    assert report['status'] == 'PASS' and report['app_exit_code'] == 0
    assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
    assert report['installation_rows'] == report['fingerprint_rows'] == 1
    assert report['source_before'] == report['source_after']
    assert len(report['checks']) >= 5
