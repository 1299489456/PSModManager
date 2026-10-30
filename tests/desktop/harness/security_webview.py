"""Production WebView2 safety UI against owned inert packages; no game/Mod execution."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.integration.harness.security_cycle import prepare, seed_difference, ok
from backend.app import composition
import webview
import win32gui
import win32ui
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', type=Path, required=True)
args = parser.parse_args()
out = args.evidence_root.resolve()
assert out.is_relative_to(ROOT / '.preview')
context, fixture = prepare(out)
seed_difference(context, fixture)
report = dict(status='FAIL', checks=[], screenshots=[], scope='Production WebView2/API and real isolated static parser; self-authored inert packages; no Defender/Steam account/game execution')
windows = []
original_create, original_start = webview.create_window, webview.start


def wait(window, expression, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = window.evaluate_js(expression)
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('WebView timeout: ' + expression)


def click(window, selector):
    expr = f'document.querySelector({json.dumps(selector)})'
    wait(window, expr + ' && !' + expr + '.disabled')
    window.evaluate_js(expr + '.click()')


def button(window, text):
    expr = f'Array.from(document.querySelectorAll("dialog[open] button")).find(b=>b.textContent.trim()==={json.dumps(text)})'
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


def check_native():
    snap = ok(context, 'native.snapshot', installation_id=fixture['installation_id'])
    assert snap['enabled_ids'] == []
    assert all(mod['approval']['state'] != 'approved' for mod in snap['mods'])


def restore_record(window, mod_id):
    click(window, '[data-testid=security-records]')
    expr = f'Array.from(document.querySelectorAll("dialog[open] label")).find(l=>l.textContent.includes({json.dumps(mod_id)})&&!l.querySelector("input").disabled)'
    wait(window, expr)
    window.evaluate_js(expr + '.querySelector("input").click()')
    click(window, '[data-testid=security-restore-confirm]')
    wait(window, f'Array.from(document.querySelectorAll("dialog[open] label")).some(l=>l.textContent.includes({json.dumps(mod_id)})&&l.textContent.includes("已恢复"))')
    wait(window, 'document.querySelector("dialog[open] button[aria-label=关闭]")&&!document.querySelector("dialog[open] button[aria-label=关闭]").disabled')


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait()
        window.evaluate_js('window.__errors=[];addEventListener("error",e=>__errors.push(e.message));addEventListener("unhandledrejection",e=>__errors.push(String(e.reason)))')
        wait(window, 'document.querySelectorAll(".row[data-id]").length===3')
        wait(window, 'document.querySelector(".row[data-id=\\"security.demo\\"] .security-row-state")?.textContent.includes("内容已变化")')
        click(window, '.row[data-id="security.demo"] .security-row-state')
        click(window, '[data-testid=security-scan-mod]')
        wait(window, 'document.querySelector(".security-conclusion")?.textContent.includes("高风险告警")&&document.querySelector(".security-diff-counts")?.textContent.includes("修改 1")')
        window.evaluate_js('document.querySelector(".security-conclusion").scrollIntoView({block:"start"})')
        screenshot(window, 'security-result-1366')
        window.evaluate_js('Array.from(document.querySelectorAll(".native-security details")).filter(d=>/新增文件|修改文件/.test(d.querySelector("summary")?.textContent||"")).forEach(d=>d.open=true);document.querySelector(".security-diff-counts").scrollIntoView({block:"center"})')
        assert window.evaluate_js('document.querySelector(".native-security").textContent.includes("new-content.txt")&&document.querySelector(".native-security").textContent.includes("Main.dll")')
        screenshot(window, 'security-diff')
        report['checks'].append('Changed content is stale, actual background static scan exposes high risk and added/modified files')
        click(window, '[data-testid=game-launch]')
        button(window, '保存并启动')
        wait(window, 'document.querySelector("[data-testid=launch-security-details]")')
        wait(window, '!document.querySelector("dialog[open]")')
        assert window.evaluate_js('document.querySelector("[data-testid=game-launch-status]").dataset.state') == 'failed'
        screenshot(window, 'security-launch-blocked')
        click(window, '[data-testid=launch-security-details]')
        click(window, '[data-testid=security-quarantine]')
        wait(window, 'document.querySelector("dialog[open]")?.textContent.includes("security.dependent")')
        screenshot(window, 'security-local-preview')
        click(window, '[data-testid=security-dispose-confirm]')
        wait(window, '!document.querySelector("dialog[open]")&&!document.querySelector(".row[data-id=\\"security.demo\\"]")')
        assert not Path(fixture['target']).exists()
        check_native()
        report['checks'].append('High risk blocks launch; local disposition previews dependent closure, backs up and removes only owned target')
        restore_record(window, 'security.demo')
        assert Path(fixture['target']).is_dir()
        check_native()
        screenshot(window, 'security-local-restored')
        click(window, 'dialog[open] button[aria-label="关闭"]')
        wait(window, 'document.querySelector(".row[data-id=\\"security.demo\\"] .security-row-state")?.textContent.includes("未检查")')
        report['checks'].append('Local restoration returns files without automatic enable/approval and requires a fresh check')
        workshop = Path(fixture['workshop']) / '1234567890'
        files = {p.name:p.read_bytes() for p in workshop.iterdir()}
        click(window, '.row[data-id="security.workshop"] .security-row-state')
        click(window, '[data-testid=security-quarantine]')
        wait(window, 'document.querySelector("dialog[open]")?.textContent.includes("文件仍在 Steam 工坊")')
        screenshot(window, 'security-workshop-preview')
        click(window, '[data-testid=security-dispose-confirm]')
        wait(window, '!document.querySelector("dialog[open]")&&document.querySelector(".row[data-id=\\"security.workshop\\"] .security-row-state")?.textContent.includes("已隔离")')
        assert files == {p.name:p.read_bytes() for p in workshop.iterdir()}
        restore_record(window, 'security.workshop')
        assert files == {p.name:p.read_bytes() for p in workshop.iterdir()}
        check_native()
        screenshot(window, 'security-all-records')
        click(window, 'dialog[open] button[aria-label="关闭"]')
        report['checks'].append('Workshop disposition is logical only; quarantine/restore leaves every package byte intact')
        for width, height in ((1100,760),(1600,900)):
            window.resize(width,height)
            click(window, '.row[data-id="security.demo"] .security-row-state')
            window.evaluate_js('document.querySelector(".security-conclusion").scrollIntoView({block:"start"})')
            screenshot(window, 'security-' + str(width))
            assert window.evaluate_js('document.documentElement.scrollWidth<=innerWidth+1')
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
        window.destroy()


def create(title, **options):
    window = original_create(title + ' · 本地安全隔离验收', **options)
    window.create_confirmation_dialog = lambda *args: (_ for _ in ()).throw(AssertionError('Unexpected native permission dialog'))
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
