"""CASE-023 production WebView import and persistent library, synthetic only."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def test_case023_actual_import_preview_commit_and_reopen(tmp_path):
    assert sys.platform == 'win32', 'BLOCKED: Windows and installed WebView2 required'
    reports = []
    for phase in ('first', 'reopen'):
        command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/import_preview.py'),
                   '--evidence-root', str(tmp_path), '--phase', phase]
        (tmp_path / f'{phase}-command.json').write_text(json.dumps(command), encoding='utf-8')
        result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=180,
                             stdout_path=tmp_path / f'{phase}-stdout.log',
                             stderr_path=tmp_path / f'{phase}-stderr.log')
        (tmp_path / f'{phase}-process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        assert result['exit_code'] == 0, f'Inspect {tmp_path}'
        report = json.loads((tmp_path / f'{phase}-import.json').read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['app_exit_code'] == 0, report
        assert report['source_before'] == report['source_after']
        assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
        assert len(report['screenshots']) >= 2
        assert report['persisted']['file_count'] == 2
        assert report['persisted']['profiles'] == report['persisted']['deployments'] == 0
        reports.append(report)
    assert reports[0]['persisted'] == reports[1]['persisted']
    assert reports[0]['source_before'] == reports[1]['source_after']
