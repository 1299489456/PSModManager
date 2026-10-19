"""Actual DOM -> production RPC/jobs/import/storage; only native picker injected."""
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
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=['first', 'reopen'], required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-02-02')
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
for key, name in [('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'), ('TEMP', 'temp'), ('TMP', 'temp')]:
    os.environ[key] = str(out / name)
    (out / name).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import webview
import win32gui
import win32ui
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi
from backend.storage.package_repository import content_hash

source = out / '本地导入 空格 %.zip'
payloads = {'Mods/图片 %.png': b'synthetic resource bytes', 'unknown.txt': b'unknown ownership bytes'}
if args.phase == 'first':
    with zipfile.ZipFile(source, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in payloads.items():
            archive.writestr(name, value)
data = out / 'isolated-data'
report = dict(status='FAIL', phase=args.phase, requests=[], checks=[], screenshots=[],
              data_root=str(data), source_before=hashlib.sha256(source.read_bytes()).hexdigest(),
              scope='Production WebView DOM clicks and real import service; only native source picker returns synthetic ZIP')
contexts, managers, windows = [], [], []
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    api = DesktopApi(context)
    dispatch = api.dispatch

    def observed(request):
        response = dispatch(request)
        report['requests'].append(dict(command=request.get('command'), ok=response['ok'],
                                       error_code=(response.get('error') or {}).get('code')))
        return response

    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        raise PermissionError('No Python business networking in synthetic import harness')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real reference/game access')


sys.addaudithook(audit)


def wait_for(window, script, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(script)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('UI timeout: ' + script[:180])


def click(window, testid):
    wait_for(window, f"(()=>{{const e=document.querySelector('[data-testid={testid}]');return e&&!e.disabled}})()")
    window.evaluate_js(f"document.querySelector('[data-testid={testid}]').click()")


def screenshot(window, name):
    window.evaluate_js('window.__captureReady=false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.__captureReady=true))')
    wait_for(window, 'window.__captureReady')
    time.sleep(.18)
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(dc)
    memory = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    previous = memory.SelectObject(bitmap)
    filename = f'{args.phase}-{name}.png'
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / filename)
        report['screenshots'].append(filename)
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def persisted():
    with sqlite3.connect((data / 'psmodmanager.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        releases = [json.loads(row[0]) for row in db.execute('SELECT payload FROM mod_releases')]
        assert len(releases) == 1, releases
        release = releases[0]
        files = [json.loads(row[0]) for row in db.execute('SELECT payload FROM file_entries')]
        assert len(files) == len(payloads)
        assert {row['relative_path'] for row in files} == {'图片 %.png', 'unknown.txt'}
        artifacts = [json.loads(row[0]) for row in db.execute('SELECT payload FROM artifacts')]
        expected = {hashlib.sha256(value).hexdigest() for value in payloads.values()}
        assert {row['sha256'] for row in artifacts} == expected
        for artifact in artifacts:
            path = data / 'library' / artifact['library_relative_path']
            reject_reparse(path)
            assert path.stat().st_nlink == 1
            assert path.stat().st_size == artifact['size']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
        manifest = [{key: row[key] for key in ('source_hash', 'target_root_kind', 'relative_path', 'lifecycle')} for row in files]
        manifest.sort(key=lambda row: (row['target_root_kind'], row['relative_path']))
        assert content_hash({'version_raw': release['version_raw'], 'files': manifest}) == release['manifest_hash']
        return dict(release_id=release['release_id'], manifest_hash=release['manifest_hash'], file_count=len(files),
                    artifact_hashes=sorted(expected), profiles=db.execute('SELECT count(*) FROM profiles').fetchone()[0],
                    deployments=db.execute('SELECT count(*) FROM deployment_generations').fetchone()[0])


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=import-open]'))")
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        hwnd = window.native.Handle.ToInt64()
        report['display'] = dict(os_dpi=int(ctypes.windll.user32.GetDpiForWindow(hwnd)), window_rect=list(win32gui.GetWindowRect(hwnd)))
        if args.phase == 'reopen':
            report['before_reimport'] = persisted()
            report['checks'].append('Previous real desktop publication survives app restart with matching manifest and artifact hashes')
        click(window, 'import-open')
        click(window, 'import-zip')
        preview = wait_for(window, "document.querySelector('[data-testid=import-preview]')?.innerText")
        assert all(value in preview for value in ('Mods/图片 %.png', 'unknown.txt', '未知归属', '未知角色')), preview
        report['preview_text'] = preview
        report['checks'].append('Actual import preview preserves Chinese, space and percent names and displays target plus unknown ownership')
        screenshot(window, 'preview')
        click(window, 'import-commit')
        result = wait_for(window, "document.querySelector('[data-testid=import-result]')?.innerText")
        assert '2 个文件' in result, result
        assert '尚未启用或部署' in window.evaluate_js('document.body.innerText')
        screenshot(window, 'committed')
        report['persisted'] = persisted()
        assert report['persisted']['release_id'] in result
        assert report['persisted']['profiles'] == report['persisted']['deployments'] == 0
        if args.phase == 'reopen':
            assert report['before_reimport'] == report['persisted']
            report['checks'].append('Reimport through visible UI is idempotent across desktop restarts')
        for command in ('package.choose', 'package.import_preview', 'package.import_commit'):
            assert any(row['command'] == command and row['ok'] for row in report['requests']), command
        report['checks'].append('UI commit publishes actual SQLite release and content-addressed verified bytes without Profile or Deployment')
        report['status'] = 'PASS'
    except Exception as exc:
        report['error'] = str(exc)[:1000]
        report['failure_location'] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)]
        try:
            report['failure_document'] = str(window.evaluate_js('document.body.innerText'))[:12000]
            screenshot(window, 'failure')
        except Exception:
            pass
    finally:
        window.destroy()


def create(*args, **kwargs):
    kwargs.update(width=1280, height=720)
    window = original_create(*args, **kwargs)
    windows.append(window)
    return window


def start(*args, **kwargs):
    contexts[0].choose_import_source = lambda kind: str(source) if kind == 'zip' else None
    return original_start(lambda: observe(windows[0]), **kwargs)


webview.create_window, webview.start = create, start
code = main.main(['--psmm-data-root', str(data)])
report['app_exit_code'] = code
report['source_after'] = hashlib.sha256(source.read_bytes()).hexdigest()
with managers[0]._lock:
    report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),
                             subscriptions=len(managers[0]._subscriptions))
if report['source_after'] != report['source_before'] or any(report['cleanup'].values()):
    report['status'] = 'FAIL'
(out / f'{args.phase}-import.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=code, checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)
