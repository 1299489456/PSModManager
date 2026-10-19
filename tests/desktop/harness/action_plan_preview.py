"""Actual offline WebView plan preview and native OK/Cancel confirmation.

Only the installation picker is injected. Setup uses production import/Profile
commands; plan commands originate in the real page and native dialogs are kept.
"""
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
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-03-02')
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
for key, name in [('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'), ('TEMP', 'temp'), ('TMP', 'temp')]:
    os.environ[key] = str(out / name)
    (out / name).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import webview
import win32gui
import win32process
import win32ui
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi
from tests.unit.test_installation_layout import make_installation, hashes

game = make_installation(out / 'synthetic-installation')
for name in ('Mods', 'Plugins', 'UserLibs', 'UserData'):
    (game / name).mkdir()
(game / 'Mods/unmanaged.png').write_bytes(b'unchanged unmanaged resource')
(game / 'UserData/save-sentinel.bin').write_bytes(b'unchanged synthetic save')
archive = out / 'synthetic-package.zip'
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as stream:
    for index in range(23):
        stream.writestr(f'Mods/resource-{index:02}.png', f'self authored resource {index}'.encode())
data = out / 'private-data'
report = dict(status='FAIL', requests=[], checks=[], screenshots=[], native_confirmations=[],
              source_before=hashes(game), archive_before=hashlib.sha256(archive.read_bytes()).hexdigest(),
              scope='Real WebView page and backend; native confirmation is never replaced; only picker returns a synthetic path')
contexts, managers, windows = [], [], []
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context); managers.append(context.jobs)
    api = DesktopApi(context)
    original = api.dispatch
    def observed(request):
        result = original(request)
        report['requests'].append(dict(command=request.get('command'), ok=result['ok'],
            payload=request.get('payload'), error_code=(result.get('error') or {}).get('code')))
        return result
    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        raise PermissionError('No Python business networking in synthetic plan harness')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real reference or game access')


sys.addaudithook(audit)


def wait_for(window, script, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(script)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('UI timeout: ' + script[:180])


def mouse_click(hwnd, x, y):
    win32gui.SetForegroundWindow(hwnd)
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)


def click(window, testid):
    ready = """(()=>{const e=document.querySelector('[data-testid=TESTID]');const modal=[...document.querySelectorAll('dialog[open]')].at(-1);if(!e||e.disabled||!e.getClientRects().length||modal&&!modal.contains(e))return false;e.scrollIntoView({block:'center'});return true})()""".replace('TESTID', testid)
    wait_for(window, ready)
    # Read physical input coordinates only after the scroll has painted. An
    # expanded details block can otherwise leave the old hit-test position.
    window.evaluate_js('window.__planInputReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__planInputReady=true))')
    wait_for(window, 'window.__planInputReady')
    geometry = """(()=>{const e=document.querySelector('[data-testid=TESTID]');const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2,target=document.elementFromPoint(x,y);return !e.disabled&&e.contains(target)&&{x,y,width:innerWidth,height:innerHeight}})()""".replace('TESTID', testid)
    point = wait_for(window, geometry)
    hwnd = window.native.Handle.ToInt64()
    rect = win32gui.GetClientRect(hwnd)
    x, y = win32gui.ClientToScreen(hwnd, (round(point['x'] * rect[2] / point['width']), round(point['y'] * rect[3] / point['height'])))
    mouse_click(hwnd, x, y)


