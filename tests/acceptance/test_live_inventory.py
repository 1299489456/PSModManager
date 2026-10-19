"""CASE-021 actual product WebView, explicit source grant and immutable baseline."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from scripts.verification.processes import run_command
from scripts.verification.readonly import (load_authorization, load_historical_baseline,
    snapshot_authorized, compare_sets, parse_loader_hashes, _check_path)


def test_authorized_live_inventory_matches_historical_scope():
    authorization=load_authorization()
    project=Path(__file__).resolve().parents[2]
    run=Path(os.environ['PSMM_VERIFY_RUN_ROOT'])
    if not run.is_absolute() or not run.is_relative_to(project/'docs/development/evidence/PS-01-07'):
        raise ValueError('READONLY_ISOLATED_RUN_ROOT_REQUIRED')
    _check_path(run)
    resource=authorization['resources'];root=Path(resource['installation_root'])
    if root==run or root in run.parents or run in root.parents:
        raise ValueError('READONLY_ROOT_OVERLAP')
    baseline=load_historical_baseline(project)
    before=snapshot_authorized(authorization)
    out=run/'artifacts/live-workbench';out.mkdir(parents=True,exist_ok=True)
    report=dict(historical_date=baseline['historical_date'],baseline_sources=baseline['source_hashes'],
                baseline_gaps=list(baseline['gaps']),before=before,game_started=False,network=False,upload=False,
                ui_real_readonly='not_run',runtime_registration='not_run',
                observation='Actual WebView current scan versus fixed 2026-09-24 historical log; no game launch')
    try:
        command=[sys.executable,'-I','-B',str(project/'tests/desktop/harness/live_readonly.py'),
                 '--mode','real-readonly','--evidence-root',str(out)]
        process=run_command(command,cwd=project,env=os.environ.copy(),timeout=240,
                            stdout_path=out/'stdout.log',stderr_path=out/'stderr.log')
        report['process']=process
        ui_path=out/'live-readonly.json'
        if ui_path.is_file():
            ui=json.loads(ui_path.read_text(encoding='utf-8'))
            report.update(ui_real_readonly=ui['ui_real_readonly'],ui_status=ui['status'],
                          cleanup=ui.get('cleanup'),retained_dlls=ui.get('retained_dlls',[]),
                          denied_operations=ui.get('denied_operations',[]),
                          current_fingerprint=ui.get('current_fingerprint'),
                          actual_reads=ui.get('observed_reads'),
                          special_file_details=ui.get('product_evidence',{}).get('inventory.detail',[]),
                          diagnostics=ui.get('product_evidence',{}).get('diagnostics.explain',[]),
                          evidence_windows=ui.get('product_evidence',{}).get('evidence.read',[]))
        assert process['started'] and process['exit_code']==0 and not process['timed_out'],'READONLY_WEBVIEW_PROCESS_FAILED'
        assert process['cleanup']['remaining_processes']==0,'READONLY_PROCESS_CLEANUP_FAILED'
        assert report['ui_real_readonly']=='passed' and report['ui_status']=='PASS','READONLY_REAL_WORKBENCH_UI_NOT_PASSED'
        database=out/'isolated-data/psmodmanager.sqlite3'
        db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
        try:
            pointer=db.execute('SELECT installation_id,latest_complete FROM inventory_current').fetchone()
            assert pointer and pointer[1],'READONLY_COMPLETE_INVENTORY_MISSING'
            rows=[json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM scan_entries WHERE installation_id=? AND generation=?',pointer)]
        finally:db.close()
        physical=[r for r in rows if r['relative_path'].lower().endswith('.dll') and r.get('presence')!='missing']
        predicted=[r for r in physical if r['prediction']['status']=='candidate']
        report.update(physical_count=len(physical),predicted_count=len(predicted),
                      physical_comparison=compare_sets(baseline['physical'],physical),
                      historical_reported_comparison=compare_sets(baseline['loaded_reported'],predicted),
                      inventory=[{key:r.get(key) for key in ('relative_path','sha256','size','status','assembly_name','assembly_version','prediction')} for r in physical])
        for name in [n for n in resource['files']+resource['logs'] if n.endswith('.log')]:
            record=next((r for r in before['files'] if r['relative_path']==name),None)
            if record and record['sha256']==baseline['historical_log_sha256']:
                path=_check_path(root/name)
                if path.stat().st_size>8*1024**2:continue
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise AssertionError('READONLY_HISTORICAL_LOG_DRIFT')
                loaded=parse_loader_hashes(raw.decode('utf-8-sig'))
                report['historical_raw_comparison']=compare_sets(baseline['loaded_reported'],loaded)
                report['historical_raw_count']=len(loaded)
                report['historical_raw_log_sha256']=record['sha256']
                if len(loaded)==43 and not any(report['historical_raw_comparison'].values()):report['baseline_gaps']=[]
    finally:
        try:
            report['after']=snapshot_authorized(authorization)
            report['unchanged']=report['before']==report['after']
        except Exception as exc:
            report['after_error']=type(exc).__name__;report['unchanged']=False
        (run/'artifacts/live-inventory-readonly.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert report['unchanged'],'READONLY_REGISTERED_TREE_CHANGED'
    assert not report.get('retained_dlls') and not report.get('denied_operations'),'READONLY_SCOPE_OR_CLEANUP_FAILED'
    assert not any(report['physical_comparison'].values()),'READONLY_PHYSICAL_BASELINE_DRIFT'
    assert not any(report['historical_reported_comparison'].values()),'READONLY_PREDICTED_BASELINE_DRIFT'
    assert not report['baseline_gaps'],'READONLY_HISTORICAL_RAW_BASELINE_INCOMPLETE'
