"""Actual main/WebView -> selected evidence -> query -> suggestion -> ordinary Mod.

Owned synthetic resources only. Native picker substitution supplies the owned
directory chosen by the user; it does not substitute import, files, HTTP or jobs.
Automated DOM input is not trusted pointer, manual experience or DPI acceptance.
"""
import argparse
import ctypes
import faulthandler
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--provider-url', required=True)
parser.add_argument('--offline-url', required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT/'docs/development/evidence/PS-04-05')
for endpoint in (args.provider_url, args.offline_url):
    uri = urlsplit(endpoint)
    assert uri.scheme == 'http' and uri.hostname == '127.0.0.1' and uri.path == '/v1'
for key, name in [('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'), ('TEMP', 'temp'), ('TMP', 'temp')]:
    (out/name).mkdir(exist_ok=True)
    os.environ[key] = str(out/name)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.app.paths import reject_reparse
from tests.integration.harness.transaction_recovery import TransactionFixture, request, finish, tree
from tests.integration.harness.ai_workbench_provider import LOG_TEXT, FINAL_TEXT

reject_reparse(out)
fixture = TransactionFixture(out)
game, data = fixture.game, fixture.data
release = fixture.publish({'Mods/recommend.png': b'owned-workbench-recommend-resource'}, '闭环壁纸包')
other = fixture.publish({'Plugins/independent.png': b'independent-workbench-mod'}, '保留的 Mod')
fixture.call('mod.action', dict(installation_id=fixture.installation, release_id=other['release_id'],
    manifest_hash=other['manifest_hash'], action='install', idempotency_key=str(uuid4())))
(game/'MelonLoader/Latest.log').write_text(LOG_TEXT+'[12:00:01] workbench-unselected-log-secret\n', encoding='utf-8')
started = fixture.context.api.dispatch(request('logs.watch', dict(installation_id=fixture.installation)))
assert started['ok']
deadline = time.monotonic()+8
entries = []
while len(entries) < 2 and time.monotonic() < deadline:
    entries = fixture.call('evidence.list', dict(installation_id=fixture.installation))['entries']
    time.sleep(.03)
assert len(entries) == 2
evidence_id = next(row['evidence_id'] for row in entries if '用户已批准删除存档' in row['text'])
fixture.context.api.dispatch(request('task.cancel', dict(task_id=started['data']['task_id'])))
assert finish(fixture.context.api, started['data'])['state'] == 'cancelled'
(out/'workbench-meta.json').write_text(json.dumps(dict(selected_evidence_id=evidence_id, display_name='闭环壁纸包'),
    ensure_ascii=False), encoding='utf-8')
fixture.close()
baseline = tree(game)
target = game/'Mods/recommend.png'
imports = {}
for name in ('cold', 'closed', 'offline'):
    source = out/('owned-ui-'+name)
    (source/'Mods').mkdir(parents=True)
    payload = ('owned-ui-'+name+'-resource').encode()
    (source/'Mods'/('ui-'+name+'.png')).write_bytes(payload)
    imports[name] = dict(source=source, baseline=tree(source), payload=payload,
                         target=game/'Mods'/('ui-'+name+'.png'))
report = dict(status='FAIL', profile='desktop-local', checks=[], screenshots=[], commands=[], actual_imports=[],
    input_method='Automated DOM and hit-tested visible viewport in actual WebView; no trusted pointer/manual/DPI claim',
    scope='Actual directory picker input, import/SQLite, selected log watcher, localhost HTTP/SSE, user suggestion and ordinary file actions')
contexts, windows, managers = [], [], []
ai_task_ids = set()
original_api = composition.DesktopApi
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    api = original_api(context)
    dispatch = api.dispatch
    def observed(value):
        response = dispatch(value)
        row = dict(command=value.get('command'), ok=response.get('ok'))
        if value.get('command') == 'ai.chat.send' and response.get('ok'):
            ai_task_ids.add(response['data']['task_id'])
        if value.get('command') == 'mod.action':
            assert set(value['payload']) == {'installation_id','release_id','manifest_hash','action','idempotency_key'}
            row['action'] = value['payload']['action']
        report['commands'].append(row)
        return response
    api.dispatch = observed
    return api
composition.DesktopApi = factory


