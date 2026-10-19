"""Actual offline WebView apply, controlled cancellation, and startup recovery.

The native picker alone chooses an isolated installation. Constructor-only
checkpoint hooks pause real writes or terminate this exact owned process; they
never substitute executor results, journal facts, or native approval receipts.
"""
import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=('first', 'reopen'), required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-03-03')
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
from backend.deployments import process_guard
from backend.deployments.executor import DeploymentExecutor
from tests.unit.test_installation_layout import make_installation
from tests.integration.harness.transaction_recovery import durable_json, database_snapshot, tree

game, data = out / 'synthetic-installation', out / 'private-data'
if args.phase == 'first':
    make_installation(game)
    for name in ('Mods', 'Plugins', 'UserLibs', 'UserData'):
        (game / name).mkdir()
    (game / 'Mods/unmanaged.png').write_bytes(b'unchanged unmanaged resource')
    (game / 'UserData/save-sentinel.bin').write_bytes(b'unchanged synthetic save')
    for index, content in enumerate((b'first deployed resource', b'second proposed resource')):
        with zipfile.ZipFile(out / f'package-{index}.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('Mods/asset.png', content)
    durable_json(out / 'owner.json', {'root': str(out), 'synthetic': True, 'owner': 'PS-03-03-desktop'})
else:
    assert json.loads((out / 'owner.json').read_text(encoding='utf-8')) == {'root': str(out), 'synthetic': True, 'owner': 'PS-03-03-desktop'}

report = dict(status='FAIL', phase=args.phase, requests=[], screenshots=[], native_confirmations=[], checks=[],
    protected_before={key: value for key, value in tree(game).items() if key != 'Mods/asset.png'},
    archives_before={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in out.glob('package-*.zip')},
    scope='Real WebView, native confirmation, physical writes, Job cancellation and DesktopContext startup recovery; no game launched')
contexts, managers, windows = [], [], []
mode = None
paused, cancelled, release_pause = threading.Event(), threading.Event(), threading.Event()


def guard_observation(value):
    report.setdefault('guard_observations', []).append(value)
    report['guard_observations'] = report['guard_observations'][-1000:]
    if value.get('ntstatus') not in (None, '0x00000000', '0xc0000001'):
        report.setdefault('native_failures', []).append(value)


original_dll = ctypes.WinDLL


class ObservedNativeFunction:
    def __init__(self, function, name):
        object.__setattr__(self, 'function', function)
        object.__setattr__(self, 'name', name)

    def __getattr__(self, key):
        return getattr(self.function, key)

    def __setattr__(self, key, value):
        setattr(self.function, key, value)

    def __call__(self, *values):
        result = self.function(*values)
        entry = dict(api=self.name, ntstatus=f'0x{result & 0xffffffff:08x}',
            handle=int(values[0]), last_fault=report.get('last_fault'))
        if self.name == 'NtQueryInformationProcess' and values[1] == 44:
            from ctypes import wintypes
            kernel = original_dll('kernel32', use_last_error=True)
            pid = kernel.GetProcessId
            pid.argtypes, pid.restype = [wintypes.HANDLE], wintypes.DWORD
            entry['pid'] = pid(values[0]); entry['class'] = 44
        elif self.name == 'NtQueryInformationFile' and values[-1] == 47:
            entry['class'] = 47
            class IoStatus(ctypes.Structure):
                _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]
            io = ctypes.cast(values[1], ctypes.POINTER(IoStatus)).contents
            entry.update(io_status=io.Status, information=io.Information, buffer_bytes=values[3])
            if result == 0 and values[3] >= 8:
                count = ctypes.c_ulong.from_buffer(values[2]).value
                entry['count'] = count
                if 0 <= count <= 32768 and 8 + count * ctypes.sizeof(ctypes.c_size_t) <= values[3]:
                    entry['pids'] = list((ctypes.c_size_t * count).from_buffer(values[2], 8))[:64]
        guard_observation(entry)
        return result  # Preserve the exact native return and output buffers.


