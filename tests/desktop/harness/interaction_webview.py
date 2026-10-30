"""Exercise manual checking and ordinary save/launch as independent UI paths.

Uses the production API, static parser and our waiting process probe. No real
game/Mod code, Defender or Steam account operation is executed.
"""
import argparse
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.integration.harness.interaction_cycle import prepare, stopped, ok
from backend.app import composition, native_api
from backend.app.api import DesktopApi
from tests.integration.harness.ai_provider import SyntheticAIProvider
from tests.unit.test_ai_privacy import configured
import webview
import win32gui
import win32ui
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', type=Path, required=True)
parser.add_argument('--panel-only', action='store_true', help='Only verify changed report/AI workspace rendering')
args = parser.parse_args()
out = args.evidence_root.resolve()
assert out.is_relative_to(ROOT / '.preview')
context, fixture = prepare(out)
identity, game = fixture['installation_id'], Path(fixture['game'])
provider = SyntheticAIProvider(chunks=['隔离验收分析：', '报告只提供静态证据，由用户决定是否处置。'])
provider.__enter__()
configured(context, provider, key='owned-ui-fixture-not-a-real-key', remember=False)
# Reproduce the old-version state: enabled order already saved, no approvals.
installation = native_api.installation(context, identity)
initial = context.native_mods.snapshot(installation)
context.native_mods.save(installation, initial['revision'], fixture['enabled_ids'])
assert all(m['approval']['state'] != 'approved' for m in ok(context, 'native.snapshot', installation_id=identity)['mods'])
report = dict(status='FAIL', checks=[], screenshots=[], commands=[],
    scope='Production WebView2/API/static parser on owned inert packages; only a self-authored waiting process executes')
original_dispatch = DesktopApi.dispatch


def dispatch(self, request):
    if request.get('command') in {'native.save', 'game.launch'} or request.get('command', '').startswith(('security.', 'ai.')):
        report['commands'].append(request['command'])
    return original_dispatch(self, request)


DesktopApi.dispatch = dispatch
windows = []
original_create, original_start = webview.create_window, webview.start


