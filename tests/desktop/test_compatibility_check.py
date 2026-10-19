"""CASE-026 retired UI absence and production desktop compatibility API."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case026_retired_ui_absent_and_actual_api_candidate_readonly(tmp_path):
    assert sys.platform == 'win32', 'BLOCKED: Windows and installed WebView2 required'
    command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/compatibility_check.py'),
               '--evidence-root', str(tmp_path)]
    (tmp_path / 'command.json').write_text(json.dumps(command), encoding='utf-8')
    result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=240,
                         stdout_path=tmp_path / 'stdout.log', stderr_path=tmp_path / 'stderr.log')
    (tmp_path / 'process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['started'] and not result['timed_out'] and not result['error'], result
    assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
    assert result['exit_code'] == 0, f'Inspect {tmp_path}'
    report = json.loads((tmp_path / 'compatibility.json').read_text(encoding='utf-8'))
    assert report['status'] == 'PASS' and report['app_exit_code'] == 0, report
    assert report['source_before'] == report['source_after']
    assert report['archive_before'] == report['archive_after']
    assert report['preview_before'] == report['preview_after']
    assert report['retired_ui_absent'] is True
    assert report['conflict_report']['blocking'] is True
    assert report['candidate_report']['disable_preview']['candidate_blocking'] is False
    assert report['after_disable_report']['blocking'] is False
    assert report['command_counts']['compatibility.check'] == 3
    assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
    assert len(report['screenshots']) >= 3
    assert report['before']['deployment_generations'] == report['persisted']['deployment_generations'] == []
