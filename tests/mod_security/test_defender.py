"""Command boundary only: never start the real engine or execute a sample."""
import os
import subprocess

import pytest

from backend.mod_security import defender


def test_unavailable_engine_does_not_spawn(monkeypatch, tmp_path):
    monkeypatch.setattr(defender, 'discover', lambda: None)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('engine must not start'))
    assert defender.DefenderScanner().scan(tmp_path)['state'] == 'unavailable'


@pytest.mark.skipif(os.name != 'nt', reason='Windows file-pin ABI')
def test_only_pinned_signed_engine_fixed_nonremediating_arguments(monkeypatch, tmp_path):
    executable = tmp_path / 'engine.exe'; executable.write_bytes(b'inert fixture')
    monkeypatch.setattr(defender, 'discover', lambda: executable)
    monkeypatch.setattr(defender, '_trusted_microsoft', lambda p: p == executable)
    seen = []
    class Process:
        returncode = 0
        def poll(self): return 0
    def spawn(args, **kwargs):
        seen.append((args, kwargs))
        kwargs['stdout'].write(b'No threats found.\n')
        return Process()
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    result = defender.DefenderScanner().scan(tmp_path)
    assert result['state'] == 'clean' and result['remediation'] is False
    assert seen[0][0] == [str(executable), '-Scan', '-ScanType', '3', '-File', str(tmp_path), '-DisableRemediation']
    assert seen[0][1]['creationflags'] == subprocess.CREATE_NO_WINDOW
    assert 'shell' not in seen[0][1] and result['cloud_policy'] == 'system_managed_unknown'


@pytest.mark.skipif(os.name != 'nt', reason='Windows file-pin ABI')
def test_signature_failure_after_pin_does_not_spawn(monkeypatch, tmp_path):
    executable = tmp_path / 'engine.exe'; executable.write_bytes(b'inert fixture')
    monkeypatch.setattr(defender, 'discover', lambda: executable)
    monkeypatch.setattr(defender, '_trusted_microsoft', lambda _: False)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('signature invalid'))
    assert defender.DefenderScanner().scan(tmp_path)['reason'] == 'engine_signature_invalid'


@pytest.mark.skipif(os.name != 'nt', reason='Windows file-pin ABI')
def test_cancel_only_terminates_owned_client_not_global_scans(monkeypatch, tmp_path):
    executable = tmp_path / 'engine.exe'; executable.write_bytes(b'inert fixture')
    monkeypatch.setattr(defender, 'discover', lambda: executable)
    monkeypatch.setattr(defender, '_trusted_microsoft', lambda _: True)
    class Process:
        returncode = None
        killed = 0
        def poll(self): return self.returncode
        def kill(self): self.killed += 1; self.returncode = -1
        def wait(self): return self.returncode
    process = Process()
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: process)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 1
    result = defender.DefenderScanner().scan(tmp_path, cancelled=cancelled)
    assert result['state'] == 'incomplete' and process.killed == 1
