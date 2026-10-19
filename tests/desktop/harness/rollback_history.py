"""Real WebView history rollback, drift preservation and manual-repair recheck.

Only the installation picker selects a synthetic root. Setup uses actual import
and Profile RPCs. All rollback previews, native approval and execution originate
in the product page; fixture edits represent a player's external file changes.
"""
import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-03-04')
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
for key, name in [('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'), ('TEMP', 'temp'), ('TMP', 'temp')]:
    os.environ[key] = str(out / name)
    (out / name).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import webview
import win32gui
import win32process
import win32ui
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi
from tests.unit.test_installation_layout import make_installation
from tests.integration.harness.transaction_recovery import durable_json, database_snapshot, tree

game, data = out / 'synthetic-installation', out / 'private-data'
make_installation(game)
for name in ('Mods', 'Plugins', 'UserLibs', 'UserData'):
    (game / name).mkdir()
(game / 'Mods/unmanaged.png').write_bytes(b'unchanged unmanaged resource')
(game / 'UserData/save-sentinel.bin').write_bytes(b'unchanged synthetic save')
versions = (b'first deployed resource', b'second deployed resource')
for index, content in enumerate(versions):
    with zipfile.ZipFile(out / f'package-{index}.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('Mods/asset.png', content)
report = dict(status='FAIL', requests=[], screenshots=[], native_confirmations=[], checks=[],
    protected_before={key: value for key, value in tree(game).items() if key != 'Mods/asset.png'},
    archives_before={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in out.glob('package-*.zip')},
    scope='Actual native WebView/history/preview/approval/rollback and real files/SQLite; no game launch, network or manual/DPI acceptance')
contexts, managers, windows = [], [], []


def load_functions(filename, names):
    path = Path(__file__).with_name(filename)
    source = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    nodes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), globals())


# Import only pure input/capture functions, never old harness top-level scenarios.
load_functions('action_plan_preview.py', {'wait_for', 'mouse_click', 'click', 'native_confirmation', 'screenshot'})
shared_click = click
load_functions('deployment_execution.py', {'activate_window', 'mouse_click', 'click', 'editor', 'open_plan', 'approve_plan', 'call'})


