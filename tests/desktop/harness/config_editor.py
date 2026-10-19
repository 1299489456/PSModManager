"""Actual WebView/Vue/bridge config editing with synthetic files and DOM events.

DOM events here are automated and untrusted; this is not a manual/pointer test.
The backend, installation discovery, file writes and persistence are real.
"""
import argparse
import hashlib
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
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-03-05')
out.mkdir(parents=True, exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
from backend.app import composition
from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.integration.test_deployment_guards import register
from tests.unit.test_config_adapters import TEXT
from tests.unit.test_installation_layout import make_installation

game, data = out / 'synthetic-game', out / 'isolated-data'
config = game / 'UserData/PSWallpaper.cfg'
if args.phase == 'first':
    make_installation(game)
    config.parent.mkdir()
    config.write_bytes(b'\xef\xbb\xbf' + TEXT.replace('\n', '\r\n').encode())
    (game / 'synthetic-save.dat').write_bytes(b'untouched synthetic save')
    stage = DesktopContext(resolve_paths(str(data)))
    stage.initialize()
    try:
        installation_id = register(stage, game)
    finally:
        stage.close()
    (out / 'installation-id.txt').write_text(installation_id, encoding='ascii')

report = dict(phase=args.phase, profile='synthetic', status='FAIL', checks=[], requests=[],
              input_method='Automated DOM events in actual WebView; no trusted pointer/manual claim')
original_api = composition.DesktopApi


class ObservedApi(original_api):
    def dispatch(self, request):
        result = super().dispatch(request)
        report['requests'].append(dict(command=request.get('command'), ok=result.get('ok'),
                                       error_code=result.get('error', {}).get('code')))
        return result


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


def input_value(window, name, value):
    window.evaluate_js(f"(() => {{ const input = document.querySelector('[data-testid={name}]'); input.value={json.dumps(value)}; input.dispatchEvent(new Event('input', {{bubbles:true}})); }})()")


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait()
        # Registration survives restart; the currently selected installation does not.
        # Use the real saved-installation UI rather than changing selection in a fixture.
        click(window, 'installation-open')
        wait(window, "Boolean(document.querySelector('.installation-content section .installation-row button'))")
        window.evaluate_js("document.querySelector('.installation-content section .installation-row button').click()")
        wait(window, "Boolean(document.querySelector('[data-testid=config-open]')) && !document.querySelector('[data-testid=config-open]').disabled")
        click(window, 'config-open')
        wait(window, "Boolean(document.querySelector('[data-testid=config-WallpaperBrightness]'))")
        if args.phase == 'first':
            before = config.read_bytes()
            input_value(window, 'config-WallpaperBrightness', '80')
            window.evaluate_js("(() => { const s=document.querySelector('[data-testid=config-WallpaperFit]'); s.value='fit'; s.dispatchEvent(new Event('change',{bubbles:true})); const c=document.querySelector('[data-testid=config-LogToFile]'); c.checked=false; c.dispatchEvent(new Event('change',{bubbles:true})); })()")
            click(window, 'config-save')
            wait(window, "document.querySelector('[data-testid=config-notice]')?.innerText.includes('已保存')")
            expected = before.replace(b'WallpaperFit = cover', b'WallpaperFit = fit').replace(
                b'WallpaperBrightness=55', b'WallpaperBrightness=80').replace(b'LogToFile=true', b'LogToFile=false')
            assert config.read_bytes() == expected
            installation_id = (out / 'installation-id.txt').read_text(encoding='ascii')
            assert (data / 'config-backups' / installation_id / config.name).read_bytes() == before
            click(window, 'config-close')
            click(window, 'config-open')
            wait(window, "document.querySelector('[data-testid=config-WallpaperBrightness]')?.value==='80'")
            changed = expected + '# 外部修改保留\r\n'.encode()
            config.write_bytes(changed)
            input_value(window, 'config-WallpaperBrightness', '40')
            click(window, 'config-save')
            wait(window, "document.querySelector('[data-testid=config-error]')?.innerText.includes('重新读取')")
            assert config.read_bytes() == changed
            assert window.evaluate_js("document.querySelector('[data-testid=config-save]').disabled")
            click(window, 'config-refresh')
            wait(window, "document.querySelector('[data-testid=config-WallpaperBrightness]')?.value==='80' && !document.querySelector('[data-testid=config-error]')")
            report['checks'] = ['Actual Vue form and bridge write all three selected fields',
                'Untouched bytes, BOM, CRLF, comments and unknown fields preserved', 'Original optional backup exists',
                'Closing/reopening rereads saved values', 'External edit is rejected and kept',
                'UI error explains reread and disables stale save', 'Refresh loads current values']
        else:
            assert window.evaluate_js("document.querySelector('[data-testid=config-WallpaperBrightness]').value==='80' && document.querySelector('[data-testid=config-WallpaperFit]').value==='fit' && !document.querySelector('[data-testid=config-LogToFile]').checked")
            assert '# 外部修改保留' in config.read_bytes().decode('utf-8-sig')
            report['checks'] = ['New actual desktop process rereads saved values', 'Unknown external comment survives restart']
        report['dom'] = window.evaluate_js("document.querySelector('[data-testid=config-dialog]').outerHTML")
        report['file_hash'] = hashlib.sha256(config.read_bytes()).hexdigest()
        assert not any(row['command'].startswith('plan.') for row in report['requests'])
        assert (game / 'synthetic-save.dat').read_bytes() == b'untouched synthetic save'
        report['checks'].extend(['No ActionPlan preview/approval needed', 'Synthetic save untouched'])
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        window.destroy()


def start(*values, **options):
    return original_start(observe, **options)


webview.create_window, webview.start = create, start
report['app_exit_code'] = main.main(['--psmm-data-root', str(data)])
(out / (args.phase + '-config-editor.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=report['app_exit_code'], checks=len(report['checks']))))
raise SystemExit(0 if report['status'] == 'PASS' and report['app_exit_code'] == 0 else 1)
