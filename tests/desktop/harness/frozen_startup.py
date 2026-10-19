"""Observe two owned frozen versions; normal closes are done through native UI.

Manager data is isolated; automatic Steam discovery may read an existing game,
but none is launched or modified. No protocol registration or external download
occurs. Secondary activation exercises the shipped EXE.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.desktop.harness.portable_launcher import environment, owned_window

parser = argparse.ArgumentParser()
parser.add_argument('--new-bundle', type=Path, required=True)
parser.add_argument('--old-bundle', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
out = args.out.resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/20261019-startup-ipc-fix')
out.mkdir(parents=True, exist_ok=False)
env = environment(out)
processes = []
report = dict(status='FAIL', windows={}, checks=[])


def launch(bundle, extra=()):
    executable = bundle.resolve() / 'PSModManager.exe'
    assert executable.is_relative_to(ROOT / '.preview') and executable.is_file()
    p = subprocess.Popen([str(executable), '--psmm-data-root', str(out / 'isolated-data'), *extra], cwd=out, env=env)
    processes.append(p)
    return p


def wait_window(process, label):
    deadline = time.monotonic() + 45
    while process.poll() is None and time.monotonic() < deadline:
        hwnd = owned_window(process.pid)
        if hwnd:
            report['windows'][label] = dict(pid=process.pid, hwnd=hwnd)
            (out / 'windows.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            return
        time.sleep(.1)
    raise AssertionError(label + ' did not open its window')


try:
    old = launch(args.old_bundle)
    wait_window(old, 'old')
    current = launch(args.new_bundle)
    wait_window(current, 'new')
    assert old.poll() is None
    report['checks'].append('New frozen application opens with the released old version still running on the same isolated data root')
    assert launch(args.new_bundle).wait(timeout=15) == 0
    report['checks'].append('Repeated normal activation forwards to current instance and exits zero')
    url = f'nxm://probablystolen/mods/81/files/13?key=owned-startup-test&expires={int(time.time()+600)}&user_id=7'
    assert launch(args.new_bundle, ['--nxm', url]).wait(timeout=15) == 0
    report['checks'].append('Protocol activation still forwards successfully without exposing authorization')
    (out / 'windows.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    assert current.wait(timeout=600) == 0
    assert old.wait(timeout=600) == 0
    report['checks'].append('Both owned primary windows close normally with exit zero')
    report['status'] = 'PASS'
except Exception:
    report['error'] = traceback.format_exc()
finally:
    report['forced_cleanup'] = False
    for p in processes:
        if p.poll() is None:
            report['forced_cleanup'] = True
            p.terminate()
            p.wait(timeout=10)
    (out / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
raise SystemExit(0 if report['status'] == 'PASS' else 1)