def activate_window(hwnd):
    """Activate only this process's real window, never bypass foreground checks."""
    from ctypes import wintypes
    trace = dict(hwnd=hwnd, owner_pid=os.getpid(), attempts=[], caption_clicks=[], result='pending')
    report.setdefault('activations', []).append(trace)
    topmost_changed = False
    deadline = time.monotonic() + 3
    hit_test = ctypes.windll.user32.SendMessageTimeoutW
    hit_test.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    hit_test.restype = ctypes.c_ssize_t

    def owned(require_visible=True):
        if not win32gui.IsWindow(hwnd):
            raise AssertionError('Owned input window no longer exists')
        thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid != os.getpid():
            raise AssertionError('Input window belongs to another process')
        if require_visible and not win32gui.IsWindowVisible(hwnd):
            raise AssertionError('Owned input window is not visible')
        trace['window_pid'] = pid
        trace['window_thread'] = thread
        return thread

    def caption_click():
        # Query only our window. Do not click a caption merely because its
        # coordinates look right: another application's window may cover it.
        owned()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
        observation = dict(rect=[left, top, right, bottom],
            client_origin=[client_left, client_top], foreground=win32gui.GetForegroundWindow(), probes=[])
        trace.setdefault('caption_observations', []).append(observation)
        if right <= left or bottom <= top or client_top <= top:
            observation['rejected'] = 'No usable native nonclient caption rectangle'
            return False
        x = left + (right - left) // 2
        for y in range(top + 2, min(client_top, top + 80), 2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            owned()
            point_hwnd = win32gui.WindowFromPoint((x, y))
            probe = dict(x=x, y=y, point_hwnd=point_hwnd)
            observation['probes'].append(probe)
            if point_hwnd != hwnd:
                probe['rejected'] = 'Owned caption point is occluded'
                continue
            packed = (x & 0xffff) | ((y & 0xffff) << 16)
            hit_result = ctypes.c_size_t()
            # SMTO_BLOCK|SMTO_ABORTIFHUNG keeps an unresponsive owned UI thread
            # from turning a caption query into an unbounded activation wait.
            completed = hit_test(hwnd, 0x0084, 0, packed, 3,
                                 max(1, min(80, int(remaining * 1000))), ctypes.byref(hit_result))
            if not completed:
                probe['rejected'] = 'Owned hit-test query did not complete'
                trace.setdefault('caption_query_timeouts', []).append(dict(x=x, y=y))
                continue
            hit = hit_result.value  # WM_NCHITTEST result.
            probe['hit'] = hit
            if hit != 2:  # HTCAPTION, never a minimize/close/menu/control point.
                probe['rejected'] = 'Point is not HTCAPTION'
                continue
            ctypes.windll.user32.SetCursorPos(x, y)
            owned()
            hit_hwnd = win32gui.WindowFromPoint((x, y))
            if hit_hwnd != hwnd:
                continue
            ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
            ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
            trace['caption_clicks'].append(dict(x=x, y=y, hit=hit,
                hit_hwnd=hit_hwnd, input='Actual Win32 mouse input to verified owned caption'))
            return True
        return False

    try:
        owned()
        trace['topmost_before'] = bool(win32gui.GetWindowLong(hwnd, -20) & 8)  # GWL_EXSTYLE/WS_EX_TOPMOST
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE, only this owned window.
        message = wintypes.MSG()
        ctypes.windll.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        thread = ctypes.windll.kernel32.GetCurrentThreadId()
        while time.monotonic() < deadline:
            target_thread = owned()
            foreground = win32gui.GetForegroundWindow()
            if foreground == hwnd:
                break
            attempt = dict(foreground_before=foreground, target_thread=target_thread, attachments=[], errors=[])
            trace['attempts'].append(attempt)
            attached = []
            try:
                threads = {target_thread}
                if foreground and win32gui.IsWindow(foreground):
                    threads.add(win32process.GetWindowThreadProcessId(foreground)[0])
                for other in sorted(threads - {thread}):
                    joined = bool(ctypes.windll.user32.AttachThreadInput(thread, other, True))
                    attempt['attachments'].append(dict(thread=other, attached=joined))
                    if joined:
                        attached.append(other)
                owned()
                try:
                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)
                except Exception as exc:
                    attempt['errors'].append(str(exc)[:300])
            finally:
                detach_failures = []
                for other in reversed(attached):
                    detached = bool(ctypes.windll.user32.AttachThreadInput(thread, other, False))
                    attempt.setdefault('detachments', []).append(dict(thread=other, detached=detached))
                    if not detached:
                        detach_failures.append(other)
                if detach_failures:
                    raise AssertionError('Owned activation could not detach an input queue')
            owned()
            attempt['foreground_after_request'] = win32gui.GetForegroundWindow()
            if attempt['foreground_after_request'] == hwnd:
                break
            # A real verified caption click supplies actual native input when
            # SetForegroundWindow is denied by Windows foreground policy.
            attempt['caption_available'] = caption_click()
            if (not attempt['caption_available'] and not trace['topmost_before']
                    and not topmost_changed and time.monotonic() < deadline):
                owned()
                # Expose only our own caption above an occluding topmost window,
                # without activating it or changing any position or size.
                topmost_changed = True
                win32gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)  # HWND_TOPMOST; NOMOVE|NOSIZE|NOACTIVATE
                trace['topmost_requested'] = True
                owned()
                trace['topmost_observed'] = bool(win32gui.GetWindowLong(hwnd, -20) & 8)
                if not trace['topmost_observed']:
                    raise AssertionError('Owned window did not expose its caption at requested z-order')
                attempt['caption_after_topmost'] = caption_click()
            time.sleep(.05)
            attempt['foreground_after_input'] = win32gui.GetForegroundWindow()
            if attempt['foreground_after_input'] == hwnd:
                break
        owned()
        trace['foreground_final'] = win32gui.GetForegroundWindow()
        if trace['foreground_final'] != hwnd:
            raise AssertionError('Owned input window is not foreground after bounded native activation')
        trace['result'] = 'activated'
    except BaseException as exc:
        trace['result'] = 'failed'
        trace['error'] = str(exc)[:500]
        raise
    finally:
        if topmost_changed:
            try:
                # A recycled or foreign HWND is never restored or otherwise
                # touched; visibility is unnecessary for our metadata cleanup.
                owned(require_visible=False)
                win32gui.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x13)  # HWND_NOTOPMOST, original false bit.
                trace['topmost_restored'] = not bool(win32gui.GetWindowLong(hwnd, -20) & 8)
                if not trace['topmost_restored']:
                    raise AssertionError('Owned window z-order was not restored')
                trace['foreground_after_restore'] = win32gui.GetForegroundWindow()
                if trace['result'] == 'activated' and trace['foreground_after_restore'] != hwnd:
                    raise AssertionError('Owned input window lost foreground while restoring its z-order')
            except BaseException as exc:
                trace['result'] = 'failed'
                trace['restore_error'] = str(exc)[:500]
                raise


