"""Real WebView2 acceptance with simulated Steam mutations and localhost AI."""
import argparse
import ctypes
import json
import re
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.desktop.harness.workshop_fixture import prepare_workshop
from backend.app import composition
import webview
import win32gui
import win32ui
from PIL import Image


def run(out, *, subscriptions_only=False, usability=False, connection_state=False):
    context, fixture = prepare_workshop(out)
    connection_blocked = [connection_state]
    explicit_refreshes = []
    if connection_state:
        runtime = context.workshop.process_status
        status = context.workshop.status
        def controlled_runtime(value):
            observed = runtime(value)
            return {**observed, 'writable': False, 'blockers': ['GUARD_RECOVERY_REQUIRED']} if connection_blocked[0] else observed
        def observed_status(*args, **kwargs):
            if kwargs.get('refresh'):
                explicit_refreshes.append(True)
            return status(*args, **kwargs)
        context.workshop.process_status = controlled_runtime
        context.workshop.status = observed_status
    report = dict(status='FAIL', scope='Real WebView2/bridge/state machine; simulated Steam mutations and localhost AI', checks=[])
    report['frontend_assets'] = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', (ROOT/'frontend/dist/index.html').read_text(encoding='utf-8'))
    original_create, original_start = webview.create_window, webview.start
    windows = []

    def wait(window, expression, timeout=70):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = window.evaluate_js(expression)
            if result:
                return result
            time.sleep(.12)
        raise AssertionError('UI timeout: ' + expression)

    def click(window, selector):
        expr = 'document.querySelector(' + json.dumps(selector) + ')'
        wait(window, expr + ' && !' + expr + '.disabled')
        window.evaluate_js(expr + '.click()')

    def text_button(window, label):
        expr = 'Array.from(document.querySelectorAll("button")).find(b=>b.textContent.trim()===' + json.dumps(label) + ')'
        wait(window, expr + ' && !' + expr + '.disabled')
        window.evaluate_js(expr + '.click()')

    def shot(window, label):
        time.sleep(.5)
        hwnd = window.native.Handle.ToInt64()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        dc = win32gui.GetWindowDC(hwnd)
        source = win32ui.CreateDCFromHandle(dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, right-left, bottom-top)
        old = memory.SelectObject(bitmap)
        try:
            assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2)
            Image.frombuffer('RGB', (right-left, bottom-top), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out/(label+'.png'))
        finally:
            memory.SelectObject(old)
            win32gui.DeleteObject(bitmap.GetHandle())
            memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd, dc)

    def observe():
        window = windows[0]
        try:
            wait(window, 'document.querySelectorAll(".row[data-id]").length===7')
            window.evaluate_js('window.__uiErrors=[];addEventListener("error",e=>__uiErrors.push(e.message));addEventListener("unhandledrejection",e=>__uiErrors.push(String(e.reason)))')
            if connection_state:
                click(window, '[data-testid=workshop-open]')
                wait(window, 'document.querySelector("[data-testid=workshop-connection-status]")?.textContent.includes("安装恢复检查未通过")')
                message = window.evaluate_js('document.querySelector("[data-testid=workshop-connection-status]").textContent')
                assert '连接暂不可用' not in message and '保留上次' not in message and '未确认的工坊操作' not in message
                shot(window, 'recovery-blocked-1366')
                connection_blocked[0] = False
                click(window, '[data-testid=workshop-refresh-status]')
                wait(window, 'document.querySelector("[data-testid=workshop-connection-status]")?.textContent.includes("已连接本机 Steam")')
                assert explicit_refreshes
                text_button(window, '我的订阅')
                wait(window, 'document.querySelectorAll(".ws-card").length===7')
                wait(window, 'Array.from(document.querySelectorAll("[data-testid^=card-unsubscribe-]")).some(b=>!b.disabled)')
                shot(window, 'recovered-workshop-1366')
                assert not context._ui_steam_fixture.calls
                report.update(status='PASS', page_errors=window.evaluate_js('window.__uiErrors'),
                    checks=['Recovery blocker is accurately shown once without claiming a previous Steam observation',
                            'Explicit refresh immediately recovers to ready and enables card actions after blocker is removed'],
                    explicit_refreshes=len(explicit_refreshes), steam_calls=[])
                assert not report['page_errors']
                return
            if usability:
                from tests.desktop.harness.workshop_usability_checks import check
                check(window, context, report, wait, click, text_button, shot)
                report['page_errors'] = window.evaluate_js('window.__uiErrors')
                assert not report['page_errors']
                report['steam_calls'] = context._ui_steam_fixture.calls
                report['dpi'] = ctypes.windll.user32.GetDpiForWindow(window.native.Handle.ToInt64())
                report['status'] = 'PASS'
                return
            wait(window, 'document.querySelector(".workshop-update-bar")?.textContent.includes("4 个模组待更新")')
            if not subscriptions_only:
                click(window, '.row[data-id="CyberElectronics"]')
                wait(window, 'Array.from(document.querySelectorAll(".detail [data-testid=mod-cover] img")).some(i=>i.complete&&i.naturalWidth>0&&i.src.startsWith("https://images.steamusercontent.com/"))')
                report['checks'].append('Missing local CyberElectronics preview is displayed from actual Steam CDN')
                shot(window, 'mods-cover-1366')
                click(window, '.row[data-id="cx_rat_keeper"] .leaf-switch')
                click(window, '.row[data-id="item_details"] .pick')
                click(window, '[data-testid=mods-update-selected]')
                click(window, '[data-testid=mods-steam-confirm]')
                wait(window, 'document.querySelector(".row[data-id=item_details] .download-status")?.textContent.includes("无待更新")')
                wait(window, '!document.querySelector("[data-testid=mods-update-all]").disabled')
                assert window.evaluate_js('document.querySelector(".row[data-id=cx_rat_keeper]").dataset.where==="en"')
                report['checks'].append('Selected Steam update completes through real service/SDK state machine; unsaved enable draft survives')
                click(window, '[data-testid=mods-update-all]')
                click(window, '[data-testid=mods-steam-confirm]')
                wait(window, 'document.querySelector(".workshop-update-bar")?.textContent.includes("0 个模组待更新")')
                report['checks'].append('Update all processes remaining eligible Steam items, not local Mods or draft order')
            click(window, '[data-testid=workshop-open]')
            text_button(window, '我的订阅')
            wait(window, 'document.querySelectorAll(".ws-card").length===7')
            window.evaluate_js('Array.from(document.querySelectorAll(".ws-card")).find(b=>b.textContent.includes("Item Details")).click()')
            click(window, '[data-testid=workshop-unsubscribe]')
            click(window, '[data-testid=workshop-unsubscribe-confirm]')
            wait(window, 'document.querySelector("[data-testid=workshop-subscribe]") && !document.querySelector("[data-testid=workshop-subscribe]").disabled')
            click(window, '[data-testid=workshop-subscribe]')
            wait(window, 'document.querySelector("[data-testid=workshop-unsubscribe]") && !document.querySelector("[data-testid=workshop-unsubscribe]").disabled')
            report['checks'].append('Unsubscribe then subscribe confirmed from callbacks within page; no official-page handoff')
            click(window, '[data-testid=workshop-translate]')
            wait(window, 'document.querySelector("[data-testid=workshop-translation-toggle]") && document.body.textContent.includes("界面验收译文")')
            shot(window, 'workshop-translation-1366')
            click(window, '[data-testid=workshop-translation-toggle]')
            report['checks'].append('AI translation uses real jobs and localhost SSE; original text toggle remains available')
            for width, height in ((1100, 760), (1600, 900)):
                window.resize(width, height)
                wait(window, f'innerWidth>={width-40}&&innerWidth<={width}')
                assert window.evaluate_js('document.documentElement.scrollWidth<=innerWidth+1')
                shot(window, 'workshop-' + str(width))
            report['checks'].append('1366/1100/1600 actual WebView2 layouts have no document horizontal overflow')
            report['page_errors'] = window.evaluate_js('window.__uiErrors')
            assert not report['page_errors']
            report['steam_calls'] = context._ui_steam_fixture.calls
            report['dpi'] = ctypes.windll.user32.GetDpiForWindow(window.native.Handle.ToInt64())
            report['status'] = 'PASS'
        except Exception:
            report['error'] = traceback.format_exc()
            try:
                report['visible_text'] = window.evaluate_js('document.body.innerText')
                shot(window, 'failure')
            except Exception:
                pass
        finally:
            window.destroy()

    def create(title, **options):
        window = original_create(title + ' · Steam 隔离验收', **options)
        windows.append(window)
        return window

    context.initialize = lambda: None
    composition.DesktopContext = lambda _: context
    webview.create_window = create
    webview.start = lambda *args, **kwargs: original_start(observe, **kwargs)
    report['exit_code'] = composition.run_desktop(str(context.paths.root))
    (out/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--subscriptions-only', action='store_true')
    parser.add_argument('--usability', action='store_true')
    parser.add_argument('--connection-state', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run(args.evidence_root.resolve(), subscriptions_only=args.subscriptions_only, usability=args.usability, connection_state=args.connection_state))
