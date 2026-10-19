"""Actual DOM -> production RPC/jobs/import/storage; only native picker injected."""
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
parser.add_argument('--phase', choices=['first', 'reopen'], required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert any(out.is_relative_to(ROOT / 'docs/development/evidence' / task) for task in ('PS-02-03', 'PS-02-05'))
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
            archive.writestr(f'Mods/图片 {index} %.png', f'synthetic resource {index}'.encode())
data = out / 'isolated-data'
report = dict(status='FAIL', phase=args.phase, requests=[], checks=[], screenshots=[], data_root=str(data),
              source_before=hashes(game), archive_before={},
              scope='Actual production WebView and services; only native file/folder pickers return isolated fixture paths')
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
                                       error_code=(response.get('error') or {}).get('code')))
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
    wait_for(window, f"(()=>{{const e=document.querySelector('[data-testid={testid}]');const modal=[...document.querySelectorAll('dialog[open]')].at(-1);return e&&!e.disabled&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&(!modal||modal.contains(e))}})()")
    window.evaluate_js(f"document.querySelector('[data-testid={testid}]').click()")


def open_editor(window):
    click(window, 'workspace-mods')
    wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
    if window.evaluate_js("document.querySelector('[data-testid=profile-manage]')?.getAttribute('aria-expanded')==='false'"):
        click(window, 'profile-manage')
    wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")


def open_library(window):
    if not window.evaluate_js("document.querySelector('[data-testid=profile-library]')?.open"):
        click(window, 'profile-library-open')
    wait_for(window, "document.querySelector('[data-testid=profile-library]')?.open&&document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")


def profile_visual_ready(window):
    return wait_for(window, """(()=>{
      const panel=document.querySelector('[data-testid=profile-editor]');
      if(!panel || panel.querySelector('[data-testid=profile-progress]') ||
         panel.dataset.busy==='true' ||
         panel.querySelector('[data-testid=profile-error]')?.innerText) return null;
      const packages=[...panel.querySelectorAll('[data-testid=profile-package]')];
      const selections=[...panel.querySelectorAll('[data-testid=profile-selection]')];
      if(packages.length!==2 || selections.length!==2 ||
         packages.some(e=>e.querySelector('[data-testid=profile-add]')?.disabled)) return null;
      return {busy:false,package_count:packages.length,selection_count:selections.length,
        package_names:packages.map(e=>e.querySelector('strong')?.innerText),
        selection_names:selections.map(e=>e.querySelector('strong')?.innerText),
        active_dialog:[...document.querySelectorAll('dialog[open]')].at(-1)?.dataset.testid||null,
        revision:panel.querySelector('[data-testid=profile-state]')?.dataset.revision,
        dirty:panel.querySelector('[data-testid=profile-state]')?.dataset.dirty};
    })()""")


def screenshot(window, name):
    before = profile_visual_ready(window) if name != 'failure' else None
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
        tables = [name for name in names if name.startswith('profile') or name == 'deployment_generations']
        snapshot = {}
        for name in tables:
            assert name.replace('_', '').isalnum()
            cursor = db.execute(f'SELECT * FROM "{name}" ORDER BY 1')
            columns = [item[0] for item in cursor.description]
            snapshot[name] = [dict(zip(columns, row)) for row in cursor]
        releases = [json.loads(row[0]) for row in db.execute('SELECT payload FROM mod_releases')]
        snapshot['release_locks'] = sorted((row['release_id'], row['manifest_hash']) for row in releases)
        return snapshot


def fill(window, testid, value):
    wait_for(window, f"(()=>{{const e=document.querySelector('[data-testid={testid}]');return e&&!e.disabled&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'}})()")
    window.evaluate_js("(()=>{const e=document.querySelector('[data-testid=" + testid + "]');e.value=" + json.dumps(value) + ";e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()")