class ObservedDll:
    def __init__(self, dll):
        self.dll = dll

    def __getattr__(self, key):
        function = getattr(self.dll, key)
        return ObservedNativeFunction(function, key) if key in {'NtQueryInformationProcess', 'NtQueryInformationFile'} else function


def observed_dll(name, *values, **options):
    dll = original_dll(name, *values, **options)
    return ObservedDll(dll) if str(name).casefold() == 'ntdll' else dll


ctypes.WinDLL = observed_dll
original_inspect = process_guard.ProcessGuard._inspect_processes


def observed_inspect(self, executable):
    result = original_inspect(self, executable)
    guard_observation(dict(api='ProcessGuard._inspect_processes', result=result,
        last_fault=report.get('last_fault')))
    return result


process_guard.ProcessGuard._inspect_processes = observed_inspect
original_recover = DeploymentExecutor._recover_transaction


def observed_recover(self, transaction, *values, **options):
    entry = dict(transaction_id=str(transaction.transaction_id), state=transaction.state)
    try:
        result = original_recover(self, transaction, *values, **options)
        entry.update(result=result, resulting_state=transaction.state)
        return result
    except BaseException as exc:
        entry.update(error_type=type(exc).__name__, error_code=getattr(exc, 'code', None), error=str(exc)[:500],
            frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)])
        raise
    finally:
        report.setdefault('recovery_observations', []).append(entry)


DeploymentExecutor._recover_transaction = observed_recover

# Reuse only existing pure UI input/capture functions. Importing the old script
# would start its unrelated standalone scenario, so its top-level code is not run.
helper_path = Path(__file__).with_name('action_plan_preview.py')
helper_names = {'wait_for', 'mouse_click', 'click', 'native_confirmation', 'screenshot'}
helper_ast = ast.parse(helper_path.read_text(encoding='utf-8'), filename=str(helper_path))
helper_nodes = [node for node in helper_ast.body if isinstance(node, ast.FunctionDef) and node.name in helper_names]
assert {node.name for node in helper_nodes} == helper_names
exec(compile(ast.Module(body=helper_nodes, type_ignores=[]), str(helper_path), 'exec'), globals())


def activate_window(hwnd):
    # SetForegroundWindow can return zero for an already foreground window.
    # Verify the actual destination instead of treating that return as failure.
    if win32gui.GetForegroundWindow() != hwnd:
        win32gui.ShowWindow(hwnd, 9)  # Restore only our own window.
        attached = []
        try:
            # Join input queues briefly, without changing system foreground
            # timeout policy, then detach even if activation fails.
            from ctypes import wintypes
            message = wintypes.MSG()
            ctypes.windll.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            thread = ctypes.windll.kernel32.GetCurrentThreadId()
            foreground = win32gui.GetForegroundWindow()
            threads = {win32process.GetWindowThreadProcessId(hwnd)[0]}
            if foreground:
                threads.add(win32process.GetWindowThreadProcessId(foreground)[0])
            for other in threads - {thread}:
                if ctypes.windll.user32.AttachThreadInput(thread, other, True):
                    attached.append(other)
            win32gui.BringWindowToTop(hwnd)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                if win32gui.GetForegroundWindow() != hwnd:
                    raise
        finally:
            for other in attached:
                ctypes.windll.user32.AttachThreadInput(thread, other, False)
    assert win32gui.GetForegroundWindow() == hwnd, 'Owned input window is not foreground'


def mouse_click(hwnd, x, y):
    activate_window(hwnd)
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)


shared_click = click


def click(window, testid):
    # Restore/focus before calculating coordinates; restoration may move or
    # resize the native client rectangle used by the shared trusted-input helper.
    activate_window(window.native.Handle.ToInt64())
    shared_click(window, testid)


