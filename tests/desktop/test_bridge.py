"""CASE-004: real Windows/WebView2, never a browser or bridge mock."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_real_webview_bridge_and_reopen(tmp_path):
    assert sys.platform == 'win32', 'BLOCKED: Windows/WebView2 desktop required'
    runs = []
    for phase in ('first', 'reopen'):
        command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/bridge_smoke.py'),
                   '--evidence-root', str(tmp_path), '--phase', phase]
        (tmp_path / f'{phase}-command.json').write_text(json.dumps(command), encoding='utf-8')
        result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=100,
                             stdout_path=tmp_path / f'{phase}-stdout.log', stderr_path=tmp_path / f'{phase}-stderr.log')
        (tmp_path / f'{phase}-runner-cleanup.json').write_text(json.dumps(result), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        code = result['exit_code']
        (tmp_path / f'{phase}-exit.json').write_text(json.dumps({'exit_code': code}), encoding='utf-8')
        assert code == 0, f'{phase}: desktop failed with {code}; inspect {tmp_path}'
        report = json.loads((tmp_path / f'{phase}-desktop.json').read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['app_exit_code'] == 0
        stderr = (tmp_path / f'{phase}-stderr.log').read_text(encoding='utf-8')
        assert '_returnValuesCallbacks' not in stderr, 'Obsolete JS callback delivery must not log an exception'
        assert report['cleanup']['active_workers'] == report['cleanup']['subscriptions'] == 0
        import psutil
        remaining = report.get('owned_children', [])
        for _ in range(50):
            alive = []
            for item in remaining:
                try:
                    child = psutil.Process(item['pid'])
                    if child.create_time() == item['created_at'] and child.is_running():
                        alive.append(item)
                except psutil.NoSuchProcess:
                    pass
            remaining = alive
            if not remaining:
                break
            time.sleep(.1)
        (tmp_path / f'{phase}-process-cleanup.json').write_text(json.dumps({'remaining_owned_processes': remaining}), encoding='utf-8')
        assert remaining == [], 'Owned WebView2 descendants remained after desktop exit'
        runs.append(report)
    assert runs[0]['data_root'] == runs[1]['data_root']
    assert runs[0]['initial_subscriptions'] == runs[1]['initial_subscriptions'] == 0