def audit(event, values):
    if event in {'socket.bind','socket.connect'}:
        address = values[1]
        if not isinstance(address, tuple) or address[0] not in {'127.0.0.1','::1'}:
            raise PermissionError('Only owned localhost sockets')
    if event == 'open' and str(values[0]).replace('\\','/').lower().startswith(('e:/workspace/github/rimcrow/','e:/entertainment/steam/')):
        raise PermissionError('No real game/source access')
sys.addaudithook(audit)


def element(name):
    return "document.querySelector('[data-testid="+name+"]')"


def wait(window, expression, timeout=40):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(expression)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('WebView condition timed out: '+expression[:240])


def visible(window, selector):
    window.evaluate_js(selector+'.scrollIntoView({block:"center"})')
    wait(window, '(()=>{const e='+selector+';const r=e.getBoundingClientRect();const h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return r.width>0 && r.height>0 && r.top>=0 && r.bottom<=innerHeight && r.left>=0 && r.right<=innerWidth && (h===e || e.contains(h))})()')


def click_selector(window, selector):
    wait(window, f'Boolean({selector}) && !{selector}.disabled')
    visible(window, selector)
    window.evaluate_js(selector+'.click()')


def click(window, name):
    click_selector(window, element(name))


def fill_selector(window, selector, value, event='input'):
    wait(window, f'Boolean({selector}) && !{selector}.disabled')
    visible(window, selector)
    window.evaluate_js(f"(()=>{{const e={selector};e.value={json.dumps(value)};e.dispatchEvent(new Event('{event}',{{bubbles:true}}));}})()")


def fill(window, name, value, event='input'):
    fill_selector(window, element(name), value, event)


def screenshot(window, label, marker):
    time.sleep(.3)
    visible(window, marker)
    window.evaluate_js('window.__captureReady=false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.__captureReady=true))')
    wait(window, 'window.__captureReady')
    time.sleep(.2)
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
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def idle(window):
    wait(window, element('ai-panel')+"?.getAttribute('aria-busy')==='false'")


def workers_stopped(timeout=5):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        with managers[0]._lock:
            if not any(job.thread.is_alive() for task_id, job in managers[0]._jobs.items() if task_id in ai_task_ids):
                return
        time.sleep(.02)
    raise AssertionError('AI worker remains alive after terminal result')


def unchanged_resources():
    actual = tree(game)
    assert {key:actual[key] for key in baseline} == baseline
    for value in imports.values():
        assert tree(value['source']) == value['baseline']
    assert not any(row['command'].startswith(('plan.','profile.','assistant.','game.launch','config.save')) for row in report['commands'])


def row_for(release_id):
    return "Array.from(document.querySelectorAll('[data-testid=mod-row]')).find(r=>r.dataset.releaseId==="+json.dumps(release_id)+')'


