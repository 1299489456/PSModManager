"""Trusted, stdin-gated console host for the unchanged LPAC parser boundary.

Only the manager launches this file; it is not a bridge or file-import entry.
No input assembly is opened here. The existing sandbox owns all parser work.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

MAX_RESPONSE = 2 * 1024 * 1024


def run_console_host(argv, request, *, stage, timeout, cancelled):
    import win32api
    import win32job

    result = dict(started=False, exit_code=None, status='sandbox_unavailable',
                  stdout='', stderr='', cleanup_remaining=None, appcontainer=False,
                  capabilities=None, peak_memory=None)
    if not 0 < timeout <= 30 or len(request) > 16384:
        raise ValueError('Invalid worker limits')
    if getattr(sys, 'frozen', False):
        host_command = [str(Path(sys.executable).with_name('PSModManager.WorkerHost.exe'))]
    else:
        host_command = [str(Path(sys.executable).with_name('python.exe')), '-I', '-B', str(Path(__file__).resolve())]
    if not Path(host_command[0]).is_file():
        return dict(result, error='CONSOLE_HOST_UNAVAILABLE')
    job = process = None
    assigned_to_job = False
    readers = []
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    host = dict(started=False, exit_code=None, cleanup_remaining=None)
    cancel_error = None
    try:
        job = win32job.CreateJobObject(None, '')
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen(host_command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=stage)
        host['pid'] = process.pid
        # The host cannot launch its worker before reading this message, so
        # assigning the outer kill-on-close job precedes every descendant.
        result['phase'] = 'assign_job'
        win32job.AssignProcessToJobObject(job, int(process._handle))
        assigned_to_job = True
        host.update(started=True, pid=process.pid)

        def drain(stream, index):
            while block := stream.read(8192):
                left = MAX_RESPONSE - len(buffers[index])
                buffers[index].extend(block[:max(0, left)])
                if len(block) > left:
                    overflow.set()

        for index, stream in enumerate((process.stdout, process.stderr)):
            reader = threading.Thread(target=drain, args=(stream, index), daemon=True)
            reader.start()
            readers.append(reader)
        payload = dict(argv=list(map(str, argv)), request=base64.b64encode(request).decode('ascii'),
                       stage=str(stage), timeout=timeout)
        process.stdin.write(json.dumps(payload).encode('utf-8') + b'\n')
        process.stdin.flush()
        deadline = time.monotonic() + timeout + 5
        cancel_sent = False
        while process.poll() is None:
            if overflow.is_set():
                raise ValueError('CONSOLE_HOST_OUTPUT_LIMIT')
            try:
                should_cancel = cancelled()
            except Exception as exc:
                cancel_error = exc
                should_cancel = True
            if should_cancel and not cancel_sent:
                process.stdin.write(b'cancel\n')
                process.stdin.flush()
                cancel_sent = True
            if time.monotonic() >= deadline:
                raise ValueError('CONSOLE_HOST_TIMEOUT')
            time.sleep(.02)
        host['exit_code'] = process.returncode
        for reader in readers:
            reader.join(2)
        if process.returncode != 0 or overflow.is_set():
            raise ValueError('CONSOLE_HOST_FAILED')
        decoded = json.loads(buffers[0])
        if not isinstance(decoded, dict):
            raise ValueError('CONSOLE_HOST_PROTOCOL_INVALID')
        result = decoded
    except Exception as exc:
        result['error'] = str(exc) if isinstance(exc, ValueError) else 'CONSOLE_HOST_UNAVAILABLE'
    finally:
        if process is not None and not assigned_to_job:
            process.kill()
            process.wait(timeout=5)
            result['unassigned_process_terminated'] = process.poll() is not None
            result['pid'] = process.pid
        if job is not None:
            win32job.TerminateJobObject(job, 124)
            if process is not None:
                process.wait(timeout=5)
            cleanup_deadline = time.monotonic() + 2
            while True:
                host['cleanup_remaining'] = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
                if not host['cleanup_remaining'] or time.monotonic() >= cleanup_deadline:
                    break
                time.sleep(.02)
            win32api.CloseHandle(job)
            if not assigned_to_job:
                result['cleanup_remaining'] = host['cleanup_remaining']
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        for reader in readers:
            reader.join(2)
    result['console_host'] = host
    if cancel_error is not None:
        raise cancel_error
    return result


def main():
    # -I omits the workspace from sys.path; add only this installed source root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.scanning.worker_sandbox import _run_sandbox
    line = sys.stdin.buffer.readline(32769)
    if len(line) > 32768:
        return 2
    payload = json.loads(line)
    cancel = threading.Event()

    def listen():
        # Closing the controlling pipe also cancels abandoned work.
        sys.stdin.buffer.readline(16)
        cancel.set()

    threading.Thread(target=listen, daemon=True).start()
    result = _run_sandbox(payload['argv'], base64.b64decode(payload['request'], validate=True),
                          stage=Path(payload['stage']), timeout=payload['timeout'], cancelled=cancel.is_set)
    sys.stdout.buffer.write(json.dumps(result).encode('utf-8'))
    sys.stdout.buffer.flush()
    # Avoid interpreter shutdown waiting for the intentionally blocked daemon
    # pipe listener. All sandbox kernel objects have already been closed.
    os._exit(0)


if __name__ == '__main__':
    raise SystemExit(main())
