"""CASE-029..033: real product execution, durable I/O and owned process crashes.

Checkpoint ENOSPC injection, actual Windows sharing/DACL failures, and process
termination are separately recorded. None is hardware power-loss evidence.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

import pytest
import pywintypes
import win32api
import win32con
import win32file
import win32security

from backend.deployments.policy import check_value
from backend.deployments.paths import GuardRejected
from backend.games.probably_stolen.layout import ASSEMBLY
from tests.integration.harness.transaction_recovery import (
    FAULT_POINTS, ROOTS, TransactionFixture, candidates, child_at, database_snapshot,
    durable_json, finish, request, tree,
)

pytestmark = pytest.mark.skipif(sys.platform!='win32',reason='Durable executor acceptance requires Windows primitives')


@pytest.fixture
def app(tmp_path_factory,request):
    # Evidence remains under the caller's basetemp; a short directory name
    # keeps immutable library leaves within the product's 240-character policy.
    fixture = TransactionFixture(tmp_path_factory.mktemp('tx'))
    durable_json(fixture.root / 'case.json',{'nodeid':request.node.nodeid})
    try:
        yield fixture
    finally:
        try:
            fixture.assert_sentinels()
        finally:
            fixture.close()


def assert_database(app):
    snapshot = database_snapshot(app.data / 'psmodmanager.sqlite3')
    assert snapshot['quick_check']==[('ok',)] and snapshot['foreign_key_check']==[]
    return snapshot['entities']


def assert_committed(app, payload, expected):
    from backend.deployments.journal import Journal
    status = app.status(payload['plan_id'])
    assert status['transaction']['state']=='committed',status
    assert status['verification_status']=='not_run' and not status['recovery_required'],status
    assert tree(app.game)==expected
    entities = assert_database(app)
    transaction = next(item for item in entities['transactions'] if item['plan_id']==payload['plan_id'])
    generation = next(item for item in entities['deployment_generations'] if item['transaction_id']==transaction['transaction_id'])
    plan = app.context.store.get('ActionPlan',payload['plan_id'])
    assert generation['manifest_hash']==check_value(plan,'result_manifest')
    assert generation['generation_id']==transaction['target_generation']
    pointer = app.context.store.connection.execute('SELECT * FROM deployment_current WHERE installation_id=?',
        (app.installation,)).fetchone()
    assert pointer['generation_id']==status['current_generation']==generation['generation_id']
    installation = app.context.store.get('Installation',app.installation)
    slot = hashlib.sha256(installation.file_identity.encode('ascii')).hexdigest()
    manifest_bytes = (app.data / 'deployments/installations' / slot / 'current.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    assert hashlib.sha256(manifest_bytes).hexdigest()==pointer['manifest_file_hash']
    assert manifest['manifest_hash']==pointer['manifest_hash']==generation['manifest_hash']
    assert manifest['generation_id']==generation['generation_id'] and manifest['transaction_id']==transaction['transaction_id']
    assert manifest['owned_files']==json.loads(check_value(plan,'owned_after'))
    records = Journal(app.data,transaction['transaction_id']).load()
    assert any(item['kind']=='committed' for item in records) and records[-1]['sequence']==transaction['last_sequence']
    assert records[0]['data']['after']==manifest['files']
    return transaction,generation


def assert_recovered(app, before, after):
    status = app.status()
    assert not status['recovery_required'],status
    assert tree(app.game) in (before,after),'Recovery left a partial game tree'
    entities = assert_database(app)
    assert all(item['state'] in {'committed','rolled_back','cancelled','failed'} for item in entities['transactions']),entities
    for generation in entities['deployment_generations']:
        transaction = next(item for item in entities['transactions'] if item['transaction_id']==generation['transaction_id'])
        assert transaction['state']=='committed' and transaction['target_generation']==generation['generation_id']
    assert status['verification_status']=='not_run'
    return status


def test_case029_actual_user_and_ai_apply_replay_once_and_true_generation(app):
    _,preview,payload,before,after = app.mixed()
    assert preview['plan']['base_generation'] is None and before!=after
    result = app.call('plan.apply',payload)
    assert not result.get('error_code'),result
    transaction,generation = assert_committed(app,payload,after)
    files = {p.relative_to(app.game).as_posix():(p.stat().st_ino,p.stat().st_mtime_ns)
             for p in app.game.rglob('*') if p.is_file()}
    for command in ('plan.apply','assistant.apply_plan','plan.apply'):
        retry = app.call(command,payload)
        assert not retry.get('error_code'),retry
        assert assert_committed(app,payload,after)==(transaction,generation)
        assert len(app.context.store.list('Transaction'))==len(app.context.store.list('DeploymentGeneration'))==1
        assert {p.relative_to(app.game).as_posix():(p.stat().st_ino,p.stat().st_mtime_ns)
                for p in app.game.rglob('*') if p.is_file()}==files
    app.reopen()
    assert assert_committed(app,payload,after)==(transaction,generation)
    app.evidence('case029-real-replay')


def test_case029_actual_second_generation_and_noop_have_no_fake_commits(app):
    a = app.publish({'Mods/shared.png':b'shared'},name='Owner A')
    b = app.publish({'Mods/shared.png':b'shared'},name='Owner B')
    state = app.profile([a,b]); first,payload = app.approved(state)
    before = tree(app.game)
    after = dict(before,**{'Mods/shared.png':hashlib.sha256(b'shared').hexdigest()})
    assert not app.call('plan.apply',payload).get('error_code')
    assert_committed(app,payload,after)
    state = app.save(app.select(state,a,False))
    owners,owners_payload = app.approved(state)
    assert not owners['no_op'] and owners_payload['approval_id']
    owner_plan = app.context.store.get('ActionPlan',owners_payload['plan_id'])
    assert len(owner_plan.operations)==1 and owner_plan.operations[0].kind=='replace_owned'
    assert owner_plan.operations[0].expected_before==owner_plan.operations[0].desired_after
    assert [str(owner) for owner in owner_plan.operations[0].owner_ids]==[b['release_id']]
    assert not app.call('plan.apply',owners_payload).get('error_code')
    assert_committed(app,owners_payload,after)
    # Identical bytes with a different owner set is a real generation. Only
    # replaying the already-applied owner selection is a genuine no-op.
    noop,no_payload = app.approved(state)
    assert noop['no_op'] and no_payload['approval_id'] is None
    response = app.call('plan.apply',no_payload)
    assert not response.get('error_code'),response
    assert tree(app.game)==after and len(app.context.store.list('Transaction'))==len(app.context.store.list('DeploymentGeneration'))==2
    state = app.save(app.select(state,b,False))
    second,second_payload = app.approved(state)
    assert second['plan']['base_generation'] is not None and not second['no_op']
    # Obtain the real operation from the stored contract rather than depending
    # on visible row order.
    plan = app.context.store.get('ActionPlan',second_payload['plan_id'])
    assert len(plan.operations)==1 and plan.operations[0].kind=='remove_owned'
    assert not app.call('assistant.apply_plan',second_payload).get('error_code')
    assert_committed(app,second_payload,before)
    assert [item.sequence for item in sorted(app.context.store.list('DeploymentGeneration'),key=lambda item:item.sequence)]==[1,2,3]
    app.evidence('case029-real-second-generation')


@pytest.mark.parametrize('drift',['profile','dirty_draft','target_same_size_mtime','fingerprint',
    'approval_actor','approval_scope','approval_missing','approval_expired','plan_hash','plan_operation','library_bytes'])
def test_case029_actual_executor_revalidates_before_any_game_write(app,drift):
    state,preview,payload,_,_ = app.mixed()
    store = app.context.store
    if drift in {'profile','dirty_draft'}:
        state = app.call('profile.edit',{'profile_id':state['profile_id'],'expected_revision':state['revision'],
            'action':{'op':'rename','name':'Changed after approval'}})
        if drift=='profile': app.save(state)
    elif drift=='target_same_size_mtime':
        import os
        target = app.game / 'Mods/replace.png'; stamp = target.stat()
        target.write_bytes(b'bad-replace'); os.utime(target,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        assert target.stat().st_size==stamp.st_size and target.stat().st_mtime_ns==stamp.st_mtime_ns
    elif drift=='fingerprint':
        target = app.game / ASSEMBLY
        body = bytearray(target.read_bytes()); body[-1]^=1; target.write_bytes(body)
        # This intentional fixture mutation is outside the deployable roots.
        app.sentinels = {key:value for key,value in tree(app.game).items()
                         if not key.startswith(('Mods/','Plugins/','UserLibs/'))}
    elif drift=='approval_missing':
        payload = dict(payload,approval_id=str(uuid4()))
    elif drift=='plan_hash':
        payload = dict(payload,plan_hash='0'*64)
    elif drift=='plan_operation':
        plan = store.get('ActionPlan',payload['plan_id']).model_dump(mode='json')
        plan['operations'][0]['relative_path']='foreign.json'
        store.connection.execute('UPDATE action_plans SET payload=? WHERE plan_id=?',(json.dumps(plan),payload['plan_id']))
    elif drift=='library_bytes':
        artifact = next(item for item in store.list('Artifact') if item.size==len(b'new-replace'))
        path = app.data / 'library' / artifact.library_relative_path
        assert path.resolve().is_relative_to(app.data.resolve())
        path.write_bytes(b'bad-replace')
    else:
        approval = store.get('Approval',payload['approval_id']).model_dump(mode='json')
        if drift=='approval_actor': approval['actor']='ai'
        elif drift=='approval_scope': approval['scope']=['install_owned']
        else: approval['expires_at']='2000-01-01T00:00:00Z'
        store.connection.execute('UPDATE approvals SET payload=? WHERE approval_id=?',(json.dumps(approval),payload['approval_id']))
    before = tree(app.game)
    result = app.call('plan.apply',payload)
    assert result.get('error_code'),result
    assert tree(app.game)==before and not store.list('DeploymentGeneration')
    assert all(item.last_sequence==0 for item in store.list('Transaction'))
    app.evidence('case029-rejected-'+drift)


def test_case029_actual_executor_rechecks_capacity_with_controlled_low_space(app,monkeypatch):
    import shutil
    _,_,payload,_,_ = app.mixed()
    before = tree(app.game)
    actual = shutil.disk_usage(app.data)
    probes = []
    def low_space(path):
        probes.append(str(path))
        return type(actual)(actual.total,actual.total-1,1)
    with monkeypatch.context() as patch:
        patch.setattr(shutil,'disk_usage',low_space)
        result = app.call('plan.apply',payload)
    assert probes and result.get('error_code'),result
    assert tree(app.game)==before and not app.context.store.list('DeploymentGeneration')
    durable_json(app.root / 'capacity-control.json',{'layer':'controlled OS-capacity result, not a physically full volume',
        'probes':probes,'free_bytes':1,'result':result})
    app.evidence('case029-capacity-rechecked')


@pytest.mark.parametrize('point',FAULT_POINTS)
def test_case030_case031_case033_real_process_kill_at_every_persistent_boundary(app,point):
    _,_,payload,before,after = app.mixed()
    allowed_candidates = set(candidates(app.game)) | {'UserLibs/add.png'}
    with child_at(app,payload,point) as (process,boundary):
        assert set(boundary['candidates'])<=allowed_candidates,'Private staging/old bytes became scan candidates'
        if point in FAULT_POINTS[:9]:
            assert boundary['game']==before,'Game changed before verified backup/staging and ready'
        process.kill()
        process.wait(timeout=10)
        assert process.returncode!=0
        app.evidence('case031-before-restart-'+point.replace('.','-'))
    status = assert_recovered(app,before,after)
    if point in ('db.committed','journal.committed','cleanup.before','cleanup.after'):
        assert tree(app.game)==after and status['transaction']['state']=='committed',status
    assert set(candidates(app.game))<=allowed_candidates
    first = app.evidence('case031-first-restart-'+point.replace('.','-'))
    app.reopen()
    assert_recovered(app,before,after)
    second = app.evidence('case031-second-restart-'+point.replace('.','-'))
    assert first['game']==second['game'] and first['database']['entities']==second['database']['entities']


@pytest.mark.parametrize('point',['operation.intent','operation.evacuated','operation.published','operation.done'])
def test_case031_replacing_an_owned_file_crash_restores_whole_generation(app,point):
    target = app.game / 'Mods/only-replace.png'; target.write_bytes(b'old')
    app.call('inventory.scan',{'installation_id':app.installation,'force':True})
    snap = app.call('package.adopt_preview',{'installation_id':app.installation})
    adopted = app.call('package.adopt',{'snapshot_id':snap['snapshot_id'],'manifest_hash':snap['manifest_hash'],
        'paths':['Mods/only-replace.png'],'display_name':'Original'})
    assert not adopted.get('error_code'),adopted
    state = app.profile([app.publish({'Mods/only-replace.png':b'new'})])
    _,payload = app.approved(state)
    before = tree(app.game); after = dict(before,**{'Mods/only-replace.png':hashlib.sha256(b'new').hexdigest()})
    with child_at(app,payload,point) as (process,_):
        process.kill(); process.wait(timeout=10)
    assert_recovered(app,before,after)
    app.evidence('case031-owned-replace-'+point.replace('.','-'))


@pytest.mark.parametrize('point',['journal.header','backup.write','backup.flush','stage.write','stage.flush',
    'operation.intent','operation.published','manifest.prepared','manifest.published','db.before_commit'])
def test_case032_checkpoint_enospc_is_bounded_and_preserves_recovery_information(app,point):
    _,_,payload,before,after = app.mixed()
    attempted = []
    def fault(actual,details):
        if actual==point:
            attempted.append(dict(details))
            raise OSError(errno.ENOSPC,'Controlled checkpoint disk-full fault')
    app.context.deployments.fault = fault
    start = time.monotonic()
    result = app.call('plan.apply',payload)
    elapsed = time.monotonic()-start
    # The real API task wait is bounded at 45s. Durable NTFS copies and rollback
    # can exceed an arbitrary 15s under I/O contention without retrying the fault.
    durable_json(app.root / 'io-fault.json',{'layer':'trusted checkpoint hook; not a physically full volume',
        'point':point,'errno':errno.ENOSPC,'attempts':len(attempted),'elapsed':elapsed,'result':result})
    assert attempted and len(attempted)<=8 and elapsed<45,(point,attempted,elapsed,result)
    if result.get('error_code'):
        assert result['io_error']['kind']=='disk_full' and result['io_error']['errno']==errno.ENOSPC,result
        assert result['io_error']['native_error'] is None and 1<=result['io_error']['attempts']<=8,result
    else:
        # A sealed commit_intent may be completed during internal recovery.
        assert result['transaction']['state']=='committed' and result['deployed'] is True,result
    app.context.deployments.fault = None
    app.reopen()
    assert_recovered(app,before,after)
    assert set(candidates(app.game))<=set(before)|{'UserLibs/add.png'}
    app.evidence('case032-enospc-'+point.replace('.','-'))


def test_case032_actual_windows_sharing_violation_refuses_overwrite(app):
    _,_,payload,before,_ = app.mixed()
    target = app.game / 'Mods/replace.png'
    handle = win32file.CreateFile(str(target),win32con.GENERIC_READ,0,None,win32con.OPEN_EXISTING,0,None)
    try:
        with pytest.raises(pywintypes.error) as control:
            win32file.CreateFile(str(target),win32con.GENERIC_READ,win32con.FILE_SHARE_READ,None,win32con.OPEN_EXISTING,0,None)
        assert control.value.winerror==32
        start = time.monotonic(); result = app.call('plan.apply',payload)
        assert result.get('error_code') and time.monotonic()-start<15,result
        assert result['io_error']['kind']=='file_busy' and result['io_error']['native_error']==32,result
        assert 1<=result['io_error']['attempts']<=3,result
        durable_json(app.root / 'windows-sharing-error.json',{'layer':'actual Windows CreateFile sharing control and product RPC',
            'control_native_error':32,'result':result})
    finally:
        handle.Close()
    assert tree(app.game)==before and not app.context.store.list('DeploymentGeneration')
    app.evidence('case032-real-sharing-violation')


def test_case032_actual_file_dacl_denial_refuses_write_without_elevation(app):
    _,_,payload,before,_ = app.mixed()
    target = str(app.game / 'Mods/replace.png')
    original = win32security.GetFileSecurity(target,win32security.DACL_SECURITY_INFORMATION)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    try:
        user = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
    finally:
        token.Close()
    # Deny file data read/write/delete, keep WRITE_DAC to restore only this
    # owned fixture's original DACL even when running without administrator.
    deny = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        'D:(D;;0x00010003;;;'+user+')(A;;FA;;;'+user+')',win32security.SDDL_REVISION_1)
    try:
        win32security.SetFileSecurity(target,win32security.DACL_SECURITY_INFORMATION,deny)
        with pytest.raises(pywintypes.error) as control:
            win32file.CreateFile(target,win32con.GENERIC_READ,win32con.FILE_SHARE_READ,None,win32con.OPEN_EXISTING,0,None)
        assert control.value.winerror==5
        result = app.call('plan.apply',payload)
        assert result.get('error_code'),result
        assert result['io_error']['kind']=='permission_denied' and result['io_error']['native_error']==5,result
        assert result['io_error']['attempts']==1,result
        durable_json(app.root / 'windows-dacl-error.json',{'layer':'actual owned-file Windows DACL and product RPC',
            'control_native_error':5,'result':result})
    finally:
        win32security.SetFileSecurity(target,win32security.DACL_SECURITY_INFORMATION,original)
    assert tree(app.game)==before and not app.context.store.list('DeploymentGeneration')
    app.evidence('case032-real-file-permission-denial')


@pytest.mark.parametrize('restriction',['sharing','write_permission'])
def test_case032_actual_replace_is_refused_after_intent_and_prior_change_is_recovered(app,restriction):
    from backend.deployments.journal import Journal
    _,_,payload,before,_ = app.mixed()
    plan = app.context.store.get('ActionPlan',payload['plan_id'])
    operation = next(item for item in plan.operations if item.kind=='replace_owned')
    target = str(app.game / ROOTS[operation.target_root_kind] / operation.relative_path)
    expected_native = 32 if restriction=='sharing' else 5
    controls,handles = [],[]
    original_dacl = win32security.GetFileSecurity(target,win32security.DACL_SECURITY_INFORMATION)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    try:
        user = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
    finally:
        token.Close()
    write_denied = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        'D:(D;;0x00010002;;;'+user+')(A;;FA;;;'+user+')',win32security.SDDL_REVISION_1)
    def restrict_after_intent(point,details):
        if point!='operation.intent' or details.get('operation_id')!=str(operation.operation_id):
            return
        if restriction=='sharing':
            handles.append(win32file.CreateFile(target,win32con.GENERIC_READ,win32con.FILE_SHARE_READ,
                None,win32con.OPEN_EXISTING,0,None))
        else:
            # Read/hash stays possible; the backend replacement's requested
            # GENERIC_WRITE|DELETE access is denied by actual file permissions.
            win32security.SetFileSecurity(target,win32security.DACL_SECURITY_INFORMATION,write_denied)
        with pytest.raises(pywintypes.error) as control:
            probe = win32file.CreateFile(target,win32con.GENERIC_READ|win32con.GENERIC_WRITE|win32con.DELETE,win32con.FILE_SHARE_READ,
                None,win32con.OPEN_EXISTING,0,None)
            probe.Close()
        assert control.value.winerror==expected_native
        controls.append(dict(details,native_error=control.value.winerror))
    app.context.deployments.fault = restrict_after_intent
    try:
        result = app.call('plan.apply',payload)
        assert controls and result.get('error_code'),result
        assert result['io_error']['native_error']==expected_native,result
        assert result['io_error']['kind']==('file_busy' if restriction=='sharing' else 'permission_denied'),result
        assert 1<=result['io_error']['attempts']<=(3 if restriction=='sharing' else 1),result
        initial = app.status(payload['plan_id'])
        assert initial['transaction']['state'] in {'rolled_back','recovery_required'},initial
        durable_json(app.root / 'late-replacement-error.json',{'layer':'actual Windows restriction after durable product intent',
            'restriction':restriction,'controls':controls,'result':result,'initial_status':initial,'initial_game':tree(app.game)})
    finally:
        app.context.deployments.fault = None
        for handle in handles:
            handle.Close()
        if restriction=='write_permission':
            win32security.SetFileSecurity(target,win32security.DACL_SECURITY_INFORMATION,original_dacl)
    if app.status()['recovery_required']:
        repaired = app.call('deployment.recover',{'installation_id':app.installation})
        assert not repaired['recovery_required'],repaired
    assert tree(app.game)==before and not app.context.store.list('DeploymentGeneration')
    transaction = app.status(payload['plan_id'])['transaction']
    records = Journal(app.data,transaction['transaction_id']).load()
    assert transaction['state']=='rolled_back' and any(item['kind']=='done' for item in records)
    assert any(item['kind']=='intent' and item['data']['operation_id']==str(operation.operation_id) for item in records)
    assert any(item['kind']=='backup' and item['data']['operation_id']==str(operation.operation_id) for item in records)
    assert any(item['kind']=='stage' and item['data']['operation_id']==str(operation.operation_id) for item in records)
    retained_originals(app,payload)
    app.reopen()
    assert tree(app.game)==before and not app.status()['recovery_required']
    app.evidence('case032-late-replacement-'+restriction)


@pytest.mark.parametrize('point',['transaction.created','stage.flush','transaction.ready','operation.published','operation.done','commit.intent'])
def test_case030_case032_product_job_cancel_rolls_back_applying_instead_of_half_success(app,point):
    _,_,payload,before,_ = app.mixed()
    reached,release = threading.Event(),threading.Event()
    def pause(actual,details):
        if actual==point and not reached.is_set():
            reached.set()
            assert release.wait(15),'Owned cancellation control timed out'
    app.context.deployments.fault = pause
    response = app.context.api.dispatch(request('plan.apply',payload))
    assert response['ok'],response
    try:
        assert reached.wait(15),'Product did not reach cancellation boundary '+point
        cancelled = app.context.api.dispatch(request('task.cancel',{'task_id':response['data']['task_id']}))
        assert cancelled['ok'],cancelled
    finally:
        release.set()
    terminal = finish(app.context.api,response['data'])
    assert terminal['state'] in {'cancelled','succeeded'},terminal
    app.context.deployments.fault = None
    status = app.status(payload['plan_id'])
    assert status['transaction']['state'] in {'cancelled','rolled_back'},status
    if point.startswith('operation.') or point=='commit.intent':
        assert status['transaction']['state']=='rolled_back',status
    assert not status['recovery_required'] and tree(app.game)==before
    assert not app.context.store.list('DeploymentGeneration')
    app.reopen()
    assert not app.status()['recovery_required'] and tree(app.game)==before
    app.evidence('case032-cancel-'+point.replace('.','-'))


def test_case030_external_change_after_real_crash_is_preserved_and_blocks_recovery(app):
    state,_,payload,_,_ = app.mixed()
    plan = app.context.store.get('ActionPlan',payload['plan_id'])
    with child_at(app,payload,'operation.published') as (process,boundary):
        process.kill(); process.wait(timeout=10)
        operation = next(item for item in plan.operations if str(item.operation_id)==boundary['details']['operation_id'])
        target = app.game / ROOTS[operation.target_root_kind] / operation.relative_path
        target.write_bytes(b'external-owner-after-crash')
        changed = tree(app.game)
    status = app.status()
    assert status['recovery_required'] and status['blockers'],status
    assert target.read_bytes()==b'external-owner-after-crash' and tree(app.game)==changed
    assert not app.context.store.list('DeploymentGeneration')
    conflicts = app.data / 'deployments/transactions' / status['transaction']['transaction_id'] / 'conflicts'
    copies = [path for path in conflicts.glob('*.bin') if path.read_bytes()==b'external-owner-after-crash']
    assert copies and all(path.resolve().is_relative_to(app.data.resolve()) for path in copies)
    plan_count = len(app.context.store.list('ActionPlan'))
    retry = app.call('plan.apply',payload)
    assert retry.get('error_code') and tree(app.game)==changed,retry
    proposed = app.call('plan.create',{'profile_id':state['profile_id'],
        'expected_revision':state['profile_revision'],'idempotency_key':str(uuid4())})
    assert proposed.get('error_code') and len(app.context.store.list('ActionPlan'))==plan_count,proposed
    # This is the real shared launch guard. The later game-launch consumer and
    # runtime load/verification remain outside this card's evidence.
    with pytest.raises(GuardRejected) as launch:
        with app.context.deployment_guards.operation(app.installation,intent='launch'):
            raise AssertionError('Pending recovery opened the launch guard')
    assert launch.value.code=='GUARD_RECOVERY_REQUIRED'
    first = app.evidence('case030-external-conflict')
    result = app.call('deployment.recover',{'installation_id':app.installation})
    assert app.status()['recovery_required'],result
    app.reopen()
    assert tree(app.game)==changed and app.status()['recovery_required']
    assert first['database']['entities']['deployment_generations']==[]


@pytest.mark.parametrize('mutation',['claim_committed','missing_transaction','truncated_journal'])
def test_case033_recovery_reconciles_real_journal_manifest_and_sqlite_instead_of_trusting_db(app,mutation):
    _,_,payload,before,after = app.mixed()
    point = 'operation.published' if mutation=='claim_committed' else 'manifest.published'
    with child_at(app,payload,point) as (process,boundary):
        process.kill(); process.wait(timeout=10)
        transaction = next(item for item in boundary['database']['entities']['transactions'] if item['plan_id']==payload['plan_id'])
        import sqlite3
        with sqlite3.connect(app.data / 'psmodmanager.sqlite3') as db:
            if mutation=='claim_committed':
                forged = dict(transaction,state='committed')
                db.execute('UPDATE transactions SET payload=? WHERE transaction_id=?',(json.dumps(forged),transaction['transaction_id']))
            elif mutation=='missing_transaction':
                db.execute('DELETE FROM transactions WHERE transaction_id=?',(transaction['transaction_id'],))
            else:
                path = app.data / transaction['journal_path']
                assert path.resolve().is_relative_to(app.data.resolve()) and path.is_file()
                path.write_bytes(b'{"truncated":')
    status = app.status()
    if mutation=='truncated_journal':
        assert status['recovery_required'] and tree(app.game)==after,status
    else:
        assert_recovered(app,before,after)
    first = app.evidence('case033-reconciled-'+mutation)
    app.reopen()
    second = app.evidence('case033-repeated-'+mutation)
    assert first['game']==second['game'] and first['database']['entities']==second['database']['entities']


def test_case030_real_compiled_dll_and_framework_candidates_never_include_private_copies(app):
    from tests.integration.test_content_catalog import compile_fixture
    original = compile_fixture(app.root / 'self-authored-old-clr','OwnedDeploymentFixture',version='1.0.0.0',melon=False)
    artifact = compile_fixture(app.root / 'self-authored-new-clr','OwnedDeploymentFixture',version='2.0.0.0',melon=False)
    assert original.read_bytes()!=artifact.read_bytes()
    (app.game / 'Mods/OwnedDeploymentFixture.dll').write_bytes(original.read_bytes())
    app.call('inventory.scan',{'installation_id':app.installation,'force':True})
    preview = app.call('package.adopt_preview',{'installation_id':app.installation})
    adopted = app.call('package.adopt',{'snapshot_id':preview['snapshot_id'],'manifest_hash':preview['manifest_hash'],
        'paths':['Mods/OwnedDeploymentFixture.dll'],'display_name':'Self-authored owned DLL'})
    assert not adopted.get('error_code'),adopted
    release = app.publish({'Mods/OwnedDeploymentFixture.dll':artifact.read_bytes(),
                           'Mods/ItemForge-owned.json':b'{"Items":[]}'})
    state = app.profile([release]); _,payload = app.approved(state)
    allowed = set(candidates(app.game))|{'Mods/OwnedDeploymentFixture.dll','Mods/ItemForge-owned.json'}
    observations = []
    def observe(point,details):
        observed = candidates(app.game)
        assert set(observed)<=allowed,'Staged/backup DLL or framework resource leaked into a scan tree'
        observations.append({'point':point,'candidates':observed})
    app.context.deployments.fault = observe
    result = app.call('plan.apply',payload)
    assert not result.get('error_code'),result
    assert observations and app.status()['transaction']['state']=='committed'
    durable_json(app.root / 'candidate-proof.json',{'scope':'Complete enumeration of supported scan roots; no runtime loading claim',
        'observations':observations,'power_loss':'NOT_RUN','physical_disk_full':'NOT_RUN'})
    app.evidence('case030-compiled-dll-candidates')


@pytest.mark.parametrize('folder',['stage','backup'])
def test_case030_safe_layout_refuses_private_junction_into_loader_tree(app,folder):
    from tests.integration.test_deployment_guards import junction
    _,_,payload,before,_ = app.mixed()
    planted = []
    def poison_layout(point,details):
        if point=='transaction.created':
            area = app.data / 'deployments/transactions' / details['transaction_id']
            area.mkdir(parents=True)
            link = area / folder
            assert link.parent.resolve().is_relative_to(app.data.resolve())
            junction(link,app.game / 'Mods')
            planted.append(link)
    app.context.deployments.fault = poison_layout
    try:
        result = app.call('plan.apply',payload)
        assert planted and result.get('error_code')=='GUARD_REPARSE_POINT',result
        assert tree(app.game)==before and not app.context.store.list('DeploymentGeneration')
        durable_json(app.root / 'unsafe-layout.json',{'layer':'real Windows junction into supported scan tree',
            'private_folder':folder,'link':str(planted[0]),'destination':str(app.game / 'Mods'),'result':result})
    finally:
        app.context.deployments.fault = None
        for link in planted:
            assert link.parent.resolve().is_relative_to(app.data.resolve())
            os.rmdir(link)  # Remove only our exact junction; never traverse its target.
    app.reopen()
    assert tree(app.game)==before and not app.status()['recovery_required']
    app.evidence('case030-unsafe-layout-'+folder)


def retained_originals(app,payload):
    """Verify executor backups/displaced originals, excluding package-library copies."""
    plan = app.context.store.get('ActionPlan',payload['plan_id'])
    transaction = app.status(payload['plan_id'])['transaction']
    area = app.data / 'deployments/transactions' / transaction['transaction_id']
    assert area.resolve().is_relative_to(app.data.resolve())
    result = {}
    for operation in plan.operations:
        if operation.expected_before=='absent':
            continue
        for folder in ('backup','displaced'):
            path = area / folder / (str(operation.operation_id)+'.bin')
            if folder=='displaced' and not path.exists():
                continue  # An operation not yet evacuated has only its verified backup.
            assert path.is_file(),path
            assert hashlib.sha256(path.read_bytes()).hexdigest()==operation.expected_before,path
            result[path.relative_to(app.data).as_posix()] = {
                'sha256':operation.expected_before,'identity':path.stat().st_ino,'mtime_ns':path.stat().st_mtime_ns}
    assert result,'No executor-owned original backup retained'
    return result


@pytest.mark.parametrize('point',['cleanup.before','cleanup.after'])
def test_case030_cleanup_fault_stays_committed_and_retains_original_backups(app,point):
    from backend.deployments.journal import Journal
    _,_,payload,_,after = app.mixed()
    attempts = []
    def cleanup_fault(actual,details):
        if actual==point:
            attempts.append(dict(details))
            raise OSError(errno.ENOSPC,'Controlled cleanup fault')
    app.context.deployments.fault = cleanup_fault
    result = app.call('plan.apply',payload)
    assert attempts and not result.get('error_code'),result
    transaction,_ = assert_committed(app,payload,after)
    saved = retained_originals(app,payload)
    plan = app.context.store.get('ActionPlan',payload['plan_id'])
    assert len(saved)==2*sum(operation.expected_before!='absent' for operation in plan.operations)
    tail = Journal(app.data,transaction['transaction_id']).load()[-1]
    assert tail['kind']=='cleanup' and tail['data']['status']=='pending',tail
    durable_json(app.root / 'cleanup-retention.json',{'point':point,'fault_layer':'trusted checkpoint hook',
        'retained':saved,'result':result,'journal_tail':tail})
    app.context.deployments.fault = None
    app.reopen()
    assert_committed(app,payload,after)
    assert retained_originals(app,payload)==saved
    first = app.evidence('case030-cleanup-restarted-'+point.replace('.','-'))
    app.reopen()
    second = app.evidence('case030-cleanup-repeated-'+point.replace('.','-'))
    assert first['game']==second['game'] and first['database']['entities']==second['database']['entities']


@pytest.mark.parametrize('point',['recovery.before_restore','recovery.after_restore'])
def test_case030_recovery_fault_remains_pending_then_real_retry_restores_originals(app,point):
    _,_,payload,before,_ = app.mixed()
    attempts = []
    def recovery_fault(actual,details):
        if actual==point:
            attempts.append(dict(details))
            raise OSError(errno.EIO,'Controlled restore checkpoint failure')
    with child_at(app,payload,'operation.published',restart_fault=recovery_fault) as (process,_):
        process.kill(); process.wait(timeout=10)
    status = app.status(payload['plan_id'])
    assert attempts and status['recovery_required'] and status['startup_recovery']['pending_count']>=1,status
    assert status['transaction']['state']=='recovery_required' and not app.context.store.list('DeploymentGeneration')
    original_copies = retained_originals(app,payload)
    durable_json(app.root / 'restore-fault.json',{'point':point,'fault_layer':'trusted recovery checkpoint hook',
        'attempts':attempts,'status':status,'retained':original_copies})
    app.context.deployments.fault = None
    result = app.call('deployment.recover',{'installation_id':app.installation})
    assert not result['recovery_required'] and tree(app.game)==before,result
    assert retained_originals(app,payload)==original_copies
    first = app.evidence('case030-recovery-retry-'+point.replace('.','-'))
    app.reopen()
    assert not app.status()['recovery_required'] and tree(app.game)==before
    second = app.evidence('case030-recovery-retry-restarted-'+point.replace('.','-'))
    assert first['game']==second['game'] and first['database']['entities']==second['database']['entities']