def wait(window, expression, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = window.evaluate_js(expression)
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('WebView timeout: ' + expression)


def click(window, selector):
    expression = f'document.querySelector({json.dumps(selector)})'
    wait(window, expression + ' && !' + expression + '.disabled')
    window.evaluate_js(expression + '.click()')


def screenshot(window, label):
    time.sleep(.5)
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


def state_is(value):
    return f'document.querySelector("[data-testid=game-launch-status]")?.dataset.state==={json.dumps(value)}'


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait()
        window.evaluate_js('window.__errors=[];addEventListener("error",e=>__errors.push(e.message));addEventListener("unhandledrejection",e=>__errors.push(String(e.reason)))')
        wait(window, 'document.querySelectorAll(".row[data-where=en]").length===3')
        wait(window, 'document.querySelector(".basket-bar")?.textContent.includes("3 个待保存批准")')
        wait(window, 'document.querySelector("[data-testid=app-version]")?.textContent==="v0.3.3"')
        click(window, '[data-testid=profile-open]')
        wait(window, 'document.querySelector("[data-testid=profile-open]")?.getAttribute("aria-current")==="page"')
        click(window, '[data-testid=workspace-mods]')
        window.evaluate_js('requestAnimationFrame(()=>requestAnimationFrame(()=>window.__frameReady=true))')
        wait(window, 'window.__frameReady')
        screenshot(window, 'ordinary-before-check')
        if not args.panel_only:
            start = time.monotonic()
            click(window, '[data-testid=game-launch]')
            wait(window, state_is('running'))
            report['ordinary_first_click_seconds'] = round(time.monotonic()-start, 3)
            # Count from App bootstrap, never reset or slice away initial calls.
            assert report['commands'] == ['native.save', 'game.launch'], report['commands']
            report['before_check_page_commands'] = list(report['commands'])
            snap = ok(context, 'native.snapshot', installation_id=identity)
            assert all(m['approval']['state']=='approved' and m['approval']['approval_basis']=='user_enable'
                       and m['approval']['detected'] is None for m in snap['mods'])
            assert not provider.requests
            assert not window.evaluate_js('Boolean(document.querySelector("dialog[open]"))')
            screenshot(window, 'ordinary-running-no-scan')
            report['checks'].append('From App bootstrap through Mods, Profiles and first save/launch: zero security.* and AI RPC; visible v0.3.3; user_enable approval with detected=null')
            stopped(context, fixture)
            wait(window, state_is('exited'))
            wait(window, '!document.querySelector("[data-testid=game-launch]").disabled')

        shutil.copyfile(fixture['risk_dll'], Path(fixture['target'])/'Main.dll')
        click(window, '.garden-intro-actions button[title="重新扫描"]')
        wait(window, '!document.querySelector("[data-testid=game-launch]").disabled')
        if args.panel_only:
            window.resize(1100,760)
        click(window, '[data-testid=workspace-security]')
        wait(window, 'document.querySelector("[data-testid=security-workspace]")?.getBoundingClientRect().height>200')
        wait(window, 'document.querySelector(".security-workspace .security-conclusion")')
        assert not window.evaluate_js('Boolean(document.querySelector("dialog[open]"))')
        assert not [c for c in report['commands'] if c=='security.scan']
        assert not provider.requests
        screenshot(window, 'manual-check-entry')
        # Opening reads reports, not scans. Leaving without an explicit scan must
        # not keep polling or fetch a report from a late completion callback.
        click(window, '[data-testid=workspace-mods]')
        before = len(report['commands'])
        time.sleep(3)
        assert report['commands'][before:] == [], report['commands'][before:]
        click(window, '[data-testid=workspace-security]')
        click(window, '[data-testid=security-select-all]')
        assert window.evaluate_js('document.querySelectorAll(".security-report-entry input:checked").length')==3
        click(window, '[data-testid=security-select-all]')
        assert window.evaluate_js('document.querySelectorAll(".security-report-entry input:checked").length')==0
        click(window, '[data-testid="security-pick-security.demo"]')
        click(window, '[data-testid="security-pick-security.dependent"]')
        assert window.evaluate_js('document.querySelectorAll(".security-report-entry input:checked").length')==2
        click(window, '[data-testid="security-pick-security.dependent"]')
        click(window, '[data-security-id="security.demo"]')
        click(window, '[data-testid=security-start-selected]')
        wait(window, 'document.querySelector("[data-testid=security-progress]")?.textContent.includes("后台检查中")')
        screenshot(window, 'manual-progress')
        wait(window, """document.querySelector('[data-security-id="security.demo"]')?.textContent.includes('高风险')""", timeout=120)
        wait(window, '!document.querySelector("[data-testid=security-start-selected]").disabled')
        wait(window, 'document.querySelector(".security-workspace .security-conclusion")?.textContent.includes("高风险")')
        screenshot(window, 'manual-high-risk-report')
        assert len([c for c in report['commands'] if c=='security.scan']) == 1
        report['checks'].append('Top-level workspace owns multiselect/select-all; page entry only reads, leaving idle stops all reads; explicit selected scan shows real worker progress and high-risk report')

        window.resize(1100,760)
        click(window, '[data-testid=security-ai-preview]')
        wait(window, 'document.querySelector(".security-send-preview")?.textContent.includes("已生成的手动检查报告摘要")')
        assert not provider.requests
        screenshot(window, 'ai-exact-preview-before-send')
        click(window, '[data-testid=security-ai-send]')
        wait(window, 'document.querySelector(".security-ai-reply")?.textContent.includes("隔离验收分析")')
        wait(window, 'document.querySelector(".security-ai .mint-note")?.textContent.includes("分析完成")')
        assert len(provider.requests)==1
        assert window.evaluate_js('document.querySelector(".security-ai-reply").getBoundingClientRect().top<document.querySelector(".security-panel-report").getBoundingClientRect().bottom')
        report['checks'].append('AI preview makes zero provider requests; explicit confirmation sends one request to owned localhost analysis provider and renders its response')
        screenshot(window, 'ai-manual-analysis')
        window.resize(1100,760)
        screenshot(window, 'manual-workspace-1100')
        assert window.evaluate_js('document.documentElement.scrollWidth<=innerWidth+1')
        assert window.evaluate_js('document.querySelector(".security-ai-reply").getBoundingClientRect().top<document.querySelector(".security-panel-report").getBoundingClientRect().bottom')
        if args.panel_only:
            assert window.evaluate_js('!document.querySelector(".security-send-preview").open')
            report['page_errors'] = window.evaluate_js('window.__errors')
            assert not report['page_errors']
            report['ai_requests'] = len(provider.requests)
            report['status'] = 'PASS'
            return
        click(window, '[data-testid=workspace-mods]')

        # A high-risk report is advice, not a launch policy. The EXE below is our
        # waiting probe and cannot load or execute the inert candidate packages.
        (game/'exit-request.txt').unlink()
        before = len(report['commands'])
        click(window, '[data-testid=game-launch]')
        wait(window, state_is('running'))
        assert report['commands'][before:] == ['native.save','game.launch'], report['commands'][before:]
        assert len(provider.requests)==1
        screenshot(window, 'high-risk-report-does-not-block')
        report['checks'].append('A high-risk report does not gate normal save/launch, start another scan, or contact AI')
        stopped(context, fixture)
        wait(window, state_is('exited'))
        wait(window, '!document.querySelector("[data-testid=game-launch]").disabled')
        click(window, '[data-testid=workspace-security]')
        click(window, '[data-testid=security-start-all]')
        wait(window, 'document.querySelector("[data-testid=security-progress]")?.textContent.includes("/ 3")')
        screenshot(window, 'manual-all-progress')
        click(window, '[data-testid=workspace-mods]')
        before = len(report['commands'])
        click(window, '.row[data-id="security.workshop"] .leaf-switch')
        expression = 'Array.from(document.querySelectorAll(".basket-actions button")).find(b=>b.textContent.trim().startsWith("保存")&&!b.textContent.includes("启动"))'
        wait(window, expression+'&&!'+expression+'.disabled')
        window.evaluate_js(expression+'.click()')
        click(window, '[data-testid=native-save-confirm]')
        wait(window, 'document.querySelector("dialog[open]")?.textContent.includes("保存完成")')
        screenshot(window, 'save-while-manual-check-independent')
        assert len([c for c in report['commands'] if c=='security.scan'])==2
        assert len(provider.requests)==1
        # Only the already-requested scan's light activity poll may continue.
        assert all(c in {'native.save','security.activity'} for c in report['commands'][before:]), report['commands'][before:]
        wait(window, '!document.querySelector("[data-testid=security-progress]")?.textContent.includes("后台检查中")')
        after = len(report['commands']);time.sleep(2)
        assert report['commands'][after:] == [], report['commands'][after:]
        report['checks'].append('All-packages manual scan continues after leaving; ordinary save is independent, completion does not fetch security.status/detail while hidden')
        click(window, 'dialog[open] .sh-head .x')
        click(window, '[data-testid=workspace-security]')
        click(window, '[data-security-id="security.demo"]')
        wait(window, 'document.querySelector("[data-testid=security-quarantine]")&&!document.querySelector("[data-testid=security-quarantine]").disabled')
        click(window, '[data-testid=security-quarantine]')
        wait(window, 'document.querySelector("dialog[open]")?.textContent.includes("security.dependent")')
        screenshot(window, 'disposition-dependencies-preview')
        assert Path(fixture['target']).is_dir()
        click(window, '[data-testid=security-dispose-confirm]')
        wait(window, '!document.querySelector("dialog[open]")')
        assert not Path(fixture['target']).exists()
        click(window, '[data-testid=security-records]')
        wait(window, 'document.querySelector("dialog[open] input[type=radio]:not(:disabled)")')
        click(window, 'dialog[open] input[type=radio]:not(:disabled)')
        click(window, '[data-testid=security-restore-confirm]')
        wait(window, 'document.querySelector("dialog[open]")?.textContent.includes("已恢复")')
        assert Path(fixture['target']).is_dir()
        snap = ok(context, 'native.snapshot', installation_id=identity)
        assert 'security.demo' not in snap['enabled_ids'] and 'security.dependent' not in snap['enabled_ids']
        screenshot(window, 'disposition-restored-not-enabled')
        report['checks'].append('Disposition preview and dependency confirmation live on check page; owned local package is recoverably moved then restored without automatic enabling')
        report['page_errors'] = window.evaluate_js('window.__errors')
        assert not report['page_errors'], report['page_errors']
        report['ai_requests'] = len(provider.requests)
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            screenshot(window, 'failure')
        except Exception:
            pass
    finally:
        stopped(context, fixture)
        window.destroy()


def create(title, **options):
    window = original_create(title + ' · 保存启动隔离验收', **options)
    window.create_confirmation_dialog = lambda *args: (_ for _ in ()).throw(AssertionError('Unexpected native permission dialog'))
    windows.append(window)
    return window


context.initialize = lambda: None
composition.DesktopContext = lambda paths: context
webview.create_window = create
webview.start = lambda *args, **kwargs: original_start(observe, **kwargs)
try:
    report['exit_code'] = composition.run_desktop(str(context.paths.root))
finally:
    provider.__exit__(None, None, None)
(out/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status']=='PASS' else 1)
