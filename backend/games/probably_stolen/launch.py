"""Optional direct launch: fixed installation executable, no shell or history gate."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import threading

from backend.deployments.paths import GuardRejected, _information, file_identity, pinned_chain, pinned_file
from backend.deployments.process_guard import same_process_image
from .layout import EXE


class LaunchRejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class LaunchService:
    def __init__(self, guards):
        self.guards = guards
        self._lock = threading.RLock()
        self._records = {}
        self._closed = False

    @staticmethod
    def _view(installation_id, state, *, pid=None, exit_code=None, started_at=None, message=None):
        return dict(installation_id=installation_id, state=state, pid=pid, exit_code=exit_code,
                    started_at=started_at, message=message or {
                        'not_started': '未启动游戏。', 'running': '游戏进程已启动。',
                        'failed': '游戏启动失败，请检查安装目录和权限。',
                        'unknown': '暂时无法确认游戏进程，请刷新后重试。',
                        'exited': f'游戏进程已退出（退出码 {exit_code}）。',
                    }[state])

    def _poll(self, installation_id):
        import win32event
        import win32process
        record = self._records.get(installation_id)
        if record and record.get('handle') is not None:
            # A live process handle avoids PID reuse and STILL_ACTIVE exit-code ambiguity.
            if win32event.WaitForSingleObject(record['handle'], 0) == win32event.WAIT_OBJECT_0:
                code = win32process.GetExitCodeProcess(record['handle'])
                record['handle'].Close()
                record['handle'] = None
                record['data'] = self._view(installation_id, 'exited', pid=record['data']['pid'],
                    exit_code=code, started_at=record['data']['started_at'])
        return record['data'].copy() if record else None

    def status(self, installation_id):
        with self._lock:
            if self._closed:
                raise LaunchRejected('RUNTIME_CLOSED')
            previous = self._poll(installation_id)
            if previous and previous['state'] == 'running':
                return previous
            # Detect a game started by Steam or a different manager; no name matching.
            with self.guards._installation(installation_id) as (_, process):
                observed = process.inspect()
            if observed['status'] == 'running':
                return dict(self._view(installation_id, 'running', pid=observed['pids'][0],
                                      message='游戏已在运行。'), already_running=True)
            if observed['status'] == 'unknown':
                return self._view(installation_id, 'unknown')
            return previous or self._view(installation_id, 'not_started')

    def launch(self, installation_id):
        import pywintypes
        import win32con
        import win32event
        import win32process
        with self._lock:
            if self._closed:
                raise LaunchRejected('RUNTIME_CLOSED')
            previous = self._poll(installation_id)
            if previous and previous['state'] == 'running':
                return dict(previous, already_running=True)
            process_handle = thread_handle = None
            resumed = False
            validated = False
            try:
                with self.guards.operation(installation_id, 'launch') as session:
                    validated = True
                    root = Path(session.installation.canonical_root)
                    executable = root / EXE
                    with pinned_chain(root) as chain, pinned_file(executable, execute=True) as image:
                        if file_identity(_information(image, False)) != session.process.exe_identity:
                            raise GuardRejected('GUARD_INSTALLATION_CHANGED')
                        session.check()
                        # Create suspended and verify the actual mapped image before running it.
                        # This reuses the existing image check, without a command/argument surface.
                        process_handle, thread_handle, pid, _ = win32process.CreateProcess(
                            str(executable), subprocess.list2cmdline([str(executable)]), None, None, False,
                            win32con.CREATE_SUSPENDED, None, str(root), win32process.STARTUPINFO())
                        chain.check()
                        session.lease.check()
                        if not same_process_image(process_handle, image):
                            raise LaunchRejected('GAME_IMAGE_CHANGED')
                        if win32process.ResumeThread(thread_handle) != 1:
                            raise LaunchRejected('GAME_LAUNCH_FAILED')
                        resumed = True
                        self._records[installation_id] = dict(handle=process_handle,
                            data=self._view(installation_id, 'running', pid=pid,
                                started_at=datetime.now(timezone.utc).isoformat()))
                        process_handle = None  # Owned by the record until exit or manager close.
                return self._poll(installation_id)
            except (GuardRejected, LaunchRejected, pywintypes.error, OSError) as exc:
                code = getattr(exc, 'code', 'GAME_LAUNCH_FAILED')
                if code == 'GUARD_PROCESS_RUNNING':
                    # This is an existing external process, not a failed child launch.
                    raise LaunchRejected('GAME_ALREADY_RUNNING') from None
                code = {'GUARD_PATH_UNAVAILABLE': 'GAME_LAUNCH_FAILED',
                        'GUARD_PROCESS_UNKNOWN': 'GAME_PROCESS_UNKNOWN',
                        'GUARD_REPARSE_POINT': 'GAME_IMAGE_CHANGED',
                        'GUARD_HARDLINK': 'GAME_IMAGE_CHANGED'}.get(code, code)
                if validated:
                    self._records[installation_id] = dict(handle=None,
                        data=self._view(installation_id, 'failed', message={
                            'GAME_LAUNCH_FAILED': '无法启动游戏，请检查可执行文件、权限和所需运行环境。',
                            'GUARD_INSTALLATION_CHANGED': '游戏文件缺失或已变化，请重新选择安装。',
                            'GUARD_RECOVERY_REQUIRED': '文件操作尚未完成，暂时无法启动游戏。',
                            'GUARD_LEASE_BUSY': '此安装正在处理其他操作，请稍后再启动。',
                        }.get(code, '启动检查未通过，请查看错误提示后重试。')))
                raise LaunchRejected(code) from None
            finally:
                if thread_handle is not None:
                    thread_handle.Close()
                if process_handle is not None:
                    try:
                        if not resumed and win32event.WaitForSingleObject(process_handle, 0) == win32event.WAIT_TIMEOUT:
                            # Only this newly created suspended child, never an external process.
                            win32process.TerminateProcess(process_handle, 1)
                            win32event.WaitForSingleObject(process_handle, 5000)
                    finally:
                        process_handle.Close()

    def close(self):
        with self._lock:
            self._closed = True
            for record in self._records.values():
                if record.get('handle') is not None:
                    record['handle'].Close()
                    record['handle'] = None
            # Closing the manager must not terminate the game.
