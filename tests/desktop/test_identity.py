"""CASE-002: repeat the real independent desktop entry in an isolated root."""
import hashlib
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command


ROOT = Path(__file__).resolve().parents[2]


def test_real_identity_first_and_second_start(tmp_path):
    assert sys.platform == 'win32', 'BLOCKED: actual Windows/WebView2 required'
    assert tmp_path.resolve().is_relative_to(ROOT / 'docs/development/evidence/PS-00-02'), \
        'Use the PS-00-02 desktop-local runner to provide its isolated evidence root'
    reports = []
    stable_config = None
    for phase in ('first', 'second'):
        command = [sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/identity_smoke.py'),
                   '--evidence-root', str(tmp_path), '--phase', phase]
        result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=100,
                             stdout_path=tmp_path / f'{phase}-stdout.log',
                             stderr_path=tmp_path / f'{phase}-stderr.log')
        (tmp_path / f'{phase}-process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'], result
        assert result['exit_code'] == 0, result
        assert result['cleanup']['job_closed'] is True, result
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        report = json.loads((tmp_path / f'{phase}-desktop.json').read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['exit_code'] == 0, report.get('error')
        assert report['legacy_access_attempts'] == []
        sentinel = tmp_path / 'legacy-local/RimCrow/config.json'
        assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == report['legacy_before_sha256']
        assert report['process_app_user_model_id'] == 'PSModManager.Desktop'
        assert report['chromium_document_offline'] is True
        assert report['identity']['keyring_service'] == report['window_title'] == 'PSModManager'
        assert report['identity']['update_source'] is None
        assert report['owned_files_after_exit'].keys() >= {'config.json', 'psmodmanager.sqlite3', 'logs/psmodmanager.log'}
        if stable_config is None:
            stable_config = report['owned_files_after_exit']['config.json']
        assert report['owned_files_after_exit']['config.json'] == stable_config
        reports.append(report)
    assert reports[0]['identity']['data_root'] == reports[1]['identity']['data_root']
    assert reports[0]['webview_profile'] == reports[1]['webview_profile']
