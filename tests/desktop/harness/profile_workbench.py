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
parser.add_argument('--phase', choices=['first', 'reopen', 'adoption'], required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-02-05')
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

from tests.integration.test_content_catalog import compile_fixture
import shutil

game = out / '合成安装 %'
if args.phase != 'reopen':
    make_installation(game)
    (game / 'UserData').mkdir()
    (game / 'UserData' / 'synthetic-save.bin').write_bytes(b'inert save sentinel')
    dll = compile_fixture(out / 'inputs' / '自制程序集', 'ProfileFixture')
    (game / 'Mods').mkdir()
    shutil.copyfile(dll, game / 'Mods' / 'ProfileFixture.dll')
    (game / 'Mods' / '共享图片 %.png').write_bytes(b'shared synthetic image')
    for index in range(2):
        with zipfile.ZipFile(out / 'inputs' / f'资源包 {index} %.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Mods/共享图片 %.png', b'shared synthetic image')
            if index:
                archive.writestr('Mods/独有图片 %.png', b'unique synthetic image')
data = out / 'isolated-data'
report = dict(status='FAIL', phase=args.phase, process_id=os.getpid(), requests=[], checks=[], screenshots=[], data_root=str(data), host_executable=sys.executable,
              source_before=hashes(game), inputs_before=hashes(out / 'inputs'),
              scope='Production WebView; native pickers provide synthetic paths. WebView2 Input.dispatchMouseEvent drives trusted browser pointer selection and dragging. Other controls use visible DOM clicks; this is not OS mouse automation.')
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
                                       payload=request.get('payload') if request.get('command') in ('profile.edit', 'package.detail', 'package.adopt', 'app.theme.set') else None))
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
    window.evaluate_js(f"(()=>{{const e=document.querySelector('[data-testid={testid}]');e.scrollIntoView({{block:'nearest',behavior:'instant'}});window.__clickReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__clickReady=true))}})()")
    wait_for(window, 'window.__clickReady')
    window.evaluate_js(f"(()=>{{const e=document.querySelector('[data-testid={testid}]');e.focus({{preventScroll:true}});e.click()}})()")


def open_editor(window):
    click(window, 'workspace-mods')
    wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
    if window.evaluate_js("document.querySelector('[data-testid=profile-manage]')?.getAttribute('aria-expanded')==='false'"):
        click(window, 'profile-manage')
    wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")


def visible_dialog(window, testid):
    state = wait_for(window, """(()=>{
      const element=document.querySelector('[data-testid=""" + testid + """]');
      if(!element?.open||!element.matches(':modal'))return null;
      const rect=element.getBoundingClientRect();
      return {tag:element.tagName,modal:element.matches(':modal'),text:element.innerText,
        left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom,
        viewport_width:innerWidth,viewport_height:innerHeight};
    })()""")
    assert state['tag'] == 'DIALOG' and state['modal'], state
    assert state['left'] >= -1 and state['top'] >= -1, state
    assert state['right'] <= state['viewport_width'] + 1 and state['bottom'] <= state['viewport_height'] + 1, state
    report.setdefault('visible_dialogs', []).append(dict(testid=testid, **state))
    return state


def open_library(window, count=3):
    click(window, 'profile-library-open')
    visible_dialog(window, 'profile-library')
    wait_for(window, f"document.querySelectorAll('[data-testid=profile-package]').length==={count}&&document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")


def profile_visual_ready(window, selection_count=3):
    return wait_for(window, """(()=>{
      const panel=document.querySelector('[data-testid=profile-editor]');
      if(!panel || panel.querySelector('[data-testid=profile-progress]') ||
         panel.dataset.busy==='true' ||
         panel.querySelector('[data-testid=profile-error]')?.innerText) return null;
      const packages=[...document.querySelectorAll('[data-testid=profile-package]')];
      const selections=[...panel.querySelectorAll('[data-testid=profile-selection]')];
      if(selections.length!==""" + str(selection_count) + """) return null;
      return {busy:false,package_count:packages.length,selection_count:selections.length,
        package_names:packages.map(e=>e.querySelector('strong')?.innerText),
        selection_names:selections.map(e=>e.querySelector('strong')?.innerText),
        revision:panel.querySelector('[data-testid=profile-state]')?.dataset.revision,
        dirty:panel.querySelector('[data-testid=profile-state]')?.dataset.dirty};
    })()""")


