"""The exact live acceptance WebView workflow, using only a self-authored tree."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command

ROOT=Path(__file__).resolve().parents[2]


def test_live_harness_with_synthetic_installation(tmp_path):
    command=[sys.executable,'-I','-B',str(ROOT/'tests/desktop/harness/live_readonly.py'),
             '--mode','synthetic','--evidence-root',str(tmp_path)]
    result=run_command(command,cwd=ROOT,env=os.environ.copy(),timeout=240,
                       stdout_path=tmp_path/'stdout.log',stderr_path=tmp_path/'stderr.log')
    (tmp_path/'process.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    assert result['started'] and result['exit_code']==0 and not result['timed_out'],result
    report=json.loads((tmp_path/'live-readonly.json').read_text(encoding='utf-8'))
    assert report['status']=='PASS' and report['ui_real_readonly']=='passed'
    assert report['source_before']==report['source_after'] and not report['denied_operations']
    assert report['cleanup']==dict(active_workers=0,subscriptions=0)
    assert len(report['screenshots'])==4
