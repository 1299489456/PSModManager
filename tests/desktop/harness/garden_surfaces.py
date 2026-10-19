"""Actual desktop visual pass for the greenhouse UI's secondary surfaces.

Owns a synthetic game and private data root. Opens the change basket, filters,
every bottom sheet, the info page and the unconfigured log page in the real
WebView and saves window screenshots; also records page script errors.
Also covers Mod search and paging, the continuous log reader and the custom
title bar. The drag/resize step briefly moves the real mouse pointer and puts
it back afterwards.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', type=Path, required=True)
args = parser.parse_args()
out = args.evidence_root.resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/20261016-garden-ui')
out.mkdir(parents=True, exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--disable-background-networking --disable-component-update'

import main
import webview
import win32api
import win32con
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from tests.integration.harness.transaction_recovery import TransactionFixture, rpc

fixture = TransactionFixture(out / 'owned')
game, data, installation_id = fixture.game, fixture.data, fixture.installation
for name in ['BrewingExpansion', 'MoreStorageColors', 'InventorySorter', 'PSWallpaper', 'AutoBatterySwap'] + [f'ExtraMod{index:02d}' for index in range(1, 31)]:
    (game / 'Mods' / (name + '.dll')).write_bytes(b'owned-synthetic-mod-' + name.encode())
resources = game / 'Mods' / 'BrewingExpansion' / 'textures'
resources.mkdir(parents=True)
for index in range(6):
    (resources / f'item-{index}.png').write_bytes(b'owned-resource')
fixture.publish({'Mods/LocalTheme/preview.png': b'new-imported-resource'}, '本地主题包')
log_lines = [f'[12:{index // 60:02d}:{index % 60:02d}] line {index:04d} ' + ('[ERROR] InventorySorter: Missing dependency ExampleRuntime' if index == 5 else 'Loading synthetic content for the continuous reader') for index in range(3000)]
(game / 'MelonLoader/Latest.log').write_text('\n'.join(log_lines) + '\n', encoding='utf-8')
fixture.close()
report = {'status': 'FAIL', 'checks': [], 'screenshots': [], 'page_errors': [], 'scope': 'synthetic desktop, actual WebView, automated DOM; visual review only'}
windows = []
original_api, original_create, original_start = composition.DesktopApi, webview.create_window, webview.start


def factory(context):
    api = original_api(context)
    rpc(api, 'installation.use', {'installation_id': installation_id})
    return api


def wait(window, expression, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window.evaluate_js(expression):
            return
        time.sleep(.05)
    raise AssertionError('WebView timeout: ' + expression)


def element(name):
    return "document.querySelector('[data-testid=" + name + "]')"


def click(window, name):
    wait(window, f'{element(name)} && !{element(name)}.disabled')
    window.evaluate_js(element(name) + '.click()')


def toggle(window, name):
    row = "Array.from(document.querySelectorAll('[data-testid=workspace-mod-row]')).find(row=>row.innerText.includes(" + json.dumps(name) + '))'
    wait(window, row + ' && !' + row + ".querySelector('button').disabled")
    window.evaluate_js(row + ".querySelector('button').click()")


def screenshot(window, label, settle=.9):
    time.sleep(settle)
    window.evaluate_js('window.__captureReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__captureReady=true))')
    wait(window, 'window.__captureReady')
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, right - left, bottom - top)
    previous = memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (right - left, bottom - top), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / (label + '.png'))
        report['screenshots'].append(label + '.png')
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd, dc)


def rect(hwnd):
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right - left, bottom - top


def mouse_gesture(start, delta):
    """Press at start, move by delta in small steps, release; always releases the button."""
    x, y = start
    win32api.SetCursorPos((x, y)); time.sleep(.15)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        time.sleep(.45)
        for step in range(1, 13):
            win32api.SetCursorPos((x + delta[0] * step // 12, y + delta[1] * step // 12)); time.sleep(.03)
        time.sleep(.2)
    finally:
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(.5)


def front(hwnd):
    try:
        win32gui.SetForegroundWindow(hwnd); time.sleep(.2)
    except win32gui.error:
        pass


def ours(hwnd, point):
    """Only send real input when the point is on this window, never on another app."""
    target = win32gui.WindowFromPoint(point)
    return target == hwnd or bool(win32gui.IsChild(hwnd, target))


def backdrop_click(window):
    """Real left click on the dimmed area above a bottom sheet; the pointer is put back.
    Falls back to the same pointer events inside the page if another window covers the spot."""
    hwnd = window.native.Handle.ToInt64()
    left, top, width, height = rect(hwnd)
    front(hwnd)
    if win32gui.GetForegroundWindow() != hwnd or not ours(hwnd, (left + 24, top + 64)):
        report['synthetic_backdrop'] = report.get('synthetic_backdrop', 0) + 1
        window.evaluate_js("(()=>{const d=[...document.querySelectorAll('dialog[open]')].at(-1);for(const type of ['pointerdown','click'])d.dispatchEvent(new (type==='click'?MouseEvent:PointerEvent)(type,{bubbles:true,button:0,clientX:24,clientY:40}))})()")
        return
    cursor = win32api.GetCursorPos()
    try:
        win32api.SetCursorPos((left + 24, top + 64)); time.sleep(.15)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0); time.sleep(.08)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0); time.sleep(.3)
    finally:
        win32api.SetCursorPos(cursor)


def sheet(window, opener, label):
    click(window, opener)
    wait(window, "Boolean(document.querySelector('dialog[open]'))")
    screenshot(window, label)
    backdrop_click(window)
    wait(window, "!document.querySelector('dialog[open]')", 10)


def accept_first_run(window, timeout=20):
    """New data roots open on the storage choice; keep the default location."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = window.evaluate_js("document.querySelector('[data-testid=storage-default]') ? 'first' : !document.querySelector('[data-testid=storage-first-run]') && document.querySelector('[data-testid=mod-workspace]')?.offsetParent ? 'ready' : ''")
        if state == 'first':
            window.evaluate_js("document.querySelector('[data-testid=storage-default]').click()")
        elif state == 'ready':
            return
        time.sleep(.1)
    raise AssertionError('Workspace did not open after the storage choice')


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait()
        accept_first_run(window)
        window.evaluate_js("window.__pageErrors=[];addEventListener('error',e=>__pageErrors.push(String(e.message)));addEventListener('unhandledrejection',e=>__pageErrors.push(String(e.reason)))")
        wait(window, "Boolean(" + element('mod-pager') + ")")
        window.evaluate_js("document.querySelector('[aria-label=每页数量]').click()")
        wait(window, "Boolean(document.querySelector('.select-options [role=option][data-value=\"24\"]'))")
        window.evaluate_js("document.querySelector('.select-options [role=option][data-value=\"24\"]').click();document.querySelector('[aria-label=卡片视图]').click()")
        wait(window, "document.querySelectorAll('[data-testid=workspace-mod-row]').length===24")
        assert window.evaluate_js("document.querySelector('[data-testid=mod-pager]').innerText.includes('共 36 个')")
        assert window.evaluate_js("document.querySelector('.garden-titlebar').innerText.includes('PSMM') && !document.querySelector('.canopy').innerText.includes('本地桌面') && !document.querySelector('.canopy').innerText.includes('PSModManager')")
        report['checks'].append('36 Mods split into pages of 24; title bar shows PSMM; no readiness text in the visible header')
        screenshot(window, 'mods-overview', 1.4)
        window.evaluate_js("[...document.querySelectorAll('.pager-pages button')].find(b=>b.innerText.trim()==='2').click()")
        wait(window, "document.querySelectorAll('[data-testid=workspace-mod-row]').length===12")
        screenshot(window, 'mods-page-2')
        search = element('mod-search')
        window.evaluate_js(search + ".value='extramod2';" + search + ".dispatchEvent(new Event('input',{bubbles:true}))")
        wait(window, "document.querySelectorAll('[data-testid=workspace-mod-row]').length===10 && document.querySelector('.search-count')?.innerText.includes('10 个结果')")
        screenshot(window, 'mods-search')
        window.evaluate_js("document.querySelector('[aria-label=清除搜索]').click()")
        wait(window, "document.querySelectorAll('[data-testid=workspace-mod-row]').length===24")
        report['checks'].append('Page 2 holds the remaining 12; search narrows to 10 results and resets to page 1; clearing restores the list')
        window.evaluate_js("document.querySelector('[aria-label=列表视图]').click()")
        wait(window, "Boolean(document.querySelector('.garden-plants.list'))")
        screenshot(window, 'mods-list')
        window.evaluate_js("document.querySelector('[aria-label=卡片视图]').click()")
        toggle(window, 'BrewingExpansion'); toggle(window, '本地主题包')
        wait(window, "document.querySelector('[data-testid=workspace-basket]').classList.contains('show') && !" + element('workspace-save') + '.disabled')
        screenshot(window, 'basket-two-changes')
        report['checks'].append('Two toggles fill the change basket and enable 保存')
        window.evaluate_js("Array.from(document.querySelectorAll('.garden-chips button')).find(b=>b.innerText.includes('已启用')).click()")
        screenshot(window, 'filter-enabled')
        window.evaluate_js("Array.from(document.querySelectorAll('.garden-chips button')).find(b=>b.innerText.includes('全部')).click()")
        window.evaluate_js("Array.from(document.querySelectorAll('.garden-basket button')).find(b=>b.innerText.includes('撤销')).click()")
        wait(window, element('workspace-save') + '.disabled')
        report['checks'].append('Undo empties the basket')
        sheet(window, 'installation-open', 'sheet-installation')
        sheet(window, 'import-open', 'sheet-import')
        sheet(window, 'loader-details', 'sheet-environment')
        report['checks'].append('Installation, import and environment sheets open, and a real click on the dimmed area closes each one')
        assert window.evaluate_js("[...document.querySelectorAll('.canopy-nav>button')].map(b=>b.innerText.trim()).join('|')") == '模组|方案|Nexus|日志|设置'
        editor = element('profile-editor')
        click(window, 'profile-open')
        wait(window, editor + "?.dataset.busy==='false' && " + editor + ".offsetParent !== null && !" + element('mod-workspace') + ".offsetParent")
        screenshot(window, 'profiles-empty')
        click(window, 'profile-new')
        wait(window, element('profile-name-dialog') + "?.open && " + element('profile-name') + ".value==='我的 Mod 方案'")
        screenshot(window, 'profiles-new-dialog')
        click(window, 'profile-capture-new')
        wait(window, element('profile-state') + "?.dataset.dirty==='false' && " + editor + ".dataset.busy==='false'", 180)
        report['profile_rows'] = window.evaluate_js("[document.querySelectorAll('[data-testid=profile-enabled-list] [data-testid=profile-selection]').length, document.querySelectorAll('[data-testid=profile-disabled-list] [data-testid=profile-selection]').length]")
        assert sum(report['profile_rows']) >= 35, report['profile_rows']
        assert window.evaluate_js("Boolean(" + element('profile-apply') + ") && !" + element('profile-save'))
        screenshot(window, 'profiles-saved')
        report['checks'].append('方案 is its own page; 新方案 → 当前模组列表 saves the current Mod list and offers only 应用到游戏 while clean')
        window.evaluate_js("document.querySelector('[data-testid=profile-enabled-list] [data-testid=profile-toggle]').click()")
        wait(window, element('profile-state') + "?.dataset.dirty==='true' && " + editor + ".dataset.busy==='false'")
        assert window.evaluate_js("Boolean(" + element('profile-save') + ") && Boolean(" + element('profile-save-as') + ") && !" + element('profile-apply'))
        assert window.evaluate_js(element('profile-save') + ".innerText.trim()") == '保存'
        for index in range(3):
            window.evaluate_js(f"document.querySelectorAll('[data-testid=profile-enabled-list] [data-testid=profile-pick]')[{index}].click()")
            time.sleep(.15)
        wait(window, "document.querySelector('.selection-bar')?.innerText.includes('已选 3 项')")
        screenshot(window, 'profiles-dirty-selection')
        window.evaluate_js("document.querySelector('.selection-bar .clear').click()")
        click(window, 'profile-more')
        wait(window, "Boolean(document.querySelector('.garden-menu[role=menu]'))")
        screenshot(window, 'profiles-more-menu', .5)
        window.evaluate_js("document.querySelector('.garden-intro h1').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))")
        wait(window, "!document.querySelector('.garden-menu[role=menu]')")
        click(window, 'profile-undo')
        wait(window, element('profile-state') + "?.dataset.dirty==='false' && " + editor + ".dataset.busy==='false'")
        report['checks'].append('A change shows 保存 / 另存为…; selecting rows shows the selection bar; 更多 menu opens and closes on an outside press; undo returns to saved')
        sheet(window, 'profile-library-open', 'sheet-profile-library')
        report['checks'].append('Profile library sheet closes on a real click outside it')
        click(window, 'workspace-settings')
        wait(window, "Boolean(" + element('workspace-check') + ") && " + element('workspace-check') + ".offsetParent !== null")
        screenshot(window, 'info-page')
        overview = window.evaluate_js("document.querySelector('.app-info').innerText")
        assert 'RimCrow' not in overview and '基于' not in overview, 'the overview must not mention the reference project'
        click(window, 'info-licenses')
        wait(window, "Boolean(document.querySelector('[data-testid=about-licenses]'))")
        assert 'MIT License' in window.evaluate_js("document.querySelector('[data-testid=license-text]').textContent")
        assert 'Vue' in window.evaluate_js("document.querySelector('[data-testid=about-licenses]').innerText")
        screenshot(window, 'info-licenses')
        click(window, 'info-overview')
        report['checks'].append('Info overview has no reference-project text; 许可与致谢 shows the MIT license, components, services and notices')
        click(window, 'workspace-ai')
        log = element('diagnostic-log-text')
        wait(window, log + "?.innerText.includes('line 2999')")
        assert not window.evaluate_js(log + ".innerText.includes('line 0000')")
        screenshot(window, 'logs-unconfigured', 1.6)
        pages_before = None
        for _ in range(40):
            if window.evaluate_js(element('diagnostic-log-edge') + "?.innerText.includes('已到日志开头')"):
                break
            window.evaluate_js(log + '.scrollTop=0')
            time.sleep(.45)
            kept = window.evaluate_js(log + '.scrollTop')
            if pages_before is None:
                pages_before = kept
        assert window.evaluate_js(element('diagnostic-log-edge') + "?.innerText.includes('已到日志开头') && " + log + ".innerText.includes('line 0000') && " + log + ".innerText.includes('line 2999')")
        assert pages_before and pages_before > 100, pages_before
        report['continuous_log'] = {'scroll_top_after_first_prepend': pages_before}
        screenshot(window, 'logs-continuous-start', .6)
        window.evaluate_js("document.querySelector('[data-testid=diagnostic-context] summary').click()")
        wait(window, "document.querySelector('.attach-panel')?.offsetParent !== null")
        screenshot(window, 'logs-attach-panel', .5)
        window.evaluate_js("document.querySelector('.workspace-heading h2').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))")
        wait(window, "!document.querySelector('[data-testid=diagnostic-context]').open")
        report['checks'].append('The attachment chip opens the details of what is sent to AI and closes on an outside press')
        report['checks'].append('Scrolling to the top loads older log pages automatically, keeps the reading position, and stops at the start of the file')
        click(window, 'workspace-mods')
        hwnd = window.native.Handle.ToInt64()
        original = rect(hwnd)
        work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))['Work']
        click(window, 'window-maximize'); time.sleep(.6)
        assert rect(hwnd) == (work[0], work[1], work[2] - work[0], work[3] - work[1]), (rect(hwnd), work)
        wait(window, element('window-maximize') + ".getAttribute('aria-label')==='还原'")
        screenshot(window, 'window-maximized')
        click(window, 'window-maximize'); time.sleep(.6)
        assert rect(hwnd) == original, (rect(hwnd), original)
        click(window, 'window-minimize'); time.sleep(.6)
        assert win32gui.IsIconic(hwnd)
        # Restore the way a taskbar click does; a raw ShowWindow restore leaves WebView2 hidden (framed windows too).
        win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0); time.sleep(.8)
        assert not win32gui.IsIconic(hwnd)
        report['checks'].append('Title bar maximizes to the monitor work area (taskbar stays visible), restores the previous bounds and minimizes')
        cursor = win32api.GetCursorPos()
        front(hwnd)
        if win32gui.GetForegroundWindow() != hwnd:
            # Someone is using another window: never move their pointer into it.
            report['window_gestures'] = 'skipped: window not in the foreground'
        else:
          try:
            left, top, width, height = rect(hwnd)
            assert ours(hwnd, (left + width // 2, top + 18)) and ours(hwnd, (left + width - 3, top + height - 3)), 'window is covered'
            mouse_gesture((left + width // 2, top + 18), (120, 60))
            moved = rect(hwnd)
            assert abs(moved[0] - left - 120) <= 4 and abs(moved[1] - top - 60) <= 4 and moved[2:] == (width, height), (moved, (left, top, width, height))
            mouse_gesture((moved[0] + moved[2] - 3, moved[1] + moved[3] - 3), (80, 40))
            grown = rect(hwnd)
            assert abs(grown[2] - width - 80) <= 4 and abs(grown[3] - height - 40) <= 4 and grown[:2] == moved[:2], (grown, moved)
            report['window_gestures'] = {'start': [left, top, width, height], 'after_drag': list(moved), 'after_resize': list(grown)}
          finally:
            win32api.SetCursorPos(cursor)
          report['checks'].append('Real mouse: dragging the title bar moves the window and dragging the bottom-right edge resizes it')
        toggle(window, 'BrewingExpansion')
        screenshot(window, 'basket-after-resize')
        report['page_errors'] = window.evaluate_js('window.__pageErrors') or []
        assert not report['page_errors'], report['page_errors']
        report['checks'].append('No page script errors recorded')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            screenshot(window, 'failure', 0)
        except Exception:
            pass
    finally:
        window.destroy()


def create(title, **options):
    options.update(width=1366, height=900)
    window = original_create('PSModManager · 温室界面检查', **options)
    window.create_confirmation_dialog = lambda *values: (_ for _ in ()).throw(AssertionError('Unexpected approval dialog'))
    windows.append(window)
    return window


composition.DesktopApi = factory
webview.create_window = create
webview.start = lambda *values, **options: original_start(observe, **options)
report['exit_code'] = main.main(['--psmm-data-root', str(data)])
(out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': report['status'], 'checks': report['checks'], 'error': report.get('error')}, ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status'] == 'PASS' and report['exit_code'] == 0 else 1)
