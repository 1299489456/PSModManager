"""Current CASE-045 basic-management consumer, isolated actual WebView."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from scripts.verification.processes import run_command
from tests.integration.harness.transaction_recovery import tree
from tests.performance.basic_fixture import prepare_basic_fixture

ROOT = Path(__file__).resolve().parents[2]


def test_case045_current_basic_management_keyboard_chinese_path_and_reopen(tmp_path_factory):
    out = tmp_path_factory.mktemp('中文')
    started = time.monotonic()
    fixture, metadata, files = prepare_basic_fixture(out)
    fixture.close()
    metadata['setup_ms'] = round((time.monotonic()-started)*1000, 2)
    metadata['files'] = {name:dict(size=len(value), sha256=hashlib.sha256(value).hexdigest())
                         for name,value in files.items()}
    (out/'fixture.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    source = ROOT/'tests/desktop/harness/basic_accessibility.py'
    for phase in ('first', 'reopen'):
        result = run_command([sys.executable, '-I', '-B', str(source), '--evidence-root',str(out),
            '--phase',phase], cwd=ROOT, env=os.environ.copy(), timeout=240,
            stdout_path=out/(phase+'-stdout.log'), stderr_path=out/(phase+'-stderr.log'))
        (out/(phase+'-process.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['exit_code']==0, f'Inspect {out}/{phase}.json and stderr'
        assert result['cleanup']['remaining_processes']==0 and not result['cleanup'].get('error'), result
        report = json.loads((out/(phase+'.json')).read_text(encoding='utf-8'))
        assert report['status']=='PASS' and report['app_exit_code']==0, report
        assert report['cleanup']==dict(active_workers=0, subscriptions=0), report
        assert all(row['command'].split('.')[0] not in {'ai','plan','profile','assistant'}
                   and row['command']!='game.launch' for row in report['requests'])
        if phase=='first':
            assert report['pagination_counts']==[20,20,20]
            assert report['imported']['file_count']==3
            assert [row['action'] for row in report['operations']]==['install-conflict','install','disable','enable']
            assert all(row['frame_count']>0 and row['busy_observed'] for row in report['operations'])
            assert any(row['key']=='Enter' and row['trusted'] for row in report['trusted_keys'])
            assert any(row['key']==' ' and row['trusted'] for row in report['trusted_keys'])
            assert any(row['key']=='Escape' and row['trusted'] for row in report['trusted_keys'])
            assert len(report['window_layouts'])==2
        else:
            assert report['reopened_state']=='enabled'
            assert [row['action'] for row in report['operations']]==['uninstall']
    # Uninstall removes this Mod's files; harmless empty target directories are
    # outside the basic-management promise. Every pre-existing file must remain.
    actual=tree(Path(metadata['game']))
    assert {key:value for key,value in actual.items() if value!='<directory>'}=={
        key:value for key,value in metadata['baseline'].items() if value!='<directory>'}
    assert tree(Path(metadata['import_source']))==metadata['source_baseline']
