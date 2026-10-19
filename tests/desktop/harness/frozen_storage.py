"""Observe the shipped EXE's first-run migration and real child-process restart.

UI actions and visual inspection are performed with Computer Use. Only owned
temporary data is written; no registry edits, downloads, or game operations.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

import psutil

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.desktop.harness.portable_launcher import environment

parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
out, bundle = args.out.resolve(), args.bundle.resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/20261019-storage-profiles')
assert bundle.is_relative_to(ROOT / '.preview')
out.mkdir(parents=True, exist_ok=False)
exe = bundle / 'PSModManager.exe'
env = environment(out)
local = Path(tempfile.mkdtemp(prefix='PSMM-owned-frozen-storage-')).resolve()
env['LOCALAPPDATA'] = str(local)
source, target = local / 'PSModManager', out / 'new-data'
report = dict(status='FAIL', source=str(source), target=str(target), bundle=str(bundle), checks=[])
first = second = None


def phase(name):
    (out / 'phase.json').write_text(json.dumps(dict(phase=name, source=str(source), target=str(target),
        first_pid=first.pid if first else None, second_pid=second.pid if second else None), ensure_ascii=False), 'utf-8')


def wait(test, timeout=600):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(.2)
    raise AssertionError('Frozen observation timed out; see phase.json')


def restarted():
    for process in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            if (process.pid != first.pid and process.info['exe'] and Path(process.info['exe']).resolve() == exe
                    and process.cmdline()[-2:] == ['--psmm-data-root', str(target)]):
                return process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


try:
    first = subprocess.Popen([str(exe)], env=env, cwd=out)
    wait(lambda: (source / 'config.json').is_file(), timeout=60)
    (source / 'notes').mkdir()
    (source / 'notes/retained.txt').write_bytes(b'owned portable storage sentinel')
    phase('first-use')
    wait(lambda: (target / 'psmodmanager.sqlite3').is_file() and (source / '.storage-redirect.json').is_file())
    assert (target / 'notes/retained.txt').read_bytes() == b'owned portable storage sentinel'
    assert json.loads((local / 'PSModManager-storage.json').read_text('utf-8'))['root'] == str(target)
    assert json.loads((target / 'config.json').read_text('utf-8'))['storage_setup_complete'] is True
    report['checks'].append('Actual frozen first-run flow migrates C-to-E, retains file bytes, and commits per-user locator')
    phase('waiting-for-restart')
    assert first.wait(timeout=600) == 0
    second = wait(restarted, timeout=60)
    wait(lambda: (target / 'webview2').is_dir(), timeout=60)
    report['checks'].append('Restart button closes original process with exit zero and starts the shipped EXE using new root')
    for extra in ([], ['--psmm-data-root', str(source)]):
        assert subprocess.run([str(exe), *extra], env=env, cwd=out, timeout=20).returncode == 0
    report['checks'].append('Default activation and old explicit-root activation both forward to the migrated instance')
    phase('restarted-ready-for-inspection')
    wait(lambda: (out / 'finish').exists())
    second.wait(timeout=45)
    report['checks'].append('Migrated frozen window closes normally after settings inspection')
    report['status'] = 'PASS'
except Exception:
    report['error'] = traceback.format_exc()
finally:
    report['forced_cleanup'] = False
    if first is not None and first.poll() is None:
        report['forced_cleanup'] = True
        first.terminate(); first.wait(timeout=10)
    if second is not None:
        try:
            if second.is_running() and second.status() != psutil.STATUS_ZOMBIE:
                report['forced_cleanup'] = True
                second.terminate(); second.wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
    (out / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
print(json.dumps(report, ensure_ascii=False), flush=True)
raise SystemExit(0 if report['status'] == 'PASS' and not report['forced_cleanup'] else 1)