def checkpoint(point, details):
    report['last_fault'] = dict(point=point, details=details)
    if point != 'operation.done' or mode not in {'cancel', 'crash'} or paused.is_set():
        return
    report[mode + '_boundary'] = dict(point=point, details=details, pid=os.getpid(), game=tree(game),
        database=database_snapshot(data / 'psmodmanager.sqlite3'))
    if mode == 'crash':
        report['trusted_clicks'] = windows[0].evaluate_js('window.__executionClicks')
        report['protected_after'] = {key: value for key, value in tree(game).items() if key != 'Mods/asset.png'}
        report['archives_after'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in out.glob('package-*.zip')}
        assert report['protected_before'] == report['protected_after'] and report['archives_before'] == report['archives_after']
        report['status'] = 'CRASH_EXPECTED'
        durable_json(out / 'first-deployment-execution.json', report)
        os._exit(73)  # This exact synthetic child only, after durable target done.
    paused.set()
    assert release_pause.wait(60), 'Actual UI cancellation did not release owned pause'


original_context = composition.DesktopContext


class ObservedContext(original_context):
    def __init__(self, *values, **options):
        super().__init__(*values, **options)
        self.deployment_fault = checkpoint


composition.DesktopContext = ObservedContext


def factory(context):
    contexts.append(context); managers.append(context.jobs)
    api = DesktopApi(context)
    original = api.dispatch
    def observed(request):
        result = original(request)
        report['requests'].append(dict(command=request.get('command'), payload=request.get('payload'),
            ok=result['ok'], error_code=(result.get('error') or {}).get('code')))
        if request.get('command') == 'deployment.status' and result['ok']:
            report.setdefault('status_snapshots', []).append(result['data'])
        if request.get('command') == 'task.cancel' and mode == 'cancel':
            cancelled.set()
        return result
    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        raise PermissionError('No Python business networking in synthetic deployment harness')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real source or game access')


sys.addaudithook(audit)


def call(command, **payload):
    context = contexts[0]
    result = context.api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))
    assert result['ok'], result
    value = result['data']
    deadline = time.monotonic() + 45
    while isinstance(value, dict) and value.get('task_id'):
        if value['state'] in ('succeeded', 'failed', 'cancelled'):
            assert value['state'] == 'succeeded' and not value['data'].get('error_code'), value
            return value['data']
        assert time.monotonic() < deadline, value
        time.sleep(.02); value = context.jobs.snapshot(value['task_id'])
    return value


def editor(window):
    wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'&&!document.querySelector('[data-testid=profile-plan-preview]')?.disabled")


def open_plan(window):
    click(window, 'profile-plan-preview')
    wait_for(window, "document.querySelector('[data-testid=plan-summary]')&&!document.querySelector('[data-testid=plan-progress]')")
    assert window.evaluate_js("document.querySelector('[data-testid=plan-apply]').disabled")


def approve_plan(window):
    click(window, 'plan-approve'); native_confirmation(True)
    wait_for(window, "document.querySelector('[data-testid=plan-approval]')&&!document.querySelector('[data-testid=plan-apply]').disabled")


