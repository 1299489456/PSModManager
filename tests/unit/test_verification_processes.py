"""Real child processes verify the runner's operating-system lifetime boundary."""
import json
import builtins
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from scripts.verification.processes import run_command


def execute(tmp_path, code, timeout=10):
    result = run_command([sys.executable, "-I", "-B", "-c", code], cwd=str(tmp_path),
                         env=os.environ.copy(), timeout=timeout,
                         stdout_path=tmp_path / "stdout.log", stderr_path=tmp_path / "stderr.log")
    (tmp_path / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


@pytest.mark.parametrize("exit_code", [0, 7, 31])
def test_raw_exit_and_complete_output(tmp_path, exit_code):
    result = execute(tmp_path, f"import sys;sys.stdout.write('x'*150000);sys.stderr.write('y'*170000);sys.exit({exit_code})")
    assert result["started"] and result["exit_code"] == exit_code
    assert not result["timed_out"]
    assert result["cleanup"] == {"job_closed": True, "remaining_processes": 0}
    assert (tmp_path / "stdout.log").read_bytes() == b"x" * 150000
    assert (tmp_path / "stderr.log").read_bytes() == b"y" * 170000
    records = list(tmp_path.glob('process-status-*.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text()) == {'started': True, 'pid': result['pid']}


@pytest.mark.parametrize("parent_exits", [False, True])
def test_descendant_cleaned_even_if_parent_exits(tmp_path, parent_exits):
    # Keep a separate process alive: cleanup must not kill other jobs/processes.
    unrelated = subprocess.Popen([sys.executable, "-I", "-c", "import time;time.sleep(60)"])
    try:
        child_code = "import time;time.sleep(60)"
        code = ("import subprocess,sys,time,json;from pathlib import Path;"
                f"p=subprocess.Popen([sys.executable,'-I','-c',{child_code!r}]);"
                "import psutil;Path('identity.json').write_text(json.dumps({'pid':p.pid,'created':psutil.Process(p.pid).create_time()}));"
                + ("sys.exit(13)" if parent_exits else "time.sleep(60)"))
        result = execute(tmp_path, code, timeout=2)
        identity = json.loads((tmp_path / "identity.json").read_text())
        assert result["started"]
        assert result["timed_out"] is not parent_exits
        assert result["exit_code"] == (13 if parent_exits else None)
        assert result["cleanup"]["remaining_processes"] == 0
        assert not psutil.pid_exists(identity["pid"]) or psutil.Process(identity["pid"]).create_time() != identity["created"]
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


def test_missing_executable_not_started(tmp_path):
    result = run_command([str(tmp_path / "does-not-exist.exe")], cwd=tmp_path,
                         env=os.environ.copy(), timeout=5,
                         stdout_path=tmp_path / "stdout.log", stderr_path=tmp_path / "stderr.log")
    assert not result["started"]
    assert result["exit_code"] is None
    assert result["error"]
    assert result["cleanup"]["remaining_processes"] == 0


def test_invalid_timeout_does_not_start(tmp_path):
    result = execute(tmp_path, "raise RuntimeError('must not execute')", timeout=-1)
    assert not result["started"] and result["exit_code"] is None
    assert not (tmp_path / "stdout.log").exists()


def test_assignment_failure_cannot_spawn_payload(tmp_path, monkeypatch):
    import win32job

    def reject_assignment(*args):
        raise OSError("injected job assignment failure")

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", reject_assignment)
    result = execute(tmp_path, "from pathlib import Path;Path('should-not-exist').write_text('bad')")
    assert not result["started"] and result["exit_code"] is None
    assert "assignment failure" in result["error"]
    assert result["cleanup"]["remaining_processes"] == 0
    assert not (tmp_path / "should-not-exist").exists()


def test_missing_job_dependency_is_blocked(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def missing_dependency(name, *args, **kwargs):
        if name == "win32job":
            raise ImportError("injected pywin32 absence")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_dependency)
    result = execute(tmp_path, "raise RuntimeError('must not execute')")
    assert not result["started"] and result["exit_code"] is None
    assert "Missing dependency: pywin32" in result["error"]
    assert not (tmp_path / "stdout.log").exists()
