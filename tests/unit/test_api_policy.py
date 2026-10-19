"""Closed bridge policy and actual application job consumers, synthetic only."""
import hashlib
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi, Empty
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths


def request(command, payload=None, **changes):
    result = dict(schema_version=1, request_id=str(uuid4()), command=command, payload={} if payload is None else payload)
    result.update(changes)
    return result


@pytest.fixture
def active(tmp_path):
    context = DesktopContext(resolve_paths(str(tmp_path/'data')))
    context.initialize()
    context.api = DesktopApi(context)
    try:
        yield context, context.api
    finally:
        context.close()


def terminal(api, task_id):
    deadline = time.monotonic()+5
    while time.monotonic()<deadline:
        result=api.dispatch(request('task.snapshot', {'task_id': task_id}))
        assert result['ok'],result
        if result['data']['state'] in ('succeeded','failed','cancelled'):
            return result['data']
        time.sleep(.01)
    raise AssertionError('Job did not finish')


@pytest.mark.parametrize('command', ['__dict__','_app_info','os.system','shell.execute','evidence.delete','game.stop','test.probe','task.__class__'])
def test_only_explicit_commands_exist(active,command):
    _,api=active
    result=api.dispatch(request(command))
    assert result['error']['code']=='UNSUPPORTED_COMMAND'
    assert [name for name in dir(api) if not name.startswith('_')]==['dispatch']


@pytest.mark.parametrize('message', [None, [], {},
    request('app.info',schema_version=True), request('app.info',schema_version=2),
    request('app.info',schema_version='1'), request('app.info',request_id=''),
    request('app.info',request_id='\nprivate'), request('app.info',request_id='x'*129),
    request('app.info',extra='ignored?'), request('app.info',expected_revision=1),
    request('app.info',{'path':'C:/private/password'}), request('app.info',payload=[]),
    request('task.snapshot',{'task_id':'not-uuid'}),
    request('task.cancel',{'task_id':str(uuid4()),'all':True}),
    request('task.subscribe',{'task_id':str(uuid4()),'after_sequence':True}),
    request('task.subscribe',{'task_id':str(uuid4()),'after_sequence':-1}),
    request('task.events',{'subscription_id':str(uuid4()),'after_sequence':2**53}),
    request('app.check',{'steps':5}), request('app.info',{'text':'z'*20000}),
],ids=['null','list','empty','bool-schema','future-schema','str-schema','empty-id','control-id','long-id',
       'extra-envelope','unexpected-revision','path','list-payload','bad-task','cancel-all','bool-cursor',
       'negative-cursor','huge-cursor','extra-check','oversize'])
def test_invalid_payloads_are_closed_and_sanitized(active,message):
    _,api=active
    result=api.dispatch(message)
    assert result['ok'] is False and result['error']['code']=='INVALID_REQUEST'
    assert result['error']['details']=={} and result['error']['evidence_ids']==[]
    assert 'password' not in json.dumps(result)


@pytest.mark.parametrize('extra', [
    {'path':'C:/private/password'}, {'apply':True}, {'profile_kind':'runtime'},
    {'expected_revision':True}, {'enable_release_ids':['not-a-uuid']},
    {'disable_release_ids':[str(uuid4())]*201}, {'rules':[]},
])
def test_case026_compatibility_api_rejects_paths_actions_and_invalid_scope(active, extra):
    _, api = active
    payload = dict(profile_id=str(uuid4()), expected_revision=0) | extra
    result = api.dispatch(request('compatibility.check', payload))
    assert result['ok'] is False and result['error']['code'] == 'INVALID_REQUEST'
    assert 'password' not in json.dumps(result)


def test_app_check_reaches_real_readonly_database_and_shared_runtime(active):
    context,api=active
    before=hashlib.sha256(context.paths.database.read_bytes()).hexdigest()
    msg=request('app.check')
    result=api.dispatch(msg)
    assert result['ok'] and result['request_id']==msg['request_id']
    task=result['task_id']
    assert api.dispatch(request('app.info'))['data']['latest_check_task_id']==task
    end=terminal(api,task)
    from backend.storage.schema import CURRENT_SCHEMA
    assert end['state']=='succeeded' and end['data']=={'database_schema':CURRENT_SCHEMA,'entity_count':0}
    assert end['request_id']==msg['request_id'] and end['completed']==end['total']==3
    stream=api.dispatch(request('task.subscribe',{'task_id':task,'after_sequence':0}))['data']
    events=stream['events']
    assert [e['sequence'] for e in events]==list(range(1,end['sequence']+1))
    assert sum(e['type']=='terminal' for e in events)==1
    assert all(e['task_id']==task and e['request_id']==msg['request_id'] for e in events)
    api._page_loading()
    assert api.dispatch(request('app.info'))['data']['latest_check_task_id']==task
    assert hashlib.sha256(context.paths.database.read_bytes()).hexdigest()==before
    assert not any('C:' in json.dumps(e) or 'E:' in json.dumps(e) for e in events)