def observe(window):
    global mode
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=installation-open]'))")
        window.evaluate_js("window.__executionClicks=[];document.addEventListener('click',e=>{const t=e.target.closest('[data-testid]');if(t)window.__executionClicks.push({testid:t.dataset.testid,trusted:e.isTrusted})},true)")
        if args.phase == 'first':
            click(window, 'installation-open'); click(window, 'installation-manual'); click(window, 'installation-select')
            wait_for(window, "!document.querySelector('dialog[open]')")
            installation = call('installation.list')['selected_installation_id']
            releases = []
            for index in range(2):
                grant = contexts[0].imports.grant('zip', str(out / f'package-{index}.zip'))
                imported = call('package.import_preview', source_handle=grant['source_handle'])
                releases.append(call('package.import_commit', preview_id=imported['preview_id'], preview_hash=imported['preview_hash'], display_name=f'合成资源 {index}'))
            profile = call('profile.create', installation_id=installation, name='真实事务界面')
            profile = call('profile.edit', profile_id=profile['profile_id'], expected_revision=profile['revision'], action=dict(op='set_selection', selection=dict(release_id=releases[0]['release_id'], manifest_hash=releases[0]['manifest_hash'], selected=True)))
            profile = call('profile.commit', profile_id=profile['profile_id'], expected_revision=profile['revision'])
            click(window, 'workspace-mods'); editor(window); open_plan(window); approve_plan(window)
            screenshot(window, 'first-approved'); click(window, 'plan-apply')
            outcome = wait_for(window, "!document.querySelector('[data-testid=plan-progress]')&&(document.querySelector('[data-testid=plan-error]')?.innerText||document.querySelector('[data-testid=deployment-state]')?.dataset.state==='committed'&&'committed')")
            assert outcome == 'committed', outcome
            report['committed_status'] = call('deployment.status', installation_id=installation)
            report['committed_disk'] = tree(game); report['committed_database'] = database_snapshot(data / 'psmodmanager.sqlite3')
            assert (game / 'Mods/asset.png').read_bytes() == b'first deployed resource'
            assert len(report['committed_database']['entities']['deployment_generations']) == 1
            assert window.evaluate_js("document.querySelector('[data-testid=deployment-verification]').innerText==='未进行'&&document.querySelector('[data-testid=plan-apply]').disabled")
            binding = next(row['payload'] for row in reversed(report['requests']) if row['command'] == 'plan.apply')
            before_replay = (game / 'Mods/asset.png').stat()
            replay = call('assistant.apply_plan', **binding)
            after_replay = (game / 'Mods/asset.png').stat()
            report['replay_result'] = replay
            report['replay_status'] = call('deployment.status', installation_id=installation, plan_id=binding['plan_id'])
            assert report['replay_status']['current_generation'] == report['committed_status']['current_generation']
            assert (before_replay.st_ino, before_replay.st_mtime_ns) == (after_replay.st_ino, after_replay.st_mtime_ns)
            replay_db = database_snapshot(data / 'psmodmanager.sqlite3')['entities']
            assert len(replay_db['deployment_generations']) == len(replay_db['transactions']) == 1
            assert tree(game) == report['committed_disk']; screenshot(window, 'committed'); click(window, 'plan-close')
            for index, selected in ((0, False), (1, True)):
                profile = call('profile.edit', profile_id=profile['profile_id'], expected_revision=profile['revision'], action=dict(op='set_selection', selection=dict(release_id=releases[index]['release_id'], manifest_hash=releases[index]['manifest_hash'], selected=selected)))
            profile = call('profile.commit', profile_id=profile['profile_id'], expected_revision=profile['revision'])
            click(window, 'profile-refresh'); editor(window); open_plan(window); approve_plan(window)
            mode = 'cancel'; click(window, 'plan-apply'); assert paused.wait(30)
            assert (game / 'Mods/asset.png').read_bytes() == b'second proposed resource', 'Cancellation test did not reach actual changed disk'
            click(window, 'plan-close'); assert cancelled.wait(30)
            wait_for(window, "document.querySelector('[data-testid=plan-preview]')?.open&&document.querySelector('[data-testid=plan-progress]')?.innerText.includes('正在取消')")
            assert window.evaluate_js("!document.querySelector('[data-testid=deployment-state]')&&Boolean(document.querySelector('[data-testid=deployment-pending]'))"), 'Previous committed transaction must not appear as current success while cancelling'
            screenshot(window, 'cancel-waits-for-rollback'); release_pause.set()
            wait_for(window, "document.querySelector('[data-testid=deployment-state]')?.dataset.state==='rolled_back'&&!document.querySelector('[data-testid=plan-progress]')")
            report['cancelled_status'] = call('deployment.status', installation_id=installation)
            assert tree(game) == report['committed_disk']; assert report['cancelled_status']['current_generation'] == report['committed_status']['current_generation']
            assert not report['cancelled_status']['recovery_required']
            assert window.evaluate_js("document.querySelector('[data-testid=plan-preview]').open&&document.querySelector('[data-testid=deployment-verification]').innerText==='未进行'&&document.querySelector('[data-testid=plan-apply]').disabled")
            screenshot(window, 'rolled-back'); click(window, 'plan-close')
            report['checks'] = ['Native approval binds actual apply', 'Real target file and DB generation commit', 'Assistant replay uses same executor without rewriting', 'Close sends actual task cancellation after target changed', 'Dialog waits for persisted rolled_back and old generation', 'Runtime verification remains not_run']
            open_plan(window); approve_plan(window)
            mode = 'crash'; paused.clear(); click(window, 'plan-apply')
            # The constructor checkpoint ends this exact process. Reaching this
            # deadline is failure, rather than fabricating a pending journal.
            time.sleep(35)
            raise AssertionError('Owned process did not terminate at operation.done')
        else:
            previous = json.loads((out / 'first-deployment-execution.json').read_text(encoding='utf-8'))
            # Installation selection is session-local. Reuse the actual picker
            # UI rather than assuming restart selects an installation for us.
            click(window, 'installation-open'); click(window, 'installation-manual'); click(window, 'installation-select')
            wait_for(window, "!document.querySelector('dialog[open]')")
            installation = call('installation.list')['selected_installation_id']
            report['startup_status'] = call('deployment.status', installation_id=installation)
            if report['startup_status']['recovery_required']:
                click(window, 'workspace-mods'); editor(window); click(window, 'profile-plan-preview')
                wait_for(window, "Boolean(document.querySelector('[data-testid=deployment-recovery-blocked]'))&&!document.querySelector('[data-testid=plan-progress]')")
                assert window.evaluate_js("document.querySelector('[data-testid=plan-apply]').disabled&&!document.querySelector('[data-testid=plan-summary]')&&Boolean(document.querySelector('[data-testid=deployment-recover]'))")
                screenshot(window, 'startup-recovery-blocked')
            assert report['startup_status']['startup_recovery']['status'] == 'recovered'
            assert report['startup_status']['startup_recovery']['recovered_count'] == 1
            assert not report['startup_status']['recovery_required']
            assert tree(game) == previous['committed_disk']
            assert report['startup_status']['current_generation'] == previous['committed_status']['current_generation']
            click(window, 'workspace-mods'); editor(window); open_plan(window)
            wait_for(window, "document.querySelector('[data-testid=deployment-startup-recovery]')?.innerText.includes('1 个')")
            assert window.evaluate_js("document.querySelector('[data-testid=deployment-verification]').innerText==='未进行'")
            report['recovered_database'] = database_snapshot(data / 'psmodmanager.sqlite3')
            assert report['recovered_database']['quick_check'] == [('ok',)] and not report['recovered_database']['foreign_key_check']
            assert len(report['recovered_database']['entities']['deployment_generations']) == 1
            screenshot(window, 'startup-recovered')
            report['trusted_clicks'] = window.evaluate_js('window.__executionClicks')
            report['checks'] = ['DesktopContext recovers actual incomplete journal before WebView', 'Physical file, DB generation and journal agree with previous deployment', 'Actual page displays startup recovery and runtime verification not_run']
            report['status'] = 'PASS'
    except Exception as exc:
        report['error'] = str(exc)[:2000]
        report['failure_location'] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)]
        try:
            report['failure_document'] = window.evaluate_js('document.body.innerText')
            report['trusted_clicks'] = window.evaluate_js('window.__executionClicks')
            screenshot(window, args.phase + '-failure')
        except Exception:
            pass
    finally:
        release_pause.set(); window.destroy()


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
durable_json(out / (args.phase + '-deployment-execution.json'), report)
print(json.dumps(dict(status=report['status'], app_exit_code=code, checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if code == 0 and report['status'] == 'PASS' else 1)
