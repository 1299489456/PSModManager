"""Operate the delivered EXE from outside its process; reuse native UI helpers."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

import win32api
import win32con
import win32gui
import win32job
from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests/desktop/harness'))
import portable_launcher as ui
from tests.acceptance.harness.standard_user import prepare_token, launch_owned


def run_session(exe, out, mode, fixture, phase):
    out.mkdir(parents=True, exist_ok=True)
    env = ui.environment(out)
    cwd = out/'中文 空格工作目录'
    cwd.mkdir()
    if mode == 'missing-runtime':
        empty = out/'empty-webview-runtime'
        empty.mkdir()
        env['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER'] = str(empty)
    token = prepare_token()
    job = win32job.CreateJobObject(None, '')
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    record = dict(status='FAIL', mode=mode, phase=phase, actions=[], screenshots=[],
                  environment={k:env[k] for k in ['PATH','LOCALAPPDATA','APPDATA','TEMP','USERPROFILE']},
                  caller_token=token.report, command=[str(exe)], forced_owned_cleanup=False)
    process = hwnd = None
    try:
        started = time.monotonic()
        process = launch_owned(token, exe, cwd, env, job)
        record.update(pid=process.pid, actual_token=process.token_report)
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            hwnd = ui.owned_window(process.pid)
            if hwnd:
                break
            assert process.poll() is None, 'Product exited before showing its UI'
            time.sleep(.1)
        assert hwnd, 'No owned product window'
        record['hwnd'] = hwnd

        def click(name):
            record['actions'].append(ui.automation(hwnd,process.pid,env,action=name))

        def wait(predicate, timeout=90):
            return ui.wait_ui(hwnd,process.pid,env,predicate,timeout=timeout)

        if mode == 'missing-runtime':
            snapshot = wait(lambda value:any('缺少可用的 WebView2 Runtime' in name for name in ui.visible_names(value)))
            record['runtime_message'] = ui.visible_names(snapshot)
            record['runtime_snapshot'] = snapshot
            ui.activate(hwnd,process.pid)
            rectangle = win32gui.GetWindowRect(hwnd)
            ImageGrab.grab(bbox=rectangle).save(out/'missing-runtime.png')
            record['screenshots'].append('missing-runtime.png')
            # Classic MessageBox controls can be exposed as a generic Pane by
            # UIA. The reused helper still verifies this owned window's exact
            # visible enabled control and uses its native screen rectangle.
            confirm = [r['name'] for r in snapshot['rows'] if r['control'] not in ('ControlType.Text','ControlType.Window')
                       and r['enabled'] and not r['offscreen'] and r['name'] in ('确定','OK')]
            assert len(confirm)==1, confirm
            click(confirm[0])
            expected_exit = 2
        else:
            wait(lambda value:'本地桌面已就绪' in ui.visible_names(value))
            record['ready_ms'] = round((time.monotonic()-started)*1000,1)
            expected_exit = 0
            if mode == 'manage':
                click('选择游戏安装')
                if phase == 'first':
                    wait(lambda value:bool(ui.button(value,'手动选择安装目录')))
                    click('手动选择安装目录')
                    record['native_installation_picker'] = ui.pick(hwnd,process.pid,env,Path(fixture['game']),directory=True)
                    wait(lambda value:bool(ui.button(value,'使用此安装')))
                    click('使用此安装')
                else:
                    wait(lambda value:bool(ui.button(value,'校验并使用')))
                    click('校验并使用')
                wait(lambda value:not ui.button(value,'关闭安装选择器'))
                target = Path(fixture['target'])
                if phase == 'first':
                    click('导入')
                    wait(lambda value:bool(ui.button(value,'选择 DLL')))
                    click('选择 DLL')
                    record['native_dll_picker'] = ui.pick(hwnd,process.pid,env,Path(fixture['dll']),directory=False)
                    wait(lambda value:bool(ui.button(value,'添加到包库')))
                    click('添加到包库')
                    wait(lambda value:any('已加入包库' in name for name in ui.visible_names(value)))
                    click('关闭导入')
                    wait(lambda value:not ui.button(value,'关闭导入'))
                    for action, next_action, present in [
                        ('安装','停用',True),('停用','启用',False),
                        ('启用','停用',True),('卸载','安装',False),('安装','停用',True)]:
                        wait(lambda value:len(ui.button(value,action))==1)
                        operation_started=time.monotonic()
                        click(action)
                        snapshot = wait(lambda value:bool(ui.button(value,next_action)) and target.is_file()==present)
                        actual = ui.package_snapshot(Path(fixture['game']))
                        expected = dict(fixture['game_before'])
                        if present:
                            expected[target.relative_to(fixture['game']).as_posix()] = dict(
                                size=Path(fixture['dll']).stat().st_size,sha256=fixture['dll_sha256'])
                        assert actual==expected, 'File action changed more than the selected Mod'
                        record['actions'][-1].update(elapsed_ms=round((time.monotonic()-operation_started)*1000,1),
                            target_present=present,expected_tree_matched=True)
                        if action in ('安装','卸载'):
                            record['screenshots'].append(ui.capture(hwnd,process.pid,out,f'action-{len(record["actions"])}.png'))
                else:
                    snapshot=wait(lambda value:bool(ui.button(value,'停用')) and target.is_file())
                    assert ui.package_snapshot(Path(fixture['game']))==fixture['game_after_first']
                    record['reopened_installed_state']=True
                    record['screenshots'].append(ui.capture(hwnd,process.pid,out,'reopened-installed.png'))
            click('应用信息')
            record['info_snapshot']=wait(lambda value:env['LOCALAPPDATA']+'\\PSModManager' in ui.visible_names(value))
            ui.verify_window(hwnd,process.pid)
            win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
        record['exit_code']=process.wait(timeout=30)
        deadline=time.monotonic()+30
        observations=[]
        while True:
            active=win32job.QueryInformationJobObject(job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
            if not observations or observations[-1]['active']!=active:
                observations.append(dict(active=active,processes=ui.job_processes(job)))
            if not active or time.monotonic()>=deadline:
                break
            time.sleep(.1)
        record.update(remaining_processes=active,natural_exit_observations=observations)
        assert record['exit_code']==expected_exit and active==0, 'Owned process did not exit as expected'
        assert not list(cwd.iterdir()), 'Product wrote into its working directory'
        record['status']='PASS'
    except BaseException:
        record['error']=traceback.format_exc()
        raise
    finally:
        active=win32job.QueryInformationJobObject(job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
        if active:
            record['forced_owned_cleanup']=True
            win32job.TerminateJobObject(job,124)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and win32job.QueryInformationJobObject(
                    job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']:
                time.sleep(.1)
        record['cleanup_remaining_processes']=win32job.QueryInformationJobObject(
            job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
        if process is not None:
            process.close()
        token.close()
        win32api.CloseHandle(job)
        ui.write_json(out/'result.json',record)
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--mode',choices=('manage','missing-runtime','restore'),required=True)
    parser.add_argument('--phase',default='first')
    parser.add_argument('--fixture',type=Path)
    args=parser.parse_args()
    assert args.out.resolve().is_relative_to(ROOT/'docs/development/evidence/PS-05-03')
    fixture=json.loads(args.fixture.read_text(encoding='utf-8')) if args.fixture else {}
    run_session(args.exe.resolve(strict=True),args.out.resolve(),args.mode,fixture,args.phase)
