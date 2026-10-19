"""Bounded argv execution: Windows jobs own descendants even after reparenting.

Dependencies are deliberately mandatory; a process-tree snapshot is not an
equivalent fallback for an operating-system lifetime boundary.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def run_command(argv, *, cwd, env, timeout, stdout_path, stderr_path):
    result = {"started": False, "exit_code": None, "timed_out": False,
              "command": list(argv) if isinstance(argv, (list, tuple)) else [], "cleanup": {"job_closed": False,
              "remaining_processes": None}, "error": None}
    if (not isinstance(argv, (list, tuple)) or not argv
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        result["error"] = "Expected nonempty argv and a finite positive timeout"
        return result
    if os.name != "nt":
        result["error"] = "Windows JobObject execution requires Windows"
        return result
    try:
        import win32api
        import win32job
    except ImportError as exc:
        result["error"] = f"Missing dependency: pywin32 (Windows JobObject): {exc}"
        return result

    job = process = None
    status_path = None
    started_at = time.monotonic()
    try:
        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        # Caller validates and creates these evidence paths; do not create arbitrary roots.
        with open(stdout_path, "xb") as stdout, open(stderr_path, "xb") as stderr:
            fd, status_name = tempfile.mkstemp(prefix="process-status-", suffix=".json",
                                             dir=Path(stdout_path).parent)
            os.close(fd)
            status_path = Path(status_name)
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", str(Path(__file__).with_name("child_bootstrap.py"))],
                cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # The bootstrap is inert until the owner has installed the kernel boundary.
            win32job.AssignProcessToJobObject(job, int(process._handle))
            payload = json.dumps({"argv": list(argv), "status_path": str(status_path.resolve())})
            process.stdin.write((payload + "\n").encode("utf-8"))
            process.stdin.close()
            try:
                result["exit_code"] = process.wait(timeout=max(0.001, timeout - (time.monotonic() - started_at)))
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                result["error"] = "Command exceeded timeout"
            if status_path.stat().st_size:
                result.update(json.loads(status_path.read_text(encoding="utf-8")))
            elif not result["timed_out"]:
                result["error"] = "Bootstrap exited without a command-start record"
            if not result["started"]:
                result["bootstrap_exit_code"] = result["exit_code"]
                result["exit_code"] = None
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if job is not None:
            try:
                # Kill every still-owned descendant, including grandchildren whose
                # parent exited normally. Poll the job, not a racy parent PID walk.
                win32job.TerminateJobObject(job, 124)
                deadline = time.monotonic() + 5
                while True:
                    active = win32job.QueryInformationJobObject(
                        job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]
                    if not active or time.monotonic() >= deadline:
                        result["cleanup"]["remaining_processes"] = active
                        break
                    time.sleep(0.02)
            except Exception as exc:
                result["cleanup"]["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                win32api.CloseHandle(job)
                result["cleanup"]["job_closed"] = True
        if process is not None:
            if process.poll() is None:
                process.kill()  # Also covers assignment failure: gated child spawned nothing.
            process.wait(timeout=5)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        # Keep the tiny command-start record as evidence. Deleting it here can
        # hit a transient Windows sharing lock after an otherwise clean timeout
        # and hide the real process result behind an unrelated cleanup exception.
        result["duration_seconds"] = round(time.monotonic() - started_at, 6)
    return result