def screenshot(window, name, selection_count=3):
    before = profile_visual_ready(window, selection_count) if name not in ('failure', 'source-drift-error') and not name.startswith('adoption') else None
    if name.startswith('adoption'):
        if name == 'adoption-library-consumer':
            wait_for(window, "document.querySelectorAll('[data-testid=profile-package]').length===1&&document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
        else:
            wait_for(window, "Boolean(document.querySelector('.adoption-dialog[open]'))&&!document.querySelector('[data-testid=adoption-progress]')&&!document.querySelector('[data-testid=adoption-error]')")
        report.setdefault('capture_barriers', []).append(dict(name=name, busy=False, route='adoption', frames=2, composition_wait_ms=180))
    window.evaluate_js('window.__captureReady=false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.__captureReady=true))')
    wait_for(window, 'window.__captureReady')
    time.sleep(.18)
    if before is not None:
        after = profile_visual_ready(window, selection_count)
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
        snapshot['release_locks'] = sorted([row['release_id'], row['manifest_hash']] for row in releases)
        return snapshot


def fill(window, testid, value):
    wait_for(window, f"(()=>{{const e=document.querySelector('[data-testid={testid}]');return e&&!e.disabled&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'}})()")
    window.evaluate_js("(()=>{const e=document.querySelector('[data-testid=" + testid + "]');e.value=" + json.dumps(value) + ";e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()")


def import_input(window, kind, count):
    click(window, 'import-open')
    click(window, 'import-' + kind)
    if count is None:
        wait_for(window, "document.querySelector('.import-dialog')?.innerText.includes('已取消文件选择')")
    else:
        wait_for(window, "Boolean(document.querySelector('[data-testid=import-preview]'))")
        click(window, 'import-commit')
        wait_for(window, "document.querySelector('[data-testid=import-result]')?.innerText.includes(" + json.dumps(f'{count} 个文件') + ")")
    window.evaluate_js("document.querySelector('.import-dialog button[aria-label=关闭导入]').click()")
    wait_for(window, "!document.querySelector('.import-dialog[open]')")


def list_state(window, enabled, disabled):
    return wait_for(window, "(()=>{const a=document.querySelectorAll('[data-testid=profile-enabled-list] [data-testid=profile-selection]');const b=document.querySelectorAll('[data-testid=profile-disabled-list] [data-testid=profile-selection]');return a.length===" + str(enabled) + "&&b.length===" + str(disabled) + "&&document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'})()")


def browser_mouse(window, kind, point, *, buttons=0, modifiers=0):
    """Use the actual embedded browser input path; never fake pointer capture."""
    from System import Action
    parameters = dict(type=kind, x=point['x'], y=point['y'], buttons=buttons,
                      button='left' if kind != 'mouseMoved' or buttons else 'none',
                      modifiers=modifiers, pointerType='mouse')
    if kind in ('mousePressed', 'mouseReleased'):
        parameters['clickCount'] = 1
    pending = []
    window.native.Invoke(Action(lambda: pending.append(
        window.native.browser.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
            'Input.dispatchMouseEvent', json.dumps(parameters)))))
    assert pending[0].Wait(10000), 'Embedded browser mouse command timed out'
    result = json.loads(str(pending[0].Result))
    assert 'error' not in result, result


def browser_key(window, key, code, virtual_key):
    from System import Action
    for event_type in ('keyDown', 'keyUp'):
        parameters = dict(type=event_type, key=key, code=code, windowsVirtualKeyCode=virtual_key, nativeVirtualKeyCode=virtual_key)
        if event_type == 'keyDown':
            parameters['text'] = '\r' if key == 'Enter' else key
        pending = []
        window.native.Invoke(Action(lambda: pending.append(
            window.native.browser.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
                'Input.dispatchKeyEvent', json.dumps(parameters)))))
        assert pending[0].Wait(10000), 'Embedded browser keyboard command timed out'
        result = json.loads(str(pending[0].Result))
        assert 'error' not in result, result


def row_point(window, lane, index):
    selector = f'[data-testid=profile-{lane}-list] [data-testid=profile-selection]'
    window.evaluate_js("(()=>{const row=document.querySelectorAll(" + json.dumps(selector) + ")[" + str(index) + "];row.scrollIntoView({block:'nearest',behavior:'instant'});window.__pointerReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__pointerReady=true))})()")
    wait_for(window, 'window.__pointerReady')
    return window.evaluate_js("(()=>{const row=document.querySelectorAll(" + json.dumps(selector) + ")[" + str(index) + "];const label=row.querySelector('.row-label strong'),rect=label.getBoundingClientRect();const x=rect.left+Math.min(40,rect.width/2),y=rect.top+rect.height/2;return {x,y,hit:document.elementFromPoint(x,y)?.closest('[data-testid=profile-selection]')===row}})()")


def row_click(window, lane, index, modifier=None):
    point = row_point(window, lane, index)
    assert point['hit'], point
    modifiers = {'ctrl': 2, 'shift': 8}.get(modifier, 0)
    browser_mouse(window, 'mouseMoved', point)
    browser_mouse(window, 'mousePressed', point, buttons=1, modifiers=modifiers)
    browser_mouse(window, 'mouseReleased', point, modifiers=modifiers)


