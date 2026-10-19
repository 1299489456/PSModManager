"""Actual optional AI interface and ordinary Mod files using owned resources.

Inputs are automated DOM events in a visible WebView, not trusted pointer or
manual acceptance. Only the timeout limit is shortened at the existing service
constructor; all HTTP, SSE, jobs, API and file operations are production paths.
"""
import argparse
import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
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
assert out.is_relative_to(ROOT/'docs/development/evidence/PS-04-04')
for endpoint in (args.provider_url, args.offline_url):
    uri = urlsplit(endpoint)
    assert uri.scheme == 'http' and uri.hostname == '127.0.0.1' and uri.path == '/v1'
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.app.paths import reject_reparse
from tests.integration.harness.transaction_recovery import TransactionFixture, tree

reject_reparse(out)
fixture = TransactionFixture(out)
game, data = fixture.game, fixture.data
release = fixture.publish({'Mods/resilience.png': b'owned-resilience-resource'}, '离线独立安装包')
other = fixture.publish({'Plugins/independent.png': b'independent-resilience-mod'}, '保留的 Mod')
fixture.call('mod.action', dict(installation_id=fixture.installation, release_id=other['release_id'],
    manifest_hash=other['manifest_hash'], action='install', idempotency_key=str(uuid4())))
fixture.close()
baseline = tree(game)
target = game/'Mods/resilience.png'
report = dict(status='FAIL', profile='desktop-local', checks=[], screenshots=[], commands=[],
    input_method='Automated DOM events and hit-tested visible viewport; no trusted pointer/manual/DPI claim',
    timeout_injection=dict(request_seconds=1.5, actual_gateway_http_sse=True))
contexts, windows, managers = [], [], []
ai_task_ids = set()
original_api = composition.DesktopApi
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    api = original_api(context)
    dispatch = api.dispatch
    def observed(request):
        response = dispatch(request)
        row = dict(command=request.get('command'), ok=response.get('ok'))
        if request.get('command') == 'ai.chat.send' and response.get('ok'):
            ai_task_ids.add(response['data']['task_id'])
        if request.get('command') == 'mod.action':
            assert set(request['payload']) == {'installation_id','release_id','manifest_hash','action','idempotency_key'}
            row['action'] = request['payload']['action']
        if request.get('command') in {'task.snapshot','task.events','task.cancel'}:
            snapshot = response.get('data') or {}
            if snapshot.get('state') in ('succeeded','failed','cancelled'):
                row.update(state=snapshot['state'], error_code=(snapshot.get('data') or {}).get('error_code'))
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
    wait(window, '(()=>{const e='+selector+';const r=e.getBoundingClientRect();const h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return r.top>=0 && r.bottom<=innerHeight && r.left>=0 && r.right<=innerWidth && (h===e || e.contains(h))})()')


def click(window, name, parent='document'):
    selector = f"{parent}.querySelector('[data-testid={name}]')"
    wait(window, f'Boolean({selector}) && !{selector}.disabled')
    visible(window, selector)
    window.evaluate_js(selector+'.click()')


def fill(window, name, value):
    selector = element(name)
    wait(window, f'Boolean({selector}) && !{selector}.disabled')
    visible(window, selector)
    window.evaluate_js(f"(()=>{{const e={selector};e.value={json.dumps(value)};e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")


def screenshot(window, label, marker):
    # Session refresh can change scroll anchoring after the terminal event.
    # Hit-test once the layout settles, then allow WebView to paint that scroll
    # position before PrintWindow captures pixels.
    time.sleep(.3)
    visible(window, marker)
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


def no_ai_writes():
    assert tree(game) == baseline
    assert not any(row['command'].startswith(('mod.action','plan.','profile.','assistant.','game.launch','config.save')) for row in report['commands'])


def save(window, model='synthetic-resilience', url=None):
    fill(window, 'ai-base-url', url or args.provider_url)
    fill(window, 'ai-model', model)
    click(window, 'ai-save')
    idle(window)


def ask(window, question):
    click(window, 'ai-new-session')
    fill(window, 'ai-question', question)
    click(window, 'ai-send')


def failed(window, phrase):
    wait(window, element('ai-error')+f'?.innerText.includes({json.dumps(phrase)}) && !'+element('ai-progress'))
    idle(window)
    visible(window, element('ai-error'))
    workers_stopped()
    no_ai_writes()


def unchanged_send_count(delay=.3):
    count = sum(row['command'] == 'ai.chat.send' for row in report['commands'])
    time.sleep(delay)
    assert sum(row['command'] == 'ai.chat.send' for row in report['commands']) == count
    return count


