"""Actual ImportPanel/ModManager/bridge/executor; isolated inert synthetic files.

Only the native source picker is substituted. Automated DOM events exercise the
production Vue interface and RPC dispatcher; this is not trusted-input/manual
acceptance and does not start a game executable.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=('first', 'reopen_enabled', 'reopen_disabled', 'reopen_uninstalled'), required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-03-07')
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from tests.integration.harness.transaction_recovery import TransactionFixture, durable_json, tree
from tests.integration.test_content_catalog import compile_fixture

game, data, source = out / 'synthetic-game', out / 'private-data', out / '自造 Mod 空格 %'
file_names = ('Mods/sample.dll', 'Plugins/plugin.dll', 'UserLibs/assets/logo.png')
owner = dict(root=str(out), synthetic=True, owner='PSModManager.CASE037.owned.v1')
if args.phase == 'first':
    fixture = TransactionFixture(out)
    fixture.close()
    (game / 'UserData/config-sentinel.cfg').write_bytes(b'unknown-user-config=keep\r\n')
    for relative, assembly in (('Mods/sample.dll', 'DirectDesktopMod'), ('Plugins/plugin.dll', 'DirectDesktopPlugin')):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(compile_fixture(out / 'compiler' / assembly, assembly), path)
    resource = source / file_names[2]
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_bytes(b'synthetic resource bytes')
    durable_json(out / 'desktop-owner.json', owner)
    durable_json(out / 'protected-files.json', {name: digest for name, digest in tree(game).items() if not name.endswith('/')})
else:
    assert json.loads((out / 'desktop-owner.json').read_text(encoding='utf-8')) == owner
files = {relative: (source / relative).read_bytes() for relative in file_names}

report = dict(profile='desktop-local', phase=args.phase, status='FAIL', checks=[], requests=[], screenshots=[],
    input_method='Automated DOM events in actual WebView; no trusted pointer or manual acceptance claim',
    scope='Import, install, disable, enable, uninstall and actual process reopening; no real game or game launch',
    source_before=tree(source))
contexts, managers, windows = [], [], []
original_api = composition.DesktopApi
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    api = original_api(context)
    dispatch = api.dispatch

    def observed(request):
        response = dispatch(request)
        row = dict(command=request.get('command'), ok=response.get('ok'), error_code=(response.get('error') or {}).get('code'))
        if request.get('command') == 'mod.action':
            row['action'] = request.get('payload', {}).get('action')
        if request.get('command') == 'task.snapshot':
            snapshot = response.get('data') or {}
            if snapshot.get('state') in ('succeeded', 'failed', 'cancelled'):
                row.update(state=snapshot['state'], task_data=snapshot.get('data'))
        report['requests'].append(row)
        return response

    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        raise PermissionError('No Python networking in synthetic desktop checks')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real source or game access')


sys.addaudithook(audit)


def wait(window, expression, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(expression)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('WebView condition timed out: ' + expression[:240])


def click(window, name, parent='document'):
    selector = f"{parent}.querySelector('[data-testid={name}]')"
    wait(window, f'Boolean({selector}) && !{selector}.disabled')
    window.evaluate_js(selector + '.click()')


def row(release_id):
    return "Array.from(document.querySelectorAll('[data-testid=mod-row]')).find(row=>row.dataset.releaseId===" + json.dumps(release_id) + ')'


def state(window, release_id, expected):
    wait(window, '(' + row(release_id) + f")?.dataset.modState==='{expected}' && document.querySelector('[data-testid=mod-manager]')?.getAttribute('aria-busy')==='false'")
    return window.evaluate_js('(' + row(release_id) + ').outerHTML')


def select(window):
    click(window, 'installation-open')
    selector = "Array.from(document.querySelectorAll('.installation-content .installation-row')).find(row=>row.querySelector('code')?.textContent===" + json.dumps(str(game)) + ')'
    wait(window, 'Boolean(' + selector + ')')
    window.evaluate_js('(' + selector + ").querySelector('button').click()")
    wait(window, "document.querySelector('[data-testid=mod-refresh]') && !document.querySelector('[data-testid=mod-refresh]').disabled && document.querySelector('[data-testid=mod-manager]')?.getAttribute('aria-busy')==='false'")
    assert not window.evaluate_js("document.querySelector('[data-testid=mod-error]')?.innerText")


def screenshot(window, label):
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right-left, bottom-top
    dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(dc)
    memory = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    previous = memory.SelectObject(bitmap)
    filename = f'{args.phase}-{label}.png'
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


def library():
    with sqlite3.connect((data / 'psmodmanager.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        releases = [json.loads(r[0]) for r in db.execute('SELECT payload FROM mod_releases')]
        assert len(releases) == 1, releases
        entries = [json.loads(r[0]) for r in db.execute('SELECT payload FROM file_entries')]
        assert len(entries) == 3, entries
        artifacts = [json.loads(r[0]) for r in db.execute('SELECT payload FROM artifacts')]
        expected = {hashlib.sha256(content).hexdigest() for content in files.values()}
        assert {value['sha256'] for value in artifacts} == expected
        for artifact in artifacts:
            path = data / 'library' / artifact['library_relative_path']
            reject_reparse(path)
            assert path.stat().st_nlink == 1
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
        return dict(release_id=releases[0]['release_id'], manifest_hash=releases[0]['manifest_hash'], file_count=len(entries))


def physical(enabled):
    for relative, content in files.items():
        path = game / relative
        if enabled:
            assert path.read_bytes() == content, relative
        else:
            assert not path.exists(), relative
    assert (game / 'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'
    assert (game / 'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'
    assert (game / 'UserData/config-sentinel.cfg').read_bytes() == b'unknown-user-config=keep\r\n'
    protected = json.loads((out / 'protected-files.json').read_text(encoding='utf-8'))
    assert {name: digest for name, digest in tree(game).items() if not name.endswith('/') and name not in files} == protected


def perform(window, release_id, action, expected):
    before = sum(r['command'] == 'mod.action' for r in report['requests'])
    click(window, 'mod-'+action, '('+row(release_id)+')')
    report.setdefault('state_snapshots', {})[action] = state(window, release_id, expected)
    notice = wait(window, "document.querySelector('[data-testid=mod-notice]')?.innerText")
    assert '完成' in notice and not window.evaluate_js("document.querySelector('[data-testid=mod-error]')?.innerText")
    assert sum(r['command'] == 'mod.action' for r in report['requests']) == before+1
    physical(expected == 'enabled')
    assert library()['release_id'] == release_id
    report['checks'].append(f'One ordinary {action} click completes actual three-root files and matching UI/library state')


def observe():
    window = windows[0]
    try:
        assert window.events.loaded.wait(30)
        select(window)
        assert window.evaluate_js("document.querySelector('[data-testid=workspace-mods]').getAttribute('aria-current')") == 'page'
        if args.phase == 'first':
            click(window, 'import-open')
            click(window, 'import-directory')
            preview = wait(window, "document.querySelector('[data-testid=import-preview]')?.innerText")
            assert all(relative in preview for relative in files), preview
            click(window, 'import-commit')
            result = wait(window, "document.querySelector('[data-testid=import-result]')?.innerText")
            assert '3 个文件' in result, result
            persisted = library()
            release_id = persisted['release_id']
            durable_json(out / 'release.json', persisted)
            click(window, 'import-show-library')
            state(window, release_id, 'not_installed')
            physical(False)
            report['checks'].append('Visible ImportPanel publishes actual immutable three-file package without installation side effects')
            unknown = game / 'Mods/sample.dll'
            unknown.write_bytes(b'unknown-owned-target-must-not-be-overwritten')
            click(window, 'mod-install', '('+row(release_id)+')')
            message = wait(window, "document.querySelector('[data-testid=mod-error]')?.innerText")
            state(window, release_id, 'not_installed')
            assert unknown.read_bytes() == b'unknown-owned-target-must-not-be-overwritten'
            assert not (game / 'Plugins/plugin.dll').exists() and not (game / 'UserLibs/assets/logo.png').exists()
            assert not window.evaluate_js("document.querySelector('[data-testid=mod-notice]')?.innerText")
            report['blocked_error'] = message
            screenshot(window, 'blocked')
            report['checks'].append('Unknown target causes visible failure without overwritten bytes or false success; install remains retryable')
            unknown.unlink()  # Only this harness-created isolated collision fixture.
            perform(window, release_id, 'install', 'enabled')
            screenshot(window, 'installed')
        else:
            release_id = json.loads((out / 'release.json').read_text(encoding='utf-8'))['release_id']
            expected = {'reopen_enabled':'enabled', 'reopen_disabled':'disabled', 'reopen_uninstalled':'not_installed'}[args.phase]
            report['reopened_state'] = state(window, release_id, expected)
            physical(expected == 'enabled')
            assert library()['release_id'] == release_id
            report['checks'].append(f'New actual desktop process reads persisted {expected} state matching files and retained library')
            if args.phase == 'reopen_enabled':
                screenshot(window, 'enabled-reopened')
                perform(window, release_id, 'disable', 'disabled')
                screenshot(window, 'disabled')
            elif args.phase == 'reopen_disabled':
                perform(window, release_id, 'enable', 'enabled')
                perform(window, release_id, 'uninstall', 'not_installed')
                screenshot(window, 'uninstalled')
            else:
                screenshot(window, 'uninstalled-reopened')
        assert all(not str(r['command']).startswith(('plan.', 'profile.', 'assistant.')) for r in report['requests'])
        assert not window.evaluate_js("Boolean(document.querySelector('dialog[open]'))")
        for command in ('package.choose', 'package.import_preview', 'package.import_commit') if args.phase == 'first' else ('mod.list',):
            assert any(r['command'] == command and r['ok'] for r in report['requests']), command
        report['checks'].append('Ordinary consumer sends no Profile/plan/AI request and opens no approval dialog')
        report['checks'].append('Unknown file and UserData save/config sentinels remain byte-identical')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
            screenshot(window, 'failure')
        except Exception:
            pass
    finally:
        window.destroy()


def create(*values, **options):
    options.update(width=1280, height=800)
    window = original_create(*values, **options)
    windows.append(window)
    return window


def unexpected_confirmation(_):
    report['unexpected_confirmation'] = True
    raise AssertionError('Ordinary Mod actions must not request plan approval')


def start(*values, **options):
    contexts[0].choose_import_source = lambda kind: str(source) if kind == 'directory' else None
    contexts[0].confirm_plan = unexpected_confirmation
    return original_start(observe, **options)


webview.create_window, webview.start = create, start
report['app_exit_code'] = main.main(['--psmm-data-root', str(data)])
report['source_after'] = tree(source)
with managers[0]._lock:
    report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),
        subscriptions=len(managers[0]._subscriptions))
if report['source_before'] != report['source_after'] or any(report['cleanup'].values()) or report.get('unexpected_confirmation'):
    report['status'] = 'FAIL'
(out / (args.phase + '-direct-mods.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=report['app_exit_code'], checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if report['status'] == 'PASS' and report['app_exit_code'] == 0 else 1)