def drag(window, source_lane, index, destination, expected_batch, enabled, disabled, *, before_index=None):
    before = len([row for row in report['requests'] if row['command'] == 'profile.edit'])
    revision = int(window.evaluate_js("document.querySelector('[data-testid=profile-state]').dataset.revision"))
    target_selector = f'[data-testid=profile-{destination}-list]'
    start = row_point(window, source_lane, index)
    assert start['hit'], start
    if before_index is None:
        end = window.evaluate_js("(()=>{const lane=document.querySelector(" + json.dumps(target_selector) + ");const rect=lane.getBoundingClientRect(),rows=[...lane.querySelectorAll('[data-testid=profile-selection]')];const x=rect.left+rect.width/2,y=rows.length?rows.at(-1).getBoundingClientRect().bottom+10:rect.top+80;return {x,y,hit:document.elementFromPoint(x,y)?.closest(" + json.dumps(target_selector) + ")===lane,before_release_id:null}})()")
    else:
        end = window.evaluate_js("(()=>{const lane=document.querySelector(" + json.dumps(target_selector) + ");const row=lane.querySelectorAll('[data-testid=profile-selection]')[" + str(before_index) + "];const rect=row.getBoundingClientRect();const x=rect.left+rect.width/2,y=rect.top+6;return {x,y,hit:document.elementFromPoint(x,y)?.closest('[data-testid=profile-selection]')===row,before_release_id:row.dataset.releaseId}})()")
    assert end['hit'], end
    window.evaluate_js("""(()=>{window.__dragPointerTrace=[];if(window.__dragTraceInstalled)return;window.__dragTraceInstalled=true;
      for(const type of ['pointerdown','pointermove','pointerup','gotpointercapture','lostpointercapture'])document.addEventListener(type,event=>{
        window.__dragPointerTrace.push({type:event.type,trusted:event.isTrusted,pointer_id:event.pointerId,buttons:event.buttons,
          captured:[...document.querySelectorAll('[data-testid=profile-selection]')].some(row=>row.hasPointerCapture(event.pointerId)),
          target:event.target.closest?.('[data-testid]')?.dataset.testid,x:event.clientX,y:event.clientY});
      },true);
    })()""")
    browser_mouse(window, 'mouseMoved', start)
    browser_mouse(window, 'mousePressed', start, buttons=1)
    try:
        for step in range(1, 9):
            point = dict(x=start['x']+(end['x']-start['x'])*step/8,
                         y=start['y']+(end['y']-start['y'])*step/8)
            browser_mouse(window, 'mouseMoved', point, buttons=1)
        wait_for(window, "document.querySelector('[data-testid=profile-drag-status]')?.innerText.includes(" + json.dumps(f'正在拖动 {expected_batch} 项') + ")")
    finally:
        browser_mouse(window, 'mouseReleased', end)
    trace = window.evaluate_js('window.__dragPointerTrace')
    report.setdefault('drag_attempts', []).append(dict(source=source_lane, destination=destination, start=start, end=end, pointer_events=trace))
    list_state(window, enabled, disabled)
    assert all(event['trusted'] for event in trace), trace
    assert {'pointerdown', 'pointermove', 'pointerup', 'gotpointercapture'}.issubset({event['type'] for event in trace}), trace
    assert any(event['captured'] for event in trace), trace
    edits = [row for row in report['requests'] if row['command'] == 'profile.edit']
    assert len(edits) == before + 1, 'A drop must be exactly one atomic draft edit'
    action = edits[-1]['payload']['action']
    assert action['op'] == 'move_selections' and len(action['release_ids']) == expected_batch
    assert action['selected'] is (destination == 'enabled')
    assert action['before_release_id'] == end['before_release_id'], action
    assert int(window.evaluate_js("document.querySelector('[data-testid=profile-state]').dataset.revision")) == revision + 1
    group = 'reorder_operations' if source_lane == destination else 'drag_operations'
    report.setdefault(group, []).append(dict(source=source_lane, destination=destination, count=expected_batch, action=action,
                                                        input='WebView2 Input.dispatchMouseEvent', pointer_events=trace))


def visible_order(window, lane):
    return window.evaluate_js(f"[...document.querySelectorAll('[data-testid=profile-{lane}-list] [data-testid=profile-selection]')].map(element=>element.dataset.releaseId)")


def reorder_rows(window):
    before = visible_order(window, 'enabled')
    drag(window, 'enabled', 1, 'enabled', 1, 3, 0, before_index=0)
    expected = [before[1], before[0], before[2]]
    assert visible_order(window, 'enabled') == expected
    click(window, 'profile-undo'); list_state(window, 3, 0)
    assert visible_order(window, 'enabled') == before
    click(window, 'profile-redo'); list_state(window, 3, 0)
    assert visible_order(window, 'enabled') == expected
    report['checks'].append('Same-lane trusted pointer movement inserts above the target row with exactly one move_selections and one revision; undo and redo preserve the full order without deployment')


