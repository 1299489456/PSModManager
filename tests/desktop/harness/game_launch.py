"""Actual Vue/bridge/Windows launches; synthetic executables, automated DOM input."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=('first', 'reopen'), required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-03-06')
out.mkdir(parents=True, exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
from backend.app import composition
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.integration.harness.launch_probe import build_probe, setup_installation
from tests.integration.test_deployment_guards import register
from tests.unit.test_installation_layout import make_installation

game, bad, data = out / '中文 空格 & 100% Game', out / 'invalid-game', out / 'isolated-data'
if args.phase == 'first':
    probe = build_probe(out / 'compiler')
    stage = DesktopContext(resolve_paths(str(data)))
    stage.initialize()
    try:
        setup_installation(stage, game, probe)
        make_installation(bad)
        register(stage, bad)
    finally:
        stage.close()
    (game / 'MelonLoader/Latest.log').write_text('old historical log: Main menu reached\n', encoding='utf-8')

report = dict(profile='desktop-local', phase=args.phase, status='FAIL', checks=[], requests=[],
              input_method='Automated DOM events in actual WebView; no trusted pointer/manual claim')
original_api = composition.DesktopApi


class ObservedApi(original_api):
    def dispatch(self, request):
        response = super().dispatch(request)
        row = dict(command=request.get('command'), ok=response.get('ok'), error_code=response.get('error', {}).get('code'))
        if request.get('command', '').startswith('game.'):
            row['data'] = response.get('data')
        report['requests'].append(row)
        return response


composition.DesktopApi = ObservedApi
windows = []
original_create, original_start = webview.create_window, webview.start


def create(*values, **options):
    window = original_create(*values, **options)
    windows.append(window)
    return window


def wait(window, expression):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if window.evaluate_js(expression):
            return
        time.sleep(.05)
    raise AssertionError('WebView condition timed out: ' + expression)


def click(window, name):
    wait(window, f"Boolean(document.querySelector('[data-testid={name}]')) && !document.querySelector('[data-testid={name}]').disabled")
    window.evaluate_js(f"document.querySelector('[data-testid={name}]').click()")


def select(window, path):
    click(window, 'installation-open')
    selector = "Array.from(document.querySelectorAll('.installation-content .installation-row')).find(row=>row.querySelector('code')?.textContent===" + json.dumps(str(path)) + ")"
    wait(window, 'Boolean(' + selector + ')')
    window.evaluate_js('(' + selector + ").querySelector('button').click()")
    wait(window, "document.querySelector('[data-testid=game-launch-status]')?.dataset.state==='not_started' && !document.querySelector('[data-testid=game-launch]').disabled")


def exited(window, code):
    wait(window, "document.querySelector('[data-testid=game-launch-status]')?.dataset.state==='exited' && document.querySelector('[data-testid=game-launch-status]').innerText.includes('" + str(code) + "')")


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait()
        select(window, game)
        assert window.evaluate_js("document.querySelector('[data-testid=game-launch-status]').innerText.includes('未启动')")
        if args.phase == 'first':
            button = "document.querySelector('[data-testid=game-launch]')"
            window.evaluate_js(button + '.click();' + button + '.click()')
            wait(window, "document.querySelector('[data-testid=game-launch-status]')?.dataset.state==='running'")
            wait(window, "document.querySelector('[data-testid=game-launch]').disabled")
            deadline = time.monotonic() + 10
            while not (game / 'launch-probe.txt').exists():
                assert time.monotonic() < deadline
                time.sleep(.02)
            lines = (game / 'launch-probe.txt').read_text(encoding='utf-8-sig').splitlines()
            assert Path(lines[1]) == game / 'Probably Stolen.exe' and Path(lines[2]) == game and lines[3] == '0'
            assert (game / 'launch-count.txt').read_text() == 'started\n'
            (game / 'exit-request.txt').write_text('7')
            exited(window, 7)
            (game / 'exit-request.txt').unlink()
            click(window, 'game-launch')
            wait(window, "document.querySelector('[data-testid=game-launch-status]')?.dataset.state==='running'")
            (game / 'exit-request.txt').write_text('0')
            exited(window, 0)
            select(window, bad)
            click(window, 'game-launch')
            wait(window, "document.querySelector('[data-testid=game-launch-status]')?.dataset.state==='failed'")
            assert window.evaluate_js("document.querySelector('.game-launch-error')?.innerText.includes('无法启动')")
            report['checks'] = ['Actual button launches fixed synthetic executable and cwd without arguments',
                'Double click creates one process', 'Running state disables launch', 'Actual exit code 7 appears',
                'After exit another launch is possible', 'Second process exit code 0 appears',
                'Actual invalid executable returns visible launch failure', 'No plan approval or runtime history prerequisite']
        else:
            assert window.evaluate_js("document.querySelector('[data-testid=game-launch-status]').innerText.includes('未启动')")
            report['checks'] = ['New actual desktop process reads current process state', 'Old main-menu log does not claim current runtime success']
        assert (game / 'synthetic-save.dat').read_bytes() == b'untouched synthetic save'
        assert not any(row['command'].startswith('plan.') for row in report['requests'])
        report['checks'].append('Synthetic save untouched and no plan commands called')
        report['dom'] = window.evaluate_js("document.querySelector('[data-testid=game-launch-status]').outerHTML")
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
        except Exception:
            pass
    finally:
        # Cooperative exit request is a test-only file read by our bounded probe.
        (game / 'exit-request.txt').write_text('0')
        window.destroy()


def start(*values, **options):
    return original_start(observe, **options)


webview.create_window, webview.start = create, start
report['app_exit_code'] = main.main(['--psmm-data-root', str(data)])
(out / (args.phase + '-game-launch.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=report['app_exit_code'], checks=len(report['checks']))))
raise SystemExit(0 if report['status'] == 'PASS' and report['app_exit_code'] == 0 else 1)