def import_and_manage(window, name):
    value = imports[name]
    before = tree(game)
    contexts[0].choose_import_source = lambda kind: str(value['source']) if kind == 'directory' else None
    count = sum(row['command']=='ai.chat.send' for row in report['commands'])
    click(window, 'import-open')
    click(window, 'import-directory')
    wait(window, element('import-preview')+f"?.innerText.includes('Mods/ui-{name}.png')")
    assert tree(game) == before
    fill_selector(window, element('import-preview')+'.querySelector("input")', '实际导入 '+name)
    click(window, 'import-commit')
    wait(window, element('import-result')+"?.innerText.includes('已加入包库') && !"+element('import-progress'))
    imported_id = window.evaluate_js(element('import-result')+'.querySelector("code").textContent')
    assert imported_id and len(imported_id) == 36
    assert tree(game) == before
    click(window, 'import-show-library')
    wait(window, "document.querySelector('.import-dialog')?.open===false")
    row = row_for(imported_id)
    wait(window, '('+row+")?.dataset.modState==='not_installed' && "+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
    for action, state in (('install','enabled'), ('disable','disabled'), ('enable','enabled'), ('uninstall','not_installed')):
        click_selector(window, '('+row+f').querySelector("[data-testid=mod-{action}]")')
        wait(window, '('+row+f")?.dataset.modState==='{state}' && "+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
        assert not window.evaluate_js(element('mod-error')+'?.innerText')
        assert value['target'].exists() == (state == 'enabled')
        if value['target'].exists():
            assert value['target'].read_bytes() == value['payload']
        unchanged_resources()
    assert tree(game) == before
    assert sum(row['command']=='ai.chat.send' for row in report['commands']) == count
    report['actual_imports'].append(dict(name=name, release_id=imported_id, source_unchanged=True, actions=['install','disable','enable','uninstall']))


def ask(window, question):
    click(window, 'ai-new-session')
    fill(window, 'ai-question', question)
    click(window, 'ai-send')


def observe():
    window = windows[0]
    try:
        assert window.events.loaded.wait(30)
        click(window, 'installation-open')
        selection = "Array.from(document.querySelectorAll('.installation-content .installation-row')).find(r=>r.querySelector('code')?.textContent==="+json.dumps(str(game))+')'
        click_selector(window, '('+selection+")?.querySelector('button')")
        wait(window, "document.querySelector('.installation-dialog')?.open===false")
        wait(window, element('mod-manager')+"?.getAttribute('aria-busy')==='false' && !"+element('mod-refresh')+'.disabled')
        import_and_manage(window, 'cold')
        assert not any(row['command'].startswith('ai.') for row in report['commands'])
        report['checks'].append('Unconfigured cold UI actually adds a directory and installs/disables/enables/uninstalls without any AI request')
        click(window, 'workspace-ai')
        idle(window)
        assert not window.evaluate_js(element('ai-allow-queries')+'.checked')
        fill(window, 'ai-base-url', args.provider_url)
        fill(window, 'ai-model', 'synthetic-workbench')
        click(window, 'ai-save')
        idle(window)
        click(window, 'ai-allow-queries')
        wait(window, element('ai-evidence')+f"?.querySelector('option[value=\"{evidence_id}\"]')")
        assert window.evaluate_js(element('ai-evidence')+'.value') == ''
        fill(window, 'ai-evidence', evidence_id, 'change')
        assert window.evaluate_js(element('ai-evidence')+'.value') == evidence_id
        report['checks'].append('Queries default off; user explicitly selects one actual logs.watch evidence entry through the UI')
        before = tree(game)
        count = sum(row['command']=='mod.action' for row in report['commands'])
        ask(window, 'CASE044 解释所选日志并推荐可安装的壁纸包')
        wait(window, element('ai-response')+f'?.innerText.includes({json.dumps(FINAL_TEXT)}) && '+element('ai-suggestion')+"?.dataset.action==='install' && !"+element('ai-progress'))
        idle(window)
        workers_stopped()
        assert window.evaluate_js(element('ai-suggestion-name')+'.innerText') == '闭环壁纸包'
        assert not window.evaluate_js('Boolean(window.__WorkbenchInjected)')
        assert not window.evaluate_js(element('ai-messages')+'.querySelector("script")')
        assert not window.evaluate_js(element('ai-suggestion')+'.querySelector("script")')
        assert 'workbench-reason-secret' not in window.evaluate_js(element('ai-suggestion-reason')+'.innerText')
        assert tree(game) == before
        assert sum(row['command']=='mod.action' for row in report['commands']) == count
        screenshot(window, 'diagnostic-suggestion-before-click', element('ai-suggestion'))
        report['checks'].append('Actual selected log, search/get-package results produce a passive redacted plain-text recommendation; embedded approval/delete/script instructions never execute')
        click(window, 'ai-suggestion-action')
        wait(window, element('ai-suggestion')+"?.dataset.status==='completed' && "+element('ai-action-notice')+"?.innerText.includes('完成')")
        assert target.read_bytes() == b'owned-workbench-recommend-resource'
        assert sum(row['command']=='mod.action' for row in report['commands']) == count+1
        unchanged_resources()
        screenshot(window, 'suggestion-install-completed', element('ai-suggestion'))
        report['checks'].append('User ordinary install button dispatches the existing mod.action and writes verified bytes without a Profile, plan or native approval')
        before = tree(game)
        count = sum(row['command']=='mod.action' for row in report['commands'])
        ask(window, 'CASE044 无效选择')
        wait(window, element('ai-response')+"?.innerText.includes('无效建议及越界操作没有执行') && !"+element('ai-progress'))
        idle(window)
        workers_stopped()
        assert not window.evaluate_js('Boolean('+element('ai-suggestion')+')')
        assert tree(game) == before and sum(row['command']=='mod.action' for row in report['commands']) == count
        screenshot(window, 'invalid-selection-visible', element('ai-response'))
        report['checks'].append('Unknown release, model-supplied direct mod.action and unselected log are denied and visibly explained without writes')
        ask(window, 'CASE044 取消诊断')
        wait(window, element('ai-progress')+'?.dataset.taskId && !'+element('ai-cancel')+'.disabled')
        deadline = time.monotonic()+20
        while not (out/'provider-stall-received.json').exists() and time.monotonic()<deadline:
            time.sleep(.03)
        assert (out/'provider-stall-received.json').exists(), 'Second actual SSE request must be received before cancel'
        click(window, 'ai-cancel')
        wait(window, '('+element('ai-notice')+"?.innerText.includes('取消') || "+element('ai-error')+"?.innerText.includes('取消')) && !"+element('ai-progress'))
        idle(window)
        workers_stopped()
        assert not window.evaluate_js('Boolean('+element('ai-suggestion')+')')
        assert not window.evaluate_js('Boolean('+element('ai-retry')+')')
        assert tree(game) == before
        report['checks'].append('User cancellation during the second real SSE query shows cancellation and releases the accepted AI worker; no partial actionable recommendation')
        click(window, 'workspace-mods')
        wait(window, element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
        row = row_for(release['release_id'])
        wait(window, '('+row+")?.dataset.modState==='enabled'")
        import_and_manage(window, 'closed')
        report['checks'].append('Closing the AI panel retains the actual recommendation-installed state and a fresh import plus all four ordinary actions remain available')
        click(window, 'workspace-ai')
        idle(window)
        fill(window, 'ai-base-url', args.offline_url)
        fill(window, 'ai-model', 'synthetic-workbench')
        click(window, 'ai-save')
        idle(window)
        ask(window, 'CASE044 离线诊断')
        wait(window, element('ai-error')+"?.innerText.includes('连接') && !"+element('ai-progress'))
        idle(window)
        workers_stopped()
        screenshot(window, 'offline-visible-local-management-available', element('ai-error'))
        assert not window.evaluate_js('Boolean('+element('ai-suggestion')+')')
        assert tree(game) == before
        report['checks'].append('Closed owned localhost endpoint shows a connection error and manual retry without recommendations, target changes or automatic requests')
        click(window, 'workspace-mods')
        wait(window, element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
        import_and_manage(window, 'offline')
        screenshot(window, 'offline-import-uninstall-completed', element('mod-notice'))
        report['checks'].append('Offline AI configuration does not block actual fresh directory import/install/disable/enable/uninstall; sources and unrelated files survive')
        unchanged_resources()
        assert sum(row['command']=='mod.action' for row in report['commands']) == 13
        assert sum(row['command']=='package.import_commit' and row['ok'] for row in report['commands']) == 3
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
            screenshot(window, 'failure', element('workspace-mods'))
        except Exception:
            pass
    finally:
        window.destroy()


def forbidden_confirmation(*values):
    raise AssertionError('Ordinary actions must not open native approval')


def create(*values, **options):
    options.update(width=1280, height=1000)
    window = original_create(*values, **options)
    window.create_confirmation_dialog = forbidden_confirmation
    windows.append(window)
    return window


def start(*values, **options):
    return original_start(observe, **options)


webview.create_window, webview.start = create, start
report['app_exit_code'] = main.main(['--psmm-data-root',str(data)])
with managers[0]._lock:
    report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),
                             subscriptions=len(managers[0]._subscriptions))
if any(report['cleanup'].values()):
    report['status'] = 'FAIL'
report['webview_lifecycle'] = windows[0]._psmm_callback_lifecycle.stats
frames = sys._current_frames()
report['python_threads_at_exit'] = [dict(name=thread.name, daemon=thread.daemon, alive=thread.is_alive(),
    stack=traceback.format_stack(frames[thread.ident]) if thread.ident in frames else [])
    for thread in threading.enumerate()]
(out/'ai-workbench.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], checks=len(report['checks']), error=report.get('error'))))
faulthandler.dump_traceback_later(15, repeat=True)
raise SystemExit(0 if report['status']=='PASS' and report['app_exit_code']==0 else 1)