def switch_profile(window, profile_id):
    assert window.evaluate_js("document.querySelector('[data-testid=profile-select]').tagName") == 'BUTTON'
    # Observe menu lifecycle without changing state or calling a store method.
    window.evaluate_js("""(()=>{
      window.__profileMenuTrace=[];
      if(window.__profileMenuTraceInstalled)return;
      window.__profileMenuTraceInstalled=true;
      const identify=e=>e?.dataset?.testid||e?.getAttribute?.('role')||e?.className?.toString?.()||e?.nodeName||'window';
      const record=event=>{const button=document.querySelector('[data-testid=profile-select]');
        const bounds=button?.getBoundingClientRect();
        window.__profileMenuTrace.push({time:performance.now(),event:event.type,target:identify(event.target),
          active:identify(document.activeElement),expanded:button?.getAttribute('aria-expanded'),
          model:button?.dataset.value,disabled:button?.disabled,button_top:bounds?.top,
          panel_scroll:document.querySelector('.profile-body')?.scrollTop,
          options:[...document.querySelectorAll('[role=option][data-value]')].map(e=>e.dataset.value)});
        if(window.__profileMenuTrace.length>200)window.__profileMenuTrace.shift();};
      for(const type of ['scroll','focusin','focusout','click','pointerdown','resize'])window.addEventListener(type,record,true);
      const button=document.querySelector('[data-testid=profile-select]');
      new MutationObserver(()=>record({type:'menu-attributes',target:button})).observe(button,{attributes:true,attributeFilter:['aria-expanded','data-value','disabled']});
    })()""")
    try:
        click(window, 'profile-select')
        option = "[...document.querySelectorAll('[role=option][data-value]')].find(e=>e.dataset.value===" + json.dumps(profile_id) + ")"
        wait_for(window, f"(()=>{{const e={option};return e&&e.getClientRects().length&&!e.disabled}})()")
        window.evaluate_js(option + '.click()')
        wait_for(window, "document.querySelector('[data-testid=profile-select]')?.dataset.value===" + json.dumps(profile_id) + "&&document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
    finally:
        report.setdefault('profile_menu_traces', []).append(dict(target_profile_id=profile_id, events=window.evaluate_js('window.__profileMenuTrace')))


def current_id(window):
    return window.evaluate_js("document.querySelector('[data-testid=profile-select]').dataset.value")


def editor_structure(window):
    state = window.evaluate_js("""(()=>{
      const panel=document.querySelector('[data-testid=profile-editor]');
      const metrics=selector=>{const element=panel.querySelector(selector),style=element&&getComputedStyle(element);
        return element?{text:element.innerText,font_size:parseFloat(style.fontSize),font_family:style.fontFamily,line_height:style.lineHeight}:null;};
      return {body:{font_size:parseFloat(getComputedStyle(panel).fontSize),font_family:getComputedStyle(panel).fontFamily},
        name:metrics('.row-label strong'),secondary:metrics('.row-label small'),
        forbidden:panel.querySelectorAll('[data-testid=profile-check],[data-testid=compatibility-check],[data-testid=compatibility-status],.compatibility-panel,.check-results').length,
        old_copy:[...panel.querySelectorAll('button,summary')].filter(element=>['检查方案','检查结果'].includes(element.innerText.trim())).map(element=>element.innerText),
        lane_count:panel.querySelectorAll('[data-testid=profile-disabled-list],[data-testid=profile-enabled-list]').length};
    })()""")
    assert state['forbidden'] == 0 and state['old_copy'] == [], state
    assert state['lane_count'] == 2, state
    assert state['body']['font_size'] >= 15 and state['name']['font_size'] >= 16 and state['secondary']['font_size'] >= 14, state
    assert 'youyuan' not in state['body']['font_family'].lower() and '幼圆' not in state['body']['font_family'], state
    assert 'Segoe UI' in state['body']['font_family'] and 'Noto Sans SC' in state['body']['font_family'], state
    report['editor_structure'] = state
    assert not any(row['command'] == 'compatibility.check' for row in report['requests'])
    report['checks'].append('The rendered editor has two lanes, 15 px body / 16 px names / 14 px secondary text and no retired check controls or result panel; backend compatibility remains covered separately by integration tests')


def resize_window(window, width, height):
    window.resize(width, height)
    hwnd = window.native.Handle.ToInt64()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if (right - left, bottom - top) == (width, height):
            break
        time.sleep(.05)
    else:
        raise AssertionError(f'Native window did not resize to {width}x{height}: {(left, top, right, bottom)}')
    window.evaluate_js('window.__resizeReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__resizeReady=true))')
    wait_for(window, 'window.__resizeReady')


def capture_layout(window, name):
    layout = window.evaluate_js("""(()=>{
      const root=document.documentElement;
      const selectors=['[data-testid=profile-editor]','[data-testid=profile-enabled-list]','[data-testid=profile-disabled-list]',
        '[data-testid=profile-package-details][open]','[data-testid=profile-import-preview][open]'];
      return {theme:root.dataset.theme,viewport:{width:innerWidth,height:innerHeight},outer:{width:outerWidth,height:outerHeight},
        document_width:root.scrollWidth,body_width:document.body.scrollWidth,
        elements:selectors.flatMap(selector=>[...document.querySelectorAll(selector)]).map(element=>{
          const r=element.getBoundingClientRect();return {testid:element.dataset.testid,left:r.left,right:r.right,top:r.top,bottom:r.bottom,
            font_size:parseFloat(getComputedStyle(element).fontSize)};})};
    })()""")
    assert layout['document_width'] <= layout['viewport']['width'] + 1 and layout['body_width'] <= layout['viewport']['width'] + 1, layout
    assert all(item['left'] >= -1 and item['right'] <= layout['viewport']['width'] + 1 for item in layout['elements']), layout
    report.setdefault('layout_checks', []).append(dict(name=name, **layout))
    screenshot(window, name)


def save_theme(window):
    config_path = data / 'config.json'
    before = json.loads(config_path.read_text(encoding='utf-8'))
    click(window, 'theme-toggle')
    click(window, 'theme-option-win11-mica')
    wait_for(window, "document.documentElement.dataset.theme==='win11-mica'&&!document.querySelector('[data-testid=theme-popup]')")
    saved = json.loads(config_path.read_text(encoding='utf-8'))
    assert saved['theme_id'] == 'win11-mica'
    assert {key: value for key, value in before.items() if key != 'theme_id'} == {key: value for key, value in saved.items() if key != 'theme_id'}
    assert any(row['command'] == 'app.theme.set' and row['ok'] and row['payload'] == {'theme_id': 'win11-mica'} for row in report['requests'])
    report['theme_persistence'] = dict(theme_id=saved['theme_id'], config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(), unrelated_fields_preserved=True)


def reopened_theme(window):
    wait_for(window, "document.documentElement.dataset.theme==='win11-mica'")
    assert any(row['command'] == 'app.theme.get' and row['ok'] for row in report['requests'])
    assert not any(row['command'] == 'app.theme.set' for row in report['requests'])
    config_path = data / 'config.json'
    saved = json.loads(config_path.read_text(encoding='utf-8'))
    assert saved['theme_id'] == 'win11-mica'
    report['theme_restored'] = dict(theme_id=saved['theme_id'], config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(), before_any_theme_selection=True)
    report['checks'].append('A separate real GUI process restores the chosen light theme from this isolated data root before any theme interaction; the actual settings get command and persisted configuration agree')


def add_visible_release(window, index):
    # A previous search must not make a successful addition look like a no-op.
    fill(window, 'profile-search', '不存在的旧搜索条件')
    wait_for(window, "document.querySelectorAll('[data-testid=profile-selection]').length===0")
    open_library(window)
    wait_for(window, "[...document.querySelectorAll('[data-testid=profile-add]')].every(element=>!element.disabled)")
    release_id = window.evaluate_js(f"document.querySelectorAll('[data-testid=profile-package]')[{index}].dataset.releaseId")
    window.evaluate_js(f"document.querySelectorAll('[data-testid=profile-package]')[{index}].querySelector('[data-testid=profile-add]').click()")
    wait_for(window, "!document.querySelector('[data-testid=profile-library]')?.open&&document.querySelector('[data-testid=profile-search]')?.value===''")
    list_state(window, index + 1, 0)
    state = wait_for(window, """(()=>{
      const row=[...document.querySelectorAll('[data-testid=profile-enabled-list] [data-testid=profile-selection]')].find(element=>element.dataset.releaseId===""" + json.dumps(release_id) + """);
      if(!row)return null;const rect=row.getBoundingClientRect(),label=row.querySelector('.row-label strong'),bounds=label.getBoundingClientRect();
      const x=bounds.left+Math.min(40,bounds.width/2),y=bounds.top+bounds.height/2;
      return {release_id:row.dataset.releaseId,text:row.innerText,picked:row.classList.contains('picked'),
        visible:rect.top>=0&&rect.bottom<=innerHeight&&document.elementFromPoint(x,y)?.closest('[data-testid=profile-selection]')===row};
    })()""")
    assert state['picked'] and state['visible'], state
    report.setdefault('added_rows', []).append(state)
    screenshot(window, f'added-visible-{index + 1}', selection_count=index + 1)


def package_details(window, screenshot_name='shared-file-ownership-drawer'):
    open_library(window)
    window.evaluate_js("[...document.querySelectorAll('[data-testid=profile-package]')].find(element=>element.innerText.includes('资源包 0 %')).querySelector('[data-testid=profile-package-detail]').click()")
    state = visible_dialog(window, 'profile-package-details')
    # Shared owners are an explicit disclosure in the file table, not hidden
    # text used to make a consumer assertion pass.
    window.evaluate_js("[...document.querySelectorAll('[data-testid=profile-package-details] summary')].find(element=>element.innerText==='共享此文件的版本').click()")
    details = wait_for(window, "document.querySelector('[data-testid=profile-package-details]')?.innerText")
    assert all(text in details for text in ('共享图片 %.png', '资源包 0 %', '资源包 1 %'))
    layout = window.evaluate_js("""(()=>{const drawer=document.querySelector('[data-testid=profile-package-details]'),
      file=drawer.querySelector('[data-testid=profile-detail-file]'),name=file.querySelector('.file-heading strong');
      return {font_size:parseFloat(getComputedStyle(drawer).fontSize),title_size:parseFloat(getComputedStyle(drawer.querySelector('h2')).fontSize),
        file_name_size:parseFloat(getComputedStyle(name).fontSize),file_size:file.querySelector('.file-size')?.innerText,
        file_meta:file.querySelector('.file-meta')?.innerText,heading_display:getComputedStyle(file.querySelector('.file-heading')).display,
        advanced_open:[...drawer.querySelectorAll('details')].filter(element=>element.querySelector('summary')?.innerText!=='共享此文件的版本').some(element=>element.open)};})()""")
    assert abs(state['right'] - state['viewport_width']) <= 1 and state['left'] > 0, state
    assert layout['font_size'] >= 15 and layout['title_size'] >= 24 and layout['file_name_size'] >= 16, layout
    assert layout['file_size'] and layout['file_meta'] and layout['heading_display'] == 'flex' and not layout['advanced_open'], layout
    report['shared_file_detail'] = details
    report['package_detail_layout'] = layout
    capture_layout(window, screenshot_name)
    window.evaluate_js("document.querySelector('[data-testid=profile-package-details] button[aria-label=关闭文件详情]').click()")
    wait_for(window, "!document.querySelector('[data-testid=profile-package-details]')")
    if window.evaluate_js("Boolean(document.querySelector('[data-testid=profile-library]')?.open)"):
        window.evaluate_js("document.querySelector('[data-testid=profile-library] button[aria-label=关闭包库]').click()")
        wait_for(window, "!document.querySelector('[data-testid=profile-library]')?.open")


def visual_sizes(window):
    resize_window(window, 2560, 1440)
    capture_layout(window, 'dark-list-2560x1440')
    package_details(window, 'dark-details-2560x1440')
    save_theme(window)
    capture_layout(window, 'light-list-2560x1440')
    package_details(window, 'light-details-2560x1440')
    resize_window(window, 1280, 720)
    capture_layout(window, 'light-list-1280x720')
    package_details(window, 'light-details-1280x720')
    resize_window(window, 1440, 900)
    report['checks'].append('Captured real 2560x1440 dark/light lists and file drawers plus 1280x720 layout with computed widths and typography; these are window-size checks, not Windows system DPI acceptance')


def manifest_preview(window):
    state = visible_dialog(window, 'profile-import-preview')
    assert '3' in state['text'], state
    assert all(name in state['text'] for name in ('资源包 0 %', '资源包 1 %', 'ProfileFixture')), state
    controls = window.evaluate_js("""(()=>{const dialog=document.querySelector('[data-testid=profile-import-preview]'),
      name=dialog.querySelector('input[aria-label=导入方案名称]'),confirm=dialog.querySelector('[data-testid=profile-import-commit]');
      const visible=element=>{if(!element||!element.getClientRects().length)return false;const r=element.getBoundingClientRect();
        return r.top>=0&&r.bottom<=innerHeight&&dialog.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));};
      return {name_visible:visible(name),confirm_visible:visible(confirm),confirmation_enabled:!confirm?.disabled};})()""")
    assert controls['name_visible'] and controls['confirm_visible'] and controls['confirmation_enabled'], controls
    report.setdefault('manifest_preview_controls', []).append(controls)
    screenshot(window, f'visible-manifest-preview-{len(report["manifest_preview_controls"])}')


def context_menu_edit(window):
    unchanged = database_snapshot()
    script = """(()=>{const row=document.querySelector('[data-testid=profile-enabled-list] [data-testid=profile-selection]');
      row.scrollIntoView({block:'center'}); const rect=row.getBoundingClientRect();
      row.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:rect.left+80,clientY:rect.top+20}));
    })()"""
    window.evaluate_js(script)
    wait_for(window, "document.activeElement?.dataset.testid==='profile-context-toggle'")
    screenshot(window, 'profile-context-menu')
    window.evaluate_js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}))")
    wait_for(window, "!document.querySelector('[data-testid=profile-context-menu]')&&document.activeElement?.dataset.testid==='profile-selection'")
    assert database_snapshot() == unchanged
    window.evaluate_js(script)
    click(window, 'profile-context-toggle')
    list_state(window, 2, 1)
    assert not window.evaluate_js("Boolean(document.querySelector('[data-testid=profile-context-menu]'))")
    click(window, 'profile-undo')
    list_state(window, 3, 0)
    report['checks'].append('Actual row context menu restores focus on Escape without mutation; its disable action edits the real draft and one undo restores the whole action')


