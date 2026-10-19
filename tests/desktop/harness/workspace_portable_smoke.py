"""Frozen UI consumer using the established owned-window Windows test driver.

Opens the workspaces and optionally toggles an owned synthetic Mod. No real game
write/launch, provider, install or native picker. Test data is always isolated.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.desktop.harness.portable_launcher import environment, owned_window, wait_ui, visible_names, automation, capture, job_processes
import win32api
import win32con
import win32gui
import win32job
import win32process
import psutil

parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--mod-files', type=int, default=0, help='Optionally exercise frozen save with this many owned synthetic files')
args = parser.parse_args()
assert 0 <= args.mod_files <= 4000
bundle, out = args.bundle.resolve(strict=True), args.out.resolve()
assert bundle.is_relative_to(ROOT / '.preview')
assert any(out.is_relative_to(ROOT / 'docs/development/evidence' / name)
           for name in ('20261015-workspace-redesign', '20261015-lightweight-mod-ops', '20261015-ui-repack', '20261016-ui-repack', '20261016-mod-sharing-analysis-online', '20261016-followup-fixes', '20261018-ui-repack'))
out.mkdir(parents=True)
exe = bundle / 'PSModManager.exe'
env = environment(out)
cwd = out / '中文 空格目录'; cwd.mkdir()
command = [str(exe)]
mod_files = []
if args.mod_files:
    from tests.integration.harness.transaction_recovery import TransactionFixture
    fixture = TransactionFixture(out / 'owned')
    try:
        mod_files = [fixture.game / 'Mods/PortableCheck.dll']
        resources = fixture.game / 'Mods/PortableCheck/resources'
        resources.mkdir(parents=True)
        mod_files += [resources / f'{index:04d}.png' for index in range(args.mod_files - 1)]
        for path in mod_files:
            path.write_bytes(b'owned-portable-resource')
        command += ['--psmm-data-root', str(fixture.data)]
    finally:
        fixture.close()
job = win32job.CreateJobObject(None, '')
settings = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
settings['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, settings)
report = dict(status='FAIL', checks=[], screenshots=[], timings=[], mod_files=args.mod_files,
              scope='Frozen EXE, isolated data, existing UIAutomation driver; optional owned synthetic Mod writes, no real game writes/launch/provider', forced_cleanup=False)
process = hwnd = None
try:
    with (out / 'stdout.log').open('xb') as stdout, (out / 'stderr.log').open('xb') as stderr:
        process = subprocess.Popen(command, env=env, cwd=cwd, stdout=stdout, stderr=stderr, creationflags=win32con.CREATE_SUSPENDED)
        win32job.AssignProcessToJobObject(job, int(process._handle))
        threads = psutil.Process(process.pid).threads()
        assert len(threads) == 1
        thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, threads[0].id)
        try: assert win32process.ResumeThread(thread) == 1
        finally: win32api.CloseHandle(thread)
        deadline = time.monotonic() + 60
        while not hwnd and process.poll() is None and time.monotonic() < deadline:
            hwnd = owned_window(process.pid); time.sleep(.1)
        assert hwnd, 'Frozen main window did not appear'
        ready = wait_ui(hwnd, process.pid, env, lambda value: any(
            row['name'] == '应用信息' and row['enabled'] and not row['offscreen']
            for row in value['rows'] if row['control'] == 'ControlType.Button'))
        names = visible_names(ready)
        log_action = '日志' if '日志' in names else '日志与 AI'
        assert '模组' in names and log_action in names
        assert '游戏文件' not in names
        report['ready'] = ready
        report['checks'].append('Frozen app starts without development PATH; unified navigation renders')
        report['screenshots'].append(capture(hwnd, process.pid, out, 'mods.png'))
        if mod_files:
            # Startup can discover the host's Steam installation as well as
            # the fixture. Choose the one saved by this isolated data root.
            if '选择游戏安装' in names:
                automation(hwnd, process.pid, env, action='选择游戏安装')
                wait_ui(hwnd, process.pid, env, lambda value: any(
                    row['name'] == '切换到此安装' and row['enabled']
                    for row in value['rows'] if row['control'] == 'ControlType.Button'))
                automation(hwnd, process.pid, env, action='切换到此安装')
            wait_ui(hwnd, process.pid, env, lambda value: '停用 PortableCheck' in visible_names(value))
            for label, enabled in [('停用 PortableCheck', False), ('启用 PortableCheck', True)]:
                automation(hwnd, process.pid, env, action=label)
                changed = wait_ui(hwnd, process.pid, env, lambda value: any(
                    ''.join(row['name'].split()) == '保存CtrlS' and row['enabled']
                    for row in value['rows'] if row['control'] == 'ControlType.Button'))
                save_name = next(row['name'] for row in changed['rows']
                                 if row['control'] == 'ControlType.Button' and ''.join(row['name'].split()) == '保存CtrlS')
                started = time.perf_counter()
                automation(hwnd, process.pid, env, action=save_name)
                wait_ui(hwnd, process.pid, env, lambda value: '已保存并应用到游戏。' in visible_names(value)
                        and all(path.exists() == enabled for path in mod_files))
                report['timings'].append(dict(action='enable' if enabled else 'disable', files=len(mod_files),
                    seconds=round(time.perf_counter() - started, 3), scope='Frozen UIAutomation click through observed completed save; includes driver overhead'))
            assert all(path.read_bytes() == b'owned-portable-resource' for path in mod_files)
            assert (fixture.game / 'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'
            assert (fixture.game / 'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'
            report['screenshots'].append(capture(hwnd, process.pid, out, 'mods-saved.png'))
            report['checks'].append('Frozen save disables and re-enables the whole owned synthetic Mod, preserving unrelated files and save sentinel')
        automation(hwnd, process.pid, env, action=log_action)
        ai = wait_ui(hwnd, process.pid, env, lambda value: any(label in visible_names(value) for label in ('日志', '日志原文'))
                     and any(label in visible_names(value) for label in ('智能分析', 'AI 排错')))
        report['ai'] = ai
        report['screenshots'].append(capture(hwnd, process.pid, out, 'logs-ai.png'))
        report['checks'].append('Frozen diagnostics module opens with visible log reader and AI conversation; unconfigured service is not contacted')
        if '最大化' in visible_names(ai):
            original_rect = win32gui.GetWindowRect(hwnd)
            automation(hwnd, process.pid, env, action='最大化')
            wait_ui(hwnd, process.pid, env, lambda value: '还原' in visible_names(value))
            assert win32gui.GetWindowRect(hwnd) != original_rect
            automation(hwnd, process.pid, env, action='还原')
            wait_ui(hwnd, process.pid, env, lambda value: '最大化' in visible_names(value))
            assert win32gui.GetWindowRect(hwnd) == original_rect
            report['checks'].append('Custom title bar maximizes and restores the frozen window')
        if '关闭' in visible_names(ai):
            automation(hwnd, process.pid, env, action='关闭')
            report['checks'].append('Custom title bar close button sends the frozen window through normal shutdown')
        else:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        report['exit_code'] = process.wait(timeout=30)
        deadline = time.monotonic() + 30
        while win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)['ActiveProcesses'] and time.monotonic() < deadline:
            time.sleep(.1)
        report['remaining'] = job_processes(job)
        assert report['exit_code'] == 0 and not report['remaining']
        report['checks'].append('Main process and owned WebView children exit naturally')
        report['status'] = 'PASS'
except Exception:
    report['error'] = traceback.format_exc()
finally:
    if process and process.poll() is None and hwnd and win32gui.IsWindow(hwnd):
        if win32process.GetWindowThreadProcessId(hwnd)[1] == process.pid:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: pass
    if win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']:
        report['forced_cleanup'] = True
        win32job.TerminateJobObject(job, 124)
    win32api.CloseHandle(job)
    (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: report.get(key) for key in ('status','checks','error','forced_cleanup')}, ensure_ascii=False))
raise SystemExit(0 if report['status'] == 'PASS' and not report['forced_cleanup'] else 1)
