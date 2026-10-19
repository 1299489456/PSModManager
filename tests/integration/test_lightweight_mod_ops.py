"""Default Mod operations: ordinary failures, old receipts, and full-call cost."""
from functools import wraps
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from backend.deployments.journal import Journal
from backend.deployments.ordinary import POLICY
from backend.deployments.policy import PlanRejected
from tests.integration.harness.transaction_recovery import TransactionFixture, durable_json, tree
from tests.integration.test_mod_workspace import save, view, write


EVIDENCE = Path(__file__).resolve().parents[2]/'docs/development/evidence/20261015-lightweight-mod-ops'


@pytest.fixture
def app(tmp_path):
    game = EVIDENCE/'fixtures'/uuid4().hex/'synthetic-game'
    fixture = TransactionFixture(tmp_path, game_root=game)
    try:
        yield fixture
    finally:
        fixture.assert_sentinels()
        fixture.close()


def uninstall(app, identity):
    item = next(item for item in view(app)['items'] if item['id'] == identity)
    return app.call('mod.action', dict(installation_id=app.installation, release_id=item['release_id'],
        manifest_hash=item['manifest_hash'], action='uninstall', idempotency_key=str(uuid4())))


@pytest.mark.parametrize('count', [130, 1863])
def test_complete_save_and_uninstall_performance(app, monkeypatch, count):
    from backend.deployments.ordinary import OrdinaryMods
    from backend.deployments.mod_workspace import ModWorkspaceService
    from backend.deployments.planner import PlanService
    from backend.deployments.process_guard import ProcessGuard
    from backend.storage.package_repository import PackageRepository
    totals = {}
    def measure(cls, name):
        original = getattr(cls, name)
        @wraps(original)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                entry = totals.setdefault(cls.__name__+'.'+name, dict(calls=0, seconds=0.0))
                entry['calls'] += 1; entry['seconds'] += time.perf_counter()-started
        monkeypatch.setattr(cls, name, wrapped)
    for cls, names in ((OrdinaryMods, ('prepare', 'apply', 'metadata')), (ModWorkspaceService, ('_view', '_adopt')),
        (PlanService, ('_scan', '_generation')), (ProcessGuard, ('inspect',)), (Journal, ('append', 'load')),
        (PackageRepository, ('release', 'verify', 'copy', 'capture_ordinary'))):
        for name in names:
            measure(cls, name)
    write(app, 'Mods/Benchmark.dll', b'mod')
    for index in range(count-1):
        write(app, f'Mods/Benchmark/assets/{index:04}.png', f'unique-resource-{index}'.encode())
    identity = view(app)['items'][0]['id']
    elapsed = {}
    for name, operation in [('disable', lambda: save(app, [])), ('enable', lambda: save(app, [identity])),
                             ('uninstall', lambda: uninstall(app, identity)), ('reinstall', lambda: save(app, [identity])),
                             ('disable_again', lambda: save(app, [])), ('enable_again', lambda: save(app, [identity]))]:
        started = time.perf_counter()
        result = operation()
        elapsed[name] = round(time.perf_counter()-started, 3)
        assert result['completed'], result
    assert len(list((app.game/'Mods/Benchmark/assets').glob('*.png'))) == count-1
    assert totals.get('PlanService._scan', {}).get('calls', 0) == 0
    assert totals.get('PlanService._generation', {}).get('calls', 0) == 0
    # Per-action batches, not one durable record/process scan per resource.
    assert totals['Journal.append']['calls'] <= 28
    assert totals['ProcessGuard.inspect']['calls'] <= 24
    result = dict(file_count=count, full_call_seconds=elapsed,
        profile={k:dict(calls=v['calls'], seconds=round(v['seconds'],3)) for k,v in totals.items()},
        data_root=str(app.data), game_root=str(app.game))
    durable_json(app.root/'performance.json', result)
    output = EVIDENCE/f'performance-{count}-{uuid4().hex[:8]}.json'
    durable_json(output, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def test_ordinary_error_rolls_back_the_action_and_keeps_sources(app):
    write(app, 'Mods/Error.dll', b'mod')
    write(app, 'Mods/Error/a.png', b'asset')
    before = tree(app.game)
    def fault(point, details):
        if point == 'ordinary.file_applied':
            raise OSError('synthetic ordinary copy failure')
    app.context.deployments.fault = fault
    result = save(app, [])
    assert result['error_code'] == 'EXEC_IO_FAILED', result
    assert tree(app.game) == before
    assert not app.status()['recovery_required']
    app.context.deployments.fault = None
    assert save(app, [])['completed']


def test_ordinary_cancel_rolls_back_the_action(app):
    from backend.runtime.jobs import _Cancelled
    write(app, 'Mods/Cancel.dll', b'mod')
    write(app, 'Mods/Cancel/a.png', b'asset')
    before = tree(app.game)
    def fault(point, details):
        if point == 'ordinary.file_applied':
            raise _Cancelled()
    app.context.deployments.fault = fault
    with pytest.raises(_Cancelled):
        save(app, [])
    assert tree(app.game) == before
    assert not app.status()['recovery_required']


@pytest.mark.parametrize('point', ['ordinary.header', 'ordinary.prepared', 'ordinary.file_applied',
                                  'ordinary.commit_intent', 'ordinary.db_committed'])
def test_process_interruption_reconciles_whole_action(app, point):
    write(app, 'Mods/Interrupted.dll', b'mod')
    write(app, 'Mods/Interrupted/a.png', b'asset')
    initial = view(app)
    identity = initial['items'][0]['id']
    app.close()
    code = '''
import os,sys
from uuid import uuid4
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
context=DesktopContext(resolve_paths(sys.argv[1]));context.initialize()
def fault(point,details):
    if point==sys.argv[3]: os._exit(73)
context.deployments.fault=fault
view=context.mod_workspace.list(sys.argv[2])
context.mod_workspace.save(dict(installation_id=sys.argv[2],snapshot_id=view['snapshot_id'],enabled_ids=[],idempotency_key=str(uuid4())))
os._exit(74)
'''
    child = subprocess.run([sys.executable, '-c', code, str(app.data), app.installation, point], capture_output=True, timeout=30)
    assert child.returncode == 73, child.stderr.decode(errors='replace')
    app.reopen()
    assert not app.status()['recovery_required']
    state = next(item for item in view(app)['items'] if item['id'] == identity)['state']
    expected = 'disabled' if point in {'ordinary.commit_intent', 'ordinary.db_committed'} else 'enabled'
    assert state == expected
    assert (app.game/'Mods/Interrupted.dll').exists() == (state == 'enabled')
    assert (app.game/'Mods/Interrupted/a.png').exists() == (state == 'enabled')


def test_external_edit_during_action_is_kept_and_reported(app):
    write(app, 'Mods/External.dll', b'mod')
    write(app, 'Mods/External/a.png', b'asset')
    def fault(point, details):
        if point == 'ordinary.prepared':
            (app.game/'Mods/External.dll').write_bytes(b'player edit')
    app.context.deployments.fault = fault
    result = save(app, [])
    assert result['error_code'] == 'PLAN_MANAGED_DRIFT'
    assert (app.game/'Mods/External.dll').read_bytes() == b'player edit'
    assert app.status()['recovery_required']


def test_old_strict_record_and_pending_recovery_still_work(app):
    from tests.integration.harness.transaction_recovery import child_at
    _, _, payload, before, after = app.mixed()
    with child_at(app, payload, 'operation.published') as (process, _):
        process.kill(); process.wait(timeout=10)
    assert tree(app.game) in (before, after)
    assert not app.status()['recovery_required']
    app.reopen()
    write(app, 'Mods/Ordinary.dll', b'ordinary')
    value = view(app)
    identity = next(item['id'] for item in value['items'] if item['display_name']=='Ordinary')
    assert save(app, [i for i in value['enabled_ids'] if i != identity])['completed']
    assert save(app, value['enabled_ids'])['completed']


def test_ordinary_history_undo_uses_the_batch_backup(app):
    from tests.integration.test_rollback_drift import rollback
    write(app, 'Mods/Undo.dll', b'mod')
    assert save(app, [])['completed']
    transaction = app.status()['transaction']['transaction_id']
    header = Journal(app.data, transaction).load()[0]['data']
    assert header['execution_policy'] == POLICY
    rollback(app, transaction)
    assert (app.game/'Mods/Undo.dll').read_bytes() == b'mod'


def test_old_prepared_direct_action_keeps_its_original_executor(app):
    from datetime import datetime, timezone
    from uuid import uuid5
    from backend.contracts import Approval, Profile
    from backend.contracts.plans import ModAction, PlanProposal
    from backend.deployments.planner import TTL
    from backend.deployments.policy import native_approval_id
    release = app.publish({'Mods/Prepared.png': b'old prepared release'})
    request = ModAction(installation_id=app.installation, release_id=release['release_id'],
        manifest_hash=release['manifest_hash'], action='install', idempotency_key=uuid4())
    plans = app.context.plans
    with plans._store(lambda: None) as store:
        profile = Profile(profile_id=uuid5(request.idempotency_key, 'PSModManager.direct-mods.profile.v1'),
            installation_id=request.installation_id, revision=0, selections=[dict(release_id=request.release_id,
                manifest_hash=request.manifest_hash, selected=True)], config_overrides={}, display_order=[])
        store.put(profile)
        plan, baseline, _, _ = plans._calculate(store,
            PlanProposal(profile_id=profile.profile_id, expected_revision=0, idempotency_key=request.idempotency_key),
            plan_id=uuid5(request.idempotency_key, 'PSModManager.ActionPlan.v1'),
            expires_at=datetime.now(timezone.utc)+TTL, check=lambda: None,
            direct_binding=request.model_dump(mode='json') | dict(state_revision=0))
        store.put(baseline); store.plans.add(plan)
        store.plans.add(Approval(approval_id=native_approval_id(plans.paths.root, plan.plan_hash, create=True),
            plan_id=plan.plan_id, plan_hash=plan.plan_hash, actor='direct-action',
            scope=plan.approval_scope, expires_at=plan.expires_at))
    result = app.call('mod.action', request.model_dump(mode='json'))
    assert result['completed'], result
    assert (app.game/'Mods/Prepared.png').read_bytes() == b'old prepared release'
    header = Journal(app.data, app.status()['transaction']['transaction_id']).load()[0]['data']
    assert 'execution_policy' not in header


def test_ordinary_action_rejects_hardlink_without_changing_other_file(app):
    import os
    release = app.publish({'Mods/Linked.png': b'mod'})
    outside = app.game.parent/'outside-mod-target.png'
    outside.write_bytes(b'user file')
    os.link(outside, app.game/'Mods/Linked.png')
    result = app.call('mod.action', dict(installation_id=app.installation, release_id=release['release_id'],
        manifest_hash=release['manifest_hash'], action='install', idempotency_key=str(uuid4())))
    assert result['error_code'] == 'MOD_WORKSPACE_UNSAFE', result
    assert outside.read_bytes() == b'user file'


@pytest.mark.parametrize('point', ['before_header', 'partial_header'])
def test_header_creation_failure_does_not_leave_recovery_block(app, monkeypatch, point):
    import errno
    import backend.deployments.journal as journal_module
    write(app, 'Mods/Header.dll', b'mod')
    before = tree(app.game)
    create = Journal.create
    def fail_before_header(*args, **kwargs):
        if point == 'partial_header':
            original = journal_module._write_bytes
            def fail_write(handle, data):
                original(handle, data[:10])
                raise OSError(errno.ENOSPC, 'synthetic partial journal write')
            with monkeypatch.context() as scoped:
                scoped.setattr(journal_module, '_write_bytes', fail_write)
                return create(*args, **kwargs)
        raise OSError(errno.ENOSPC, 'synthetic journal creation failure')
    monkeypatch.setattr(Journal, 'create', fail_before_header)
    result = save(app, [])
    assert result['error_code'] == 'PLAN_SPACE_INSUFFICIENT', result
    assert tree(app.game) == before
    assert not app.status()['recovery_required']
    monkeypatch.setattr(Journal, 'create', create)
    app.reopen()
    assert not app.status()['recovery_required']
    assert save(app, [])['completed']
