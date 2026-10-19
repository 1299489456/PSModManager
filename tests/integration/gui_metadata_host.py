"""Real pythonw entry for synthetic worker and scan-to-adoption regressions."""
import ctypes
import json
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
mode, source, output = sys.argv[1:]
source, output = Path(source), Path(output)
report = {'console_window': ctypes.WinDLL('kernel32').GetConsoleWindow()}


def call(api, command, payload):
    response = api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))
    assert response['ok'], response
    if 'task_id' not in response:
        return response['data']
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        task = api._context.jobs.snapshot(response['task_id'])
        if task['state'] in ('succeeded', 'failed', 'cancelled'):
            assert task['state'] == 'succeeded', task
            assert not task['data'].get('error_code'), task
            return task['data']
        time.sleep(.02)
    raise TimeoutError(command)


def run():
    if mode == 'scan-adopt':
        from backend.app.api import DesktopApi
        from backend.app.composition import DesktopContext
        from backend.app.paths import resolve_paths
        from tests.unit.test_installation_discovery import hashes
        report['source_before'] = hashes(source)
        context = DesktopContext(resolve_paths(str(output.parent / 'gui-data'), protected_roots=[source]))
        context.initialize()
        try:
            context.installations.allow_manual_manifest_probe = False
            api = DesktopApi(context)
            handle = context.installations.grant('manual', str(source))['source_handle']
            found = call(api, 'installation.discover', {'source_handles': [handle]})
            chosen = call(api, 'installation.select', {'candidate_id': found['candidates'][0]['candidate_id']})
            installation_id = chosen['installation']['installation_id']
            report['scan'] = call(api, 'inventory.scan', {'installation_id': installation_id, 'force': False})
            report['preview'] = call(api, 'package.adopt_preview', {'installation_id': installation_id})
            report['adoption'] = call(api, 'package.adopt', {
                'snapshot_id': report['preview']['snapshot_id'], 'manifest_hash': report['preview']['manifest_hash'],
                'paths': ['Mods/WagesPerks.dll'], 'display_name': 'GUI adoption',
            })
            report['library'] = call(api, 'package.list', {})
            report['generations'] = context.store.list('DeploymentGeneration')
        finally:
            context.close()
        report['source_after'] = hashes(source)
    else:
        from backend.scanning.worker_sandbox import run_sandbox
        payload = json.loads(source.read_text(encoding='utf-8'))
        began = time.monotonic()
        def cancelled():
            if mode == 'cancel-error' and time.monotonic() - began > .5:
                raise ValueError('owned cancellation signal')
            return mode == 'cancel' and time.monotonic() - began > .5
        report['process'] = run_sandbox(payload['argv'], json.dumps(payload['request']).encode(),
            stage=Path(payload['stage']), timeout=payload['timeout'], cancelled=cancelled)


try:
    run()
except Exception:
    report['exception'] = traceback.format_exc()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    raise
else:
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
