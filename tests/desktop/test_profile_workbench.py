"""CASE-027 actual M2 library, drag editing, restart and adoption consumers."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT = Path(__file__).resolve().parents[2]


def run_phase(root, phase, *, gui_host=False):
    assert sys.platform == 'win32', 'BLOCKED: Windows and installed WebView2 required'
    executable = Path(sys.executable).with_name('pythonw.exe') if gui_host else Path(sys.executable)
    assert executable.is_file(), f'BLOCKED: desktop host is missing: {executable}'
    command = [str(executable), '-I', '-B', str(ROOT / 'tests/desktop/harness/profile_workbench.py'),
               '--evidence-root', str(root), '--phase', phase]
    (root / f'{phase}-command.json').write_text(json.dumps(command), encoding='utf-8')
    result = run_command(command, cwd=ROOT, env=os.environ.copy(), timeout=300,
                         stdout_path=root / f'{phase}-stdout.log', stderr_path=root / f'{phase}-stderr.log')
    (root / f'{phase}-process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert result['started'] and not result['timed_out'] and not result['error'], result
    assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
    assert result['exit_code'] == 0, f'Inspect {root}'
    assert not (root / f'{phase}-stderr.log').read_text(encoding='utf-8').strip(), f'Unexpected stderr: {root}'
    report = json.loads((root / f'{phase}-workbench.json').read_text(encoding='utf-8'))
    assert report['status'] == 'PASS' and report['app_exit_code'] == 0, report
    assert report['source_before'] == report['source_after']
    assert report['inputs_before'] == report['inputs_after']
    assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
    assert report['callback_lifecycle']['closing'] and report['callback_lifecycle']['active'] == 0
    assert len(report['screenshots']) >= 3
    assert report['before']['deployment_generations'] == report['persisted']['deployment_generations'] == []
    assert Path(report['host_executable']).name.lower() == executable.name.lower()
    return report


def test_case027_actual_drag_library_and_manifest_roundtrip(tmp_path):
    first = run_phase(tmp_path, 'first', gui_host=True)
    reopened = run_phase(tmp_path, 'reopen', gui_host=True)
    assert first['persisted'] == reopened['before']
    assert first['source_before'] == reopened['source_after']
    assert len(first['drag_operations']) == 5
    assert [row['count'] for row in first['drag_operations']] == [1, 1, 2, 2, 3]
    assert len(first['reorder_operations']) == 1
    assert first['final_disabled_order'] == reopened['reopened_disabled_order']
    assert len(first['added_rows']) == 3 and all(row['picked'] and row['visible'] for row in first['added_rows'])
    assert first['editor_structure']['forbidden'] == 0
    assert first['process_id'] != reopened['process_id']
    assert first['theme_persistence']['theme_id'] == reopened['theme_restored']['theme_id'] == 'win11-mica'
    assert first['theme_persistence']['config_sha256'] == reopened['theme_restored']['config_sha256']
    assert reopened['theme_restored']['before_any_theme_selection']
    assert len(reopened['manifest_preview_controls']) == 2


def test_case027_actual_explicit_adoption_to_library_consumer(tmp_path):
    # The shipped interactive launcher uses pythonw. Console-only runs cannot
    # detect worker startup failures caused by its GUI process environment.
    report = run_phase(tmp_path, 'adoption', gui_host=True)
    assert len(report['persisted']['release_locks']) == 1
    assert report['persisted']['profiles'] == report['persisted']['profile_drafts'] == []
