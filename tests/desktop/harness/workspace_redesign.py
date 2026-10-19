"""Actual desktop consumer for the unified Mod and log/AI workflows.

Owns a synthetic game, private data root and localhost provider. DOM actions
exercise production Vue/bridge/services; --hold permits separate visual review.
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
parser.add_argument('--hold', action='store_true')
parser.add_argument('--mod-files', type=int, default=7, help='File count of the synthetic BrewingExpansion Mod, including its DLL')
args = parser.parse_args()
assert 7 <= args.mod_files <= 4000
out = args.evidence_root.resolve()
assert any(out.is_relative_to(ROOT / 'docs/development/evidence' / name)
           for name in ('20261015-workspace-redesign', '20261015-lightweight-mod-ops', '20261016-garden-ui'))
out.mkdir(parents=True, exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from tests.integration.harness.transaction_recovery import TransactionFixture, rpc
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.launch_probe import build_probe

fixture = TransactionFixture(out / 'owned')
game, data, installation_id = fixture.game, fixture.data, fixture.installation
probe = build_probe(out / 'probe')
shutil.copyfile(probe, game / 'Probably Stolen.exe')
names = ['BrewingExpansion', 'MoreStorageColors', 'InventorySorter', 'DialogueSpeed', 'ExtraPerks', 'PSWallpaper', 'AutoBatterySwap', 'WaterTower']
for name in names:
    (game / 'Mods' / (name + '.dll')).write_bytes(b'owned-synthetic-mod-' + name.encode())
for name in names[:2]:
    resources = game / 'Mods' / name / 'textures'
    resources.mkdir(parents=True)
    for index in range(args.mod_files - 1 if name == 'BrewingExpansion' else 6):
        (resources / f'item-{index}.png').write_bytes(b'owned-resource')
release = fixture.publish({'Mods/LocalTheme/preview.png': b'new-imported-resource'}, '本地主题包')
(game / 'MelonLoader/Latest.log').write_text(
    '[12:00:00] MelonLoader 0.7.3\n[12:00:01] Loading InventorySorter\n'
    '[12:00:02] [ERROR] InventorySorter: Missing dependency ExampleRuntime\n'
    '[12:00:02] System.TypeLoadException: ExampleRuntime could not be found\n', encoding='utf-8')
fixture.close()
report = {'status': 'FAIL', 'checks': [], 'commands': [], 'screenshots': [], 'timings': [], 'large_mod_files': args.mod_files, 'scope': 'synthetic desktop, real file operations and localhost HTTP/SSE',
          'input_method': 'Automated DOM in actual WebView. Native computer-use helper unavailable; no manual or system DPI claim.'}
windows, contexts = [], []
original_api, original_create, original_start = composition.DesktopApi, webview.create_window, webview.start
reply = '## 排查结论\nInventorySorter 缺少 ExampleRuntime。\n\n### 依据\n日志出现 `TypeLoadException`，并明确指出缺少依赖。\n\n### 建议\n核对依赖版本；必要时先停用 InventorySorter，再确认其他 Mod 能否正常工作。'
provider = ScriptedAIProvider([{'text': reply}, {'text': '可以先停用 InventorySorter。保留日志，再比较下一次启动结果。'}], delay=.002)


def factory(context):
    contexts.append(context)
    api = original_api(context)
    rpc(api, 'installation.use', {'installation_id': installation_id})
    rpc(api, 'ai.settings.save', {'provider': 'openai_compatible', 'base_url': provider.base_url, 'model': 'isolated-diagnostics', 'remember': False})
    original_dispatch = api.dispatch
    def dispatch(request):
        report['commands'].append(request.get('command'))
        return original_dispatch(request)
    api.dispatch = dispatch
    return api


def wait(window, expression, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(expression)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('WebView timeout: ' + expression)


def element(name):
    return "document.querySelector('[data-testid=" + name + "]')"


def click(window, name):
    wait(window, f'{element(name)} && !{element(name)}.disabled')
    window.evaluate_js(element(name) + '.click()')


def toggle(window, name):
    selector = "Array.from(document.querySelectorAll('[data-testid=workspace-mod-row]')).find(row=>row.innerText.includes(" + json.dumps(name) + '))'
    wait(window, selector + ' && !' + selector + ".querySelector('button').disabled")
    window.evaluate_js(selector + ".querySelector('button').click()")


def save(window, label='save'):
    started = time.perf_counter()
    click(window, 'workspace-save')
    wait(window, element('mod-workspace') + ".getAttribute('aria-busy')==='false' && (" + element('workspace-save') + '.disabled || ' + element('workspace-error') + ')')
    assert not window.evaluate_js('Boolean(' + element('workspace-error') + ')'), window.evaluate_js('document.body.innerText')
    report['timings'].append({'action': label, 'seconds': round(time.perf_counter() - started, 3), 'scope': 'UI click through completed workspace response'})


def screenshot(window, label):
    window.evaluate_js('window.__captureReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__captureReady=true))')
    wait(window, 'window.__captureReady')
    time.sleep(.3)
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
        Image.frombuffer('RGB', (right-left,bottom-top), bitmap.GetBitmapBits(True), 'raw','BGRX',0,1).save(out/(label+'.png'))
        report['screenshots'].append(label+'.png')
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd, dc)


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait()
        wait(window, "document.querySelectorAll('[data-testid=workspace-mod-row]').length===9")
        assert len(provider.requests) == 0
        assert not window.evaluate_js('Boolean(' + element('workspace-inventory') + ')')
        assert not window.evaluate_js('Boolean(' + element('ai-panel') + ')')
        report['checks'].append(f'9 Mod rows for {args.mod_files + 14} available Mod/resource files; no duplicate inventory page or automatic AI request')
        row = "Array.from(document.querySelectorAll('[data-testid=workspace-mod-row]')).find(row=>row.innerText.includes('BrewingExpansion'))"
        window.evaluate_js(row + '.click()')
        wait(window, element('mod-detail') + ".innerText.includes('item-5.png')")
        report['checks'].append('Left details show nested resources belonging to a single Mod')
        screenshot(window, 'mods-1366')
        toggle(window, '本地主题包')
        assert not (game / 'Mods/LocalTheme/preview.png').exists()
        save(window, 'install_imported')
        assert (game / 'Mods/LocalTheme/preview.png').read_bytes() == b'new-imported-resource'
        toggle(window, 'BrewingExpansion'); save(window, 'disable_existing_mod')
        assert not (game / 'Mods/BrewingExpansion.dll').exists()
        assert not (game / 'Mods/BrewingExpansion/textures/item-0.png').exists()
        assert (game / 'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'
        report['checks'].append('Save installs imported package and disables all files of an existing multi-file Mod without an adoption/approval workflow')
        toggle(window, 'BrewingExpansion')
        started = time.perf_counter()
        click(window, 'game-launch')
        wait(window, element('game-launch-status') + "?.dataset.state==='running'")
        report['timings'].append({'action': 'enable_and_launch', 'seconds': round(time.perf_counter() - started, 3), 'scope': 'UI click through save, launch preflight and synthetic process running'})
        assert (game / 'Mods/BrewingExpansion/textures/item-5.png').read_bytes() == b'owned-resource'
        assert (game / 'Mods/BrewingExpansion.dll').exists()
        assert report['commands'].index('game.launch') > max(i for i, cmd in enumerate(report['commands']) if cmd == 'mod.workspace.save')
        (game / 'exit-request.txt').write_text('0')
        wait(window, element('game-launch-status') + "?.dataset.state==='exited'")
        report['checks'].append('Save-and-launch restores the whole Mod before launching the owned synthetic executable; process exits 0')
        click(window, 'workspace-ai')
        game_log = "Array.from(document.querySelectorAll('[role=tab]')).find(row=>row.innerText.includes('Latest.log'))"
        wait(window, game_log)
        window.evaluate_js(game_log + '.click()')
        wait(window, element('diagnostic-log-text') + "?.innerText.includes('InventorySorter')")
        wait(window, element('diagnostic-analyze') + ' && !' + element('diagnostic-analyze') + '.disabled')
        assert len(provider.requests) == 0
        click(window, 'diagnostic-analyze')
        wait(window, element('ai-response') + "?.innerText.includes('排查结论') && !" + element('ai-progress'))
        assert len(provider.requests) >= 1
        body = json.dumps(provider.requests[0]['body'], ensure_ascii=False)
        assert 'TypeLoadException' in body and 'InventorySorter' in body
        report['checks'].append('Visible local logs feed an actual explicit localhost AI request and streamed formatted diagnosis')
        screenshot(window, 'diagnosis-1366')
        question = element('ai-question')
        window.evaluate_js(question + ".value='应该先停用哪个 Mod？';" + question + ".dispatchEvent(new Event('input',{bubbles:true}))")
        click(window, 'ai-send')
        wait(window, "Array.from(document.querySelectorAll('[data-testid=ai-response]')).some(row=>row.innerText.includes('可以先停用')) && !" + element('ai-progress'))
        click(window, 'workspace-mods'); click(window, 'workspace-ai')
        assert window.evaluate_js("Array.from(document.querySelectorAll('[data-testid=ai-response]')).some(row=>row.innerText.includes('可以先停用'))")
        report['checks'].append('Follow-up conversation survives switching back to Mod management')
        window.resize(1280, 720); time.sleep(.3)
        geometry = window.evaluate_js("""(()=>{
          const log=document.querySelector('.log-scroll').getBoundingClientRect();
          const pane=document.querySelector('.conversation-scroll').getBoundingClientRect();
          const replies=[...document.querySelectorAll('[data-testid=ai-response]')];
          const reply=replies.at(-1).querySelector('.formatted-reply').getBoundingClientRect();
          return {logHeight:log.height,answerHeight:pane.height,lastReplyTop:reply.top,answerTop:pane.top,answerBottom:pane.bottom,lastReplyVisible:reply.top>=pane.top && reply.top<pane.bottom-20};
        })()""")
        report['short_window_geometry'] = geometry
        assert geometry['logHeight'] >= 180 and geometry['answerHeight'] >= 180, geometry
        assert geometry['lastReplyVisible'], geometry
        screenshot(window, 'diagnosis-1280-720')
        report['provider_request_count'] = len(provider.requests)
        click(window, 'workspace-mods')
        target_name = 'BrewingExpansion' if args.mod_files > 7 else '本地主题包'
        target_files = ([game / 'Mods/BrewingExpansion.dll', game / f'Mods/BrewingExpansion/textures/item-{args.mod_files - 2}.png']
                        if args.mod_files > 7 else [game / 'Mods/LocalTheme/preview.png'])
        target_row = "Array.from(document.querySelectorAll('[data-testid=workspace-mod-row]')).find(row=>row.innerText.includes(" + json.dumps(target_name) + '))'
        window.evaluate_js(target_row + '.click()')
        started = time.perf_counter()
        click(window, 'workspace-uninstall')
        click(window, 'workspace-uninstall-confirm')  # the greenhouse UI confirms inside the card
        wait(window, element('mod-workspace') + ".getAttribute('aria-busy')==='false' && !" + element('workspace-uninstall'))
        report['timings'].append({'action': 'uninstall', 'files': args.mod_files if args.mod_files > 7 else 1, 'seconds': round(time.perf_counter() - started, 3), 'scope': 'UI click through uninstall and refreshed workspace'})
        assert not any(path.exists() for path in target_files)
        assert (game / 'Mods/InventorySorter.dll').exists()
        assert (game / 'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'
        window.evaluate_js(target_row + ".dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true}))")
        save(window, 'reinstall_from_library')
        assert all(path.exists() for path in target_files)
        report['checks'].append('Detail uninstall preserves other Mods and library; keyboard Space adds the same Mod again and save restores files')
        window.evaluate_js(row + '.click()')
        wait(window, element('mod-detail') + ".innerText.includes('item-5.png')")
        screenshot(window, 'mods-1280-720')
        report['status'] = 'PASS'
        if args.hold:
            (out / 'ready.json').write_text(json.dumps({'title': 'PSModManager · 工作区验证', 'game': str(game), 'data': str(data)}), encoding='utf-8')
            deadline = time.monotonic() + 1800
            while not (out / 'close.request').exists() and time.monotonic() < deadline:
                time.sleep(.2)
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            report['document'] = window.evaluate_js('document.body.innerText')
            screenshot(window, 'failure')
        except Exception: pass
    finally:
        (game / 'exit-request.txt').write_text('0')
        window.destroy()


def create(title, **options):
    options.update(width=1366, height=900)
    window = original_create('PSModManager · 工作区验证', **options)
    window.create_confirmation_dialog = lambda *args: (_ for _ in ()).throw(AssertionError('Unexpected approval dialog'))
    windows.append(window)
    return window


composition.DesktopApi = factory
webview.create_window = create
webview.start = lambda *values, **options: original_start(observe, **options)
with provider:
    report['exit_code'] = main.main(['--psmm-data-root', str(data)])
report['provider_errors'] = provider.errors
(out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': report['status'], 'checks': report['checks'], 'error': report.get('error')}, ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status'] == 'PASS' and report['exit_code'] == 0 else 1)
