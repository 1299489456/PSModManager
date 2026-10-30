"""Real save/approval/launch API on owned inert files, for source or frozen EXE.

No installed game libraries are needed. Only our small waiting process probe is
executed; candidate Mod DLLs are compiled and optionally parsed, never executed.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


spec = importlib.util.spec_from_file_location('owned_security_cycle', Path(__file__).with_name('security_cycle.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ok, rpc = base.ok, base.rpc

PROBE = '''using System; using System.IO; using System.Threading;
public static class Game { public static void Main() {
  File.AppendAllText("launch-count.txt", "started\\n");
  for (int i=0;i<1800;i++) { if(File.Exists("exit-request.txt"))return; Thread.Sleep(50); }
} }'''


def prepare(out):
    context, fixture = base.prepare(out)
    game = Path(fixture['game'])
    probe = base.compile_fixture(Path(out)/'probe-build', 'Probably Stolen', PROBE, executable=True)
    shutil.copyfile(probe, game / 'Probably Stolen.exe')
    fixture['enabled_ids'] = ['security.demo', 'security.dependent', 'security.workshop']
    fixture['scanner_source'] = 'owned_inert_fixtures_only'
    return context, fixture


def stopped(context, fixture):
    game = Path(fixture['game'])
    (game/'exit-request.txt').write_text('stop', encoding='utf-8')
    deadline = time.monotonic()+10
    while time.monotonic()<deadline:
        value = ok(context, 'game.status', installation_id=fixture['installation_id'])
        if value['state'] in {'not_started', 'exited', 'stopped'}:
            return value
        time.sleep(.05)
    raise AssertionError('Owned probe did not exit')


def launch_probe(context, fixture):
    request = Path(fixture['game'])/'exit-request.txt'
    if request.exists():
        request.unlink()
    value = ok(context, 'game.launch', installation_id=fixture['installation_id'])
    assert value['state'] == 'running', value
    return value


def run(out):
    context, fixture = prepare(out)
    identity = fixture['installation_id']
    game = Path(fixture['game'])
    result = dict(status='FAIL', checks=[], timings={},
        scope='Production APIs; ordinary approval without scanning, explicit LPAC manual scans, inert DLLs and owned waiting process; no real game/Steam operations')
    try:
        import backend.mod_security.service as safety
        import backend.native_mods.service as native
        result['product_modules'] = [safety.__file__, native.__file__]
        counts = base.scanner_spy(context)
        assert ok(context, 'security.detail', installation_id=identity, id='security.demo')['report'] is None
        snap = ok(context, 'native.snapshot', installation_id=identity)
        start = time.monotonic()
        saved = ok(context, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=fixture['enabled_ids'])
        result['timings']['first_save_seconds'] = round(time.monotonic()-start, 3)
        assert all(m['approval']['state']=='approved' and m['approval']['approval_source']=='manager'
                   and m['approval']['approval_basis']=='user_enable' and m['approval']['detected'] is None for m in saved['mods'])
        assert saved['runtime']['writable']
        assert not any(counts.values())
        assert ok(context, 'security.detail', installation_id=identity, id='security.demo')['report'] is None
        result['checks'].append('Public save with no reports approves all three selections as user_enable/detected=null, with zero scanner calls')
        # An unchanged list must still restore missing approval from an old version.
        state = Path(fixture['user'])/'SaveFile.es3'
        doc = json.loads(state.read_text('utf-8'))
        trust = json.loads(doc['modTrust']['value']); trust['records'] = []
        doc['modTrust']['value'] = json.dumps(trust)
        state.write_text(json.dumps(doc), encoding='utf-8')
        snap = ok(context, 'native.snapshot', installation_id=identity)
        start = time.monotonic()
        saved = ok(context, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=fixture['enabled_ids'])
        result['timings']['unchanged_missing_approval_save_seconds'] = round(time.monotonic()-start, 3)
        assert all(m['approval']['state']=='approved' for m in saved['mods'])
        assert not any(counts.values())
        result['checks'].append('Unchanged selection reauthorizes missing legacy approval without inspection')
        launch_probe(context, fixture)
        assert not any(counts.values())
        result['checks'].append('Ordinary launch without a report starts only the owned probe, with zero scanner calls')
        stopped(context, fixture)
        (game/'exit-request.txt').unlink()
        external = subprocess.Popen([str(game/'Probably Stolen.exe')], cwd=str(game), creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            observed = ok(context, 'game.status', installation_id=identity)
            assert observed['state']=='running' and observed['pid']==external.pid
            result['checks'].append('External restart after recorded exit is observed as running')
        finally:
            stopped(context, fixture); external.wait(timeout=5)
        # Establish a baseline only through an explicit full manual inspection.
        baseline = base.scan(context, identity)
        assert all(not item['blocking'] for item in baseline['items'])
        observed = dict(counts)
        assert counts['inspect_security'] > 0 and counts['inspect_approval'] == counts['approval_identity'] == 0
        shutil.copyfile(fixture['risk_dll'], Path(fixture['target'])/'Main.dll')
        (Path(fixture['target'])/'new-content.txt').write_text('manual-diff fixture resource', encoding='utf-8')
        snap = ok(context, 'native.snapshot', installation_id=identity)
        saved = ok(context, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=fixture['enabled_ids'])
        assert counts == observed and all(m['approval']['detected'] is None for m in saved['mods'])
        result['checks'].append('Replacing DLL bytes does not schedule inspection: ordinary save succeeds with scanner counts unchanged')
        base.scan(context, identity, ['security.demo'])
        detail = ok(context, 'security.detail', installation_id=identity, id='security.demo')
        report = detail['report']
        assert report['verdict'] == 'high_risk' and not detail['item']['blocking']
        assert [r['path'] for r in report['diff']['modified']] == ['Main.dll']
        assert [r['path'] for r in report['diff']['added']] == ['new-content.txt']
        assert ok(context, 'security.status', installation_id=identity)['blocking_reasons'] == []
        observed = dict(counts)
        snap = ok(context, 'native.snapshot', installation_id=identity)
        ok(context, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=fixture['enabled_ids'])
        launch_probe(context, fixture); stopped(context, fixture)
        assert counts == observed
        result['checks'].append('Selected manual scan reports high risk and file differences, but does not block subsequent save/launch or trigger further scans')
        preview = ok(context, 'security.dispose.preview', installation_id=identity, id='security.demo', mode='quarantine')
        assert preview['affected_ids'] == ['security.demo', 'security.dependent']
        disposed = ok(context, 'security.dispose', installation_id=identity, preview_id=preview['preview_id'])
        assert not Path(fixture['target']).exists()
        snap = ok(context, 'native.snapshot', installation_id=identity)
        blocked = rpc(context, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=fixture['enabled_ids'])
        assert not blocked['ok'] and blocked['error']['code'] == 'SECURITY_QUARANTINED', blocked
        # Simulate an external editor reintroducing quarantined intent in this
        # owned state file; the launch hook must still honor the explicit action.
        doc = json.loads(state.read_text('utf-8')); doc['modOrder']['value'] = fixture['enabled_ids']
        state.write_text(json.dumps(doc), encoding='utf-8')
        blocked = rpc(context, 'game.launch', installation_id=identity)
        assert not blocked['ok'] and blocked['error']['code'] == 'SECURITY_QUARANTINED', blocked
        restored = ok(context, 'security.restore', installation_id=identity, record_id=disposed['record']['id'])
        assert restored['record']['status'] == 'restored' and Path(fixture['target']).is_dir()
        snap = ok(context, 'native.snapshot', installation_id=identity)
        assert 'security.demo' not in snap['enabled_ids'] and 'security.dependent' not in snap['enabled_ids']
        target = next(m for m in snap['mods'] if m['id'] == 'security.demo')
        assert target['approval']['state'] != 'approved'
        assert ok(context, 'security.detail', installation_id=identity, id='security.demo')['report'] is None
        ok(context, 'native.save', installation_id=identity, expected_revision=snap['revision'], enabled_ids=fixture['enabled_ids'])
        launch_probe(context, fixture); stopped(context, fixture)
        assert counts == observed
        result['checks'].append('Only explicit quarantine blocks save/launch; restore stays disabled, then explicit save/launch works without rescan')
        result['scanner_calls'] = dict(counts)
        result['high_risk_report'] = report
        result['status']='PASS'
    finally:
        stopped(context, fixture)
        context.close()
        Path(out, 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__=='__main__':
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    parser=argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    assert args.out.resolve().is_relative_to(root/'.preview')
    print(json.dumps(run(args.out), ensure_ascii=False))
