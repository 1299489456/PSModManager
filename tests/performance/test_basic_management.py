"""CASE-045 representative real API measurements; no provider or game process."""
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
from uuid import uuid4

from tests.integration.harness.transaction_recovery import request, tree
from tests.performance.basic_fixture import prepare_basic_fixture


def test_representative_management_latency_and_cleanup(tmp_path_factory):
    root = tmp_path_factory.mktemp('中文')
    measurements = []
    report = dict(status='FAIL', environment=dict(python=sys.version,
        platform=platform.platform(), processor=platform.processor(), logical_cpus=os.cpu_count()),
        measurements=measurements, polling_interval_ms=10, root=str(root))
    app = None
    def audit(event, args):
        if event in {'socket.connect', 'socket.bind'}:
            raise AssertionError('This basic-management test has no network authorization')
        if event == 'open':
            path = str(args[0]).replace('\\', '/').lower()
            if path.startswith(('e:/entertainment/steam/', 'e:/workspace/github/rimcrow/')):
                raise AssertionError('Real game and original RimCrow are outside this test')
    sys.addaudithook(audit)

    def measured(command, payload=None):
        started = time.perf_counter()
        response = app.context.api.dispatch(request(command, payload))
        metric = dict(command=command, acknowledgement_ms=(time.perf_counter()-started)*1000)
        measurements.append(metric)
        assert response['ok'], response
        result = response['data']
        if isinstance(result, dict) and 'task_id' in result:
            deadline = time.monotonic()+45
            while result['state'] not in {'succeeded', 'failed', 'cancelled'}:
                assert time.monotonic() < deadline, 'Actual API task did not finish within 45 seconds'
                time.sleep(.01)
                response = app.context.api.dispatch(request('task.snapshot', {'task_id':result['task_id']}))
                assert response['ok'], response
                result = response['data']
            metric.update(task_state=result['state'], sequence=result['sequence'])
            assert result['state'] == 'succeeded', result
            result = result['data']
        metric['completion_ms'] = (time.perf_counter()-started)*1000
        assert not isinstance(result, dict) or not result.get('error_code'), result
        return result

    try:
        started = time.perf_counter()
        app, metadata, files = prepare_basic_fixture(root)
        report.update(setup_ms=(time.perf_counter()-started)*1000,
            sample_count=metadata['sample_count'], installed_count=4,
            import_file_count=len(files), import_bytes=sum(map(len, files.values())))
        app.context.confirm_plan = lambda _: (_ for _ in ()).throw(AssertionError('Unexpected plan dialog'))
        # Four sweeps measure the first page and both following pages, without
        # substituting storage or the native path/process checks.
        for _ in range(4):
            identities = []
            for offset in (0, 20, 40):
                page = measured('mod.list', dict(installation_id=app.installation, offset=offset, limit=20))
                assert page['total'] == 60 and len(page['items']) == 20
                assert page['next_offset'] == (offset+20 if offset<40 else None)
                identities.extend(item['release_id'] for item in page['items'])
            assert len(set(identities)) == 60
        app.context.choose_import_source = lambda _: metadata['import_source']
        grant = measured('package.choose', {'kind':'directory'})
        preview = measured('package.import_preview', {'source_handle':grant['source_handle']})
        assert preview['can_commit'], preview
        release = measured('package.import_commit', dict(preview_id=preview['preview_id'],
            preview_hash=preview['preview_hash'], display_name=metadata['imported_name'], version_raw='1.0'))
        assert not release['deployed']
        for verb, expected in [('install','enabled'), ('disable','disabled'),
                               ('enable','enabled'), ('uninstall','not_installed')]:
            result = measured('mod.action', dict(installation_id=app.installation,
                release_id=release['release_id'], manifest_hash=release['manifest_hash'],
                action=verb, idempotency_key=str(uuid4())))
            measurements[-1]['action'] = verb
            assert result.get('completed') and result['state'] == expected, result
            for relative, content in files.items():
                target = app.game/relative
                assert target.is_file() == (expected == 'enabled')
                if target.is_file():
                    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(content).digest()
            app.assert_sentinels()
        assert tree(Path(metadata['import_source'])) == metadata['source_baseline']
        final_tree = tree(app.game)
        assert {key:final_tree[key] for key in metadata['baseline']} == metadata['baseline']
        # Native guarded execution may retain empty parent directories.
        assert all(value == '<directory>' for key,value in final_tree.items() if key not in metadata['baseline'])
        assert measured('profile.list', {'installation_id':app.installation})['profiles'] == []
        manager = app.context.jobs
        app.reopen()
        assert not any(job.thread.is_alive() for job in manager._jobs.values())
        assert not manager._subscriptions
        pages = [measured('mod.list', dict(installation_id=app.installation, offset=offset, limit=20))
                 for offset in (0,20,40,60)]
        states = {item['release_id']:item['state'] for page in pages for item in page['items']}
        assert len(states) == 61 and states[release['release_id']] == 'not_installed'
        assert all(states[r['release_id']] == 'enabled' for r in metadata['seeded_installed'])
        report['status'] = 'PASS'
    finally:
        if app is not None:
            manager = app.context.jobs
            app.close()
            report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in manager._jobs.values()),
                subscriptions=len(manager._subscriptions),
                named_workers=sum(t.name.startswith('PSModManager-job-') for t in threading.enumerate()))
            assert report['cleanup'] == dict(active_workers=0, subscriptions=0, named_workers=0)
        summary = {}
        for command in sorted({m['command'] for m in measurements}):
            values = [m['completion_ms'] for m in measurements if m['command']==command and 'completion_ms' in m]
            if values:
                summary[command] = dict(count=len(values), min_ms=min(values),
                    median_ms=statistics.median(values), max_ms=max(values))
        report['summary'] = summary
        (root/'performance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