def child_keyboard_controls(window):
    unchanged = database_snapshot()
    edits = sum(row['command'] == 'profile.edit' for row in report['requests'])
    window.evaluate_js("""(()=>{window.__childKeys=[];
      for(const type of ['keydown','keyup'])document.addEventListener(type,event=>window.__childKeys.push({type:event.type,key:event.key,
        trusted:event.isTrusted,target:event.target.dataset.testid}),true);
      const button=document.querySelector('[data-testid=profile-enabled-list] [data-testid=profile-selection-detail]');
      button.scrollIntoView({block:'nearest',behavior:'instant'});button.focus({preventScroll:true});})()""")
    browser_key(window, 'Enter', 'Enter', 13)
    visible_dialog(window, 'profile-package-details')
    wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
    assert database_snapshot() == unchanged
    assert sum(row['command'] == 'profile.edit' for row in report['requests']) == edits
    window.evaluate_js("document.querySelector('[data-testid=profile-package-details] button[aria-label=关闭文件详情]').click()")
    wait_for(window, "!document.querySelector('[data-testid=profile-package-details]')")
    selected = window.evaluate_js("(()=>{const checkbox=document.querySelector('[data-testid=profile-enabled-list] [data-testid=profile-pick]');checkbox.focus({preventScroll:true});return checkbox.checked})()")
    browser_key(window, ' ', 'Space', 32)
    wait_for(window, "document.querySelector('[data-testid=profile-enabled-list] [data-testid=profile-pick]').checked===" + json.dumps(not selected))
    assert database_snapshot() == unchanged
    assert sum(row['command'] == 'profile.edit' for row in report['requests']) == edits
    trace = window.evaluate_js('window.__childKeys')
    assert len(trace) == 4 and all(event['trusted'] for event in trace), trace
    assert [event['target'] for event in trace if event['type'] == 'keydown'] == ['profile-selection-detail', 'profile-pick'], trace
    report['child_keyboard_events'] = trace
    report['checks'].append('Trusted browser Enter on a child detail button opens only its drawer; Space on a child checkbox changes selection only. Neither event triggers an ancestor row edit or changes the real draft')


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


