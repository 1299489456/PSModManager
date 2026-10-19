"""Real cold-start selector/closed bridge, scoped synthetic Steam and saved DB."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--scenario', choices=['first', 'saved'], required=True)
args = parser.parse_args()
out = args.out.resolve()
assert out.is_relative_to(ROOT/'docs/development/evidence/20261016-followup-fixes')
out.mkdir(parents=True)
for key, name in [('LOCALAPPDATA','local'),('APPDATA','roaming'),('TEMP','temp'),('TMP','temp')]:
    (out/name).mkdir(exist_ok=True)
    os.environ[key] = str(out/name)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.games.probably_stolen import discovery
from tests.unit.test_installation_layout import make_installation, manifest
from tests.integration.harness.transaction_recovery import TransactionFixture

data = out/'data'
picked, contexts, windows, managers = [], [], [], []
expected = None
if args.scenario == 'saved':
    fixture = TransactionFixture(out/'fixture')
    game, data, expected = fixture.game, fixture.data, fixture.installation
    fixture.close()
    discovery.automatic_steam_roots = lambda: []
else:
    steam = out/'steam'
    for app_id, name in [('4349200','Demo'),('4348910','Retail')]:
        make_installation(steam/('steamapps/common/'+name))
        manifest(steam,app_id,name)
    discovery.automatic_steam_roots = lambda: [steam]
    game = make_installation(out/'manual-game')
before = {p.relative_to(game).as_posix():p.read_bytes() for p in game.rglob('*') if p.is_file()}
report = dict(status='FAIL',scenario=args.scenario,checks=[],scope='Actual bundled Vue -> closed bridge -> real jobs/discovery/SQLite, synthetic-only Steam and game')
original_api, original_create, original_start = composition.DesktopApi, webview.create_window, webview.start


def api_factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    return original_api(context)


def wait(window, expression, timeout=35):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        value = window.evaluate_js(expression)
        if value: return value
        time.sleep(.05)
    raise AssertionError('Not reached: '+expression)


def shot(window):
    hwnd = window.native.Handle.ToInt64()
    left,top,right,bottom = win32gui.GetWindowRect(hwnd)
    dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source,right-left,bottom-top)
    previous = memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)==1
        Image.frombuffer('RGB',(right-left,bottom-top),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(out/'selected.png')
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd,dc)


def observe():
    window = windows[0]
    try:
        assert window.events.loaded.wait(30)
        trigger = "document.querySelector('[data-testid=installation-open]')"
        wait(window,'Boolean('+trigger+')')
        window.evaluate_js(trigger+'.click()')
        wait(window,"document.querySelector('.installation-dialog')?.open")
        wait(window,"!document.querySelector('[data-testid=installation-auto]').disabled")
        assert not window.evaluate_js("document.querySelector('.installation-dialog [role=alert]')?.innerText")
        if args.scenario == 'first':
            assert window.evaluate_js("document.querySelectorAll('[data-testid=installation-select]').length")==2
            assert window.evaluate_js(trigger+".getAttribute('data-selected-id')")==''
            report['checks'].append('Cold startup finds two synthetic Steam games without an INVALID_REQUEST or silent selection')
            window.evaluate_js("document.querySelector('[data-testid=installation-manual]').click()")
            wait(window,"document.querySelectorAll('[data-testid=installation-select]').length===1 && !document.querySelector('[data-testid=installation-select]').disabled")
            assert picked==['manual']
            window.evaluate_js("document.querySelector('[data-testid=installation-select]').click()")
            wait(window,trigger+".getAttribute('data-selected-id')")
            wait(window,"!document.querySelector('.installation-dialog').open")
            report['checks'].append('Manual native path -> discover -> select -> persisted installation -> real workspace')
            window.evaluate_js(trigger+'.click()')
        else:
            wait(window,trigger+".getAttribute('data-selected-id')==="+json.dumps(expected))
            if not window.evaluate_js("document.querySelector('.installation-dialog').open"):
                window.evaluate_js(trigger+'.click()')
            report['checks'].append('Reopened saved installation is revalidated and automatically selected with the same identity')
        wait(window,"!document.querySelector('[data-testid=installation-auto]').disabled")
        saved = "Array.from(document.querySelectorAll('.installation-row button')).find(e=>e.innerText==='切换到此安装')"
        wait(window,'Boolean('+saved+') && !('+saved+').disabled')
        window.evaluate_js('('+saved+').click()')
        time.sleep(.2)  # Allow Vue to publish the new operation before observing completion.
        wait(window,"!document.querySelector('.installation-dialog').open")
        window.evaluate_js(trigger+'.click()')
        wait(window,"document.querySelector('.installation-dialog').open")
        wait(window,"!document.querySelector('[data-testid=installation-auto]').disabled")
        assert not window.evaluate_js("document.querySelector('.installation-dialog [role=alert]')?.innerText")
        shot(window)
        report['checks'].append('Saved install reuse and reopening selector show no error')
        assert {p.relative_to(game).as_posix():p.read_bytes() for p in game.rglob('*') if p.is_file()}==before
        report['status']='PASS'
    except Exception:
        report['failure']=traceback.format_exc()
    finally:
        window.destroy()


def create(*args, **kwargs):
    window=original_create(*args,**kwargs); windows.append(window); return window


def start(*args, **kwargs):
    def choose(kind): picked.append(kind); return str(game)
    contexts[0].choose_installation_folder=choose
    return original_start(observe,**kwargs)


composition.DesktopApi, webview.create_window, webview.start = api_factory, create, start
code = main.main(['--psmm-data-root',str(data)])
report['exit_code']=code
report['active_workers']=sum(job.thread.is_alive() for job in managers[0]._jobs.values())
if code or report['active_workers']: report['status']='FAIL'
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if report['status']=='PASS' else 1)
