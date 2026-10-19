"""Real main/WebView2/JS/Python round trip; synthetic worker is explicitly injected.

This is a desktop observer, not a product runner. All API calls originate in the
real WebView document. Only browser OS dispatch is intercepted to avoid opening
an external browser. A fresh subprocess is mandatory for each invocation.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=['first', 'reopen'], required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
out.mkdir(parents=True, exist_ok=True)
data = out / 'isolated-data'
os.environ['LOCALAPPDATA'] = str(out / 'local')
os.environ['APPDATA'] = str(out / 'roaming')
os.environ['TEMP'] = os.environ['TMP'] = str(out / 'temp')
Path(os.environ['TEMP']).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

from pydantic import BaseModel, ConfigDict, Field
import webview
import webbrowser
import psutil
import win32gui
import win32ui
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi

report = {'status': 'FAIL', 'phase': args.phase, 'data_root': str(data), 'checks': [], 'requests': [],
          'scope': 'real main.main -> composition -> WebView2 -> bundled Vue -> real dispatch; synthetic injected worker only'}
contexts = []
windows = []
external = []
network_violations = []
captured_jobs = []
document_loads = 0
late_reply_started = threading.Event()
late_reply_release = threading.Event()
late_reply_delivered = threading.Event()


def diagnostics(manager):
    with manager._lock:
        return {'subscriptions': len(manager._subscriptions),
                'active_workers': sum(job.thread.is_alive() for job in manager._jobs.values())}


def capture_owned_window(hwnd, destination):
    """Capture only our HWND; a screen crop may expose unrelated foreground apps."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    window_dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    previous = memory_dc.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 2) == 1
        image = Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)
        image.save(destination)
    finally:
        memory_dc.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)


class ProbePayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    steps: int = Field(default=8, ge=1, le=200)
    fail: bool = False


def factory(context):
    contexts.append(context)
    captured_jobs.append(context.jobs)
    def probe(request_id, payload):
        def work(worker):
            for index in range(payload.steps):
                time.sleep(.025)
                worker.check_cancelled()
                worker.progress(completed=index + 1, total=payload.steps, stage='probe', message_code='probe.progress')
            if payload.fail:
                raise RuntimeError('synthetic credential=DO_NOT_REPORT C:\\private-synthetic\\secret.txt')
            return {'checked': payload.steps}
        return context.jobs.start(request_id, work)
    api = DesktopApi(context, test_commands={'test.probe': (ProbePayload, probe)})
    dispatch = api.dispatch
    def delayed_delivery(request):
        response = dispatch(request)
        if isinstance(request, dict) and request.get('request_id') == 'late-document-result':
            late_reply_started.set()
            if not late_reply_release.wait(20):
                raise RuntimeError('Delayed result test timed out')
        return response
    api.dispatch = delayed_delivery
    return api


composition.DesktopApi = factory
webbrowser.open = lambda url, *a, **kw: external.append(url) or True
original_create, original_start = webview.create_window, webview.start


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        network_violations.append(event)
        raise PermissionError('Desktop harness prohibits Python business networking')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith('e:/workspace/github/rimcrow/'):
            raise PermissionError('Reference source access prohibited in desktop process')


sys.addaudithook(audit)