def observe_adoption(window):
    assert not any(row['command'] == 'inventory.scan' for row in report['requests'])
    click(window, 'adoption-open')
    click(window, 'adoption-scan-button')
    wait_for(window, "document.querySelectorAll('[data-testid=adoption-file-checkbox]').length===2&&!document.querySelector('[data-testid=adoption-progress]')", 120)
    assert any(row['command'] == 'inventory.scan' and row['ok'] for row in report['requests'])
    wait_for(window, "document.querySelector('[data-testid=adoption-commit]').disabled&&document.querySelector('[data-testid=adoption-confirmation-reason]').innerText.includes('填写包名称')")
    click(window, 'adoption-select-page')
    wait_for(window, "[...document.querySelectorAll('[data-testid=adoption-file-checkbox]')].every(element=>element.checked)")
    wait_for(window, "document.querySelector('[data-testid=adoption-commit]').disabled&&document.querySelector('[data-testid=adoption-confirmation-reason]').innerText.includes('填写包名称')")
    click(window, 'adoption-clear-selection')
    wait_for(window, "[...document.querySelectorAll('[data-testid=adoption-file-checkbox]')].every(element=>!element.checked)")
    for path in ('Mods/ProfileFixture.dll', 'Mods/共享图片 %.png'):
        selector = '[data-testid=adoption-file-checkbox][data-path=' + json.dumps(path, ensure_ascii=False) + ']'
        window.evaluate_js('document.querySelector(' + json.dumps(selector) + ').click()')
        wait_for(window, 'document.querySelector(' + json.dumps(selector) + ').checked')
    fill(window, 'adoption-name', '明确接管的合成包')
    wait_for(window, "!document.querySelector('[data-testid=adoption-commit]').disabled&&document.querySelector('[data-testid=adoption-confirmation-reason]').innerText.includes('可以确认入库')")
    screenshot(window, 'adoption-selected-file-preview')
    click(window, 'adoption-commit')
    release_id = wait_for(window, "document.querySelector('[data-testid=adoption-result]')?.dataset.releaseId")
    wait_for(window, "!document.querySelector('[data-testid=adoption-progress]')")
    screenshot(window, 'adoption-published')
    assert any(row['command'] == 'package.adopt' and row['ok'] for row in report['requests'])
    with sqlite3.connect((data / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        files = [json.loads(row[0]) for row in db.execute('SELECT payload FROM file_entries WHERE release_id=?',(release_id,))]
        assert {row['relative_path'] for row in files} == {'ProfileFixture.dll', '共享图片 %.png'}
        for row in files:
            artifact = json.loads(db.execute('SELECT payload FROM artifacts WHERE sha256=?',(row['source_hash'],)).fetchone()[0])
            library_file = data / 'library' / artifact['library_relative_path']
            assert hashlib.sha256(library_file.read_bytes()).hexdigest() == row['source_hash']
            assert row['source_hash'] == report['source_before']['Mods/' + row['relative_path']]
        assert db.execute('SELECT count(*) FROM profiles').fetchone()[0] == 0
    click(window, 'adoption-close')
    if not window.evaluate_js("Boolean(document.querySelector('[data-testid=profile-library]')?.open)"):
        open_editor(window)
        open_library(window, count=1)
    else:
        visible_dialog(window, 'profile-library')
    wait_for(window, "document.querySelector('[data-testid=profile-package]')?.dataset.releaseId===" + json.dumps(release_id))
    screenshot(window, 'adoption-library-consumer')
    report['adopted_release_id'] = release_id
    report['checks'].append('Adoption explains the disabled confirmation, select-page/clear update real file checkboxes, and selecting explicit files plus the required name enables confirmation')
    report['checks'].append('The visible scan-and-read action starts from no scan, consumes the real inventory worker result in the GUI host, then two explicit immutable selections publish verified library bytes and appear in the Profile library without creating a Profile or deployment')


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=installation-open]'))")
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        report['webview_storage_context'] = window.evaluate_js("""(()=>{
          const key='psmm-harness-storage-'+Date.now()+'-'+Math.random();let probe={};
          try{localStorage.setItem(key,'probe');probe={available:localStorage.getItem(key)==='probe'};localStorage.removeItem(key)}
          catch(error){probe={available:false,error_name:error.name,message:error.message}}
          return {href:location.href,origin:location.origin,local_storage:probe};
        })()""")
        if args.phase == 'reopen':
            reopened_theme(window)
        report['before'] = database_snapshot()
        hwnd = window.native.Handle.ToInt64()
        report['display'] = dict(os_dpi=int(ctypes.windll.user32.GetDpiForWindow(hwnd)), window_rect=list(win32gui.GetWindowRect(hwnd)))
        click(window, 'installation-open')
        if args.phase != 'reopen':
            click(window, 'installation-manual')
            click(window, 'installation-select')
        else:
            wait_for(window, "[...document.querySelectorAll('dialog[open] button')].some(e=>e.innerText.trim()==='校验并使用'&&!e.disabled)")
            window.evaluate_js("[...document.querySelectorAll('dialog[open] button')].find(e=>e.innerText.trim()==='校验并使用').click()")
        wait_for(window, "!document.querySelector('dialog[open]')")
        if args.phase == 'adoption':
            observe_adoption(window)
        elif args.phase == 'first':
            import_input(window, 'zip', None)
            assert not database_snapshot()['release_locks']
            import_input(window, 'zip', 1)
            first_release = database_snapshot()['release_locks']
            import_input(window, 'zip', 1)
            assert first_release == database_snapshot()['release_locks']
            import_input(window, 'zip', 2)
            import_input(window, 'dll', 1)
            assert len(database_snapshot()['release_locks']) == 3
            open_editor(window)
            fill(window, 'profile-name', '原始配置')
            invoke(window, 'profile-create', 'profile.create')
            report['original_profile_id'] = current_id(window)
            click(window, 'profile-manage')
            for index in range(3):
                add_visible_release(window, index)
            editor_structure(window)
            invoke(window, 'profile-save', 'profile.commit')
            context_menu_edit(window)
            child_keyboard_controls(window)
            visual_sizes(window)
            click(window, 'profile-manage')
            fill(window, 'profile-name', '拖拽副本')
            invoke(window, 'profile-clone', 'profile.clone')
            report['edited_profile_id'] = current_id(window)
            assert report['edited_profile_id'] != report['original_profile_id']
            switch_profile(window, report['original_profile_id'])
            list_state(window, 3, 0)
            switch_profile(window, report['edited_profile_id'])
            list_state(window, 3, 0)
            click(window, 'profile-manage')
            reorder_rows(window)
            drag(window, 'enabled', 0, 'disabled', 1, 2, 1)
            click(window, 'profile-undo'); list_state(window, 3, 0)
            click(window, 'profile-redo'); list_state(window, 2, 1)
            drag(window, 'disabled', 0, 'enabled', 1, 3, 0)
            row_click(window, 'enabled', 0)
            row_click(window, 'enabled', 1, 'ctrl')
            drag(window, 'enabled', 0, 'disabled', 2, 1, 2)
            click(window, 'profile-undo'); list_state(window, 3, 0)
            click(window, 'profile-redo'); list_state(window, 1, 2)
            row_click(window, 'disabled', 0)
            row_click(window, 'disabled', 1, 'shift')
            drag(window, 'disabled', 0, 'enabled', 2, 3, 0)
            row_click(window, 'enabled', 0)
            row_click(window, 'enabled', 2, 'shift')
            drag(window, 'enabled', 0, 'disabled', 3, 0, 3)
            click(window, 'profile-undo'); list_state(window, 3, 0)
            click(window, 'profile-redo'); list_state(window, 0, 3)
            report['final_disabled_order'] = visible_order(window, 'disabled')
            window.evaluate_js("document.querySelector('[data-testid=profile-enabled-list]').scrollIntoView({block:'center'})")
            screenshot(window, 'cross-list-draft-after-redo')
            editor_structure(window)
            report['checks'].append('Actual ZIP/DLL import, cancelled picker and duplicate import preserve exactly three immutable releases; shared target bytes are visible through real package.detail')
            report['checks'].append('Trusted browser pointer Ctrl/Shift selection and actual pointer capture atomically edit one, two and three draft selections; undo/redo restores each entire batch. Browser-native input is recorded separately from OS mouse acceptance.')
        else:
            previous = json.loads((out / 'first-workbench.json').read_text(encoding='utf-8'))
            assert report['before'] == previous['persisted']
            open_editor(window)
            profile_visual_ready(window)
            assert current_id(window) == previous['edited_profile_id']
            list_state(window, 0, 3)
            report['reopened_disabled_order'] = visible_order(window, 'disabled')
            assert report['reopened_disabled_order'] == previous['final_disabled_order']
            window.evaluate_js("document.querySelector('[data-testid=profile-disabled-list]').scrollIntoView({block:'center'})")
            screenshot(window, 'reopened-disabled-draft')
            invoke(window, 'profile-export', 'profile.save_export')
            path = out / '导出清单 %.json'
            raw = path.read_bytes()
            manifest = json.loads(raw)
            assert len(manifest['selections']) == 3 and all(not item['selected'] for item in manifest['selections'])
            assert str(game) not in raw.decode('utf-8') and str(data) not in raw.decode('utf-8')
            report['export_before_drift_sha256'] = hashlib.sha256(raw).hexdigest()
            unchanged = database_snapshot()
            click(window, 'profile-import')
            wait_for(window, "document.querySelector('[data-testid=profile-notice]')?.innerText.includes('取消')")
            assert unchanged == database_snapshot()
            invoke(window, 'profile-import', 'profile.import_preview')
            manifest_preview(window)
            assert unchanged == database_snapshot(), 'A visible preview must not create a draft before confirmation'
            manifest['name'] = '合成源文件已变化'
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
            report['export_after_drift_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            click(window, 'profile-import-commit')
            wait_for(window, "document.querySelector('[data-testid=profile-error]')?.innerText")
            wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
            assert any(row['error_code'] == 'PROFILE_SOURCE_CHANGED' for row in report['requests'])
            assert unchanged == database_snapshot()
            screenshot(window, 'source-drift-error')
            window.evaluate_js("document.querySelector('[data-testid=profile-import-preview] button[aria-label=取消清单导入]').click()")
            wait_for(window, "!document.querySelector('[data-testid=profile-import-preview]')")
            invoke(window, 'profile-import', 'profile.import_preview')
            manifest_preview(window)
            invoke(window, 'profile-import-commit', 'profile.import_commit')
            wait_for(window, "!document.querySelector('[data-testid=profile-import-preview]')")
            list_state(window, 0, 3)
            assert len(database_snapshot()['profile_drafts']) == len(unchanged['profile_drafts']) + 1
            screenshot(window, 'manifest-retry-imported-once')
            report['checks'].append('Offline restart restores the edited draft; native JSON export/import cancellation and source drift refuse mutation, then refreshed preview creates exactly one new draft')
        report['persisted'] = database_snapshot()
        assert report['before']['deployment_generations'] == report['persisted']['deployment_generations'] == []
        if args.phase != 'adoption':
            locks = dict(report['persisted']['release_locks'])
            assert len(locks) == 3
            for row in report['persisted']['profile_drafts']:
                current = json.loads(row['payload'])['current']
                assert len(current['selections']) == 3
                assert all(locks[item['release_id']] == item['manifest_hash'] for item in current['selections'])
            assert len(report['persisted']['profiles']) == 1
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
    kwargs.update(width=1440, height=900)
    window = original_create(*args, **kwargs)
    windows.append(window)
    return window


def start(*args, **kwargs):
    sources = iter([None, out / 'inputs/资源包 0 %.zip', out / 'inputs/资源包 0 %.zip', out / 'inputs/资源包 1 %.zip'])
    manifest_sources = iter([None, out / '导出清单 %.json', out / '导出清单 %.json'])
    contexts[0].choose_installation_folder = lambda kind: str(game) if kind == 'manual' else None
    def pick_import(kind):
        path = out / 'inputs/自制程序集/ProfileFixture.dll' if kind == 'dll' else next(sources)
        return str(path) if path else None
    contexts[0].choose_import_source = pick_import
    contexts[0].choose_profile_source = lambda: str(path) if (path := next(manifest_sources)) else None
    contexts[0].choose_profile_destination = lambda: str(out / '导出清单 %.json')
    return original_start(lambda: observe(windows[0]), **kwargs)


webview.create_window, webview.start = create, start
code = main.main(['--psmm-data-root', str(data)])
report['app_exit_code'] = code
report['callback_lifecycle'] = windows[0]._psmm_callback_lifecycle.stats
report['source_after'] = hashes(game)
report['inputs_after'] = hashes(out / 'inputs')
with managers[0]._lock:
    report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),
                             subscriptions=len(managers[0]._subscriptions))
if report['source_after'] != report['source_before'] or report['inputs_after'] != report['inputs_before'] or any(report['cleanup'].values()):
    report['status'] = 'FAIL'
if not report['callback_lifecycle']['closing'] or report['callback_lifecycle']['active'] != 0:
    report['status'] = 'FAIL'
report['command_counts'] = {name: sum(row['command'] == name for row in report['requests']) for name in sorted({row['command'] for row in report['requests']})}
(out / f'{args.phase}-workbench.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=code, checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)