def native_confirmation(accepted):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        found = []
        def candidate(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == '批准部署计划' and win32process.GetWindowThreadProcessId(hwnd)[1] == os.getpid():
                found.append(hwnd)
        win32gui.EnumWindows(candidate, None)
        if found:
            hwnd, buttons = found[0], []
            def child(handle, _):
                if win32gui.GetDlgCtrlID(handle) == (1 if accepted else 2):
                    buttons.append(handle)
            win32gui.EnumChildWindows(hwnd, child, None)
            assert len(buttons) == 1, 'Native confirmation lacks expected OK/Cancel control'
            left, top, right, bottom = win32gui.GetWindowRect(buttons[0])
            report['native_confirmations'].append(dict(title=win32gui.GetWindowText(hwnd),
                button=win32gui.GetWindowText(buttons[0]), accepted=accepted, owner_pid=os.getpid(),
                input='Win32 mouse event to actual native OK/Cancel control'))
            mouse_click(hwnd, (left + right) // 2, (top + bottom) // 2)
            return
        time.sleep(.05)
    raise AssertionError('Actual native plan confirmation did not open')


def snapshot():
    with sqlite3.connect((data / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        result = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                  for name in ('action_plans', 'approvals', 'baseline_snapshots', 'deployment_generations', 'transactions')}
        result['approval_rows'] = [json.loads(row[0]) for row in db.execute('SELECT payload FROM approvals')]
        return result


def call(command, **payload):
    context = contexts[0]
    result = context.api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))
    assert result['ok'], result
    value = result['data']
    deadline = time.monotonic() + 45
    while isinstance(value, dict) and value.get('task_id'):
        if value['state'] in ('succeeded', 'failed', 'cancelled'):
            assert value['state'] == 'succeeded' and not value['data'].get('error_code'), value
            return value['data']
        assert time.monotonic() < deadline, value
        time.sleep(.02)
        value = context.jobs.snapshot(value['task_id'])
    return value


def screenshot(window, name):
    window.evaluate_js('window.__planPainted=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__planPainted=true))')
    wait_for(window, 'window.__planPainted'); time.sleep(.18)
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(dc); memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap(); bitmap.CreateCompatibleBitmap(source, right - left, bottom - top)
    previous = memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (right - left, bottom - top), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / f'{name}.png')
        report['screenshots'].append(f'{name}.png')
    finally:
        memory.SelectObject(previous); win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd, dc)


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=installation-open]'))")
        window.evaluate_js("window.__planClicks=[];document.addEventListener('click',e=>{const t=e.target.closest('[data-testid]');if(t)window.__planClicks.push({testid:t.dataset.testid,trusted:e.isTrusted})},true)")
        click(window, 'installation-open'); click(window, 'installation-manual'); click(window, 'installation-select')
        wait_for(window, "!document.querySelector('dialog[open]')")
        installation = call('installation.list')['selected_installation_id']
        assert installation
        context = contexts[0]
        grant = context.imports.grant('zip', str(archive))
        imported = call('package.import_preview', source_handle=grant['source_handle'])
        release = call('package.import_commit', preview_id=imported['preview_id'], preview_hash=imported['preview_hash'], display_name='合成资源包')
        profile = call('profile.create', installation_id=installation, name='合成计划预览')
        profile = call('profile.commit', profile_id=profile['profile_id'], expected_revision=profile['revision'])
        click(window, 'workspace-mods')
        wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'&&!document.querySelector('[data-testid=profile-plan-preview]')?.disabled")
        report['before'] = snapshot()
        click(window, 'profile-plan-preview')
        wait_for(window, "document.querySelector('[data-testid=plan-summary]')?.innerText.includes('没有变化')")
        assert window.evaluate_js("document.querySelector('[data-testid=plan-approve]').disabled")
        assert snapshot()['approvals'] == snapshot()['deployment_generations'] == 0
        screenshot(window, 'no-op'); click(window, 'plan-close')
        profile = call('profile.edit', profile_id=profile['profile_id'], expected_revision=profile['revision'], action=dict(op='set_selection', selection=dict(release_id=release['release_id'], manifest_hash=release['manifest_hash'], selected=True)))
        profile = call('profile.commit', profile_id=profile['profile_id'], expected_revision=profile['revision'])
        click(window, 'profile-refresh')
        wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'&&!document.querySelector('[data-testid=profile-plan-preview]').disabled")
        click(window, 'profile-plan-preview')
        wait_for(window, "document.querySelectorAll('[data-testid=plan-file]').length===20&&!document.querySelector('[data-testid=plan-progress]')")
        report['impact_summary'] = window.evaluate_js("document.querySelector('.plan-preview .facts').innerText")
        assert '需要重启游戏' in report['impact_summary'] and '不支持修改游戏配置' in report['impact_summary'] and '共 0 个文件' in report['impact_summary']
        report['first_page'] = window.evaluate_js("[...document.querySelectorAll('[data-testid=plan-file]')].map(e=>e.querySelector('strong').innerText)")
        click(window, 'plan-file-facts')
        wait_for(window, "document.querySelector('[data-testid=plan-before-hash]').innerText==='不存在'&&document.querySelector('[data-testid=plan-after-hash]').innerText.length===64")
        screenshot(window, 'first-page')
        click(window, 'plan-binding-toggle')
        report['plan_hash'] = wait_for(window, "(()=>{const hash=document.querySelector('[data-testid=plan-hash]').innerText;return hash.length===64&&hash})()")
        click(window, 'plan-binding-toggle')
        wait_for(window, "!document.querySelector('[data-testid=plan-binding-toggle]').parentElement.open")
        click(window, 'plan-next')
        wait_for(window, "document.querySelectorAll('[data-testid=plan-file]').length===4&&!document.querySelector('[data-testid=plan-progress]')")
        report['second_page'] = window.evaluate_js("[...document.querySelectorAll('[data-testid=plan-file]')].map(e=>e.querySelector('strong').innerText)")
        assert len(set(report['first_page'] + report['second_page'])) == 24
        assert window.evaluate_js("document.querySelector('[data-testid=plan-next]').disabled")
        assert window.evaluate_js("[...document.querySelectorAll('[data-testid=plan-file]')].some(e=>e.dataset.change==='keep'&&e.innerText.includes('unmanaged.png'))")
        screenshot(window, 'last-page')
        click(window, 'plan-approve'); native_confirmation(False)
        wait_for(window, "document.querySelector('[data-testid=plan-notice]')?.innerText.includes('未批准')")
        report['after_cancel'] = snapshot(); assert report['after_cancel']['approvals'] == 0
        click(window, 'plan-approve'); native_confirmation(True)
        wait_for(window, "Boolean(document.querySelector('[data-testid=plan-approval]'))")
        report['after_approve'] = snapshot(); assert report['after_approve']['approvals'] == 1
        assert report['after_approve']['approval_rows'][0]['plan_hash'] == report['plan_hash'], 'Visible full plan hash differs from persisted native approval'
        assert report['after_approve']['deployment_generations'] == report['after_approve']['transactions'] == 0
        assert window.evaluate_js("document.querySelector('[data-testid=plan-approve]').disabled")
        screenshot(window, 'approved'); click(window, 'plan-close')
        click(window, 'profile-toggle')
        wait_for(window, "document.querySelector('[data-testid=profile-state]')?.dataset.dirty==='true'")
        assert window.evaluate_js("document.querySelector('[data-testid=profile-plan-preview]').disabled")
        click(window, 'profile-save')
        wait_for(window, "document.querySelector('[data-testid=profile-state]')?.dataset.dirty==='false'&&document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
        conflict_archive = out / 'conflicting-package.zip'
        with zipfile.ZipFile(conflict_archive, 'x', compression=zipfile.ZIP_DEFLATED) as stream:
            stream.writestr('Mods/unmanaged.png', b'different proposed resource')
        grant = context.imports.grant('zip', str(conflict_archive))
        imported = call('package.import_preview', source_handle=grant['source_handle'])
        conflict_release = call('package.import_commit', preview_id=imported['preview_id'], preview_hash=imported['preview_hash'], display_name='冲突资源包')
        profile = call('profile.get', profile_id=profile['profile_id'])
        profile = call('profile.edit', profile_id=profile['profile_id'], expected_revision=profile['revision'], action=dict(op='set_selection', selection=dict(release_id=conflict_release['release_id'], manifest_hash=conflict_release['manifest_hash'], selected=True)))
        profile = call('profile.commit', profile_id=profile['profile_id'], expected_revision=profile['revision'])
        click(window, 'profile-refresh')
        wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'&&!document.querySelector('[data-testid=profile-plan-preview]').disabled")
        click(window, 'profile-plan-preview')
        wait_for(window, "document.querySelector('[data-testid=plan-error]')?.innerText.includes('PLAN_UNMANAGED_CONFLICT')&&document.querySelector('[data-testid=plan-conflict]')?.innerText.includes('Mods/unmanaged.png')")
        assert window.evaluate_js("document.querySelector('[data-testid=plan-approve]').disabled&&!document.querySelector('[data-testid=plan-approval]')&&!document.querySelector('[data-testid=plan-summary]')")
        report['after_conflict'] = snapshot()
        assert report['after_conflict']['action_plans'] == 2 and report['after_conflict']['approvals'] == 1
        report['conflict_error'] = window.evaluate_js("document.querySelector('[data-testid=plan-error]').innerText")
        screenshot(window, 'conflict-rejected')
        report['trusted_clicks'] = window.evaluate_js('window.__planClicks')
        assert report['trusted_clicks'] and all(row['trusted'] for row in report['trusted_clicks'])
        report['checks'] = ['Saved DTO revision drives real plan RPC', 'No-op requires no approval or generation', 'Both pages expose all 24 real hashed file records', 'Unmanaged file remains keep', 'Native cancellation persists no approval', 'Native OK stores one hash-bound approval', 'Approval performs no deployment or game writes', 'Dirty draft disables preview and old approval is not reused', 'Actual desktop pointer events are trusted', 'Unmanaged conflict is displayed without old plan or approval and creates no new plan']
        report['status'] = 'PASS'
    except Exception as exc:
        report['error'] = str(exc)[:2000]
        report['failure_location'] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)]
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
            screenshot(window, 'failure')
        except Exception:
            pass
    finally:
        window.destroy()


def create(*args, **kwargs):
    window = original_create(*args, **kwargs); windows.append(window); return window


def start(*args, **kwargs):
    contexts[0].choose_installation_folder = lambda kind: str(game) if kind == 'manual' else None
    return original_start(lambda: observe(windows[0]), **kwargs)


webview.create_window, webview.start = create, start
code = main.main(['--psmm-data-root', str(data)])
report['app_exit_code'] = code
report['source_after'] = hashes(game)
report['archive_after'] = hashlib.sha256(archive.read_bytes()).hexdigest()
with managers[0]._lock:
    report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()), subscriptions=len(managers[0]._subscriptions))
if report['source_before'] != report['source_after'] or report['archive_before'] != report['archive_after'] or any(report['cleanup'].values()):
    report['status'] = 'FAIL'
report['command_counts'] = {name: sum(row['command'] == name for row in report['requests']) for name in sorted({row['command'] for row in report['requests']})}
(out / 'action-plan-preview.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=code, checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)
