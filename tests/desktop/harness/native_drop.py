"""Real Windows OLE FileDrop -> trusted WebView2 File -> private import handle.

Only this harness's window receives mouse input. Sources and data are synthetic
and isolated; no game or reference executable is accessed or launched.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / '.preview'), 'Use an isolated .preview evidence directory'
assert not out.exists(), 'Use a fresh evidence directory'
out.mkdir(parents=True)
for name in ('LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP'):
    os.environ[name] = str(out/name.lower())
    Path(os.environ[name]).mkdir()
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import webview
import psutil
from types import SimpleNamespace
from backend.app.composition import install_navigation_guard
from backend.app.native_drop import install_native_drop
from backend.app.paths import resolve_paths
from backend.catalog.imports import ImportService
from backend.profiles.sharing import ProfileSharingService
from backend.storage import Store

paths = resolve_paths(str(out/'isolated-data'))
paths.root.mkdir()
Store(paths.database).close()
context = SimpleNamespace(paths=paths, imports=ImportService(paths),
                          profile_sharing=ProfileSharingService(paths, None, None))
sources = out/'synthetic-sources'
sources.mkdir()
(sources/'example.dll').write_bytes(b'synthetic-data-never-executed')
(sources/'resource-mod').mkdir()
(sources/'resource-mod'/'sample.txt').write_text('synthetic', encoding='utf8')
for name, member in [('example.zip', 'Mods/example.dll'), ('bundle.zip', 'modpack.json')]:
    with zipfile.ZipFile(sources/name, 'w') as archive:
        archive.writestr(member, 'synthetic-only')
(sources/'profile.json').write_text('{}', encoding='utf8')
report = dict(status='FAIL', checks=[], events=[], scope='real Windows OLE FileDrop and WebView2 native File objects; grant-only, no library publication or deployment')
children = []

HTML = """<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"><style>body{margin:0;background:#e8fff1;color:#173f29;font:20px sans-serif}main{padding:150px 40px 40px;height:100vh;box-sizing:border-box}pre{font:14px monospace;white-space:pre-wrap}</style></head><body><main><h1>Isolated native file-drop test</h1><p>Only synthetic fixture sources are accepted by this harness.</p><pre id="result"></pre></main><script>window.__events=[];window.__trusted=[];document.addEventListener('drop',e=>window.__trusted.push(e.isTrusted));window.addEventListener('psmm-native-import',e=>{window.__events.push(e.detail);document.getElementById('result').textContent=JSON.stringify(window.__events,null,2)})</script></body></html>"""
window = webview.create_window('PSMM isolated native-drop verification', html=HTML, width=900, height=650)
import logging
window.events.before_show += lambda: install_navigation_guard(window, logging.getLogger('native-drop-harness'))
install_native_drop(window, context)


def wait(expression, timeout=15):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        result = window.evaluate_js(expression)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('Window condition not reached: '+expression)


def observe():
    user32 = ctypes.windll.user32
    class Point(ctypes.Structure):
        _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
    cursor = Point()
    user32.GetCursorPos(ctypes.byref(cursor))
    try:
        assert window.events.loaded.wait(30)
        wait('window.__psmmNativeDropBound')
        children.extend(psutil.Process().children(recursive=True))
        from System import Array, Func, Object, String
        from System.Drawing import Point as DrawingPoint
        from System.Windows.Forms import Button, DataFormats, DataObject, DragDropEffects
        pending = []
        def create_source():
            button = Button()
            button.Text = 'Synthetic native FileDrop source'
            button.Left, button.Top, button.Width, button.Height = 20, 15, 300, 80
            def down(sender, event):
                data = DataObject()
                data.SetData(DataFormats.FileDrop, Array[String](pending))
                sender.DoDragDrop(data, DragDropEffects.Copy)
            button.MouseDown += down
            window.native.Controls.Add(button)
            button.BringToFront()
            window._synthetic_drag_source = (button, down)
            source = button.PointToScreen(DrawingPoint(150, 40))
            target = window.native.browser.webview.PointToScreen(DrawingPoint(650, 400))
            return (source.X, source.Y, target.X, target.Y)
        sx, sy, tx, ty = window.native.Invoke(Func[Object](create_source))
        hwnd = window.native.Handle.ToInt64()
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        for index, (name, expected_kind) in enumerate([
            ('example.dll', 'dll'), ('resource-mod', 'directory'), ('example.zip', 'zip'), ('bundle.zip', 'modpack'),
            ('profile.json', 'modpack')]):
            pending[:] = [str(sources/name)]
            user32.SetCursorPos(sx, sy)
            user32.mouse_event(2, 0, 0, 0, 0)
            time.sleep(.3)
            for step in range(1, 16):
                user32.SetCursorPos(sx+(tx-sx)*step//15, sy+(ty-sy)*step//15)
                time.sleep(.04)
            time.sleep(.2)
            user32.mouse_event(4, 0, 0, 0, 0)
            wait(f'window.__events.length === {index+1}')
            received = window.evaluate_js(f'window.__events[{index}]')
            assert received['errors'] == [], received
            assert received['sources'][0]['kind'] == expected_kind, received
            assert received['sources'][0]['display_name'] == name
            assert set(received['sources'][0]) == {'source_handle', 'display_name', 'kind'}
            report['checks'].append(dict(name='real trusted '+expected_kind+' drag grants native source', status='PASS'))
        assert window.evaluate_js('window.__trusted') == [True] * 5
        # A string path message has no native AdditionalObjects and grants none.
        before = len(context.imports._sources) + len(context.profile_sharing._sources)
        window.evaluate_js("chrome.webview.postMessage('FilesDropped')")
        rejected = window.evaluate_js("(()=>{try{chrome.webview.postMessageWithAdditionalObjects('FilesDropped', [new File(['fake'], 'example.dll')]);return false}catch(error){return error instanceof TypeError}})()")
        assert rejected, 'WebView2 must reject a script-created non-disk File'
        time.sleep(.2)
        assert window.evaluate_js('window.__events.length') == 5
        assert before == len(context.imports._sources) + len(context.profile_sharing._sources)
        report['checks'].append(dict(name='script paths and script-created Files cannot grant filesystem access', status='PASS'))
        assert not list((paths.root/'library').glob('**/*'))
        report['checks'].append(dict(name='drop does not publish package or write game files', status='PASS'))
        report['events'] = window.evaluate_js('window.__events')
        report['status'] = 'PASS'
    except BaseException as exc:
        report['error'] = str(exc)
        report['traceback'] = traceback.format_exc()
    finally:
        user32.mouse_event(4, 0, 0, 0, 0)
        user32.SetCursorPos(cursor.x, cursor.y)
        (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        window.destroy()


try:
    webview.settings.update(ALLOW_DOWNLOADS=False, ALLOW_FILE_URLS=False, REMOTE_DEBUGGING_PORT=None)
    webview.start(observe, gui='edgechromium', debug=False, http_server=False,
                  private_mode=False, storage_path=str(paths.webview))
finally:
    context.imports.close()
    _, alive = psutil.wait_procs(children, timeout=5)
    report['remaining_owned_child_pids'] = [process.pid for process in alive]
    if alive:
        report['status'] = 'FAIL'
        report['error'] = 'Owned WebView2 processes did not exit'
        for process in alive:
            process.terminate()
        psutil.wait_procs(alive, timeout=3)
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(dict(status=report['status'], checks=len(report['checks']), report=str(out/'report.json'))))
raise SystemExit(0 if report['status'] == 'PASS' else 1)
