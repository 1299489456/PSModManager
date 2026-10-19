"""Observe the real main entry and WebView2 without importing legacy business code.

Run in a fresh process for each startup. The evidence root must be project-local.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import ctypes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=['first', 'second'], required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-00-02')
out.mkdir(parents=True, exist_ok=True)
data = out / 'data'
legacy = out / 'legacy-local' / 'RimCrow'
os.environ['LOCALAPPDATA'] = str(out / 'legacy-local')
os.environ['APPDATA'] = str(out / 'roaming')
os.environ['TEMP'] = os.environ['TMP'] = str(out / 'temp')
Path(os.environ['TEMP']).mkdir(exist_ok=True)
# An unavailable proxy provides a repeatable offline browser environment.
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'
legacy.mkdir(parents=True, exist_ok=True)
sentinel = legacy / 'config.json'
if not sentinel.exists():
    sentinel.write_text('{"product":"RimCrow","token":"synthetic-sentinel"}', encoding='utf-8')
old_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
violations = []
requests = []
external = []
report = {'phase': args.phase, 'status': 'FAIL', 'offline_proxy': '127.0.0.1:9', 'legacy_before_sha256': old_hash}


def audit(event, values):
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if str(legacy).replace('\\', '/').lower() in path or 'e:/workspace/github/rimcrow/' in path:
            violations.append({'event': event, 'path': path})
            raise PermissionError('Legacy access denied by desktop harness')
    if event in ('socket.connect', 'socket.bind'):
        violations.append({'event': event})
        raise PermissionError('Python networking disabled by desktop harness')


import webview
import webbrowser
import psutil
import win32gui
import win32ui
from PIL import Image
import main
sys.addaudithook(audit)
webbrowser.open = lambda url, *a, **k: external.append(url) or True  # Observe OS dispatch; do not launch a remote browser during tests.
original_create = webview.create_window
original_start = webview.start
window_ref = []
done = threading.Event()
connections = []


def capture_owned_window(hwnd, destination):
    """Capture only this HWND, never unrelated apps visible on the screen."""
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


def observe(window):
    deadline = time.monotonic() + 40
    try:
        assert window.events.loaded.wait(30), 'WebView page never loaded'
        # Enforce offline at the actual Chromium document network stack, then
        # reload the real bundled page. A proxy flag alone does not prove offline.
        from System import Func, Object
        def cdp(method, parameters):
            task = window.native.Invoke(Func[Object](lambda: window.native.browser.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(method, json.dumps(parameters))))
            return str(task.GetAwaiter().GetResult())
        cdp('Network.enable', {})
        cdp('Network.emulateNetworkConditions', {'offline': True, 'latency': 0, 'downloadThroughput': 0, 'uploadThroughput': 0})
        window.load_html(window.html)
        assert window.events.loaded.wait(20), 'Offline local page failed to reload'
        time.sleep(.8)  # Let the native compositor commit the reloaded document.
        assert window.evaluate_js('navigator.onLine') is False
        report['chromium_document_offline'] = True
        while time.monotonic() < deadline:
            body = window.evaluate_js('document.body.innerText')
            if '本地桌面已就绪' in (body or ''):
                break
            time.sleep(.2)
        else:
            raise AssertionError(f'Frontend never received real app identity: {body}')
        window.evaluate_js("window.pywebview.api.dispatch({schema_version:1,request_id:'desktop-observer',command:'app.info',payload:{}}).then(x => window.__identityResult = x)")
        for _ in range(50):
            info = window.evaluate_js('window.__identityResult || null')
            if info:
                break
            time.sleep(.1)
        assert info['ok'] is True and info['request_id'] == 'desktop-observer'
        info = info['data']
        assert info['name'] == info['keyring_service'] == 'PSModManager'
        assert Path(info['data_root']) == data
        assert info['update_source'] is None
        app_id_pointer = ctypes.c_void_p()
        get_app_id = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
        get_app_id.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        assert get_app_id(ctypes.byref(app_id_pointer)) == 0
        try:
            report['process_app_user_model_id'] = ctypes.wstring_at(app_id_pointer)
            assert report['process_app_user_model_id'] == 'PSModManager.Desktop'
        finally:
            free = ctypes.windll.ole32.CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free(app_id_pointer)
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        browser_profile = str(window.native.browser.user_data_folder)
        assert Path(browser_profile) == data / 'webview2'
        report['webview_profile'] = browser_profile
        hwnd = window.native.Handle.ToInt64()
        assert win32gui.GetWindowText(hwnd) == 'PSModManager'
        time.sleep(.5)
        capture_owned_window(hwnd, out / f'{args.phase}-shell.png')
        window.evaluate_js("document.querySelector('.info-button').click()")
        time.sleep(.8)
        body = window.evaluate_js('document.body.innerText')
        assert str(data) in body
        capture_owned_window(hwnd, out / f'{args.phase}-window.png')
        # Exercise a denied top-level navigation without transmitting anything.
        window.evaluate_js("location.href='https://example.invalid/psmm-navigation-probe'")
        time.sleep(.6)
        assert external == ['https://example.invalid/psmm-navigation-probe'], external
        assert window.evaluate_js('location.href') == 'about:blank'
        assert not violations, violations
        assert not [m for m in sys.modules if m.startswith(('backend.settings', 'backend.api', 'backend.managers', 'backend.scanner', 'backend.migrations'))]
        report.update(status='PASS', window_title=win32gui.GetWindowText(hwnd), identity=info, body=body,
                      bridge_methods=['dispatch'], navigation='remote URL cancelled; OS dispatch observed', request_urls=requests,
                      legacy_access_attempts=violations, tcp_connections=connections,
                      backend_modules=[m for m in sys.modules if m.startswith('backend')])
        report['network_boundary'] = 'Chromium document offline verified; no Python connect/bind; WebView2 engine background sockets observed separately, not claimed absent'
    except Exception as exc:
        report['error'] = repr(exc)
    finally:
        done.set()
        window.destroy()


def create(*a, **kw):
    window = original_create(*a, **kw)
    window_ref.append(window)
    window.events.request_sent += lambda request: requests.append(request.url)
    return window


def monitor():
    while not done.wait(.1):
        try:
            processes = [psutil.Process(), *psutil.Process().children(recursive=True)]
            for process in processes:
                for connection in process.net_connections(kind='tcp'):
                    item = {'pid': process.pid, 'process': process.name(), 'local': list(connection.laddr), 'remote': list(connection.raddr), 'status': connection.status}
                    if item not in connections:
                        connections.append(item)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def start(*a, **kw):
    threading.Thread(target=monitor, daemon=True).start()
    return original_start(lambda: observe(window_ref[0]), **kw)


webview.create_window = create
webview.start = start
code = main.main(['--psmm-data-root', str(data)])
report['exit_code'] = code
report['legacy_access_attempts'] = violations
report['tcp_connections'] = connections
report['request_urls'] = requests
report['source'] = 'Real main.main -> run_desktop -> WebView2 -> bundled Vue -> actual dispatch(app.info); observer only'
report['owned_files_after_exit'] = {p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (data / 'config.json', data / 'psmodmanager.sqlite3', data / 'logs/psmodmanager.log') if p.is_file()}
(out / f'{args.phase}-desktop.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': report['status'], 'phase': args.phase, 'error': report.get('error'), 'exit_code': code}))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)
