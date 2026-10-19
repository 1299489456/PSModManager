"""Exercise the real selector. Only the native folder picker returns a fixture.

No game/loader is executed. Product discovery, jobs, bridge, bundled Vue and
SQLite persistence are real. Screenshots capture only our own window.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-01-01'), 'Isolated PS-01-01 evidence required'
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
data = out / 'isolated-data'
for name, relative in [('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'), ('TEMP', 'temp'), ('TMP', 'temp')]:
    os.environ[name] = str(out / relative)
    (out / relative).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import webview
import psutil
import win32gui
import win32ui
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi

game = out / 'synthetic-installation'
game.mkdir()
# Explicitly synthetic PE/metadata headers: inspected as data, never executed.
pe = bytearray(256)
pe[:2] = b'MZ'
pe[60:64] = (128).to_bytes(4, 'little')
pe[128:132] = b'PE\0\0'
for name in ('Probably Stolen.exe', 'GameAssembly.dll', 'Probably Stolen_Data/il2cpp_data/Metadata/global-metadata.dat', 'MelonLoader/net6/MelonLoader.dll'):
    target = game / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'\xaf\x1b\xb1\xfa' + (29).to_bytes(4, 'little') + b'synthetic' if name.endswith('.dat') else bytes(pe))


def tree_hashes():
    return {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(game.rglob('*')) if p.is_file()}


report = {'status': 'FAIL', 'checks': [], 'source_before': tree_hashes(),
          'scope': 'real main -> WebView2 -> bundled selector -> real discovery/service/SQLite; native picker alone returns synthetic path'}
contexts, managers, windows, picked, violations = [], [], [], [], []


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    def choose(kind):
        assert kind == 'manual'
        picked.append(kind)
        return str(game)
    context.choose_installation_folder = choose
    return DesktopApi(context)


composition.DesktopApi = factory
original_create, original_start = webview.create_window, webview.start


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        violations.append(event)
        raise PermissionError('No Python business networking in desktop harness')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real reference/game access in desktop harness')


sys.addaudithook(audit)


def wait_for(window, script, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = window.evaluate_js(script)
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Actual selector document did not reach expected state')


def screenshot(window):
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    previous = memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / 'installation-selected.png')
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def check(name):
    report['checks'].append({'name': name, 'status': 'PASS'})


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=installation-open]'))")
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        window.evaluate_js("document.querySelector('[data-testid=installation-open]').click()")
        wait_for(window, "Boolean(document.querySelector('dialog[open]'))")
        window.evaluate_js("document.querySelector('[data-testid=installation-manual]').click()")
        wait_for(window, "(()=>{const b=document.querySelector('[data-testid=installation-select]');return b && !b.disabled})()")
        assert picked == ['manual']
        check('actual UI choose and discover via real command bridge')
        window.evaluate_js("document.querySelector('[data-testid=installation-select]').click()")
        wait_for(window, "document.querySelector('[data-testid=installation-open]').innerText.includes('Probably Stolen')")
        check('actual selector consumes successful installation job and updates selected state')
        screenshot(window)
        assert violations == []
        assert not [c for c in psutil.Process().net_connections(kind='tcp') if c.status == psutil.CONN_LISTEN]
        check('no Python business bind/connect/listener')
        report['status'] = 'PASS'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['failure_location'] = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                                      for f in traceback.extract_tb(exc.__traceback__)]
    finally:
        window.destroy()


def create(*args, **kwargs):
    window = original_create(*args, **kwargs)
    windows.append(window)
    return window


def start(*args, **kwargs):
    # run_desktop installs its native chooser after create_window. Replace only
    # that OS boundary immediately before WebView starts processing commands.
    def choose(kind):
        assert kind == 'manual'
        picked.append(kind)
        return str(game)
    contexts[0].choose_installation_folder = choose
    return original_start(lambda: observe(windows[0]), **kwargs)


webview.create_window, webview.start = create, start
code = main.main(['--psmm-data-root', str(data)])
report['app_exit_code'] = code
try:
    with managers[0]._lock:
        report['cleanup'] = {'active_workers': sum(j.thread.is_alive() for j in managers[0]._jobs.values()),
                             'subscriptions': len(managers[0]._subscriptions)}
    assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
    check('desktop shutdown removes worker and subscription consumers')
    with sqlite3.connect((data / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True) as database:
        installations = database.execute('SELECT payload FROM installations').fetchall()
        fingerprints = database.execute('SELECT payload FROM runtime_fingerprints').fetchall()
    report['installation_rows'], report['fingerprint_rows'] = len(installations), len(fingerprints)
    assert len(installations) == len(fingerprints) == 1
    installation, fingerprint = json.loads(installations[0][0]), json.loads(fingerprints[0][0])
    assert Path(installation['canonical_root']) == game
    assert installation['fingerprint_id'] == fingerprint['fingerprint_id']
    assert fingerprint['coverage'] == 'complete'
    for name, field in [('Probably Stolen.exe', 'exe_hash'), ('GameAssembly.dll', 'game_assembly_hash'), ('Probably Stolen_Data/il2cpp_data/Metadata/global-metadata.dat', 'metadata_hash')]:
        assert fingerprint[field] == report['source_before'][name]
    check('actual SQLite Installation links complete current-file fingerprint')
    report['source_after'] = tree_hashes()
    assert report['source_before'] == report['source_after']
    check('synthetic installation tree remains byte-for-byte unchanged')
except Exception as exc:
    report['status'] = 'FAIL'
    report['post_close_error_type'] = type(exc).__name__
report['network_boundary'] = 'Python business networking denied; OS WebView2 engine background sockets not claimed absent'
(out / 'installation-desktop.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'app_exit_code': code}))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)
