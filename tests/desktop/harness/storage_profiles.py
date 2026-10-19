"""Exercise the actual local WebView with owned profiles and C-to-E data roots.

No network, real game writes, launches, registry edits, or real credentials.
Screenshots/visual inspection are performed independently with Computer Use.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--pause', action='store_true')
args = parser.parse_args()
out = args.out.resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/20261019-storage-profiles')
out.mkdir(parents=True, exist_ok=False)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
from backend.app import composition, storage_location
from backend.app.paths import resolve_paths
from backend.games.probably_stolen import discovery
from tests.integration.harness.launch_probe import build_probe, setup_installation
from tests.integration.test_storage_location import MemoryCredentials

data = Path(tempfile.mkdtemp(prefix='PSMM-owned-storage-')).resolve() / 'source'
target = out / 'migrated-data'
game = out / 'synthetic-game'
stage = composition.DesktopContext(resolve_paths(str(data)))
stage.ai_credentials_backend = MemoryCredentials()
stage.initialize()
try:
    installation = setup_installation(stage, game, build_probe(out / 'compiler'))
    for name in ['保留方案', '待删除方案']:
        state = stage.profiles.create(installation, name)
        stage.profiles.commit(state['profile_id'], state['revision'])
    sentinel = data / 'library/retained.bin'
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b'owned migration sentinel')
finally:
    stage.close()

discovery.automatic_steam_roots = lambda: []
report = dict(status='FAIL', checks=[], source=str(data), target=str(target), views=[])
windows, contexts, restarts = [], [], []
api_factory, create_window, start = composition.DesktopApi, webview.create_window, webview.start
copy_data = storage_location.copy_data
copy_entered, copy_release = threading.Event(), threading.Event()


def held_copy(*values, **kwargs):
    copy_entered.set()
    assert copy_release.wait(15), 'Migration close-veto observation did not finish'
    return copy_data(*values, **kwargs)


def factory(context):
    context.ai.credentials._backend = MemoryCredentials()
    context.nexus.credentials._backend = MemoryCredentials()
    context.installations._selected = installation
    contexts.append(context)
    return api_factory(context)


def create(*values, **options):
    window = create_window(*values, **options)
    windows.append(window)
    return window


def wait(window, expression, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window.evaluate_js(expression):
            return
        time.sleep(.08)
    raise AssertionError('Timed out: ' + expression)


def click(window, name):
    selector = f"document.querySelector('[data-testid={name}]')"
    wait(window, selector + ' && !' + selector + '.disabled')
    window.evaluate_js(selector + '.click()')


def view(window, label):
    state = window.evaluate_js("""(() => ({text: document.body.innerText,
      scrollWidth: document.documentElement.scrollWidth, width: innerWidth,
      height: innerHeight, firstRun: !!document.querySelector('[data-testid=storage-first-run]'),
      dialog: [...document.querySelectorAll('dialog[open]')].map(d => d.innerText)}))()""")
    assert state['scrollWidth'] <= state['width'], state
    report['views'].append(dict(label=label, **state))
    (out / 'phase.json').write_text(json.dumps(dict(label=label, pid=os.getpid(), source=str(data), target=str(target)), ensure_ascii=False), 'utf-8')
    if args.pause and label in ('first-use', 'delete-confirm', 'migration-ready'):
        deadline = time.monotonic() + 240
        while not (out / ('continue-' + label)).exists():
            if time.monotonic() > deadline:
                raise AssertionError('Visual observation was not released: ' + label)
            time.sleep(.2)


def observe():
    window = windows[0]
    try:
        wait(window, "!!document.querySelector('[data-testid=storage-default]')")
        view(window, 'first-use')
        assert not window.evaluate_js("!!document.querySelector('[data-testid=installation-open]')")
        click(window, 'storage-default')
        wait(window, "!!document.querySelector('[data-testid=installation-open]')")
        report['checks'].append('First-use choice precedes game discovery; default acceptance opens the workspace')
        click(window, 'profile-open')
        wait(window, "document.querySelector('[data-testid=profile-current-name]')?.textContent==='待删除方案'")
        click(window, 'profile-more'); click(window, 'profile-delete')
        wait(window, "document.querySelector('[data-testid=profile-delete-dialog]').open")
        view(window, 'delete-confirm')
        click(window, 'profile-delete-cancel')
        assert len(contexts[0].profiles.list(installation)['profiles']) == 2
        click(window, 'profile-more'); click(window, 'profile-delete'); click(window, 'profile-delete-confirm')
        wait(window, "document.querySelector('[data-testid=profile-current-name]')?.textContent==='保留方案'")
        report['checks'].append('Delete confirmation can be cancelled; confirmed deletion selects remaining scheme without changing the game')
        click(window, 'workspace-settings'); click(window, 'storage-change')
        window.evaluate_js("(() => { const e=document.querySelector('[data-testid=storage-target]'); e.value=" + json.dumps(str(target)) + "; e.dispatchEvent(new Event('input',{bubbles:true})); })()")
        click(window, 'storage-review')
        wait(window, "!!document.querySelector('[data-testid=storage-confirm]')")
        view(window, 'migration-confirm')
        click(window, 'storage-start')
        assert copy_entered.wait(10)
        try:
            wait(window, "!!document.querySelector('[data-testid=storage-copying]')")
            click(window, 'window-close')
            time.sleep(.5)
            assert not contexts[0].api._closed
            assert not window._psmm_callback_lifecycle.stats['closing']
            assert window.evaluate_js("!!document.querySelector('[data-testid=storage-copying]')")
            report['checks'].append('Close during copying is vetoed before bridge drain; progress and restart remain available')
        finally:
            copy_release.set()
        wait(window, "!!document.querySelector('[data-testid=storage-complete]')", timeout=120)
        view(window, 'migration-ready')
        assert storage_location.startup_paths(str(data)).root == target
        assert (target / 'library/retained.bin').read_bytes() == b'owned migration sentinel'
        assert (game / 'synthetic-save.dat').read_bytes() == b'untouched synthetic save'
        assert data.drive != target.drive
        report['checks'].append('Actual migration UI copies and verifies C-to-E data, commits redirect, and retains source and game sentinel')
        click(window, 'storage-restart')
        report['status'] = 'PASS'
    except Exception:
        copy_release.set()
        report['error'] = traceback.format_exc()
        window.destroy()


composition.DesktopApi = factory
webview.create_window = create
webview.start = lambda *values, **kwargs: start(observe, **kwargs)
storage_location.relaunch = lambda root, protected=(): restarts.append(str(root))
storage_location.copy_data = held_copy
report['exit_code'] = main.main(['--psmm-data-root', str(data)])
report['restarts'] = restarts
if report['status'] == 'PASS':
    assert restarts == [str(target)]
    reopened = composition.DesktopContext(resolve_paths(str(target)))
    reopened.initialize()
    try:
        assert [p['name'] for p in reopened.profiles.list(installation)['profiles']] == ['保留方案']
        assert not reopened.storage.status()['first_run']
    finally:
        reopened.close()
    report['checks'].append('Restart handoff names the committed destination; new composition reads retained scheme and skips onboarding')
(out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status'] == 'PASS' and report['exit_code'] == 0 else 1)
