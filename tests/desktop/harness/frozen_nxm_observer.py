"""Observe an owned frozen app; protocol callbacks exercise its normal activation entry."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.desktop.harness.portable_launcher import environment, owned_window, job_processes
from tests.integration.harness.transaction_recovery import TransactionFixture
import psutil
import win32api
import win32con
import win32gui
import win32job
import win32process

parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
env = environment(out)
fixture = TransactionFixture(out/'owned')
fixture.close()
job = win32job.CreateJobObject(None, '')
info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
exe = args.bundle.resolve()/'PSModManager.exe'
report = dict(status='FAIL', observations=[], forwarded=False, forced_cleanup=False)


def launch(file_id):
    uri = f'nxm://probablystolen/mods/81/files/{file_id}?key=owned-frozen-{file_id}&expires={int(time.time()+600)}&user_id=7'
    process = subprocess.Popen([str(exe), '--psmm-data-root', str(fixture.data), '--nxm', uri],
        cwd=out, env=env, creationflags=win32con.CREATE_SUSPENDED)
    win32job.AssignProcessToJobObject(job, int(process._handle))
    thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, psutil.Process(process.pid).threads()[0].id)
    try: win32process.ResumeThread(thread)
    finally: win32api.CloseHandle(thread)
    return process


try:
    process = launch(13)
    report['pid'] = process.pid
    deadline = time.monotonic()+600
    while process.poll() is None and time.monotonic() < deadline:
        hwnd = owned_window(process.pid)
        if hwnd:
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            children = []
            win32gui.EnumChildWindows(hwnd, lambda child, _: children.append((child, win32gui.GetClassName(child))), None)
            webview = next((child for child, name in children if name.startswith('WindowsForms10.') and 'STATIC' not in name), None)
            state = dict(iconic=bool(win32gui.IsIconic(hwnd)), bounds=win32gui.GetWindowRect(hwnd),
                minimize_box=bool(style & win32con.WS_MINIMIZEBOX), caption=bool(style & win32con.WS_CAPTION),
                child=win32gui.GetWindowRect(webview) if webview else None)
            if not report['observations'] or state != report['observations'][-1]:
                report['observations'].append(state)
                (out/'observations.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if state['iconic'] and not report['forwarded']:
                report['forward_exit'] = launch(11).wait(timeout=20)
                assert report['forward_exit'] == 0
                report['forwarded'] = True
        time.sleep(.1)
    report['exit_code'] = process.wait(timeout=3)
    deadline = time.monotonic()+15
    while job_processes(job) and time.monotonic() < deadline: time.sleep(.1)
    report['remaining'] = job_processes(job)
    assert report['forwarded'] and report['exit_code'] == 0 and not report['remaining']
    seen = report['observations']
    assert seen[0]['minimize_box'] and not seen[0]['caption']
    assert any(row['iconic'] for row in seen)
    assert not seen[-1]['iconic'] and seen[-1]['bounds'] == seen[-1]['child']
    report['status'] = 'PASS'
except Exception:
    report['error'] = traceback.format_exc()
finally:
    if job_processes(job):
        report['forced_cleanup'] = True
        win32job.TerminateJobObject(job, 124)
    win32api.CloseHandle(job)
    (out/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
