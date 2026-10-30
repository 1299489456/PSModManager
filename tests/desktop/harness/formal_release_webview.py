"""Actual WebView2 + desktop bridge acceptance, using owned native file copies."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.desktop.harness.formal_release_backend import prepare
from backend.app import composition
import webview
import win32gui
import win32ui
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', type=Path, required=True)
parser.add_argument('--running-only', action='store_true', help='Only verify the real process/read-only UI delta')
args = parser.parse_args()
out = args.evidence_root.resolve()
context, fixture = prepare(out)
report = dict(status='FAIL', checks=[], screenshots=[], scope='Real WebView2/bridge; isolated copies; no game or Mod code execution')
windows = []
original_create, original_start = webview.create_window, webview.start


def wait(window, expression, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(expression)
        if result:
            return result
        time.sleep(.1)
    raise AssertionError('WebView timeout: ' + expression)


def click(window, selector):
    quoted = json.dumps(selector)
    wait(window, f'document.querySelector({quoted}) && !document.querySelector({quoted}).disabled')
    window.evaluate_js(f'document.querySelector({quoted}).click()')


def text_button(window, text, within='document'):
    expr = f'Array.from({within}.querySelectorAll("button")).find(b=>b.textContent.trim()==={json.dumps(text)})'
    wait(window, expr + ' && !' + expr + '.disabled')
    window.evaluate_js(expr + '.click()')


def screenshot(window, label):
    time.sleep(.7)
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, right-left, bottom-top)
    previous = memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (right-left, bottom-top), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / (label + '.png'))
        report['screenshots'].append(label + '.png')
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd, dc)


def running_probe(window):
    click(window, '[data-testid=game-launch]')
    time.sleep(.5)
    if window.evaluate_js('Boolean(document.querySelector("dialog[open]"))'):
        text_button(window, '保存并启动', 'document.querySelector("dialog[open]")')
    wait(window, 'document.querySelector("[data-testid=game-launch-status]")?.dataset.state==="running"')
    if window.evaluate_js('Boolean(document.querySelector("dialog[open]"))'):
        text_button(window, '完成', 'document.querySelector("dialog[open]")')
    assert not window.evaluate_js('Boolean(document.querySelector("dialog[open]"))')
    wait(window, 'document.querySelector(".garden-sign-banner")?.textContent.includes("游戏正在运行")')
    state_path = Path(fixture['user']) / 'SaveFile.es3'
    before = state_path.read_bytes()
    click(window, '.row[data-id="cx_rat_keeper"] .leaf-switch')
    wait(window, 'document.querySelector(".row[data-id=cx_rat_keeper]").dataset.where==="en"')
    assert window.evaluate_js('Array.from(document.querySelectorAll(".basket-actions button")).filter(b=>b.textContent.includes("保存")).every(b=>b.disabled)')
    assert state_path.read_bytes() == before
    screenshot(window, 'running-draft-readonly')
    (Path(fixture['game']) / 'exit-request.txt').write_text('0')
    report['checks'].append('Real bounded EXE launch is observed; running game allows draft edits but disables saves without touching ES3')


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait()
        window.evaluate_js('window.__errors=[];addEventListener("error",e=>__errors.push(e.message));addEventListener("unhandledrejection",e=>__errors.push(String(e.reason)))')
        wait(window, 'document.querySelectorAll(".formal-ui .row[data-id]").length===7')
        wait(window, '!document.querySelector("[data-testid=native-workspace]").matches("[aria-busy=true]")')
        assert window.evaluate_js('document.querySelectorAll(".formal-ui .row[data-where=en]").length') == 2
        report['checks'].append('Real native inventory: 7 packages, 2 saved-enabled; production bridge')
        if args.running_only:
            running_probe(window)
            report['page_errors'] = window.evaluate_js('window.__errors')
            assert not report['page_errors'], report['page_errors']
            report['status'] = 'PASS'
            return
        click(window, '.row[data-id="item_details"]')
        wait(window, 'document.querySelector(".d-tabs")')
        screenshot(window, 'mods-1366')
        for tab in ('权限', '依赖', '配置', '文件', '运行记录', '概览'):
            expr = 'Array.from(document.querySelectorAll(".d-tabs button")).find(b=>b.textContent.startsWith(' + json.dumps(tab) + '))'
            wait(window, expr)
            window.evaluate_js(expr + '.click()')
            window.evaluate_js('document.querySelector(".detail .d-scroll").scrollTop=530')
            screenshot(window, 'detail-' + tab)
            window.evaluate_js('document.querySelector(".detail .d-scroll").scrollTop=0')
        report['checks'].append('Six detail tabs render through native detail/config/log services')
        click(window, '.detail button[title="放大查看"]')
        wait(window, 'document.querySelector("dialog[open] .expanded-scroll")')
        screenshot(window, 'expanded-detail')
        click(window, 'dialog[open] button[aria-label="关闭"]')
        click(window, '.row[data-id="CyberElectronics"] .leaf-switch')
        wait(window, 'document.querySelector(".row[data-id=CyberElectronics]").dataset.where==="en"')
        assert not window.evaluate_js('Boolean(document.querySelector("dialog[open]"))')
        report['checks'].append('Enabling moves to target order without permission or approval dialog')
        # Saving is an explicit separate action, with actual file write/readback.
        window.evaluate_js('window.dispatchEvent(new KeyboardEvent("keydown",{key:"s",ctrlKey:true,bubbles:true}))')
        wait(window, 'document.querySelector("dialog[open]")?.textContent.includes("保存到游戏")')
        screenshot(window, 'save-preview')
        text_button(window, '保存', 'document.querySelector("dialog[open]")')
        wait(window, 'document.querySelector("dialog[open]")?.textContent.includes("已写入并读回一致")')
        assert window.evaluate_js('document.querySelector("dialog[open]").textContent.includes("尚未确认运行")')
        state = json.loads((Path(fixture['user']) / 'SaveFile.es3').read_text(encoding='utf-8-sig'))
        assert 'CyberElectronics' in state['modOrder']['value']
        screenshot(window, 'save-result')
        text_button(window, '完成', 'document.querySelector("dialog[open]")')
        report['checks'].append('Save writes isolated ES3 with recovery; success separate from run confirmation')
        click(window, '[data-testid=profile-open]')
        screenshot(window, 'profiles')
        click(window, '[data-testid=workspace-ai]')
        wait(window, 'document.querySelector("[data-testid=diagnostic-log-text]")?.textContent.length>50')
        screenshot(window, 'logs')
        click(window, '[data-testid=workspace-settings]')
        wait(window, 'document.querySelector(".garden-settings")?.offsetParent!==null')
        screenshot(window, 'settings')
        click(window, '[data-testid=workspace-mods]')
        click(window, '[data-testid=installation-open]')
        wait(window, 'document.querySelector(".native-identity")')
        screenshot(window, 'installation')
        click(window, '[aria-label="关闭安装选择器"]')
        for width, height in ((1100,760), (1600,900)):
            window.resize(width,height)
            time.sleep(.7)
            screenshot(window, 'mods-' + str(width))
            assert window.evaluate_js('document.documentElement.scrollWidth<=innerWidth+1'), 'Document overflows horizontally'
        report['checks'].append('Settings/logs/profiles/installation and 1100/1366/1600 WebView layouts render')
        # The selected EXE is the self-authored probe, never the real game.
        running_probe(window)
        report['page_errors'] = window.evaluate_js('window.__errors')
        assert not report['page_errors'], report['page_errors']
        report['dpi'] = ctypes.windll.user32.GetDpiForWindow(window.native.Handle.ToInt64())
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            screenshot(window, 'failure')
        except Exception:
            pass
    finally:
        (Path(fixture['game']) / 'exit-request.txt').write_text('0')
        window.destroy()


def create(title, **options):
    window = original_create(title + ' · 隔离验收', **options)
    window.create_confirmation_dialog = lambda *args: (_ for _ in ()).throw(AssertionError('Unexpected native approval dialog'))
    windows.append(window)
    return window


context.initialize = lambda: None
composition.DesktopContext = lambda paths: context
webview.create_window = create
webview.start = lambda *args, **kwargs: original_start(observe, **kwargs)
report['exit_code'] = composition.run_desktop(str(context.paths.root))
(out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status'] == 'PASS' else 1)
