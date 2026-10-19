"""Actual WebView repair and launch preflight against an owned synthetic game.

Bundled tool bytes are restored but never executed. The launched executable is
the project's self-authored bounded probe, not Unity or a real game.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
out = args.out.resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/20261018-cpp2il-offline')
out.mkdir(parents=True, exist_ok=False)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.app.paths import resolve_paths
from backend.games.probably_stolen import discovery
from backend.games.probably_stolen.fingerprint import file_version
from backend.loaders.melonloader import service as loader_module
from backend.loaders.melonloader.offline import CPP2IL, RESOURCE_ROOT
from backend.loaders.melonloader.service import CORE_FILES
from tests.integration.harness.launch_probe import build_probe, setup_installation
from tests.integration.harness.transaction_recovery import rpc

data, game = out / 'isolated-data', out / 'synthetic-game'
probe = build_probe(out / 'compiler')
stage = composition.DesktopContext(resolve_paths(str(data)))
stage.initialize()
try:
    installation = setup_installation(stage, game, probe)
finally:
    stage.close()


def metadata_dll(target, version, name):
    source = out / 'compiler' / (name + '.cs')
    source.write_text('using System.Reflection;\n[assembly:AssemblyInformationalVersion("' + version + '")]\npublic class ' + name + ' {}', encoding='utf-8')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(compiler), '/nologo', '/target:library', '/platform:x64', '/out:' + str(target), str(source)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout
    assert file_version(target) == version


# Metadata-only DLLs cannot bootstrap MelonLoader into the synthetic probe.
metadata_dll(game / 'MelonLoader/net6/MelonLoader.dll', '0.7.3', 'SyntheticLoader')
metadata_dll(game / 'version.dll', '0.7.3', 'SyntheticProxy')
metadata_dll(game / 'UnityPlayer.dll', '2021.3.45f2', 'SyntheticUnity')
with zipfile.ZipFile(ROOT / 'docs/development/evidence/20261015T055229Z-melonloader/MelonLoader.x64.zip') as archive:
    for name in CORE_FILES:
        if name in ('version.dll', 'MelonLoader/net6/MelonLoader.dll'):
            continue
        path = game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(archive.read(name))
manifest = json.loads((RESOURCE_ROOT / 'manifest.json').read_text(encoding='utf-8'))
with zipfile.ZipFile(RESOURCE_ROOT / 'toolchain.zip') as archive:
    for row in manifest['files']:
        path = game / row['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(archive.read(row['path']))
exe = game / CPP2IL
original = exe.read_bytes()
exe.unlink()
config = game / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Config.cfg'
config.write_bytes(b'[Il2CppAssemblyGenerator]\nGameAssemblyHash = "sentinel"\nOldFiles = ["keep.dll"]\n')
original_config = config.read_bytes()
report = dict(status='FAIL', checks=[], screenshots=[], scope='Actual WebView and real bundled assets; self-authored game/loader metadata; network denied; no real game or MelonLoader execution')
discovery.automatic_steam_roots = lambda: []
def no_network(*args, **kwargs):
    raise AssertionError('Offline flow attempted network access')
loader_module.urlopen = no_network
windows, contexts = [], []
original_api, original_create, original_start = composition.DesktopApi, webview.create_window, webview.start


def factory(context):
    api = original_api(context)
    rpc(api, 'installation.use', {'installation_id': installation})
    contexts.append(context)
    return api


def create(*values, **options):
    window = original_create(*values, **options)
    windows.append(window)
    return window


def wait(window, expression, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window.evaluate_js(expression):
            return
        time.sleep(.06)
    raise AssertionError('Timed out: ' + expression)


def click(window, name):
    selector = f"document.querySelector('[data-testid={name}]')"
    wait(window, selector + ' && !' + selector + '.disabled')
    window.evaluate_js(selector + '.click()')


def capture(window, name):
    time.sleep(.4)
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(dc)
    memory, bitmap = source.CreateCompatibleDC(), win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, right-left, bottom-top)
    old = memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2)
        Image.frombuffer('RGB', (right-left, bottom-top), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / name)
        report['screenshots'].append(name)
    finally:
        memory.SelectObject(old)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def observe():
    window = windows[0]
    try:
        window.events.loaded.wait(30)
        click(window, 'loader-details')
        wait(window, "document.querySelector('[data-testid=loader-repair]') && !document.querySelector('[data-testid=loader-repair]').disabled")
        capture(window, 'missing-dependency.png')
        assert not exe.exists()
        click(window, 'loader-repair')
        wait(window, "document.querySelector('[data-check=generation_dependencies]').dataset.status==='ok'")
        window.evaluate_js("document.querySelector('[data-check=generation_dependencies]').scrollIntoView({block:'center'})")
        assert exe.read_bytes() == original
        assert config.read_bytes() == original_config
        capture(window, 'offline-repaired.png')
        report['checks'].append('Actual environment UI repairs missing official Cpp2IL offline and preserves generation records')
        window.evaluate_js("document.querySelector('[aria-label=关闭环境详情]').click()")
        exe.unlink()
        click(window, 'game-launch')
        wait(window, "document.querySelector('[data-testid=game-launch-status]').dataset.state==='running'")
        assert exe.read_bytes() == original
        assert (game / 'launch-probe.txt').exists()
        assert config.read_bytes() == original_config
        assert (game / 'synthetic-save.dat').read_bytes() == b'untouched synthetic save'
        report['checks'].append('Real launch button restores missing EXE under launch guard before starting bounded synthetic process; generation configuration and save sentinel unchanged')
        (game / 'exit-request.txt').write_text('0', encoding='ascii')
        wait(window, "document.querySelector('[data-testid=game-launch-status]').dataset.state==='exited'")
        capture(window, 'launch-preflight-restored.png')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        capture(window, 'failure.png')
    finally:
        (game / 'exit-request.txt').write_text('0', encoding='ascii')
        window.destroy()


composition.DesktopApi = factory
webview.create_window = create
webview.start = lambda *values, **kwargs: original_start(observe, **kwargs)
report['app_exit_code'] = main.main(['--psmm-data-root', str(data)])
(out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report['status'] == 'PASS' and report['app_exit_code'] == 0 else 1)