def test_old_cancel_and_reconnect_do_not_cross_task_ids(active):
    context,api=active
    def wait(ctx):
        for i in range(100):
            ctx.check_cancelled(); ctx.progress(i,100,'probe','PROBE'); time.sleep(.01)
        return {'complete':True}
    first=context.jobs.start('first',wait)['task_id']
    second=context.jobs.start('second',wait)['task_id']
    subscribed=api.dispatch(request('task.subscribe',{'task_id':second,'after_sequence':0}))['data']
    api.dispatch(request('task.cancel',{'task_id':first}))
    assert terminal(api,first)['state']=='cancelled'
    api.dispatch(request('task.cancel',{'task_id':first}))
    assert api.dispatch(request('task.snapshot',{'task_id':second}))['data']['state'] not in ('cancelled','cancelling')
    api._page_loading()
    assert not context.jobs._subscriptions
    old=api.dispatch(request('task.events',{'subscription_id':subscribed['subscription_id'],'after_sequence':0}))
    assert old['error']['code']=='SUBSCRIPTION_EXPIRED'
    fresh=api.dispatch(request('task.subscribe',{'task_id':second,'after_sequence':subscribed['snapshot']['sequence']}))
    assert fresh['ok'] and fresh['data']['snapshot']['task_id']==second
    assert terminal(api,second)['state']=='succeeded'


def test_expiration_and_owner_binding(active):
    context,api=active
    context.jobs._ttl=.025
    task=context.jobs.start('one',lambda ctx: None)['task_id']
    result=api.dispatch(request('task.subscribe',{'task_id':task,'after_sequence':0}))['data']
    stranger=DesktopApi(context)
    query=request('task.events',{'subscription_id':result['subscription_id'],'after_sequence':0})
    assert stranger.dispatch(query)['error']['code']=='SUBSCRIPTION_EXPIRED'
    time.sleep(.04)
    assert api.dispatch(query)['error']['code']=='SUBSCRIPTION_EXPIRED'
    assert api.dispatch(request('task.subscribe',{'task_id':task,'after_sequence':0}))['ok']


def test_internal_failure_does_not_echo_exception(active):
    context,_=active
    def fail(*args): raise RuntimeError('C:/private synthetic-password sk-proj-not-real')
    api=DesktopApi(context,test_commands={'test.fail':(Empty,fail)})
    result=api.dispatch(request('test.fail'))
    assert result['error']['code']=='INTERNAL_ERROR'
    assert not any(word in json.dumps(result) for word in ('C:/','synthetic-password','sk-proj'))
    task=context.jobs.start('failure',lambda _: fail())['task_id']
    snapshot=terminal(api,task)
    assert snapshot['state']=='failed' and snapshot['message_code']=='JOB_FAILED'
    assert not any(word in json.dumps(snapshot) for word in ('C:/','synthetic-password','sk-proj'))


def test_unknown_ids_and_future_cursor_fail(active):
    context,api=active
    assert api.dispatch(request('task.cancel',{'task_id':str(uuid4())}))['error']['code']=='TASK_NOT_FOUND'
    task=context.jobs.start('one',lambda _: None)['task_id']
    assert api.dispatch(request('task.subscribe',{'task_id':task,'after_sequence':999999}))['error']['code']=='INVALID_CURSOR'


def test_close_cleans_workers_and_subscriptions(active):
    context,api=active
    def wait(ctx):
        while True: ctx.check_cancelled();time.sleep(.005)
    task=context.jobs.start('closing',wait)['task_id']
    api.dispatch(request('task.subscribe',{'task_id':task,'after_sequence':0}))
    manager=context.jobs
    context.close()
    assert not manager._subscriptions
    assert all(not job.thread.is_alive() for job in manager._jobs.values())
    assert api.dispatch(request('app.info'))['error']['code']=='RUNTIME_CLOSED'


@pytest.mark.parametrize('extra', [dict(path='C:/private'), dict(limit=21), dict(offset=-1),
                                   dict(manifest_hash='bad'), dict(release_id='invalid')])
def test_case027_package_details_reject_unbounded_or_path_inputs(active, extra):
    _, api = active
    payload = dict(release_id=str(uuid4()), manifest_hash='a'*64) | extra
    response = api.dispatch(request('package.detail', payload))
    assert not response['ok'] and response['error']['code'] == 'INVALID_REQUEST'
    assert 'C:/private' not in json.dumps(response)


def test_case027_missing_release_detail_is_safe_actionable_result(active):
    _, api = active
    response = api.dispatch(request('package.detail', dict(release_id=str(uuid4()), manifest_hash='a'*64)))
    assert response['ok'] and response['task_id'] == response['data']['task_id']
    result = terminal(api, response['task_id'])
    assert result['state'] == 'succeeded'
    assert result['data']['error_code'] == 'PACKAGE_DETAIL_NOT_FOUND'
    assert '重新导入' in result['data']['message']
