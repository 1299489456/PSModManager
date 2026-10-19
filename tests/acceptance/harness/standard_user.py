"""Owned-process launcher for the packaged app's local non-elevated checks.

This preserves an already limited interactive token. It neither creates an
account nor claims a separate standard account or a clean Windows machine.
An elevated caller may use its existing verified limited linked token; no
credential, security-policy, ACL, or privilege changes are made.
"""
from dataclasses import dataclass
from pathlib import Path
import subprocess

import win32api
import win32con
import win32event
import win32job
import win32process
import win32security


ADMIN_SID = 'S-1-5-32-544'
MEDIUM_RID = 8192
# These enabled privileges would invalidate the non-administrative check.
ADMIN_PRIVILEGES = {
    'SeAssignPrimaryTokenPrivilege', 'SeBackupPrivilege',
    'SeCreateTokenPrivilege', 'SeDebugPrivilege', 'SeImpersonatePrivilege',
    'SeLoadDriverPrivilege', 'SeRestorePrivilege', 'SeSecurityPrivilege',
    'SeTakeOwnershipPrivilege', 'SeTcbPrivilege',
}


def token_report(handle):
    integrity, _ = win32security.GetTokenInformation(
        handle, win32security.TokenIntegrityLevel)
    integrity_sid = win32security.ConvertSidToStringSid(integrity)
    groups = win32security.GetTokenInformation(handle, win32security.TokenGroups)
    admin_attributes = next((attributes for sid, attributes in groups
                             if win32security.ConvertSidToStringSid(sid) == ADMIN_SID), None)
    privileges = [
        dict(name=win32security.LookupPrivilegeName(None, luid), attributes=attributes)
        for luid, attributes in win32security.GetTokenInformation(
            handle, win32security.TokenPrivileges)
    ]
    enabled = [row['name'] for row in privileges
               if row['attributes'] & win32con.SE_PRIVILEGE_ENABLED]
    user, _ = win32security.GetTokenInformation(handle, win32security.TokenUser)
    return dict(
        elevated=bool(win32security.GetTokenInformation(handle, win32security.TokenElevation)),
        elevation_type=win32security.GetTokenInformation(handle, win32security.TokenElevationType),
        integrity_sid=integrity_sid,
        integrity_rid=int(integrity_sid.rsplit('-', 1)[1]),
        user_sid=win32security.ConvertSidToStringSid(user),
        administrators_sid_attributes=admin_attributes,
        administrators_deny_only=(admin_attributes is not None and
                                  bool(admin_attributes & win32security.SE_GROUP_USE_FOR_DENY_ONLY)),
        enabled_privileges=enabled,
        enabled_admin_privileges=sorted(ADMIN_PRIVILEGES.intersection(enabled)),
    )


def require_non_elevated(report):
    assert not report['elevated'], report
    assert report['integrity_rid'] == MEDIUM_RID, report
    assert not report['enabled_admin_privileges'], report
    if report['administrators_sid_attributes'] is not None:
        assert report['administrators_deny_only'], report
        assert not (report['administrators_sid_attributes'] & win32security.SE_GROUP_ENABLED), report


@dataclass
class TokenContext:
    handle: object
    report: dict
    source_report: dict
    method: str

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


def prepare_token():
    """Verify a limited interactive token without changing system state."""
    current = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        source = token_report(current)
        if not source['elevated']:
            require_non_elevated(source)
            result = TokenContext(current, source, source, 'existing non-elevated token')
            current = None
            return result
        # UAC's already existing limited token, rather than creating an account
        # or synthesizing a new access policy. Launch failure is surfaced.
        linked = win32security.GetTokenInformation(current, win32security.TokenLinkedToken)
        try:
            report = token_report(linked)
            require_non_elevated(report)
            assert report['user_sid'] == source['user_sid'], 'Linked token belongs to another user'
            result = TokenContext(linked, report, source, 'existing limited linked token')
            linked = None
            return result
        finally:
            if linked is not None:
                linked.Close()
    finally:
        if current is not None:
            current.Close()


@dataclass
class OwnedProcess:
    process_handle: object
    thread_handle: object
    pid: int
    tid: int
    token_report: dict
    launch_method: str

    def poll(self):
        if win32event.WaitForSingleObject(self.process_handle, 0) == win32con.WAIT_OBJECT_0:
            return win32process.GetExitCodeProcess(self.process_handle)
        return None

    def wait(self, timeout=30):
        result = win32event.WaitForSingleObject(self.process_handle, int(timeout * 1000))
        if result == win32con.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        assert result == win32con.WAIT_OBJECT_0, result
        return win32process.GetExitCodeProcess(self.process_handle)

    def close(self):
        # Job ownership/termination remains with the caller. Closing a handle
        # does not terminate either the process or unrelated processes.
        for name in ('thread_handle', 'process_handle'):
            handle = getattr(self, name)
            if handle is not None:
                handle.Close()
                setattr(self, name, None)


def launch_owned(context, exe, cwd, env, job, command_line=None):
    """Create suspended, attach the caller's Job, verify token, then resume.

    The caller supplies a dedicated Job configured to kill owned processes on
    closure and retains responsibility for its lifecycle. No child is allowed
    to run outside that Job before assignment or token verification.
    """
    assert context.handle is not None and job is not None
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    assert (limits['BasicLimitInformation']['LimitFlags'] &
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE), 'Caller Job must own cleanup'
    executable = str(Path(exe).resolve(strict=True))
    directory = str(Path(cwd).resolve(strict=True))
    require_non_elevated(context.report)
    startup = win32process.STARTUPINFO()
    flags = win32con.CREATE_SUSPENDED | win32con.CREATE_UNICODE_ENVIRONMENT
    command = command_line or subprocess.list2cmdline([executable])
    if context.method == 'existing non-elevated token':
        current = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            report = token_report(current)
            require_non_elevated(report)
            assert report['user_sid'] == context.report['user_sid']
        finally:
            current.Close()
        created = win32process.CreateProcess(
            executable, command, None, None, False, flags, dict(env), directory, startup)
    else:
        created = win32process.CreateProcessAsUser(
            context.handle, executable, command, None, None, False,
            flags, dict(env), directory, startup)
    process, thread, pid, tid = created
    assigned = False
    try:
        win32job.AssignProcessToJobObject(job, process)
        assigned = True
        actual_token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
        try:
            actual = token_report(actual_token)
            require_non_elevated(actual)
            assert actual['user_sid'] == context.report['user_sid']
        finally:
            actual_token.Close()
        previous = win32process.ResumeThread(thread)
        assert previous == 1, previous
        return OwnedProcess(process, thread, pid, tid, actual, context.method)
    except BaseException:
        # These handles identify only the suspended child just created here.
        # If assignment succeeded its caller-owned Job owns any descendants.
        if assigned:
            win32job.TerminateJobObject(job, 1)
        else:
            win32api.TerminateProcess(process, 1)
        win32event.WaitForSingleObject(process, 5000)
        thread.Close()
        process.Close()
        raise
