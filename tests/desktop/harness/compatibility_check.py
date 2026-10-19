"""Retired check UI is absent; real WebView dispatch still verifies read-only API."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=['first'], default='first')
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert any(out.is_relative_to(ROOT / 'docs/development/evidence' / task) for task in ('PS-02-04', 'PS-02-05'))
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
for key, name in [('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'), ('TEMP', 'temp'), ('TMP', 'temp')]:
    os.environ[key] = str(out / name)
    (out / name).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import webview
import win32gui
import win32ui
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi
from tests.unit.test_installation_layout import make_installation, hashes

game = out / '合成安装 %'
if args.phase == 'first':
    make_installation(game)
    (game / 'UserData').mkdir()
    (game / 'UserData' / 'synthetic-save.bin').write_bytes(b'inert save sentinel')
    for index in range(2):
        with zipfile.ZipFile(out / f'资源包 {index} %.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Mods/共享图片 %.png', f'synthetic resource {index}'.encode())
data = out / 'isolated-data'
report = dict(status='FAIL', phase=args.phase, requests=[], checks=[], screenshots=[], data_root=str(data),
              source_before=hashes(game), archive_before={},
              scope='Production WebView dispatch/API and real services with retired check UI absent; native pickers return isolated fixture paths; no future planner verification')
report['archive_before'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.zip')}
contexts, managers, windows = [], [], []
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    api = DesktopApi(context)
    dispatch = api.dispatch

    def observed(request):
        response = dispatch(request)
        report['requests'].append(dict(command=request.get('command'), ok=response['ok'],
                                       error_code=(response.get('error') or {}).get('code'),
                                       compatibility_payload=request.get('payload') if request.get('command') == 'compatibility.check' else None))
        return response

    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        raise PermissionError('No Python business networking in synthetic import harness')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real reference/game access')


sys.addaudithook(audit)


def wait_for(window, script, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(script)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('UI timeout: ' + script[:180])


def click(window, testid):
    wait_for(window, f"(()=>{{const e=document.querySelector('[data-testid={testid}]');return e&&!e.disabled&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'}})()")
    window.evaluate_js(f"document.querySelector('[data-testid={testid}]').click()")


def open_editor(window):
    click(window, 'workspace-mods')
    wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")


def profile_visual_ready(window):
    return wait_for(window, """(()=>{
      const panel=document.querySelector('[data-testid=profile-editor]');
      if(!panel || panel.querySelector('[data-testid=profile-progress]') ||
         panel.dataset.busy==='true' ||
         panel.querySelector('[data-testid=profile-error]')?.innerText ||
         panel.querySelector('[data-testid=compatibility-status]')?.dataset.state==='checking' ||
         panel.querySelector('[data-testid=compatibility-cancel]')) return null;
      const selections=[...panel.querySelectorAll('[data-testid=profile-selection]')];
      if(selections.length!==2) return null;
      return {busy:false,selection_count:selections.length,
        selection_names:selections.map(e=>e.querySelector('strong')?.innerText),
        compatibility_state:panel.querySelector('[data-testid=compatibility-status]')?.dataset.state,
        compatibility_blocking:panel.querySelector('[data-testid=compatibility-report]')?.dataset.blocking,
        finding_codes:[...panel.querySelectorAll('[data-testid=compatibility-finding]')].map(e=>e.dataset.code),
        revision:panel.querySelector('[data-testid=profile-state]')?.dataset.revision,
        dirty:panel.querySelector('[data-testid=profile-state]')?.dataset.dirty};
    })()""")


def screenshot(window, name):
    before = profile_visual_ready(window) if name != 'failure' else None
    if before is not None:
        window.evaluate_js("document.querySelector('[data-testid=profile-editor]').scrollIntoView({block:'start'})")
    window.evaluate_js('window.__captureReady=false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.__captureReady=true))')
    wait_for(window, 'window.__captureReady')
    time.sleep(.18)
    if before is not None:
        after = profile_visual_ready(window)
        assert before == after, 'Profile changed across screenshot paint barrier'
        report.setdefault('capture_barriers', []).append(dict(name=name, **after, frames=2, composition_wait_ms=180))
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(dc)
    memory = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    previous = memory.SelectObject(bitmap)
    filename = f'{args.phase}-{name}.png'
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / filename)
        report['screenshots'].append(filename)
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def database_snapshot():
    with sqlite3.connect((data / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        tables = [name for name in names if name.startswith('profile') or name in
                  ('deployment_generations', 'action_plans', 'approvals', 'transactions')]
        snapshot = {}
        for name in tables:
            assert name.replace('_', '').isalnum()
            cursor = db.execute(f'SELECT * FROM "{name}" ORDER BY 1')
            columns = [item[0] for item in cursor.description]
            snapshot[name] = [dict(zip(columns, row)) for row in cursor]
        releases = [json.loads(row[0]) for row in db.execute('SELECT payload FROM mod_releases')]
        snapshot['release_locks'] = sorted((row['release_id'], row['manifest_hash']) for row in releases)
        return snapshot


def import_resource(window):
    click(window, 'import-open')
    click(window, 'import-zip')
    wait_for(window, "Boolean(document.querySelector('[data-testid=import-preview]'))")
    click(window, 'import-commit')
    wait_for(window, "document.querySelector('[data-testid=import-result]')?.innerText.includes('1 个文件')")
    window.evaluate_js("document.querySelector('.import-dialog button[aria-label=关闭导入]').click()")
    wait_for(window, "!document.querySelector('.import-dialog[open]')")


def selection_state(window, selected, count=2):
    return wait_for(window, "(()=>{const rows=[...document.querySelectorAll('[data-testid=profile-selection]')];return rows.length===" + str(count) + "&&rows.every(e=>e.dataset.selected===" + json.dumps(str(selected).lower()) + ")})()")


def rpc(window, command, payload):
    # Cross the actual WebView JS -> DesktopApi.dispatch boundary, without
    # installing a fake page control or invoking the Python service directly.
    request = dict(schema_version=1, request_id=f'compat-{len(report["requests"])}-{time.monotonic_ns()}',
                   command=command, payload=payload)
    window.evaluate_js("window.__compatRpc=null; window.pywebview.api.dispatch(" + json.dumps(request) +
                       ").then(value=>window.__compatRpc={value}).catch(error=>window.__compatRpc={error:String(error)})")
    response = wait_for(window, 'window.__compatRpc', timeout=60)
    assert 'error' not in response, response
    response = response['value']
    assert response['request_id'] == request['request_id'] and response['ok'], response
    return response['data']


def check_api(window, profile, **options):
    snapshot = rpc(window, 'compatibility.check', dict(profile_id=profile['profile_id'],
                   expected_revision=profile['revision'], **options))
    deadline = time.monotonic() + 60
    while snapshot['state'] not in ('succeeded', 'failed', 'cancelled'):
        assert time.monotonic() < deadline, snapshot
        time.sleep(.05)
        snapshot = rpc(window, 'task.snapshot', {'task_id': snapshot['task_id']})
    assert snapshot['state'] == 'succeeded' and not snapshot['data'].get('error_code'), snapshot
    return snapshot['data']


def assert_no_check_ui(window):
    count = window.evaluate_js("document.querySelectorAll('[data-testid=profile-check], [data-testid=compatibility-check], [data-testid=compatibility-status], [data-testid=compatibility-report], .compatibility-panel, .check-results').length")
    assert count == 0, 'Retired compatibility controls/results reappeared'
    report['retired_ui_absent'] = True


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=installation-open]'))")
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        report['before'] = database_snapshot()
        hwnd = window.native.Handle.ToInt64()
        report['display'] = dict(os_dpi=int(ctypes.windll.user32.GetDpiForWindow(hwnd)), window_rect=list(win32gui.GetWindowRect(hwnd)))
        click(window, 'installation-open')
        click(window, 'installation-manual')
        click(window, 'installation-select')
        wait_for(window, "!document.querySelector('dialog[open]')")
        import_resource(window)
        import_resource(window)
        with sqlite3.connect((data / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            installation_id, fingerprint_id = db.execute('SELECT installation_id,fingerprint_id FROM installations').fetchone()
            releases = [json.loads(row[0]) for row in db.execute('SELECT payload FROM mod_releases ORDER BY release_id')]
        assert len(releases) == 2
        profile = rpc(window, 'profile.create', {'installation_id': installation_id, 'name': '兼容服务合成方案'})
        for release in releases:
            profile = rpc(window, 'profile.edit', {'profile_id': profile['profile_id'], 'expected_revision': profile['revision'],
                          'action': {'op': 'set_selection', 'selection': {'release_id': release['release_id'],
                                     'manifest_hash': release['manifest_hash'], 'selected': True}}})
        open_editor(window)
        selection_state(window, True)
        assert_no_check_ui(window)
        readonly_before = database_snapshot()
        conflict = check_api(window, profile)
        report['conflict_report'] = conflict
        assert conflict['blocking'] and not conflict['compatibility_allows_plan']
        assert conflict['binding']['fingerprint_id'] == fingerprint_id
        findings = [row for row in conflict['findings'] if row['code'] == 'TARGET_HASH_CONFLICT']
        assert findings and all(row['category'] == 'file' and row['severity'] == 'blocker' for row in findings)
        finding = findings[0]
        assert finding['source']['kind'] == 'loader_rule'
        assert finding['scope']['fingerprint_id'] == fingerprint_id
        assert len(finding['release_ids']) == 2 and finding['reason_chain']
        assert finding['detail']['target'] == 'mods/共享图片 %.png'
        assert len(set(finding['detail']['hashes'])) == 2
        assert database_snapshot() == readonly_before
        screenshot(window, 'editor-without-check-ui')
        report['checks'].append('Removed check UI stays absent; actual WebView dispatch/jobs reports the real imported file conflict with bound evidence')
        report['preview_before'] = database_snapshot()
        edits_before = sum(row['command'] == 'profile.edit' for row in report['requests'])
        report['picked_release'] = releases[0]['release_id']
        candidate = check_api(window, profile, disable_release_ids=[report['picked_release']])
        report['candidate_report'] = candidate
        report['disable_set'] = candidate['disable_preview']['release_ids']
        assert report['disable_set'] == [report['picked_release']]
        assert candidate['disable_preview']['candidate_blocking'] is False
        selection_state(window, True)
        report['preview_after'] = database_snapshot()
        assert report['preview_after'] == report['preview_before']
        assert edits_before == sum(row['command'] == 'profile.edit' for row in report['requests'])
        assert hashes(game) == report['source_before']
        assert_no_check_ui(window)
        screenshot(window, 'api-preview-keeps-editor-unchanged')
        report['checks'].append('API disable candidate is read-only: Profile, selection UI, library locks and game tree remain unchanged; no candidate controls are fabricated')
        # Explicit editor action remains the only change to the draft.
        wait_for(window, "!document.querySelector('[data-testid=profile-pick]').disabled")
        window.evaluate_js("document.querySelector('[data-testid=profile-pick]').click()")
        click(window, 'profile-batch-disable')
        wait_for(window, "(()=>{const rows=[...document.querySelectorAll('[data-testid=profile-selection]')];return rows.length===2&&rows.filter(e=>e.dataset.selected==='true').length===1})()")
        wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
        current = rpc(window, 'profile.get', {'profile_id': profile['profile_id']})
        assert current['revision'] > profile['revision']
        updated = check_api(window, current)
        report['after_disable_report'] = updated
        assert not updated['blocking']
        assert not any(row['category'] == 'file' and row['severity'] == 'blocker' for row in updated['findings'])
        assert updated['binding']['profile_revision'] == current['revision']
        assert_no_check_ui(window)
        screenshot(window, 'editor-after-explicit-disable')
        compatibility_calls = [row for row in report['requests'] if row['command'] == 'compatibility.check']
        assert len(compatibility_calls) == 3 and all(row['ok'] for row in compatibility_calls)
        assert compatibility_calls[1]['compatibility_payload']['disable_release_ids'] == [report['picked_release']]
        report['persisted'] = database_snapshot()
        assert len(report['persisted']['release_locks']) == 2
        assert report['before']['deployment_generations'] == report['persisted']['deployment_generations'] == []
        assert all(report['persisted'][table] == [] for table in ('action_plans', 'approvals', 'transactions'))
        report['consumer_semantics'] = 'Retired compatibility UI absent; production desktop bridge/API and read-only candidate consumer verified. No compatibility page or future planner claimed.'
        report['checks'].append('Explicit draft editing changes revision; the production API rechecks current data without rendering a compatibility page or deploying')
        report['status'] = 'PASS'
    except Exception as exc:
        report['error'] = str(exc)[:1000]
        report['failure_location'] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)]
        try:
            report['failure_document'] = str(window.evaluate_js('document.body.innerText'))[:18000]
            screenshot(window, 'failure')
        except Exception:
            pass
    finally:
        window.destroy()


def create(*args, **kwargs):
    kwargs.update(width=1280, height=720)
    window = original_create(*args, **kwargs)
    windows.append(window)
    return window


def start(*args, **kwargs):
    sources = iter(out / f'资源包 {index} %.zip' for index in range(2))
    contexts[0].choose_installation_folder = lambda kind: str(game) if kind == 'manual' else None
    contexts[0].choose_import_source = lambda kind: str(next(sources)) if kind == 'zip' else None
    return original_start(lambda: observe(windows[0]), **kwargs)


webview.create_window, webview.start = create, start
code = main.main(['--psmm-data-root', str(data)])
report['app_exit_code'] = code
report['source_after'] = hashes(game)
report['archive_after'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.zip')}
with managers[0]._lock:
    report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),
                             subscriptions=len(managers[0]._subscriptions))
if report['source_after'] != report['source_before'] or report['archive_after'] != report['archive_before'] or any(report['cleanup'].values()):
    report['status'] = 'FAIL'
report['command_counts'] = {name: sum(row['command'] == name for row in report['requests']) for name in sorted({row['command'] for row in report['requests']})}
(out / 'compatibility.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=code, checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)


