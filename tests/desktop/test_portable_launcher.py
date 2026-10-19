"""CASE-046 consumer: launch the actual frozen executable outside the source tree."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import hashlib

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_case046_frozen_gui_default_data_and_reopen():
    if os.name != 'nt':
        pytest.skip('The portable consumer requires Windows x64 and an interactive desktop')
    configured = os.environ.get('PSMM_PORTABLE_ROOT')
    if not configured:
        pytest.skip('PSMM_PORTABLE_ROOT must select the actual built portable directory')
    package = Path(configured).resolve()
    assert (package / 'PSModManager.exe').is_file(), package
    run = Path(os.environ.get('PSMM_PORTABLE_EVIDENCE_ROOT') or
               (ROOT / '.preview/ps0502-run.txt').read_text(encoding='utf-8').strip()).resolve()
    assert run.is_relative_to(ROOT / 'docs/development/evidence/PS-05-02')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = run / ('desktop-' + stamp)
    out.mkdir(parents=True)
    if os.environ.get('PSMM_PORTABLE_GUI_IMPORT') == '1':
        from tests.unit.test_installation_layout import make_installation
        from tests.integration.test_content_catalog import compile_fixture
        game = make_installation(out/'合成游戏')
        for name in ('Mods','Plugins','UserLibs','UserData'):
            (game/name).mkdir()
        dll = compile_fixture(out/'compiler', 'PortableFrozenMod')
        fixture = dict(game=str(game), dll=str(dll), dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest())
        (out/'fixture.json').write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf-8')
    harness = ROOT / 'tests/desktop/harness/portable_launcher.py'
    with (out / 'stdout.log').open('xb') as stdout, (out / 'stderr.log').open('xb') as stderr:
        result = subprocess.run([sys.executable, '-I', '-B', str(harness),
                                 '--portable-root', str(package), '--evidence-root', str(out)],
                                cwd=ROOT, env=os.environ.copy(), stdout=stdout, stderr=stderr,
                                timeout=240, check=False)
    report = json.loads((out / 'result.json').read_text(encoding='utf-8'))
    assert result.returncode == 0, f'Inspect {out}/result.json and stderr.log'
    assert report['status'] == 'PASS', report
    assert report['package_unchanged']
    assert len(report['launches']) == 2
    assert all(phase['exit_code'] == 0 and phase['remaining_processes'] == 0
               for phase in report['launches'])
    assert all(not phase['forced_owned_cleanup'] and phase['cleanup_remaining_processes']==0
               for phase in report['launches'])
    assert report['database']['application_id'] == 0x50534D4D
    assert report['database']['user_version'] == 7
    assert report['database']['quick_check'] == 'ok'
    expected = int(os.environ.get('PSMM_PORTABLE_GUI_IMPORT') == '1')
    assert report['database']['installations'] == expected
    assert report['database']['mod_releases'] == expected
    if expected:
        assert report['synthetic_game_unchanged']
        assert report['owned_dll_unchanged']
        assert report['imported_artifact_matches']