def factory(context):
    contexts.append(context); managers.append(context.jobs)
    api = DesktopApi(context)
    original = api.dispatch
    def observed(request):
        result = original(request)
        report['requests'].append(dict(command=request.get('command'), payload=request.get('payload'), ok=result['ok'], error_code=(result.get('error') or {}).get('code')))
        return result
    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        raise PermissionError('No Python business networking in synthetic rollback harness')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real source or game access')


sys.addaudithook(audit)


def history_ready(window):
    wait_for(window, "document.querySelector('[data-testid=deployment-history]')&&!document.querySelector('[data-testid=history-progress]')&&!document.querySelector('[data-testid=history-error]')")


def committed(window):
    expected_plan = next(row['payload']['plan_id'] for row in reversed(report['requests']) if row['command'] == 'plan.approve')
    outcome = wait_for(window, "!document.querySelector('[data-testid=plan-progress]')&&(document.querySelector('[data-testid=plan-error]')?.innerText||document.querySelector('[data-testid=deployment-state]')?.dataset.state==='committed'&&document.querySelector('[data-testid=deployment-state]')?.dataset.planId===" + json.dumps(expected_plan) + "&&'committed')")
    assert outcome == 'committed', outcome
    assert window.evaluate_js("document.querySelector('[data-testid=deployment-verification]').innerText==='未进行'&&document.querySelector('[data-testid=plan-apply]').disabled")


def deploy(window):
    click(window, 'workspace-mods'); editor(window); open_plan(window); approve_plan(window)
    click(window, 'plan-apply'); committed(window); click(window, 'plan-close')


