"""Windows AppContainer with no capabilities; suspended job assignment.

No ordinary-process fallback. ACL grants apply only to the caller-owned staging
copy. A uniquely named temporary AppContainer profile is removed after exit;
no firewall exception, machine configuration or runtime ACL edits.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

MAX_OUTPUT = 256 * 1024
MAX_ERROR = 64 * 1024
MEMORY_LIMIT = 512 * 1024 * 1024
NONINTERACTIVE_ERROR_MODE = 0x1 | 0x2 | 0x8000
_PROCESS_CREATION_LOCK = threading.Lock()


def grant_read_tree(root, sid):
    import win32security as sec
    import win32api
    import ntsecuritycon as rights
    token = sec.OpenProcessToken(win32api.GetCurrentProcess(), 8)
    try:
        user = sec.GetTokenInformation(token, sec.TokenUser)[0]
    finally:
        token.Close()
    # Every object is a newly created isolated copy, never the source/runtime.
    for path in [root, *root.rglob('*')]:
        if path.is_symlink() or path.lstat().st_file_attributes & 0x400:
            raise ValueError('Reparse point in worker staging tree')
        sd = sec.GetFileSecurity(str(path), sec.DACL_SECURITY_INFORMATION)
        acl = sd.GetSecurityDescriptorDacl()
        acl.AddAccessAllowedAce(sec.ACL_REVISION, rights.FILE_GENERIC_READ | rights.FILE_GENERIC_EXECUTE, user)
        acl.AddAccessAllowedAce(sec.ACL_REVISION, rights.FILE_GENERIC_READ | rights.FILE_GENERIC_EXECUTE, sid)
        sec.SetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, sec.DACL_SECURITY_INFORMATION,
                                 None, None, acl, None)


def run_sandbox(argv, request, *, stage, timeout=8.0, cancelled=lambda: False):
    # A GUI Python host has no console. Starting a console .NET host directly
    # with the LPAC token then fails during Windows DLL initialization. Give
    # the trusted launcher a hidden console; the parser still uses exactly
    # the restricted token, private desktop and job configured below.
    if os.name == 'nt' and not c.WinDLL('kernel32').GetConsoleWindow():
        from .worker_host import run_console_host
        return run_console_host(argv, request, stage=stage, timeout=timeout, cancelled=cancelled)
    return _run_sandbox(argv, request, stage=stage, timeout=timeout, cancelled=cancelled)


def _run_sandbox(argv, request, *, stage, timeout=8.0, cancelled=lambda: False):
    """Execute trusted helper code against an isolated, untrusted input copy.

    argv is internal host configuration, never a bridge argument. A fresh SID
    prevents reuse of other stages' ACL grants. Returns actual kernel facts.
    """
    result = dict(started=False, exit_code=None, status='sandbox_unavailable',
                  stdout='', stderr='', cleanup_remaining=None, appcontainer=False,
                  capabilities=None, peak_memory=None)
    if os.name != 'nt':
        return result
    if not 0 < timeout <= 30 or len(request) > 16384:
        raise ValueError('Invalid worker limits')
    import win32api
    import win32job
    import win32security
    k = c.WinDLL('kernel32', use_last_error=True)
    u = c.WinDLL('userenv', use_last_error=True)
    a = c.WinDLL('advapi32', use_last_error=True)
    ui = c.WinDLL('user32', use_last_error=True)
    H = w.HANDLE
    class SA(c.Structure):
        _fields_ = [('nLength', w.DWORD), ('lpSecurityDescriptor', c.c_void_p), ('bInheritHandle', w.BOOL)]
    class SI(c.Structure):
        _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR), ('lpTitle', w.LPWSTR),
                    ('dwX', w.DWORD), ('dwY', w.DWORD), ('dwXSize', w.DWORD), ('dwYSize', w.DWORD),
                    ('dwXCountChars', w.DWORD), ('dwYCountChars', w.DWORD), ('dwFillAttribute', w.DWORD),
                    ('dwFlags', w.DWORD), ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                    ('lpReserved2', c.c_void_p), ('hStdInput', H), ('hStdOutput', H), ('hStdError', H)]
    class SX(c.Structure):
        _fields_ = [('StartupInfo', SI), ('lpAttributeList', c.c_void_p)]
    class PI(c.Structure):
        _fields_ = [('hProcess', H), ('hThread', H), ('dwProcessId', w.DWORD), ('dwThreadId', w.DWORD)]
    class SC(c.Structure):
        _fields_ = [('AppContainerSid', c.c_void_p), ('Capabilities', c.c_void_p), ('CapabilityCount', w.DWORD), ('Reserved', w.DWORD)]
    def bind(lib, name, args, result_type=w.BOOL):
        fn = getattr(lib, name); fn.argtypes = args; fn.restype = result_type; return fn
    close = bind(k, 'CloseHandle', [H])
    pipe = bind(k, 'CreatePipe', [c.POINTER(H), c.POINTER(H), c.POINTER(SA), w.DWORD])
    inherit = bind(k, 'SetHandleInformation', [H, w.DWORD, w.DWORD])
    init = bind(k, 'InitializeProcThreadAttributeList', [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)])
    update = bind(k, 'UpdateProcThreadAttribute', [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p])
    delete = bind(k, 'DeleteProcThreadAttributeList', [c.c_void_p], None)
    create = bind(k, 'CreateProcessW', [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(SX), c.POINTER(PI)])
    get_error_mode = bind(k, 'GetErrorMode', [], w.UINT)
    set_error_mode = bind(k, 'SetErrorMode', [w.UINT], w.UINT)
    wait = bind(k, 'WaitForSingleObject', [H, w.DWORD], w.DWORD)
    resume = bind(k, 'ResumeThread', [H], w.DWORD)
    exitcode = bind(k, 'GetExitCodeProcess', [H, c.POINTER(w.DWORD)])
    read = bind(k, 'ReadFile', [H, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p])
    write = bind(k, 'WriteFile', [H, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p])
    create_profile = bind(u, 'CreateAppContainerProfile', [w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,c.c_void_p,w.DWORD,c.POINTER(c.c_void_p)], c.c_long)
    delete_profile = bind(u, 'DeleteAppContainerProfile', [w.LPCWSTR], c.c_long)
    free = bind(a, 'FreeSid', [c.c_void_p], c.c_void_p)
    opentoken = bind(a, 'OpenProcessToken', [H, w.DWORD, c.POINTER(H)])
    tokeninfo = bind(a, 'GetTokenInformation', [H, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)])
    create_desktop = bind(ui, 'CreateDesktopW', [w.LPCWSTR,w.LPCWSTR,c.c_void_p,w.DWORD,w.DWORD,c.POINTER(SA)], H)
    close_desktop = bind(ui, 'CloseDesktop', [H])
    def ok(value):
        if not value: raise c.WinError(c.get_last_error())
    handles, threads = [], []
    job = None; attrs = None; sid = c.c_void_p(); pi = PI(); profile = None; desktop = None
    assigned_to_job = False
    overflow = threading.Event(); started = time.monotonic()
    buffers = [bytearray(), bytearray()]
    try:
        name = 'PSModManager.Metadata.' + uuid.uuid4().hex
        hr = create_profile(name, name, 'Temporary read-only metadata worker', None, 0, c.byref(sid))
        if hr != 0: raise OSError(f'AppContainer profile creation failed: {hr}')
        profile = name
        result['profile_name'] = name
        sid_value = win32security.SID(c.string_at(sid, 8 + c.cast(sid, c.POINTER(c.c_ubyte))[1] * 4))
        grant_read_tree(Path(stage), sid_value)
        sa = SA(c.sizeof(SA), None, True)
        ends = []
        for _ in range(3):
            r, wr = H(), H(); ok(pipe(c.byref(r), c.byref(wr), c.byref(sa), 16384))
            handles += [r.value, wr.value]; ends.append((r.value, wr.value))
        for handle in (ends[0][1], ends[1][0], ends[2][0]): ok(inherit(handle, 1, 0))
        size = c.c_size_t(); init(None, 4, 0, c.byref(size)); attrs = c.create_string_buffer(size.value)
        ok(init(attrs, 4, 0, c.byref(size)))
        caps = SC(sid, None, 0, 0)
        ok(update(attrs, 0, 0x20009, c.byref(caps), c.sizeof(caps), None, None))
        inherited = (H * 3)(ends[0][0], ends[1][1], ends[2][1])
        ok(update(attrs, 0, 0x20002, inherited, c.sizeof(inherited), None, None))
        child_policy = w.DWORD(1)
        ok(update(attrs, 0, 0x2000E, c.byref(child_policy), c.sizeof(child_policy), None, None))
        package_policy = w.DWORD(1)
        ok(update(attrs, 0, 0x2000F, c.byref(package_policy), c.sizeof(package_policy), None, None))
        result['lpac_policy_applied'] = True
        sx = SX(); sx.StartupInfo.cb = c.sizeof(SX); sx.lpAttributeList = c.cast(attrs, c.c_void_p)
        desktop_name = 'PSMM_Metadata_' + uuid.uuid4().hex
        owner_token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 8)
        try:
            owner_sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(owner_token, win32security.TokenUser)[0])
        finally:
            owner_token.Close()
        desktop_sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            'D:(A;;GA;;;SY)(A;;GA;;;' + owner_sid + ')(A;;GA;;;' + win32security.ConvertSidToStringSid(sid_value) + ')S:(ML;;NW;;;LW)', 1)
        desktop_bytes = bytes(desktop_sd)
        desktop_buffer = c.create_string_buffer(desktop_bytes)
        desktop_sa = SA(c.sizeof(SA), c.cast(desktop_buffer,c.c_void_p), False)
        desktop = create_desktop(desktop_name,None,None,0,0x10000000,c.byref(desktop_sa))
        ok(desktop)
        sx.StartupInfo.lpDesktop = desktop_name
        # CoreCLR needs a usable desktop even for a console-only process. Do not
        # grant access to the user's desktop or use CREATE_NO_WINDOW (which can
        # fail DLL initialization under AppContainer). Our private desktop is
        # never switched to, and the inherited console is explicitly hidden.
        sx.StartupInfo.dwFlags = 0x101
        sx.StartupInfo.wShowWindow = 0
        sx.StartupInfo.hStdInput, sx.StartupInfo.hStdOutput, sx.StartupInfo.hStdError = inherited
        env = {'SystemRoot': os.environ['SystemRoot'], 'WINDIR': os.environ['SystemRoot'],
               'USERPROFILE': os.environ['USERPROFILE'], 'LOCALAPPDATA': os.environ['LOCALAPPDATA'],
               'SystemDrive': os.environ.get('SystemDrive','C:'),
               'DOTNET_EnableDiagnostics':'0', 'COMPlus_EnableDiagnostics':'0',
               'DOTNET_MULTILEVEL_LOOKUP':'0', 'DOTNET_NOLOGO':'1',
               'DOTNET_GCHeapHardLimit':'0x10000000', 'TEMP':str(stage), 'TMP':str(stage)}
        envblock = c.create_unicode_buffer('\0'.join(f'{key}={value}' for key,value in sorted(env.items())) + '\0\0')
        job = win32job.CreateJobObject(None, '')
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] = 0x2000 | 0x8 | 0x100 | 0x200 | 0x400
        limits['BasicLimitInformation']['ActiveProcessLimit'] = 1
        limits['ProcessMemoryLimit'] = MEMORY_LIMIT; limits['JobMemoryLimit'] = MEMORY_LIMIT
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        result['phase'] = 'create_process'
        # The child inherits the PROCESS error mode, not SetThreadErrorMode.
        # Set it before creation so loader failures before managed Main cannot
        # raise system-modal hard-error dialogs. Restore the host immediately;
        # serialize our two worker launchers so they cannot restore each
        # other's temporary mode. No registry/WER machine policy is changed.
        with _PROCESS_CREATION_LOCK:
            previous_mode = get_error_mode()
            inherited_mode = previous_mode | NONINTERACTIVE_ERROR_MODE
            set_error_mode(inherited_mode)
            try:
                created = create(str(argv[0]), c.create_unicode_buffer(subprocess.list2cmdline(list(map(str, argv)))), None, None,
                    True, 0x80000 | 0x400 | 0x4, envblock, str(stage), c.byref(sx), c.byref(pi))
                creation_error = c.get_last_error()
            finally:
                set_error_mode(previous_mode)
            result['inherited_error_mode'] = inherited_mode
            result['host_error_mode_restored'] = get_error_mode() == previous_mode
            if not created:
                raise c.WinError(creation_error)
        handles += [pi.hProcess, pi.hThread]
        result['pid'] = pi.dwProcessId
        result['phase'] = 'assign_job'
        win32job.AssignProcessToJobObject(job, pi.hProcess)
        assigned_to_job = True
        result['die_on_unhandled_exception'] = True
        tok = H(); ok(opentoken(pi.hProcess, 8, c.byref(tok))); handles.append(tok.value)
        result['phase'] = 'verify_token'
        flag = w.DWORD(); needed = w.DWORD()
        ok(tokeninfo(tok, 29, c.byref(flag), c.sizeof(flag), c.byref(needed)))
        result['appcontainer'] = flag.value == 1
        tokeninfo(tok, 30, None, 0, c.byref(needed)); capbuffer = c.create_string_buffer(needed.value)
        ok(tokeninfo(tok, 30, capbuffer, needed.value, c.byref(needed)))
        result['capabilities'] = c.cast(capbuffer, c.POINTER(w.DWORD))[0]
        if not result['appcontainer'] or result['capabilities'] != 0:
            raise OSError('Unexpected worker token')
        result['pid'] = pi.dwProcessId
        for handle in inherited:
            close(handle); handles.remove(handle)
        def drain(handle, index, bound):
            block = c.create_string_buffer(8192); count = w.DWORD()
            while read(handle, block, len(block), c.byref(count), None) and count.value:
                left = bound - len(buffers[index]); buffers[index].extend(block.raw[:min(left, count.value)])
                if count.value > left: overflow.set()
        for index in range(2):
            thread = threading.Thread(target=drain, args=(ends[index+1][0], index, (MAX_OUTPUT,MAX_ERROR)[index]), daemon=True)
            thread.start(); threads.append(thread)
        if resume(pi.hThread) == 0xFFFFFFFF: raise c.WinError(c.get_last_error())
        result['started'] = True
        count = w.DWORD(); ok(write(ends[0][1], request, len(request), c.byref(count), None))
        close(ends[0][1]); handles.remove(ends[0][1])
        result['status'] = 'exited'
        while wait(pi.hProcess, 20) == 258:
            if cancelled(): result['status'] = 'cancelled'; break
            if overflow.is_set(): result['status'] = 'output_limit'; break
            if time.monotonic() - started > timeout: result['status'] = 'timeout'; break
        if result['status'] != 'exited': win32job.TerminateJobObject(job, 124)
        wait(pi.hProcess, 2000)
        code = w.DWORD(); ok(exitcode(pi.hProcess, c.byref(code))); result['exit_code'] = code.value
        result['peak_memory'] = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)['PeakJobMemoryUsed']
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        # A created but unassigned suspended process is outside the job. Job
        # ActiveProcesses==0 alone cannot prove cleanup on assignment failure.
        if pi.hProcess and not assigned_to_job:
            terminate = bind(k, 'TerminateProcess', [H,w.UINT])
            terminate(pi.hProcess,124)
            result['unassigned_process_terminated'] = wait(pi.hProcess,2000) == 0
        if job is not None:
            win32job.TerminateJobObject(job, 124)
            if pi.hProcess: wait(pi.hProcess, 2000)
            deadline = time.monotonic() + 2
            while True:
                active = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
                if not active or time.monotonic() >= deadline: break
                time.sleep(.01)
            result['cleanup_remaining'] = active
            win32api.CloseHandle(job)
        for thread in threads: thread.join(2)
        if overflow.is_set(): result['status'] = 'output_limit'
        for handle in reversed(handles): close(handle)
        if attrs is not None: delete(attrs)
        if sid.value: free(sid)
        if profile: result['profile_deleted'] = delete_profile(profile) == 0
        if desktop: result['desktop_closed'] = bool(close_desktop(desktop))
        result['stdout'] = buffers[0].decode('utf-8', errors='replace')
        result['stderr'] = buffers[1].decode('utf-8', errors='replace')
        result['duration_seconds'] = round(time.monotonic() - started, 4)
    return result
