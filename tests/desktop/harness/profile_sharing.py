"""Actual Vue/WebView -> desktop jobs -> private libraries and synthetic game.

Native chooser results are the only UI input substitutions. Synthetic DLLs are
compiled for metadata, copied for deployment, and never loaded or executed.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--attempt', default='01')
args = parser.parse_args()
out = args.out.resolve()
assert (out.is_relative_to(ROOT/'docs/development/evidence/20261016-mod-sharing-analysis-online')
        or out.is_relative_to(ROOT/'docs/development/evidence/20261016-followup-fixes/profiles')
        or out.is_relative_to(ROOT/'docs/development/evidence/20261016-garden-ui'))
out.mkdir(parents=True, exist_ok=True)
runtime = out/('runtime-'+args.attempt)
runtime.mkdir()
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.games.probably_stolen import discovery
from tests.integration.harness.transaction_recovery import TransactionFixture, rpc
from tests.integration.test_content_catalog import compile_fixture

# Cold-start discovery must never inspect a real Steam installation.
discovery.automatic_steam_roots = lambda: []

fixture = TransactionFixture(runtime/'owned')
game, data, installation_id = fixture.game, fixture.data, fixture.installation
versions, sources = {}, {}
for version in ('1.0.0.0', '2.0.0.0'):
    dll = compile_fixture(runtime/('compiler-'+version), 'UiVersion', version=version, melon=False)
    versions[version] = dll.read_bytes()
    path = runtime/('UI-version-'+version+'.zip')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('Mods/UiVersion.dll', versions[version])
        archive.writestr('Mods/UiVersion/main.png', version.encode())
        archive.writestr('Mods/UiVersion/'+('obsolete.png' if version.startswith('1') else 'new.png'), version.encode())
        archive.writestr('README.txt', ('Owned version '+version+' notes').encode())
        archive.writestr('Mods/UiVersion/Reference/secret_admirer.png', b'legitimate named texture')
        archive.writestr('Mods/UiVersion/settings.toml', b'[display]\nscale=2')
        archive.writestr('Mods/UiVersion/assets/color.dds', b'additional resource format')
    sources[version] = path
source = fixture.context.imports.grant('zip', str(sources['1.0.0.0']))
preview = fixture.context.imports.preview(source['source_handle'])
old = fixture.context.imports.commit(preview['preview_id'], preview['preview_hash'], '合成版本 Mod')
view = fixture.context.mod_workspace.list(installation_id)
assert fixture.context.mod_workspace.save(dict(installation_id=installation_id, snapshot_id=view['snapshot_id'],
    enabled_ids=[old['release_id']], idempotency_key=str(uuid4())))['completed']
(game/'Mods/ExistingMod.dll').write_bytes(b'owned external Mod copied but never executed')
for index in range(600):
    path = game/'Mods/ExistingMod/Reference'/('secret_admirer.png' if index == 0 else 'asset-'+str(index)+'.png')
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(('Owned synthetic texture '+str(index)).encode())
fixture.close()

report = dict(status='FAIL', checks=[], screenshots=[], requests=[], attempt=args.attempt,
    scope='Actual production Vue and WebView, actual desktop API/jobs, isolated library and synthetic game; native chooser paths injected',
    game=str(game), data_root=str(data), executed_dlls=False)
windows, contexts = [], []
original_api, original_create, original_start = composition.DesktopApi, webview.create_window, webview.start
selected_source = dict(path=sources['2.0.0.0'])
bundle = runtime/'webview-modpack.zip'


def factory(context):
    contexts.append(context)
    api = original_api(context)
    dispatch = api.dispatch
    def observed(request):
        response = dispatch(request)
        report['requests'].append(dict(command=request.get('command'), ok=response.get('ok'),
            error_code=(response.get('error') or {}).get('code')))
        return response
    api.dispatch = observed
    rpc(api, 'installation.use', {'installation_id': installation_id})
    return api


def wait(window, expression, timeout=60):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = window.evaluate_js(expression)
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Timed out: '+expression[:400])


def click_selector(window, selector):
    node = 'document.querySelector('+json.dumps(selector)+')'
    wait(window, f"(()=>{{const e={node};const m=[...document.querySelectorAll('dialog[open]')].at(-1);return e&&!e.disabled&&e.getClientRects().length&&(!m||m.contains(e))}})()")
    window.evaluate_js(f"(()=>{{const e={node};e.scrollIntoView({{block:'nearest'}});e.click()}})()")


def click(window, testid):
    click_selector(window, '[data-testid='+testid+']')


def fill(window, selector, value):
    node = 'document.querySelector('+json.dumps(selector)+')'
    wait(window, f'{node} && !{node}.disabled')
    window.evaluate_js(f"(()=>{{const e={node};e.value={json.dumps(value)};e.dispatchEvent(new Event('input',{{bubbles:true}}))}})()")


def ready_profile(window):
    wait(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
    error = window.evaluate_js("document.querySelector('[data-testid=profile-error]')?.innerText")
    assert not error, error


def capture(window, name):
    # Bottom-sheet motion lasts .55s; a two-frame barrier alone captures it
    # half-open. Wait for the product animation before judging modal bounds.
    time.sleep(.7)
    window.evaluate_js('window.__profileCaptureReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__profileCaptureReady=true))')
    wait(window, 'window.__profileCaptureReady')
    time.sleep(.2)
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    dc = win32gui.GetWindowDC(hwnd); source = win32ui.CreateDCFromHandle(dc)
    memory = source.CreateCompatibleDC(); bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, right-left, bottom-top); previous = memory.SelectObject(bitmap)
    filename = args.attempt+'-'+name+'.png'
    report.setdefault('capture_geometry', []).append(window.evaluate_js("""(()=>{
      const dialog=[...document.querySelectorAll('dialog[open]')].at(-1);
      const rect=dialog?.getBoundingClientRect();
      return {viewport:{width:innerWidth,height:innerHeight},dialog:rect?{top:rect.top,bottom:rect.bottom,height:rect.height}:null,
        footerButtons:[...(dialog?.querySelectorAll('footer button')||[])].map(button=>{const r=button.getBoundingClientRect();return{text:button.innerText,top:r.top,bottom:r.bottom,visible:r.top>=0&&r.bottom<=innerHeight}})};
    })()"""))
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (right-left,bottom-top), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out/filename)
        report['screenshots'].append(filename)
    finally:
        memory.SelectObject(previous); win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd, dc)


def profile_id(window):
    return window.evaluate_js("document.querySelector('[data-testid=profile-select]').dataset.value")


def install_version(window, version, relation):
    selected_source['path'] = sources[version]
    click(window, 'import-open'); click(window, 'import-zip')
    wait(window, "document.querySelector('[data-testid=import-preview]')")
    assert window.evaluate_js("document.querySelector('input[placeholder=\"未识别，可手动填写\"]').value") == version
    capture(window, relation+'-version-preview')
    click(window, 'import-commit')
    wait(window, "document.querySelector('[data-testid=import-result]')")
    assert '模组卡片' in window.evaluate_js("document.querySelector('[data-testid=import-result]').innerText")
    click(window, 'import-install-preview')
    wait(window, "document.querySelector('[data-testid=import-update-preview]')")
    text = window.evaluate_js("document.querySelector('[data-testid=import-update-preview]').innerText")
    assert ('可升级' if relation == 'upgrade' else '将降级') in text, text
    capture(window, relation+'-confirm')
    # No write before the explicit confirmation action.
    previous = '1.0.0.0' if relation == 'upgrade' else '2.0.0.0'
    assert (game/'Mods/UiVersion.dll').read_bytes() == versions[previous]
    click(window, 'import-install-confirm')
    wait(window, "document.body.innerText.includes('已安装并启用此版本')")
    assert (game/'Mods/UiVersion.dll').read_bytes() == versions[version]
    expected = 'obsolete.png' if relation == 'downgrade' else 'new.png'
    absent = 'new.png' if relation == 'downgrade' else 'obsolete.png'
    assert (game/'Mods/UiVersion'/expected).exists() and not (game/'Mods/UiVersion'/absent).exists()
    assert not (game/'README.txt').exists()
    report['checks'].append(relation+' metadata preview -> explicit replacement confirmation -> actual file bytes and obsolete-file cleanup')
    capture(window, relation+'-applied')
    click_selector(window, '.import-dialog button[aria-label="关闭导入"]')
    wait(window, "!document.querySelector('.import-dialog[open]')")


def observe():
    window = windows[0]
    try:
        assert window.events.loaded.wait(30)
        wait(window, "document.querySelectorAll('[data-testid=workspace-mod-row]').length===2")
        click(window, 'profile-open'); ready_profile(window)
        click(window, 'profile-new')
        fill(window, '[data-testid=profile-name]', 'WebView 当前方案')
        click(window, 'profile-capture-new')
        progress = wait(window, "(()=>{const text=document.querySelector('[data-testid=profile-progress]')?.innerText;return text?.includes('个文件')?text:null})()")
        assert '首次保存' in progress
        report['capture_progress'] = progress
        wait(window, "document.querySelector('[data-testid=profile-state]')?.dataset.dirty==='false'")
        ready_profile(window)
        original_id = profile_id(window)
        current = contexts[0].profiles.get(original_id)
        assert len(current['selections']) == 2 and all(item['selected'] for item in current['selections'])
        report['checks'].append('Capture saved loading state including an external Mod into a committed scheme')
        capture(window, 'captured-current')
        revision = window.evaluate_js("document.querySelector('[data-testid=profile-state]').dataset.revision")
        click(window, 'profile-more'); click(window, 'profile-capture-replace')
        click(window, 'profile-capture-replace-confirm')
        wait(window, "document.querySelector('[data-testid=profile-notice]')?.innerText.includes('已用模组页当前的列表覆盖')")
        ready_profile(window)
        assert profile_id(window) == original_id
        assert window.evaluate_js("document.querySelector('[data-testid=profile-state]').dataset.revision") != revision
        assert window.evaluate_js("document.querySelector('[data-testid=profile-current-name]').innerText") == 'WebView 当前方案'
        report['checks'].append('Replace capture (更多 → 用当前模组列表覆盖 → 覆盖) keeps the profile identity and name and advances its revision')
        # Change the external Mod only in the draft, then save the edit as new.
        external = next(item for item in contexts[0].profiles.get(original_id)['selections'] if item['display_name'] == 'ExistingMod')
        click_selector(window, '[data-testid=profile-selection][data-release-id="'+external['release_id']+'"] [data-testid=profile-toggle]')
        wait(window, "document.querySelector('[data-testid=profile-state]')?.dataset.dirty==='true'")
        click(window, 'profile-save-as')
        assert window.evaluate_js("document.querySelector('[data-testid=profile-name]').value") == 'WebView 当前方案 副本'
        capture(window, 'save-as-dialog')
        click(window, 'profile-save-as-confirm')
        wait(window, "document.querySelector('[data-testid=profile-current-name]')?.innerText.includes('副本') && document.querySelector('[data-testid=profile-state]')?.dataset.dirty==='false'")
        ready_profile(window)
        clone_id = profile_id(window)
        assert clone_id != original_id
        # The original committed scheme remains unchanged by save-as.
        manifest = contexts[0].profiles.export(original_id)
        assert next(item for item in manifest['selections'] if item['release_id'] == external['release_id'])['selected'] is False
        # Its committed snapshot is separately retained until an explicit replace.
        from backend.storage import Store
        store = Store(contexts[0].paths.database)
        try:
            assert next(item for item in store.get('Profile', original_id).selections if str(item.release_id) == external['release_id']).selected is True
        finally:
            store.close()
        report['checks'].append('Edit -> save-as creates a new committed scheme while preserving original committed selections')
        capture(window, 'saved-as-new')
        click(window, 'profile-more'); click(window, 'profile-share-export')
        wait(window, "document.querySelector('[data-testid=profile-notice]')?.innerText.includes('整合包已导出')")
        ready_profile(window); assert bundle.exists()
        with zipfile.ZipFile(bundle) as archive:
            exported = json.loads(archive.read('modpack.json'))
            assert len(exported['releases']) == 2 and len(exported['artifacts']) >= 4
        report['checks'].append('Full bundle export contains both fixed Mod versions and private artifact bytes')
        click(window, 'workspace-mods')
        wait(window, "document.querySelector('[data-testid=mod-workspace]')?.offsetParent !== null")
        # A captured external Mod must have refreshed the root workspace receipt.
        click_selector(window, '[data-mod-id="'+old['release_id']+'"] .leaf-switch')
        click(window, 'workspace-save')
        wait(window, "document.querySelector('.garden-notice')?.innerText.includes('已保存并应用')")
        assert not (game/'Mods/UiVersion.dll').exists()
        assert (game/'Mods/ExistingMod.dll').exists()
        report['checks'].append('Capture refreshes the main card snapshot; subsequent ordinary save succeeds without manual refresh')
        click(window, 'profile-open'); ready_profile(window)
        click(window, 'profile-share-import')
        wait(window, "document.querySelector('[data-testid=profile-import-preview][open]')")
        assert '导入 Mod 整合包' in window.evaluate_js("document.querySelector('[data-testid=profile-import-preview]').innerText")
        fill(window, 'input[aria-label="导入方案名称"]', 'WebView 整合包导入')
        capture(window, 'bundle-import-preview')
        click(window, 'profile-import-apply')
        wait(window, "document.querySelector('[data-testid=profile-notice]')?.innerText.includes('已将「WebView 整合包导入」应用')")
        ready_profile(window)
        assert (game/'Mods/UiVersion.dll').read_bytes() == versions['1.0.0.0']
        assert not (game/'Mods/ExistingMod.dll').exists()
        report['checks'].append('Bundle preview -> import-and-apply restores the selected Mod and disables the omitted external Mod')
        capture(window, 'bundle-applied')
        click(window, 'workspace-mods')
        install_version(window, '2.0.0.0', 'upgrade')
        install_version(window, '1.0.0.0', 'downgrade')
        assert (game/'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'
        assert (game/'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'
        report['checks'].append('Save and unrelated-file sentinels remain unchanged across all UI operations')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
            capture(window, 'failure')
        except Exception:
            pass
    finally:
        window.destroy()


def create(title, **options):
    window = original_create(title, **options)
    windows.append(window)
    return window


def start(*values, **options):
    context = contexts[0]
    context.choose_import_source = lambda kind: str(selected_source['path']) if kind == 'zip' else None
    context.choose_profile_share_source = lambda: str(bundle)
    context.choose_profile_share_destination = lambda kind: str(bundle if kind == 'bundle' else runtime/'scheme.json')
    return original_start(observe, **options)


composition.DesktopApi = factory
webview.create_window, webview.start = create, start
report['exit_code'] = main.main(['--psmm-data-root', str(data)])
report['command_counts'] = {name: sum(row['command'] == name for row in report['requests']) for name in sorted({row['command'] for row in report['requests']})}
(out/('result-'+args.attempt+'.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(dict(status=report['status'], exit_code=report['exit_code'], checks=len(report['checks']), error=report.get('error')), ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status'] == 'PASS' and report['exit_code'] == 0 else 1)