def rollback_latest(window):
    click(window, 'history-rollback')
    wait_for(window, "document.querySelector('[data-testid=plan-summary]')&&!document.querySelector('[data-testid=plan-progress]')")
    assert window.evaluate_js("document.querySelector('#plan-title').innerText==='回退计划预览'&&document.querySelector('[data-testid=plan-apply]').disabled")
    approve_plan(window); click(window, 'plan-apply'); committed(window); click(window, 'plan-close')
    wait_for(window, "!document.querySelector('[data-testid=plan-preview]')")
    history_ready(window)


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=installation-open]'))")
        window.evaluate_js("window.__historyClicks=[];window.__historyKeys=[];document.addEventListener('click',e=>{const t=e.target.closest('[data-testid]');if(t)window.__historyClicks.push({testid:t.dataset.testid,trusted:e.isTrusted})},true);document.addEventListener('keydown',e=>{const t=e.target.closest('[data-testid]');if(t)window.__historyKeys.push({testid:t.dataset.testid,key:e.key,trusted:e.isTrusted})},true)")
        click(window, 'installation-open'); click(window, 'installation-manual'); click(window, 'installation-select')
        wait_for(window, "!document.querySelector('dialog[open]')")
        installation = call('installation.list')['selected_installation_id']
        releases = []
        for index in range(2):
            grant = contexts[0].imports.grant('zip', str(out / f'package-{index}.zip'))
            imported = call('package.import_preview', source_handle=grant['source_handle'])
            releases.append(call('package.import_commit', preview_id=imported['preview_id'], preview_hash=imported['preview_hash'], display_name=f'合成历史资源 {index}'))
        profile = call('profile.create', installation_id=installation, name='真实回退历史')
        profile = call('profile.edit', profile_id=profile['profile_id'], expected_revision=profile['revision'], action=dict(op='set_selection', selection=dict(release_id=releases[0]['release_id'], manifest_hash=releases[0]['manifest_hash'], selected=True)))
        profile = call('profile.commit', profile_id=profile['profile_id'], expected_revision=profile['revision'])
        deploy(window)
        for index, selected in ((0, False), (1, True)):
            profile = call('profile.edit', profile_id=profile['profile_id'], expected_revision=profile['revision'], action=dict(op='set_selection', selection=dict(release_id=releases[index]['release_id'], manifest_hash=releases[index]['manifest_hash'], selected=selected)))
        profile = call('profile.commit', profile_id=profile['profile_id'], expected_revision=profile['revision'])
        click(window, 'profile-refresh'); editor(window); deploy(window)
        assert (game / 'Mods/asset.png').read_bytes() == versions[1]
        before = call('deployment.status', installation_id=installation)
        report['two_deployments'] = dict(status=before, database=database_snapshot(data / 'psmodmanager.sqlite3'), disk=tree(game))
        report['profile_before_rollback'] = call('profile.get', profile_id=profile['profile_id'])
        assert len(report['two_deployments']['database']['entities']['deployment_generations']) == 2
        click(window, 'workspace-history'); history_ready(window)
        assert window.evaluate_js("document.querySelectorAll('[data-testid=history-record]').length===2&&document.querySelector('[data-testid=history-record]').dataset.generationId===" + json.dumps(before['current_generation']))
        screenshot(window, 'two-generations-history')
        rollback_latest(window)
        rolled = call('deployment.status', installation_id=installation)
        report['first_rollback'] = dict(status=rolled, history=call('deployment.history', installation_id=installation), database=database_snapshot(data / 'psmodmanager.sqlite3'), disk=tree(game))
        assert (game / 'Mods/asset.png').read_bytes() == versions[0]
        assert rolled['current_generation'] != before['current_generation']
        assert len(report['first_rollback']['database']['entities']['deployment_generations']) == 3
        assert report['first_rollback']['history']['records'][0]['kind'] == 'rollback'
        assert report['first_rollback']['history']['records'][0]['reverts_transaction'] == before['transaction']['transaction_id']
        assert window.evaluate_js("document.querySelector('[data-testid=history-record]').dataset.kind==='rollback'&&document.querySelector('[data-testid=history-verification]').innerText.includes('未进行')")
        report['profile_after_rollback'] = call('profile.get', profile_id=profile['profile_id'])
        assert report['profile_after_rollback'] == report['profile_before_rollback'], 'History rollback must not rewrite editable Profile'
        screenshot(window, 'rollback-committed-history')
        # The saved Profile still asks for v2, so its real deployment creates a
        # fourth generation; the player's later edit is outside the executor.
        deploy(window)
        current = call('deployment.status', installation_id=installation)
        (game / 'Mods/asset.png').write_bytes(b'player external edit must survive')
        external_hash = hashlib.sha256((game / 'Mods/asset.png').read_bytes()).hexdigest()
        report['external_edit'] = dict(sha256=external_hash, generation=current['current_generation'])
        click(window, 'workspace-history'); history_ready(window)
        click(window, 'history-rollback')
        error = wait_for(window, "!document.querySelector('[data-testid=plan-progress]')&&document.querySelector('[data-testid=plan-error]')?.innerText")
        assert 'ROLLBACK' in error or 'DRIFT' in error, error
        assert window.evaluate_js("document.querySelector('[data-testid=plan-approve]').disabled&&document.querySelector('[data-testid=plan-apply]').disabled")
        assert hashlib.sha256((game / 'Mods/asset.png').read_bytes()).hexdigest() == external_hash
        screenshot(window, 'drift-blocked-preview'); click(window, 'plan-close'); history_ready(window)
        assert window.evaluate_js("Boolean(document.querySelector('[data-testid=history-recovery-required]'))&&[...document.querySelectorAll('[data-testid=history-rollback]')].every(button=>button.disabled)&&document.querySelector('[data-testid=history-preservation]').innerText.includes('保全')")
        report['drift_history'] = call('deployment.history', installation_id=installation)
        assert report['drift_history']['recovery_required']
        preserved_files = [file for issue in report['drift_history']['issues'] for file in issue['files']]
        assert any(file['preserved'] and file.get('copy_sha256') == external_hash for file in preserved_files)
        screenshot(window, 'preserved-conflict-history')
        click(window, 'history-recheck'); history_ready(window)
        assert window.evaluate_js("Boolean(document.querySelector('[data-testid=history-recovery-required]'))&&!document.querySelector('[data-testid=history-notice]')?.innerText.includes('已解除')")
        assert hashlib.sha256((game / 'Mods/asset.png').read_bytes()).hexdigest() == external_hash
        # Explicit fixture action models manual restoration, preserving the
        # external file's byte copy; the product rechecks rather than forcing.
        (game / 'Mods/asset.png').write_bytes(versions[1])
        report['manual_restore'] = dict(sha256=hashlib.sha256(versions[1]).hexdigest(), generation_before=current['current_generation'])
        click(window, 'history-recheck'); history_ready(window)
        assert window.evaluate_js("!document.querySelector('[data-testid=history-recovery-required]')&&document.querySelector('[data-testid=history-notice]')?.innerText.includes('已解除')")
        report['rechecked_history'] = call('deployment.history', installation_id=installation)
        assert not report['rechecked_history']['recovery_required']
        assert report['rechecked_history']['current_generation'] == current['current_generation']
        assert any(issue['state'] == 'resolved' for issue in report['rechecked_history']['issues'])
        assert all(file['copy_sha256'] == external_hash for issue in report['rechecked_history']['issues'] for file in issue['files'] if file.get('copy_sha256'))
        screenshot(window, 'manual-restore-rechecked')
        rollback_latest(window)
        assert (game / 'Mods/asset.png').read_bytes() == versions[0]
        report['final_history'] = call('deployment.history', installation_id=installation)
        report['final_database'] = database_snapshot(data / 'psmodmanager.sqlite3')
        assert len(report['final_database']['entities']['deployment_generations']) == 5
        assert report['final_database']['quick_check'] == [('ok',)] and not report['final_database']['foreign_key_check']
        assert report['final_history']['records'][0]['kind'] == 'rollback'
        assert report['final_history']['verification_status'] == 'not_run' and not report['final_history']['cleanup_available']
        screenshot(window, 'retry-rollback-committed')
        click(window, 'history-refresh'); history_ready(window)
        activate_window(window.native.Handle.ToInt64())
        # A native HTML button loses focus while its real request disables it.
        # Reach the enabled refresh button through native Tab navigation rather
        # than assigning focus or synthesizing DOM keyboard events.
        for _ in range(60):
            if window.evaluate_js("document.activeElement?.dataset.testid==='history-refresh'"):
                break
            ctypes.windll.user32.keybd_event(0x09, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x09, 0, 2, 0)
            time.sleep(.04)
        else:
            raise AssertionError('Native Tab navigation did not reach history refresh')
        ctypes.windll.user32.keybd_event(0x20, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x20, 0, 2, 0)
        wait_for(window, "window.__historyKeys.some(event=>event.testid==='history-refresh'&&event.key===' '&&event.trusted)")
        history_ready(window)
        report['trusted_clicks'] = window.evaluate_js('window.__historyClicks')
        report['trusted_keys'] = window.evaluate_js('window.__historyKeys')
        report['checks'] = ['Two actual generations precede history selection', 'Native approved history rollback creates new generation and leaves Profile unchanged', 'External edit is copied and its original bytes are untouched', 'Rollback and new writes remain blocked until manual restoration and actual UI recheck', 'Unrepaired recheck never claims recovery', 'Resolved issue and copy hash remain in history', 'Retry rollback commits fifth actual generation', 'Real mouse and keyboard inputs are trusted', 'Runtime verification remains not_run; no cleanup action']
        report['status'] = 'PASS'
    except Exception as exc:
        report['error'] = str(exc)[:2000]
        report['failure_location'] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)]
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
            report['trusted_clicks'] = window.evaluate_js('window.__historyClicks')
            report['trusted_keys'] = window.evaluate_js('window.__historyKeys')
            screenshot(window, 'failure')
        except Exception:
            pass
    finally:
        window.destroy()


original_create, original_start = webview.create_window, webview.start


def create(*values, **options):
    window = original_create(*values, **options); windows.append(window); return window


def start(*values, **options):
    contexts[0].choose_installation_folder = lambda kind: str(game) if kind == 'manual' else None
    return original_start(lambda: observe(windows[0]), **options)


webview.create_window, webview.start = create, start
code = main.main(['--psmm-data-root', str(data)])
report['app_exit_code'] = code
report['protected_after'] = {key: value for key, value in tree(game).items() if key != 'Mods/asset.png'}
report['archives_after'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in out.glob('package-*.zip')}
if managers:
    with managers[0]._lock:
        report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()), subscriptions=len(managers[0]._subscriptions))
if report['protected_before'] != report['protected_after'] or report['archives_before'] != report['archives_after'] or any(report.get('cleanup', {}).values()):
    report['status'] = 'FAIL'
durable_json(out / 'rollback-history.json', report)
print(json.dumps(dict(status=report['status'], app_exit_code=code, checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)
