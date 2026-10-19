"""External Windows UIAutomation consumer; no product imports or injected bridge.

The executable receives only isolated Windows user/temp paths. UIAutomation
locates controls for verified owned foreground pointer input. The optional
owned fixture uses real native pickers and the frozen metadata parser; it never
installs Mod files into, or launches, any game.
"""
import argparse
import base64
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import time
import traceback

import psutil
import win32api
import win32con
import win32gui
import win32job
import win32process
from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[3]
ACTIVATIONS = []
AUTOMATION_FAILURES = []
PICKERS = []

UIA = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]([long]$env:PSMM_UIA_HWND))
$elements = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
if ($env:PSMM_UIA_MODE -eq 'invoke') {
  $matches = @($elements | Where-Object { $_.Current.Name -eq $env:PSMM_UIA_NAME -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen })
  if ($matches.Count -ne 1) { throw "Expected one visible enabled action, found $($matches.Count)" }
  $pattern = $matches[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
  $pattern.Invoke()
  @{ invoked = $env:PSMM_UIA_NAME; root_pid = $root.Current.ProcessId } | ConvertTo-Json -Compress
} else {
  $rows = @($elements | ForEach-Object {
    $c = $_.Current
    if ($c.Name -or $c.AutomationId) {
      $value = $null
      try { $value = $_.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { }
      @{ name = $c.Name; control = $c.ControlType.ProgrammaticName; enabled = $c.IsEnabled;
         offscreen = $c.IsOffscreen; pid = $c.ProcessId; automation_id = $c.AutomationId; value = $value;
         rect = @($c.BoundingRectangle.X, $c.BoundingRectangle.Y, $c.BoundingRectangle.Width, $c.BoundingRectangle.Height) }
    }
  })
  @{ root_pid = $root.Current.ProcessId; rows = $rows } | ConvertTo-Json -Compress -Depth 6
}
'''


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def package_snapshot(package):
    result = {}
    for path in sorted(package.rglob('*')):
        if path.is_file():
            assert not path.is_symlink(), path
            result[path.relative_to(package).as_posix()] = dict(
                size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def pe_identity(path):
    with path.open('rb') as stream:
        assert stream.read(2) == b'MZ', 'Not an executable PE image'
        stream.seek(0x3c)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        assert stream.read(4) == b'PE\0\0'
        machine = struct.unpack('<H', stream.read(2))[0]
    assert machine == 0x8664, f'Expected x64 PE, got {machine:#x}'
    return dict(path=str(path), machine=hex(machine), size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def owned_window(pid):
    matches = []
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
            if win32gui.GetWindowText(hwnd) == 'PSModManager':
                matches.append(hwnd)
    win32gui.EnumWindows(visit, None)
    assert len(matches) <= 1, matches
    return matches[0] if matches else None


def verify_window(hwnd, pid, *, foreground=False):
    assert win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd)
    assert win32process.GetWindowThreadProcessId(hwnd)[1] == pid
    if foreground:
        assert win32gui.GetForegroundWindow() == hwnd, 'Owned window is not foreground; input withheld'


def activate(hwnd, pid):
    verify_window(hwnd, pid)
    if win32gui.GetForegroundWindow() == hwnd:
        return
    from ctypes import wintypes
    trace = dict(hwnd=hwnd,pid=pid,title=win32gui.GetWindowText(hwnd),attempts=[])
    ACTIVATIONS.append(trace)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    message = wintypes.MSG()
    ctypes.windll.user32.PeekMessageW(ctypes.byref(message),None,0,0,0)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    for _ in range(3):
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        foreground = win32gui.GetForegroundWindow()
        threads = {target_thread}
        if foreground and win32gui.IsWindow(foreground):
            threads.add(win32process.GetWindowThreadProcessId(foreground)[0])
        attempt = dict(foreground_before=foreground,attachments=[])
        trace['attempts'].append(attempt)
        attached = []
        try:
            for other in threads-{current_thread}:
                joined = bool(ctypes.windll.user32.AttachThreadInput(current_thread,other,True))
                attempt['attachments'].append(dict(thread=other,attached=joined))
                if joined:
                    attached.append(other)
            verify_window(hwnd,pid)
            try:
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except Exception as exc:
                attempt['error'] = str(exc)
        finally:
            for other in reversed(attached):
                assert ctypes.windll.user32.AttachThreadInput(current_thread,other,False)
        attempt['foreground_after'] = win32gui.GetForegroundWindow()
        if attempt['foreground_after'] == hwnd:
            return
        time.sleep(.1)
    # Click only the owned caption after verifying the hit window. A locked
    # desktop or inaccessible foreground fails here without input elsewhere.
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                         win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        client_top = win32gui.ClientToScreen(hwnd, (0, 0))[1]
        point = (left + min(180, (right-left)//2), (top + client_top)//2)
        assert client_top > top and point[1] < bottom
        hit = win32gui.WindowFromPoint(point)
        assert win32gui.GetAncestor(hit, win32con.GA_ROOT) == hwnd
        verify_window(hwnd, pid)
        win32api.SetCursorPos(point)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(.2)
        trace['foreground_after_caption'] = win32gui.GetForegroundWindow()
        verify_window(hwnd, pid, foreground=True)
    finally:
        if win32gui.IsWindow(hwnd):
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                 win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)


def automation(hwnd, pid, env, *, action=None):
    verify_window(hwnd, pid)
    if action:
        activate(hwnd, pid)
        verify_window(hwnd, pid, foreground=True)
    query_env = dict(env, PSMM_UIA_HWND=str(hwnd), PSMM_UIA_MODE='snapshot',
                     PSMM_UIA_NAME=action or '')
    executable = Path(env.get('SYSTEMROOT') or env['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    encoded = base64.b64encode(UIA.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            env=query_env, capture_output=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    def decode(value):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return value.decode('cp'+str(ctypes.windll.kernel32.GetACP()))
    stdout,stderr = decode(result.stdout),decode(result.stderr)
    if result.returncode:
        AUTOMATION_FAILURES.append(dict(hwnd=hwnd,pid=pid,action=action,
                                       exit_code=result.returncode,stdout=stdout,stderr=stderr))
    assert result.returncode == 0, stderr
    observed = json.loads(stdout.lstrip('\ufeff'))
    assert observed['root_pid'] == pid
    if action:
        matches = [row for row in observed['rows'] if row['name']==action and row['enabled']
                   and not row['offscreen'] and row['control'] not in ('ControlType.Text','ControlType.Window')]
        assert len(matches)==1,(action,matches)
        x,y,width,height = matches[0]['rect']
        assert width>0 and height>0
        point = (int(x+width/2),int(y+height/2))
        activate(hwnd,pid)
        verify_window(hwnd,pid,foreground=True)
        hit_root = win32gui.GetAncestor(win32gui.WindowFromPoint(point), win32con.GA_ROOT)
        assert hit_root == hwnd, dict(reason='Input withheld: target point is not over the owned window',
            expected_hwnd=hwnd, hit_root=hit_root,
            hit_pid=win32process.GetWindowThreadProcessId(hit_root)[1] if hit_root else None,
            point=point, control_rect=matches[0]['rect'], window_rect=win32gui.GetWindowRect(hwnd),
            foreground=win32gui.GetForegroundWindow())
        win32api.SetCursorPos(point)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,0,0,0,0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,0,0,0,0)
        return dict(invoked=action,method='UIAutomation rectangle / verified owned foreground native pointer',
                    rectangle=matches[0]['rect'],root_pid=pid)
    return observed


def wait_ui(hwnd, pid, env, predicate, timeout=40):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = automation(hwnd, pid, env)
        if predicate(last):
            return last
        time.sleep(.3)
    raise AssertionError(f'Actual UI did not become ready: {last}')


def visible_names(snapshot):
    return [row['name'] for row in snapshot['rows'] if not row['offscreen']]


def native_dialog(pid, timeout=20):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        matches = []
        def visit(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                if win32gui.GetWindowText(hwnd) in ('打开','Open','选择文件夹','Select Folder'):
                    matches.append(hwnd)
        win32gui.EnumWindows(visit, None)
        if len(matches) == 1:
            return matches[0]
        assert len(matches) < 2, 'More than one owned native picker'
        time.sleep(.2)
    raise AssertionError('Owned native file picker did not appear')


def address_input(hwnd, pid, value):
    """Type only into the verified owned native picker's address field."""
    from ctypes import wintypes
    activate(hwnd,pid)
    verify_window(hwnd,pid,foreground=True)
    win32api.keybd_event(win32con.VK_CONTROL,0,0,0)
    win32api.keybd_event(ord('L'),0,0,0)
    win32api.keybd_event(ord('L'),0,win32con.KEYEVENTF_KEYUP,0)
    win32api.keybd_event(win32con.VK_CONTROL,0,win32con.KEYEVENTF_KEYUP,0)
    time.sleep(.15)
    verify_window(hwnd,pid,foreground=True)
    class Keyboard(ctypes.Structure):
        _fields_ = [('vk',wintypes.WORD),('scan',wintypes.WORD),('flags',wintypes.DWORD),
                    ('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
    class Mouse(ctypes.Structure):
        _fields_ = [('dx',wintypes.LONG),('dy',wintypes.LONG),('data',wintypes.DWORD),
                    ('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
    class Payload(ctypes.Union):
        _fields_ = [('keyboard',Keyboard),('mouse',Mouse)]
    class Input(ctypes.Structure):
        _fields_ = [('type',wintypes.DWORD),('payload',Payload)]
    units = struct.unpack('<'+'H'*(len(value.encode('utf-16-le'))//2),value.encode('utf-16-le'))
    events = (Input*(len(units)*2))()
    for index,unit in enumerate(units):
        for offset,flags in ((0,4),(1,6)):
            events[index*2+offset].type = 1
            events[index*2+offset].payload.keyboard = Keyboard(0,unit,flags,0,0)
    send = ctypes.windll.user32.SendInput
    send.argtypes = [wintypes.UINT,ctypes.POINTER(Input),ctypes.c_int]
    send.restype = wintypes.UINT
    assert send(len(events),events,ctypes.sizeof(Input)) == len(events)
    verify_window(hwnd,pid,foreground=True)
    win32api.keybd_event(win32con.VK_RETURN,0,0,0)
    win32api.keybd_event(win32con.VK_RETURN,0,win32con.KEYEVENTF_KEYUP,0)


def pick(hwnd,pid,env,path,*,directory):
    picker = native_dialog(pid)
    before = automation(picker,pid,env)
    observation = dict(hwnd=picker,pid=pid,path=str(path),directory=directory,before=before)
    PICKERS.append(observation)
    if directory:
        address_input(picker,pid,str(path))
    else:
        # Native OpenFileDialog's file-name edit is a Windows control. Some
        # hosts expose it to UIA as a generic Pane, so address this actual edit
        # through its standard dialog control ID instead of a product hook.
        activate(picker,pid)
        verify_window(picker,pid,foreground=True)
        controls = []
        def visit(child,_):
            controls.append(dict(hwnd=child,control_id=win32gui.GetDlgCtrlID(child),
                                 parent=win32gui.GetParent(child),class_name=win32gui.GetClassName(child)))
        win32gui.EnumChildWindows(picker,visit,None)
        observation['native_controls'] = controls
        hosts = {row['hwnd'] for row in controls if row['control_id'] in (1148,1152)}
        edits = [row['hwnd'] for row in controls if row['class_name']=='Edit' and
                 (row['control_id'] in (1148,1152) or row['parent'] in hosts)]
        assert len(edits)==1,observation
        edit = edits[0]
        assert win32process.GetWindowThreadProcessId(edit)[1]==pid
        win32gui.SendMessage(edit,win32con.WM_SETTEXT,0,str(path))
        from ctypes import wintypes
        send_message = ctypes.windll.user32.SendMessageW
        send_message.argtypes = [wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        send_message.restype = ctypes.c_ssize_t
        # GetWindowText deliberately omits another process's Edit text.
        # WM_GETTEXT reads this exact verified owned control instead.
        buffer = ctypes.create_unicode_buffer(len(str(path))+1)
        send_message(edit,win32con.WM_GETTEXT,len(buffer),ctypes.cast(buffer,ctypes.c_void_p).value)
        assert buffer.value==str(path),(buffer.value,str(path))
        observation['file_name_edit'] = dict(hwnd=edit,value=buffer.value)
    time.sleep(.3)
    # Some native folder dialogs accept the absolute path on Enter directly.
    # The actual consumer still verifies the saved installation's canonical
    # root against the owned fixture after the product validates it.
    if not win32gui.IsWindow(picker):
        return dict(method='owned native picker / real Ctrl+L + Unicode SendInput',
                    path=str(path),directory=directory,hwnd=picker,snapshot=before,
                    closed_by_enter=True)
    if directory:
        snapshot = wait_ui(picker,pid,env,lambda value:any(
            str(path) in (row['name'] or '') or str(path) in (row['value'] or '')
            for row in value['rows']) and any(
                row['control'] not in ('ControlType.Text','ControlType.Window') and row['enabled'] and not row['offscreen'] and
                ('选择文件夹' in row['name'] or 'Select Folder' in row['name']) for row in value['rows']))
        names = [row['name'] for row in snapshot['rows'] if row['control'] not in ('ControlType.Text','ControlType.Window')
                 and row['enabled'] and not row['offscreen'] and
                 ('选择文件夹' in row['name'] or 'Select Folder' in row['name'])]
        assert len(names)==1,snapshot
        automation(picker,pid,env,action=names[0])
    else:
        snapshot = wait_ui(picker,pid,env,lambda value:any(
                row['control'] not in ('ControlType.Text','ControlType.Window') and row['enabled']
                and not row['offscreen'] and ('打开' in row['name'] or row['name'].startswith('Open'))
                for row in value['rows']))
        names = [row['name'] for row in snapshot['rows'] if row['control'] not in ('ControlType.Text','ControlType.Window')
                 and row['enabled'] and not row['offscreen'] and
                 ('打开' in row['name'] or row['name'].startswith('Open'))]
        assert len(names)==1,snapshot
        automation(picker,pid,env,action=names[0])
    observation['confirmed_snapshot'] = snapshot
    deadline = time.monotonic()+10
    while win32gui.IsWindow(picker) and time.monotonic()<deadline:
        time.sleep(.1)
    assert not win32gui.IsWindow(picker),'Owned file picker did not close'
    verify_window(hwnd,pid)
    return dict(method='owned native picker / real Ctrl+L + Unicode SendInput',
                path=str(path),directory=directory,hwnd=picker,snapshot=before)


def button(snapshot, name):
    return [row for row in snapshot['rows'] if row['name'] == name and
            row['control'] == 'ControlType.Button' and not row['offscreen'] and row['enabled']]


def capture(hwnd, pid, out, name):
    activate(hwnd, pid)
    verify_window(hwnd, pid, foreground=True)
    rect = win32gui.GetWindowRect(hwnd)
    image = ImageGrab.grab(bbox=rect)
    assert image.width >= 1200 and image.height >= 700
    image.save(out / name)
    return dict(file=name, hwnd=hwnd, pid=pid, rectangle=list(rect), dimensions=list(image.size))


def job_processes(job):
    """Read identities from the kernel-owned job, including reparented children."""
    rows = []
    for pid in win32job.QueryInformationJobObject(job,win32job.JobObjectBasicProcessIdList):
        try:
            process = psutil.Process(pid)
            arguments = process.cmdline()
            role = next((value.removeprefix('--type=') for value in arguments if value.startswith('--type=')), 'browser-or-host')
            utility = next((value.removeprefix('--utility-sub-type=') for value in arguments if value.startswith('--utility-sub-type=')), None)
            cpu = process.cpu_times()
            rows.append(dict(pid=pid,name=process.name(),exe=process.exe(),created=process.create_time(),
                             role=role,utility=utility,cpu_seconds=round(cpu.user+cpu.system,3)))
        except psutil.NoSuchProcess:
            rows.append(dict(pid=pid,state='exited during observation'))
    return rows


def environment(out):
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith(('PYTHON', 'PSMM_', 'VIRTUAL_ENV', 'CONDA', 'DOTNET_')):
            env.pop(key)
    system = Path(env.get('SYSTEMROOT') or env['SystemRoot'])
    env['PATH'] = os.pathsep.join(map(str, (system/'System32', system, system/'System32/Wbem')))
    for key, relative in [('LOCALAPPDATA','local'), ('APPDATA','roaming'), ('TEMP','temp'), ('TMP','temp')]:
        location = out / relative
        location.mkdir(exist_ok=True)
        env[key] = str(location)
    profile = out/'profile'
    profile.mkdir()
    for name in ('Desktop','Documents','Downloads','AppData/Local','AppData/Roaming','AppData/LocalLow'):
        (profile/name).mkdir(parents=True,exist_ok=True)
    env['USERPROFILE'] = str(profile)
    env['HOMEDRIVE'] = profile.drive
    # pywebview reads HOMEPATH directly for its initial picker directory.
    # A full owned path avoids opening the real user's home in this consumer.
    env['HOMEPATH'] = str(profile)
    env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (
        '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> '
        '--disable-background-networking --disable-component-update')
    env.pop('WEBVIEW2_BROWSER_EXECUTABLE_FOLDER', None)
    assert shutil.which('python', path=env['PATH']) is None
    assert shutil.which('dotnet', path=env['PATH']) is None
    return env


def launch(exe, cwd, env, out, phase, fixture):
    record = dict(phase=phase, command=[str(exe)], cwd=str(cwd), actions=[], screenshots=[],
                  forced_owned_cleanup=False,
                  exit_code=None, remaining_processes=None, status='FAIL')
    job = win32job.CreateJobObject(None, '')
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    process = None
    hwnd = None
    try:
        with (out/(phase+'-stdout.log')).open('xb') as stdout, (out/(phase+'-stderr.log')).open('xb') as stderr:
            started = time.monotonic()
            process = subprocess.Popen([str(exe)], cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                       creationflags=win32con.CREATE_SUSPENDED)
            win32job.AssignProcessToJobObject(job, int(process._handle))
            record['pid'] = process.pid
            record['process_image'] = psutil.Process(process.pid).exe()
            assert Path(record['process_image']).resolve() == exe.resolve()
            threads = psutil.Process(process.pid).threads()
            assert len(threads) == 1, 'Suspended bootloader must have one initial thread'
            thread = win32api.OpenThread(win32con.THREAD_SUSPEND_RESUME, False, threads[0].id)
            try:
                assert win32process.ResumeThread(thread) == 1
                record['job_assigned_before_resume'] = True
            finally:
                win32api.CloseHandle(thread)
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline and process.poll() is None:
                hwnd = owned_window(process.pid)
                if hwnd:
                    break
                time.sleep(.2)
            assert hwnd, f'Frozen GUI never appeared; exit={process.poll()}'
            record['hwnd'] = hwnd
            snapshot = wait_ui(hwnd, process.pid, env, lambda value: '本地桌面已就绪' in visible_names(value))
            record['ready_ms'] = round((time.monotonic()-started)*1000, 2)
            record['ready_snapshot'] = snapshot
            assert button(snapshot, '导入') and button(snapshot, '模组')
            if phase=='reopen' and fixture:
                # Saved installations are durable; the active selection is
                # intentionally session-local and is revalidated on reuse.
                record['actions'].append(automation(hwnd,process.pid,env,action='选择游戏安装'))
                saved = wait_ui(hwnd,process.pid,env,lambda value:bool(button(value,'校验并使用')))
                record['saved_installation_snapshot'] = saved
                record['actions'].append(automation(hwnd,process.pid,env,action='校验并使用'))
                snapshot = wait_ui(hwnd,process.pid,env,lambda value:not button(value,'关闭安装选择器'),timeout=60)
                record['reopened_package_snapshot'] = wait_ui(hwnd,process.pid,env,
                    lambda value:bool(button(value,'安装')),timeout=60)
            selection = [row for row in snapshot['rows'] if row['name'].startswith(
                         'Probably Stolen' if phase=='reopen' and fixture else '选择游戏安装')
                         and row['control'] == 'ControlType.Button' and row['enabled'] and not row['offscreen']]
            assert len(selection) == 1, snapshot
            record['screenshots'].append(capture(hwnd, process.pid, out, phase+'-ready.png'))
            if phase == 'first':
                if not fixture:
                    record['actions'].append(automation(hwnd, process.pid, env, action='导入'))
                    dialog = wait_ui(hwnd, process.pid, env, lambda value: '导入本地文件' in visible_names(value))
                    assert all(button(dialog, name) for name in ('选择 DLL', '选择目录', '选择 ZIP', '关闭导入'))
                    record['import_snapshot'] = dialog
                    record['screenshots'].append(capture(hwnd, process.pid, out, phase+'-import.png'))
                    record['actions'].append(automation(hwnd, process.pid, env, action='关闭导入'))
                    wait_ui(hwnd, process.pid, env, lambda value: not button(value, '关闭导入'))
                    record['actions'].append(automation(hwnd, process.pid, env, action=selection[0]['name']))
                    chooser = wait_ui(hwnd, process.pid, env, lambda value: bool(button(value, '关闭安装选择器')))
                    record['installation_snapshot'] = chooser
                    record['screenshots'].append(capture(hwnd, process.pid, out, phase+'-installation.png'))
                    record['actions'].append(automation(hwnd, process.pid, env, action='关闭安装选择器'))
                    wait_ui(hwnd, process.pid, env, lambda value: not button(value, '关闭安装选择器'))
                if fixture:
                    record['actions'].append(automation(hwnd,process.pid,env,action=selection[0]['name']))
                    wait_ui(hwnd,process.pid,env,lambda value:bool(button(value,'手动选择安装目录')))
                    record['actions'].append(automation(hwnd,process.pid,env,action='手动选择安装目录'))
                    record['native_installation_picker'] = pick(hwnd,process.pid,env,Path(fixture['game']),directory=True)
                    wait_ui(hwnd,process.pid,env,lambda value:bool(button(value,'使用此安装')),timeout=60)
                    record['actions'].append(automation(hwnd,process.pid,env,action='使用此安装'))
                    wait_ui(hwnd,process.pid,env,lambda value:not button(value,'关闭安装选择器'),timeout=60)
                    record['actions'].append(automation(hwnd,process.pid,env,action='导入'))
                    wait_ui(hwnd,process.pid,env,lambda value:bool(button(value,'选择 DLL')))
                    record['actions'].append(automation(hwnd,process.pid,env,action='选择 DLL'))
                    record['native_dll_picker'] = pick(hwnd,process.pid,env,Path(fixture['dll']),directory=False)
                    preview = wait_ui(hwnd,process.pid,env,lambda value:bool(button(value,'添加到包库')),timeout=60)
                    record['frozen_import_preview'] = preview
                    record['screenshots'].append(capture(hwnd,process.pid,out,phase+'-preview.png'))
                    record['actions'].append(automation(hwnd,process.pid,env,action='添加到包库'))
                    committed = wait_ui(hwnd,process.pid,env,lambda value:any(
                        '已加入包库' in name and '1 个文件' in name for name in visible_names(value)),timeout=60)
                    record['frozen_import_committed'] = committed
                    record['screenshots'].append(capture(hwnd,process.pid,out,phase+'-imported.png'))
                    record['actions'].append(automation(hwnd,process.pid,env,action='关闭导入'))
                    wait_ui(hwnd,process.pid,env,lambda value:not button(value,'关闭导入'))
                record['actions'].append(automation(hwnd, process.pid, env, action='应用信息'))
                details = wait_ui(hwnd, process.pid, env, lambda value: env['LOCALAPPDATA'] + '\\PSModManager' in visible_names(value))
                record['info_snapshot'] = details
                record['screenshots'].append(capture(hwnd, process.pid, out, phase+'-info.png'))
            record['descendants'] = [dict(pid=child.pid, name=child.name(), exe=child.exe())
                                     for child in psutil.Process(process.pid).children(recursive=True)]
            verify_window(hwnd, process.pid)
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            record['close_method'] = 'WM_CLOSE addressed only to verified owned window'
            close_started = time.monotonic()
            record['exit_code'] = process.wait(timeout=30)
            record['main_close_ms'] = round((time.monotonic()-close_started)*1000,2)
            # WebView2's owned renderer/service processes may exit after the
            # host. Observe natural exit for at most 30 seconds; never exempt
            # any child from the assertion or count Job termination as success.
            wait_started = time.monotonic()
            deadline = wait_started+30
            record['natural_exit_wait_limit_seconds'] = 30
            record['natural_exit_observations'] = []
            prior_count = None
            last_observed = -5
            while True:
                active = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
                elapsed = time.monotonic()-wait_started
                if active!=prior_count or elapsed-last_observed>=5 or not active or time.monotonic()>=deadline:
                    record['natural_exit_observations'].append(dict(elapsed_ms=round(elapsed*1000,2),
                        active_processes=active,processes=job_processes(job)))
                    prior_count,last_observed = active,elapsed
                if not active or time.monotonic()>=deadline:
                    break
                time.sleep(.1)
            record['natural_exit_wait_ms'] = round((time.monotonic()-wait_started)*1000,2)
            record['remaining_processes'] = active
            assert record['exit_code'] == 0 and active == 0, (
                f"Frozen GUI exit={record['exit_code']}; owned descendants={active} "
                f"after {record['natural_exit_wait_ms']} ms natural waiting")
            record['status'] = 'PASS'
    except Exception:
        record['error'] = traceback.format_exc()
        # First ask this exact owned window to close. Never close or terminate
        # an unrelated desktop process. Job cleanup is failure containment and
        # is never recorded as natural-exit success.
        if hwnd and process is not None and win32gui.IsWindow(hwnd):
            if win32process.GetWindowThreadProcessId(hwnd)[1] == process.pid:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        if process is not None:
            try:
                record['exit_code'] = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                record['forced_owned_cleanup'] = True
        raise
    finally:
        active = win32job.QueryInformationJobObject(job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
        record['cleanup_before_processes'] = active
        if active:
            # Failure containment for this owned job only, including children
            # whose main host has already exited. The original natural count
            # and FAIL remain unchanged regardless of successful cleanup.
            record['forced_owned_cleanup'] = True
            win32job.TerminateJobObject(job, 124)
            cleanup_started = time.monotonic()
            deadline = cleanup_started+5
            while active and time.monotonic()<deadline:
                active = win32job.QueryInformationJobObject(job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
                if active:
                    time.sleep(.05)
            record['forced_cleanup_wait_ms'] = round((time.monotonic()-cleanup_started)*1000,2)
        if process is not None and process.poll() is None:
            process.wait(timeout=5)
        record['cleanup_remaining_processes'] = win32job.QueryInformationJobObject(
            job, win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
        win32api.CloseHandle(job)
        write_json(out/(phase+'.json'), record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--portable-root', required=True)
    parser.add_argument('--evidence-root', required=True)
    args = parser.parse_args()
    package, out = Path(args.portable_root).resolve(), Path(args.evidence_root).resolve()
    assert out.is_relative_to(ROOT/'docs/development/evidence/PS-05-02')
    assert out.is_dir() and package.is_dir() and out != package and not out.is_relative_to(package)
    exe = package/'PSModManager.exe'
    report = dict(status='FAIL', scope='Actual frozen GUI / Windows UIAutomation / default isolated LocalAppData / reopen; no development PATH, real game, game launch, provider, or injected product hook',
                  utc=datetime.now(timezone.utc).isoformat(), launches=[])
    before = package_snapshot(package)
    write_json(out/'package-before.json', before)
    try:
        report['executable'] = pe_identity(exe)
        env = environment(out)
        cwd = out/'中文 工作目录'
        cwd.mkdir()
        report['environment'] = {key:env[key] for key in ('PATH','LOCALAPPDATA','APPDATA','TEMP','TMP')}
        report['python_visible'] = shutil.which('python', path=env['PATH'])
        report['dotnet_visible'] = shutil.which('dotnet', path=env['PATH'])
        data = Path(env['LOCALAPPDATA'])/'PSModManager'
        assert not data.exists()
        fixture_path = out/'fixture.json'
        fixture = json.loads(fixture_path.read_text(encoding='utf-8')) if fixture_path.is_file() else None
        game_before = package_snapshot(Path(fixture['game'])) if fixture else None
        for phase in ('first','reopen'):
            record = launch(exe, cwd, env, out, phase, fixture)
            report['launches'].append(record)
        database = data/'psmodmanager.sqlite3'
        assert database.is_file() and (data/'config.json').is_file()
        with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
            report['database'] = dict(path=str(database), application_id=connection.execute('PRAGMA application_id').fetchone()[0],
                user_version=connection.execute('PRAGMA user_version').fetchone()[0],
                quick_check=connection.execute('PRAGMA quick_check').fetchone()[0],
                installations=connection.execute('SELECT count(*) FROM installations').fetchone()[0],
                mod_releases=connection.execute('SELECT count(*) FROM mod_releases').fetchone()[0])
            if fixture:
                report['selected_root'] = connection.execute('SELECT canonical_root FROM installations').fetchone()[0]
        assert report['database']['application_id'] == 0x50534D4D
        assert report['database']['user_version'] == 7 and report['database']['quick_check'] == 'ok'
        expected = int(bool(fixture))
        assert report['database']['installations'] == report['database']['mod_releases'] == expected
        if fixture:
            assert Path(report['selected_root']).resolve() == Path(fixture['game']).resolve()
            report['synthetic_game_unchanged'] = game_before == package_snapshot(Path(fixture['game']))
            report['owned_dll_unchanged'] = hashlib.sha256(Path(fixture['dll']).read_bytes()).hexdigest() == fixture['dll_sha256']
            artifact_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (data/'library').rglob('*') if path.is_file()]
            report['imported_artifact_matches'] = fixture['dll_sha256'] in artifact_hashes
            assert report['synthetic_game_unchanged'] and report['owned_dll_unchanged'] and report['imported_artifact_matches']
        report['config'] = json.loads((data/'config.json').read_text(encoding='utf-8'))
        report['startup_log'] = (data/'logs/psmodmanager.log').read_text(encoding='utf-8')
        assert report['startup_log'].count('updater=disabled') == 2
        assert report['startup_log'].count('shutdown') == 2
        assert not list(cwd.iterdir()), 'Product wrote into the foreign working directory'
        after = package_snapshot(package)
        write_json(out/'package-after.json', after)
        report['package_file_count'] = len(before)
        report['package_unchanged'] = before == after
        assert report['package_unchanged'], 'Portable program files changed during normal startup'
        report['status'] = 'PASS'
        return 0
    except Exception:
        report['error'] = traceback.format_exc()
        return 1
    finally:
        report['activations'] = ACTIVATIONS
        report['automation_failures'] = AUTOMATION_FAILURES
        report['native_pickers'] = PICKERS
        write_json(out/'result.json', report)


if __name__ == '__main__':
    raise SystemExit(main())