def wait_for(window, script, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = window.evaluate_js(script)
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for actual desktop document state')


def reload_document(window):
    before = document_loads
    window.load_html(window.html)
    deadline = time.monotonic() + 15
    while document_loads <= before and time.monotonic() < deadline:
        time.sleep(.05)
    assert document_loads > before
    wait_for(window, "document.body.innerText.includes('本地桌面已就绪')")


serial = 0
def rpc(window, command, payload=None, expected=True, envelope=None):
    global serial
    serial += 1
    request_id = f'harness-{args.phase}-{serial}'
    request = envelope if envelope is not None else {'schema_version': 1, 'request_id': request_id,
                                                   'command': command, 'payload': {} if payload is None else payload}
    script = json.dumps(request, ensure_ascii=True)
    window.evaluate_js(f'window.__bridgeResult=null; window.pywebview.api.dispatch({script}).then(value=>window.__bridgeResult=value)')
    result = wait_for(window, 'window.__bridgeResult')
    # Record protocol summaries only, never raw malicious input or errors.
    report['requests'].append({'command': command, 'request_id': result.get('request_id'), 'ok': result.get('ok'),
                               'error_code': result.get('error', {}).get('code')})
    assert result['ok'] is expected
    if envelope is None:
        assert result['request_id'] == request_id
    assert 'DO_NOT_REPORT' not in json.dumps(result) and 'private-synthetic' not in json.dumps(result)
    return result


def check(name):
    report['checks'].append({'name': name, 'status': 'PASS'})


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "document.body.innerText.includes('本地桌面已就绪')")
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        report['initial_subscriptions'] = diagnostics(contexts[0].jobs)['subscriptions']
        assert report['initial_subscriptions'] == 0
        info = rpc(window, 'app.info')['data']
        assert info['name'] == 'PSModManager' and Path(info['data_root']) == data
        check('real app.info and dispatch-only public API')

        # Hold a real completed app.info result across document destruction.
        # This controls delivery timing, never substitutes its response.
        evaluate = window.evaluate_js
        def observe_delivery(script, *args, **kwargs):
            try:
                return evaluate(script, *args, **kwargs)
            finally:
                if script.startswith('window.pywebview._returnValuesCallbacks') and 'late-document-result' in script:
                    late_reply_delivered.set()
        window.evaluate_js = observe_delivery
        window.evaluate_js("window.pywebview.api.dispatch({schema_version:1,request_id:'late-document-result',command:'app.info',payload:{}}).then(()=>window.__obsoleteReply=true)")
        assert late_reply_started.wait(5)
        reload_document(window)
        late_reply_release.set()
        assert late_reply_delivered.wait(10)
        assert window.evaluate_js('Boolean(window.__obsoleteReply)') is False
        assert rpc(window, 'app.info')['data']['name'] == 'PSModManager'
        check('real delayed dispatch result after document reload is discarded without JS callback error; new requests still work')

        for command in ['unknown', 'os.system', 'shell.execute', 'file.read', '__getattribute__']:
            assert rpc(window, command, expected=False)['error']['code'] == 'UNSUPPORTED_COMMAND'
        for payload in [{'path': 'C:/private-synthetic'}, {'shell': 'whoami'}, {'extra': True}]:
            assert rpc(window, 'app.info', payload, expected=False)['error']['code'] == 'INVALID_REQUEST'
        for payload in [{'steps': 0}, {'steps': 201}, {'steps': '2'}, {'steps': 2, 'unknown': 1}]:
            assert rpc(window, 'test.probe', payload, expected=False)['error']['code'] == 'INVALID_REQUEST'
        bad = {'schema_version': 1, 'request_id': 'negative', 'command': 'app.info', 'payload': {}, 'extra': 1}
        assert rpc(window, 'app.info', expected=False, envelope=bad)['error']['code'] == 'INVALID_REQUEST'
        bad['schema_version'] = True
        bad.pop('extra')
        assert rpc(window, 'app.info', expected=False, envelope=bad)['error']['code'] == 'INVALID_REQUEST'
        check('unknown, shell, arbitrary path, extra fields and out-of-range parameters rejected')

        old_response = rpc(window, 'test.probe', {'steps': 160})
        old = old_response['data']['task_id']
        new_response = rpc(window, 'test.probe', {'steps': 12})
        new = new_response['data']['task_id']
        assert old != new
        subscription = rpc(window, 'task.subscribe', {'task_id': new, 'after_sequence': 0})['data']
        sub_id = subscription['subscription_id']
        rpc(window, 'task.cancel', {'task_id': old})
        rpc(window, 'task.cancel', {'task_id': old})
        events = list(subscription['events'])
        after = max((event['sequence'] for event in events), default=0)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            batch = rpc(window, 'task.events', {'subscription_id': sub_id, 'after_sequence': after})['data']
            events.extend(batch['events'])
            after = max((event['sequence'] for event in events), default=after)
            if batch['snapshot']['state'] in ('succeeded', 'failed', 'cancelled'):
                break
            time.sleep(.05)
        assert batch['snapshot']['state'] == 'succeeded'
        assert rpc(window, 'task.snapshot', {'task_id': old})['data']['state'] == 'cancelled'
        assert [event['sequence'] for event in events] == sorted(set(event['sequence'] for event in events))
        assert all(event['task_id'] == new and event['request_id'] == new_response['request_id'] for event in events)
        terminal = [event for event in events if event['state'] in ('succeeded', 'failed', 'cancelled')]
        assert len(terminal) == 1
        report['event_trace'] = events
        replay = rpc(window, 'task.events', {'subscription_id': sub_id, 'after_sequence': after})['data']
        assert replay['events'] == []
        rpc(window, 'task.unsubscribe', {'subscription_id': sub_id})
        reconnect = rpc(window, 'task.subscribe', {'task_id': new, 'after_sequence': after - 1})['data']
        assert reconnect['snapshot']['state'] == 'succeeded'
        assert len(reconnect['events']) == 1
        rpc(window, 'task.unsubscribe', {'subscription_id': reconnect['subscription_id']})
        check('request/task correlation, ordered unique events, one terminal, old cancellation isolation and resumable snapshot')

        contexts[0].jobs._ttl = .1
        expired = rpc(window, 'task.subscribe', {'task_id': new, 'after_sequence': after})['data']['subscription_id']
        time.sleep(.2)
        assert rpc(window, 'task.events', {'subscription_id': expired, 'after_sequence': after}, expected=False)['error']['code'] == 'SUBSCRIPTION_EXPIRED'
        contexts[0].jobs._ttl = 300
        check('expired real subscription rejected')

        failed = rpc(window, 'test.probe', {'steps': 1, 'fail': True})['data']['task_id']
        time.sleep(.1)
        failure = rpc(window, 'task.snapshot', {'task_id': failed})['data']
        assert failure['state'] == 'failed'
        assert 'DO_NOT_REPORT' not in json.dumps(failure) and 'private-synthetic' not in json.dumps(failure)
        check('real worker exception redacted')

        surviving_task = rpc(window, 'test.probe', {'steps': 160})['data']['task_id']
        old_subscription = rpc(window, 'task.subscribe', {'task_id': surviving_task, 'after_sequence': 0})['data']['subscription_id']
        reload_document(window)
        assert diagnostics(contexts[0].jobs)['subscriptions'] == 0
        assert rpc(window, 'task.events', {'subscription_id': old_subscription, 'after_sequence': 0}, expected=False)['error']['code'] == 'SUBSCRIPTION_EXPIRED'
        restored = rpc(window, 'task.subscribe', {'task_id': surviving_task, 'after_sequence': 0})['data']
        assert restored['snapshot']['task_id'] == surviving_task
        rpc(window, 'task.unsubscribe', {'subscription_id': restored['subscription_id']})
        rpc(window, 'task.cancel', {'task_id': surviving_task})
        check('reload invalidates old document subscription and permits same-task reconnect')

        previous_ui_task = None
        for reload_index in range(2):
            window.evaluate_js("document.querySelector('.info-button').click()")
            wait_for(window, "Boolean(document.querySelector('[data-testid=workspace-check]'))")
            window.evaluate_js("document.querySelector('[data-testid=workspace-check]').click()")
            state = wait_for(window, "(()=>{const x=document.querySelector('[data-testid=workspace-progress]');return x && x.dataset.state==='succeeded' && x.dataset.taskId!==" + json.dumps(previous_ui_task) + " ? {task:x.dataset.taskId,sequence:x.dataset.sequence}:null})()")
            assert state['task'] and int(state['sequence']) > 0
            previous_ui_task = state['task']
            wait_for(window, 'true')
            time.sleep(.3)
            assert diagnostics(contexts[0].jobs)['subscriptions'] == 0
            window.evaluate_js("document.querySelector('[data-testid=workspace-progress]').scrollIntoView({block:'center'})")
            time.sleep(.2)
            hwnd = window.native.Handle.ToInt64()
            capture_owned_window(hwnd, out / f'{args.phase}-ui-{reload_index}.png')
            reload_document(window)
            window.evaluate_js("document.querySelector('.info-button').click()")
            restored_ui = wait_for(window, "(()=>{const x=document.querySelector('[data-testid=workspace-progress]');return x && x.dataset.state==='succeeded' ? {task:x.dataset.taskId,sequence:x.dataset.sequence}:null})()")
            assert restored_ui == state
            report.setdefault('ui_reload_states', []).append({'before': state, 'after': restored_ui})
            time.sleep(.3)
            assert diagnostics(contexts[0].jobs)['subscriptions'] == 0
            window.evaluate_js("document.querySelector('.info-button').click()")
        check('actual UI command/progress and same-task reload resumption without subscription accumulation')

        window.evaluate_js("location.href='https://example.invalid/psmm-bridge-probe'")
        time.sleep(.3)
        assert external == ['https://example.invalid/psmm-bridge-probe']
        assert window.evaluate_js('location.href') == 'about:blank'
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        check('external navigation denied before bridge injection; OS dispatch observed')
        listeners = [item for item in psutil.Process().net_connections(kind='tcp') if item.status == psutil.CONN_LISTEN]
        assert listeners == [] and network_violations == []
        report['python_business_listeners'] = 0
        report['network_boundary'] = 'No Python bind/connect; no Python business listener; WebView2 engine background networking is not claimed absent'
        check('no Python business HTTP listener')
        # Leave a running job and subscription for actual run_desktop finally cleanup.
        pending = rpc(window, 'test.probe', {'steps': 200})['data']['task_id']
        rpc(window, 'task.subscribe', {'task_id': pending, 'after_sequence': 0})
        report['status'] = 'PASS'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['failure_location'] = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                                      for frame in traceback.extract_tb(exc.__traceback__)]
    finally:
        report['owned_children'] = [{'pid': child.pid, 'created_at': child.create_time()} for child in psutil.Process().children(recursive=True)]
        window.destroy()


def create(*args, **kwargs):
    def loaded():
        global document_loads
        document_loads += 1
    window = original_create(*args, **kwargs)
    window.events.loaded += loaded
    windows.append(window)
    return window


def start(*args, **kwargs):
    return original_start(lambda: observe(windows[0]), **kwargs)


webview.create_window, webview.start = create, start
code = main.main(['--psmm-data-root', str(data)])
report['app_exit_code'] = code
if captured_jobs:
    report['cleanup'] = diagnostics(captured_jobs[0])
    if report['cleanup']['active_workers'] or report['cleanup']['subscriptions']:
        report['status'] = 'FAIL'
    else:
        check('actual desktop close joins workers and removes subscriptions')
else:
    report['cleanup'] = {'active_workers': None, 'subscriptions': None}
(out / f'{args.phase}-desktop.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': report['status'], 'phase': args.phase, 'app_exit_code': code, 'checks': len(report['checks']), 'error_type': report.get('error_type')}))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)