def mod_action(window, action, state):
    row = "Array.from(document.querySelectorAll('[data-testid=mod-row]')).find(r=>r.dataset.releaseId==="+json.dumps(release['release_id'])+')'
    click(window, 'mod-'+action, '('+row+')')
    wait(window, '('+row+f")?.dataset.modState==='{state}' && "+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
    assert not window.evaluate_js(element('mod-error')+'?.innerText')
    assert target.exists() == (state == 'enabled')
    if target.exists():
        assert target.read_bytes() == b'owned-resilience-resource'
    assert {key:tree(game)[key] for key in baseline} == baseline


def observe():
    window = windows[0]
    try:
        assert window.events.loaded.wait(30)
        click(window, 'installation-open')
        row = "Array.from(document.querySelectorAll('.installation-content .installation-row')).find(r=>r.querySelector('code')?.textContent==="+json.dumps(str(game))+')'
        wait(window, 'Boolean('+row+') && !('+row+").querySelector('button').disabled")
        visible(window, '('+row+").querySelector('button')")
        window.evaluate_js('('+row+").querySelector('button').click()")
        wait(window, "document.querySelector('.installation-dialog')?.open===false")
        wait(window, element('mod-manager')+"?.getAttribute('aria-busy')==='false' && !"+element('mod-refresh')+'.disabled')
        assert not any(row['command'].startswith('ai.') for row in report['commands'])
        report['checks'].append('Default ordinary management loads without AI or a provider request')
        click(window, 'workspace-ai')
        idle(window)
        save(window)
        ask(window, 'PS0404 限流')
        failed(window, '请求过多')
        count = unchanged_send_count()
        assert count == 1
        visible(window, element('ai-retry'))
        screenshot(window, 'rate-limit-manual-retry', element('ai-retry'))
        click(window, 'ai-retry')
        wait(window, element('ai-response')+"?.innerText.includes('手动重试成功') && !"+element('ai-progress'))
        idle(window)
        assert unchanged_send_count() == count+1
        assert not window.evaluate_js('Boolean('+element('ai-retry')+')')
        no_ai_writes()
        visible(window, element('ai-response'))
        screenshot(window, 'retry-success', element('ai-response'))
        report['checks'].append('429 is visible without an automatic retry; the explicit retry button sends once and succeeds')
        save(window, 'synthetic-unknown-model')
        ask(window, 'PS0404 未知模型')
        failed(window, '模型')
        visible(window, element('ai-retry'))
        screenshot(window, 'unknown-model', element('ai-retry'))
        unchanged_send_count()
        report['checks'].append('Unknown model response is visible with a manual retry option and no automatic request')
        save(window)
        ask(window, 'PS0404 中断')
        failed(window, '中断')
        wait(window, element('ai-response')+"?.innerText.includes('已收到的部分回复')")
        wait(window, element('ai-partial-reply')+"?.innerText.includes('未完成')")
        visible(window, element('ai-response'))
        visible(window, element('ai-partial-reply'))
        visible(window, element('ai-error'))
        screenshot(window, 'partial-reply-interrupted', element('ai-error'))
        unchanged_send_count()
        report['checks'].append('SSE interruption retains received text and explicitly displays the failure instead of completion')
        ask(window, 'PS0404 取消')
        wait(window, element('ai-progress')+'?.dataset.taskId && '+element('ai-response')+"?.innerText.includes('取消前已收到流') && !"+element('ai-cancel')+'.disabled')
        click(window, 'ai-cancel')
        wait(window, '('+element('ai-notice')+"?.innerText.includes('取消') || "+element('ai-error')+"?.innerText.includes('取消')) && !"+element('ai-progress'))
        idle(window)
        workers_stopped()
        assert not window.evaluate_js('Boolean('+element('ai-retry')+')')
        unchanged_send_count(.5)
        no_ai_writes()
        marker = element('ai-notice') if window.evaluate_js('Boolean('+element('ai-notice')+')') else element('ai-error')
        visible(window, marker)
        screenshot(window, 'cancelled', marker)
        report['checks'].append('Explicit cancellation stops the real waiting HTTP task, shows cancellation and releases its worker without retry')
        original_limits = contexts[0].ai.limits
        contexts[0].ai.limits = replace(original_limits, request_seconds=1.5)
        try:
            started = time.monotonic()
            ask(window, 'PS0404 超时')
            failed(window, '超时')
            assert time.monotonic()-started < 15
        finally:
            contexts[0].ai.limits = original_limits
        visible(window, element('ai-retry'))
        screenshot(window, 'total-timeout', element('ai-retry'))
        unchanged_send_count(.5)
        report['checks'].append('Injected 1.5 second total deadline stops real HTTP/SSE and its worker and shows timeout without retry')
        ask(window, 'PS0404 普通说明')
        wait(window, element('ai-response')+"?.innerText.includes('不证明游戏问题已修复') && !"+element('ai-progress'))
        idle(window)
        workers_stopped()
        no_ai_writes()
        report['checks'].append('Actual post-failure send remains available and does not claim gameplay repair or invent fees')
        save(window, url=args.offline_url)
        ask(window, 'PS0404 离线')
        failed(window, '连接')
        visible(window, element('ai-retry'))
        unchanged_send_count()
        report['checks'].append('Closed owned localhost endpoint shows a connection failure without background retry')
        count = unchanged_send_count()
        click(window, 'workspace-mods')
        wait(window, element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
        for action, state in (('install','enabled'), ('disable','disabled'), ('enable','enabled'), ('uninstall','not_installed')):
            mod_action(window, action, state)
        assert unchanged_send_count() == count
        assert tree(game) == baseline
        assert [row['action'] for row in report['commands'] if row['command']=='mod.action'] == ['install','disable','enable','uninstall']
        assert not any(row['command'].startswith(('plan.','profile.','assistant.','game.launch','config.save')) for row in report['commands'])
        visible(window, element('mod-notice'))
        screenshot(window, 'offline-local-uninstall-complete', element('mod-notice'))
        report['checks'].append('Offline ordinary install/disable/enable/uninstall complete through real buttons, files and refreshed list; unrelated bytes survive')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
        except Exception:
            pass
    finally:
        window.destroy()


def forbidden_confirmation(*values):
    raise AssertionError('Optional AI failure must not require native approval')


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
(out/'ai-resilience.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if report['status']=='PASS' and report['app_exit_code']==0 else 1)