def fill_manifest_name(window, value):
    wait_for(window, "(()=>{const e=document.querySelector('[data-testid=profile-import-preview][open] input[aria-label=\"导入方案名称\"]');return e&&!e.disabled&&e.getClientRects().length})()")
    window.evaluate_js("(()=>{const e=document.querySelector('[data-testid=profile-import-preview][open] input[aria-label=\"导入方案名称\"]');e.value=" + json.dumps(value) + ";e.dispatchEvent(new Event('input',{bubbles:true}))})()")


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


def invoke(window, testid, command):
    previous = sum(row['command'] == command and row['ok'] for row in report['requests'])
    click(window, testid)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if sum(row['command'] == command and row['ok'] for row in report['requests']) > previous:
            wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
            assert not window.evaluate_js("document.querySelector('[data-testid=profile-editor] [role=alert]')?.innerText"), command
            return
        time.sleep(.05)
    raise AssertionError(f'No successful real {command} after {testid}')


def selected_name(window):
    return window.evaluate_js("document.querySelector('[data-testid=profile-select]')?.textContent")


def pick_all(window):
    # Vue's array v-model consumes each event on the next DOM update. Separate
    # input events as a user does, so the second checkbox sees the first value.
    for index in range(2):
        wait_for(window, f"!document.querySelectorAll('[data-testid=profile-pick]')[{index}].disabled")
        window.evaluate_js(f"(()=>{{const e=document.querySelectorAll('[data-testid=profile-pick]')[{index}];if(!e.checked)e.click()}})()")
        wait_for(window, f"document.querySelectorAll('[data-testid=profile-pick]')[{index}].checked")


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=installation-open]'))")
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        report['before'] = database_snapshot()
        hwnd = window.native.Handle.ToInt64()
        report['display'] = dict(os_dpi=int(ctypes.windll.user32.GetDpiForWindow(hwnd)), window_rect=list(win32gui.GetWindowRect(hwnd)))
        click(window, 'installation-open')
        if args.phase == 'first':
            click(window, 'installation-manual')
            click(window, 'installation-select')
        else:
            wait_for(window, "[...document.querySelectorAll('dialog[open] button')].some(e=>e.innerText.trim()==='校验并使用'&&!e.disabled)")
            window.evaluate_js("[...document.querySelectorAll('dialog[open] button')].find(e=>e.innerText.trim()==='校验并使用').click()")
        wait_for(window, "!document.querySelector('dialog[open]')")
        if args.phase == 'first':
            import_resource(window)
            import_resource(window)
        open_editor(window)
        if args.phase == 'first':
            fill(window, 'profile-name', '合成配置')
            invoke(window, 'profile-create', 'profile.create')
            wait_for(window, "document.querySelectorAll('[data-testid=profile-package]').length===2")
            for index in range(2):
                open_library(window)
                wait_for(window, "[...document.querySelectorAll('[data-testid=profile-library][open] [data-testid=profile-add]')].every(e=>!e.disabled)")
                added_id = window.evaluate_js(f"document.querySelectorAll('[data-testid=profile-library][open] [data-testid=profile-package]')[{index}].dataset.releaseId")
                window.evaluate_js(f"document.querySelectorAll('[data-testid=profile-library][open] [data-testid=profile-package]')[{index}].querySelector('[data-testid=profile-add]').click()")
                wait_for(window, "!document.querySelector('[data-testid=profile-library]')?.open")
                selection_state(window, True, index + 1)
                assert window.evaluate_js("[...document.querySelectorAll('[data-testid=profile-selection]')].some(row=>row.dataset.releaseId===" + json.dumps(added_id) + ")")
                report.setdefault('library_modal_adds', []).append({'release_id': added_id, 'closed_after_success': True})
            selection_state(window, True)
            screenshot(window, 'two-release-locks')
            # These are DOM checkbox clicks, not direct store mutation.
            pick_all(window)
            click(window, 'profile-batch-disable')
            selection_state(window, False)
            click(window, 'profile-undo')
            selection_state(window, True)
            click(window, 'profile-redo')
            selection_state(window, False)
            click(window, 'profile-undo')
            selection_state(window, True)
            report['checks'].append('A real two-release batch disable is one undo/redo step and keeps both immutable release locks')
            window.evaluate_js("document.querySelector('.group-menu > summary').click()")
            wait_for(window, "document.querySelector('.group-menu').open")
            fill(window, 'profile-group', '合成分组')
            click(window, 'profile-apply-group')
            wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
            window.evaluate_js("document.querySelector('.group-menu > summary').click()")
            fill(window, 'profile-name', '已改名')
            invoke(window, 'profile-rename', 'profile.edit')
            invoke(window, 'profile-save', 'profile.commit')
            fill(window, 'profile-name', '克隆配置')
            invoke(window, 'profile-clone', 'profile.clone')
            selection_state(window, True)
            screenshot(window, 'cloned-profile')
            invoke(window, 'profile-export', 'profile.save_export')
            export_path = out / '导出清单 %.json'
            assert export_path.is_file()
            exported = export_path.read_text(encoding='utf-8')
            json.loads(exported)
            assert '合成分组' in exported
            for forbidden in (str(game), str(data), 'synthetic-save.bin', 'synthetic resource'):
                assert forbidden not in exported
            report['export_sha256'] = hashlib.sha256(export_path.read_bytes()).hexdigest()
            invoke(window, 'profile-import', 'profile.import_preview')
            wait_for(window, "document.querySelector('[data-testid=profile-import-preview]')?.open")
            fill_manifest_name(window, '清单导入')
            screenshot(window, 'manifest-preview')
            invoke(window, 'profile-import-commit', 'profile.import_commit')
            wait_for(window, "!document.querySelector('[data-testid=profile-import-preview]')?.open")
            selection_state(window, True)
            assert selected_name(window).strip() == '清单导入'
            report['manifest_modal_import'] = {'name': selected_name(window).strip(), 'closed_after_success': True}
            report['checks'].append('Rename, clone and native-file export/import use actual Profile API and preserve groups without exporting game bytes or absolute paths')
            # Leave an actual unsaved draft for the next desktop process.
            pick_all(window)
            click(window, 'profile-batch-disable')
            selection_state(window, False)
            screenshot(window, 'pending-draft')
            report['selected_name'] = selected_name(window)
            report['selected_profile_id'] = window.evaluate_js("document.querySelector('[data-testid=profile-select]').dataset.value")
        else:
            previous = json.loads((out / 'first-profile.json').read_text(encoding='utf-8'))
            wait_for(window, "document.querySelector('[data-testid=profile-select]')?.dataset.value===" + json.dumps(previous['selected_profile_id']))
            profile_visual_ready(window)
            selection_state(window, False)
            assert selected_name(window) == previous['selected_name']
            screenshot(window, 'reopened-draft')
            report['checks'].append('Offline desktop restart restores selected Profile and actual persisted disabled draft, without deployment')
        report['persisted'] = database_snapshot()
        assert len(report['persisted']['release_locks']) == 2
        locks = dict(report['persisted']['release_locks'])
        committed_profiles = [json.loads(row['payload']) for row in report['persisted']['profiles']]
        assert len(committed_profiles) == 1
        assert len(committed_profiles[0]['selections']) == 2
        assert all(item['selected'] and locks[item['release_id']] == item['manifest_hash'] for item in committed_profiles[0]['selections'])
        drafts = [json.loads(row['payload']) for row in report['persisted']['profile_drafts']]
        assert len(drafts) == 3
        for draft in drafts:
            assert len(draft['current']['selections']) == 2
            assert '合成分组' in draft['current']['groups']
            assert all(locks[item['release_id']] == item['manifest_hash'] for item in draft['current']['selections'])
        assert report['before']['deployment_generations'] == report['persisted']['deployment_generations'] == []
        report['status'] = 'PASS'
    except Exception as exc:
        report['error'] = str(exc)[:1000]
        report['failure_location'] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)]
        try:
            report['failure_document'] = str(window.evaluate_js('document.body.innerText'))[:16000]
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
    contexts[0].choose_profile_source = lambda: str(out / '导出清单 %.json')
    contexts[0].choose_profile_destination = lambda: str(out / '导出清单 %.json')
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
(out / f'{args.phase}-profile.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=code, checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)


