"""Owned WebView regression: shell minimize/restore must preserve client bounds."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import webview
import win32con
import win32gui
from backend.app.window_frame import WindowFrame

parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--application', action='store_true')
args = parser.parse_args()
args.out = args.out.resolve()
args.out.mkdir(parents=True, exist_ok=False)
report = {'status': 'FAIL', 'states': []}


def sample(label):
    hwnd = window.native.Handle.ToInt64()
    child = window.native.webview.Handle.ToInt64()
    state = dict(label=label, iconic=bool(win32gui.IsIconic(hwnd)),
        parent=win32gui.GetWindowRect(hwnd), child=win32gui.GetWindowRect(child))
    report['states'].append(state)
    return state


def exercise():
    try:
        window.events.loaded.wait(20)
        time.sleep(1)
        for maximized, restore in ((False, 'system_command'), (True, 'system_command'), (False, 'webview')):
            if maximized != frame._maximized:
                frame.control('toggle_maximize')
                time.sleep(.5)
            before = sample('before')
            frame.control('minimize')
            time.sleep(.5)
            assert sample('minimized')['iconic']
            if restore == 'system_command':
                win32gui.PostMessage(window.native.Handle.ToInt64(), win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0)
            else:
                window.restore()
            time.sleep(.5)
            after = sample('restored')
            assert not after['iconic']
            assert before['parent'] == after['parent']
            assert before['child'] == after['child']
            assert window.evaluate_js('document.body.innerText.length') > 10
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        window.destroy()


if args.application:
    from backend.app import composition
    from backend.games.probably_stolen import discovery
    from tests.integration.harness.transaction_recovery import TransactionFixture
    fixture = TransactionFixture(args.out/'owned')
    fixture.close()
    discovery.automatic_steam_roots = lambda: []
    original_create, original_start = webview.create_window, webview.start

    def create(*a, **kw):
        global window
        window = original_create(*a, **kw)
        return window

    def start(*a, **kw):
        global frame
        frame = window._js_api._context.window_frame
        return original_start(exercise, **kw)

    webview.create_window, webview.start = create, start
    composition.run_desktop(str(fixture.data))
else:
    window = webview.create_window('PSMM owned frame regression', html='<h1>Window frame regression</h1>',
        width=1280, height=800, min_size=(1280, 720), frameless=True, easy_drag=False)
    frame = WindowFrame(window)
    window.events.shown += frame.initialize
    webview.start(exercise, gui='edgechromium', private_mode=True, storage_path=str(args.out/'webview'))
(args.out/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
raise SystemExit(0 if report['status'] == 'PASS' else 1)
