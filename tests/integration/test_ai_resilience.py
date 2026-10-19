"""CASE-043: actual DesktopApi/HTTP/SSE limits, cancellation and offline Mod work."""
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from uuid import uuid4

import pytest

from backend.ai.budgets import BudgetLimits
from tests.integration.harness.ai_provider import SyntheticAIProvider
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.transaction_recovery import request, tree
from tests.integration.test_ai_plan_handoff import app, suggestion, current_state
from tests.integration.test_ai_tools import configure, start, tool_results
from tests.unit.test_ai_privacy import active, configured, finish


def chat(context, **payload):
    result = context.api.dispatch(request('ai.chat.send', dict(question='本次问题') | payload))
    assert result['ok'], result
    terminal = finish(context.api, result['data']['task_id'], timeout=6)
    assert terminal['state'] == 'succeeded', terminal
    return terminal['data']


def no_workers(context):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not any(job.thread.is_alive() for job in context.jobs._jobs.values()):
            return
        time.sleep(.01)
    raise AssertionError('A cancelled/finished AI worker remained active')


@pytest.mark.parametrize('mode,code', [('401','AI_AUTH_FAILED'), ('429','AI_RATE_LIMIT'),
    ('500','AI_PROVIDER_FAILED'), ('malformed','AI_PROTOCOL_ERROR'), ('eof','AI_STREAM_INCOMPLETE')])
def test_case043_failures_are_bounded_and_only_manual_retry_connects(active, mode, code):
    with SyntheticAIProvider(mode=mode, chunks=['保留的部分回复']) as provider:
        configured(active, provider)
        result = chat(active)
        assert result['error_code'] == code and result['message']
        if mode == 'eof':
            assert result['text'] == '保留的部分回复'
        before = active.ai.get(result['session_id'])
        assert before['state'] == 'failed' and before['suggestions'] == []
        time.sleep(.15)
        assert len(provider.requests) == 1  # No background retry.
        no_workers(active)
        provider.set_response(chunks=['人工重试成功'])
        retried = chat(active, session_id=result['session_id'])
        assert retried['completed'] and retried['text'] == '人工重试成功'
        assert len(provider.requests) == 2


def test_case043_unknown_model_or_endpoint_has_safe_failure(active):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(404)
            self.send_header('Content-Length','0')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        active.ai.save(dict(provider='openai_compatible',base_url=f'http://127.0.0.1:{server.server_port}/v1',model='unknown'))
        result = chat(active)
        assert result['error_code'] == 'AI_MODEL_UNAVAILABLE' and '模型' in result['message']
        no_workers(active)
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


@pytest.mark.parametrize('command', ['ai.chat.send','ai.test'])
def test_case043_total_deadline_and_cancel_release_real_http(active, command):
    with SyntheticAIProvider(mode='stall') as provider:
        configured(active, provider)
        active.ai.limits = replace(active.ai.limits,request_seconds=.5)
        payload = dict(question='等待有时限') if command=='ai.chat.send' else {}
        task = active.api.dispatch(request(command,payload))['data']['task_id']
        provider.wait_requests()
        terminal = finish(active.api,task,timeout=3)
        assert terminal['state']=='succeeded' and terminal['data']['error_code']=='AI_TIMEOUT'
        no_workers(active)
        assert len(provider.requests)==1 and not active.ai._testing
        active.ai.limits = BudgetLimits()
        task = active.api.dispatch(request(command,payload))['data']['task_id']
        provider.wait_requests(2)
        assert active.api.dispatch(request('task.cancel',dict(task_id=task)))['ok']
        assert finish(active.api,task,timeout=3)['state']=='cancelled'
        no_workers(active)
        assert len(provider.requests)==2 and not active.ai._testing


@pytest.mark.parametrize('command', ['ai.chat.send','ai.test'])
def test_case043_cancel_before_worker_start_releases_admission(active, command):
    with SyntheticAIProvider() as provider:
        configured(active, provider)
        payload = dict(question='队列取消') if command=='ai.chat.send' else {}
        with active.jobs._lock:  # Real job starts, but cannot enter its worker yet.
            response = active.api.dispatch(request(command,payload))
            assert response['ok'],response
            task=response['data']['task_id']
            assert active.api.dispatch(request('task.cancel',dict(task_id=task)))['ok']
        assert finish(active.api,task)['state']=='cancelled'
        no_workers(active)
        assert provider.requests==[] and not active.ai._testing
        assert all(row['state']!='generating' for row in active.ai._sessions.values())
        assert chat(active)['completed'] and len(provider.requests)==1


def test_case043_only_one_ai_request_at_a_time_does_not_block_mod_work(app):
    with SyntheticAIProvider(mode='stall') as provider:
        configure(app,provider)
        task=start(app,installation=False)
        provider.wait_requests()
        for command,payload in [('ai.test',{}),('ai.chat.send',dict(question='并发另一会话'))]:
            result=app.context.api.dispatch(request(command,payload))
            assert not result['ok'] and result['error']['code']=='AI_BUSY'
        app.call('mod.action',dict(installation_id=app.installation,release_id=app.release['release_id'],
            manifest_hash=app.release['manifest_hash'],action='install',idempotency_key=str(uuid4())))
        assert current_state(app)=='enabled' and (app.game/'Mods/suggestion.png').exists()
        assert app.context.api.dispatch(request('task.cancel',dict(task_id=task)))['ok']
        assert finish(app.context.api,task)['state']=='cancelled'
        no_workers(app.context)
        assert len(provider.requests)==1


@pytest.mark.parametrize('limit,turns,code,count', [
    ('output_bytes',[{'text':'1234','calls':[('get_installation_summary',{})]}, {'text':'56789'}],'AI_OUTPUT_LIMIT',2),
    ('frames',[{'calls':[('get_installation_summary',{})]}, {'text':'later'}],'AI_STREAM_LIMIT',1),
    ('input_bytes',[], 'AI_INPUT_LIMIT',0),
])
def test_case043_budget_is_shared_by_real_tool_exchanges(app,limit,turns,code,count):
    values=dict(output_bytes=8,frames=1,provider_calls=1,tool_batches=1,tool_calls=1,input_bytes=1)
    app.context.ai.limits=replace(app.context.ai.limits,**{limit:values[limit]})
    before=tree(app.game)
    with ScriptedAIProvider(turns,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=chat(app.context,installation_id=app.installation)
        assert result['error_code']==code,result
        assert len(provider.requests)==count and not provider.errors
    no_workers(app.context)
    assert tree(app.game)==before and app.context.ai.get(result['session_id'])['suggestions']==[]


def test_case043_repeating_calls_stop_with_visible_receipt_without_writes(app):
    before=tree(app.game)
    calls=[('get_installation_summary',{})]*4
    with ScriptedAIProvider([{'calls':calls}]*5,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=chat(app.context,installation_id=app.installation)
        assert result['completed'] and '未生成最终分析' in result['text']
        assert len(provider.requests)==3 and len(tool_results(provider.requests[-1]['body']))==8
        assert tree(app.game)==before and not provider.errors
    no_workers(app.context)


def test_case043_cancel_after_passive_suggestion_stops_followups_and_clears_cards(app):
    before=tree(app.game)
    with ScriptedAIProvider([{'calls':[suggestion(app)]},{'mode':'stall'},{'text':'must not arrive'}]) as provider:
        configure(app,provider)
        task=start(app)
        provider.wait_requests(2)
        app.context.api.dispatch(request('task.cancel',dict(task_id=task)))
        assert finish(app.context.api,task)['state']=='cancelled'
        no_workers(app.context)
        session=next(iter(app.context.ai._sessions))
        assert app.context.ai.get(session)['state']=='cancelled'
        assert app.context.ai.get(session)['suggestions']==[]
        assert len(provider.requests)==2
    assert tree(app.game)==before


def test_case043_disconnected_ai_keeps_install_enable_disable_uninstall_local(app):
    with SyntheticAIProvider() as provider:
        configure(app,provider)
    # The owned listening socket is now closed; no arbitrary external address.
    result=chat(app.context)
    assert result['error_code']=='AI_CONNECTION_FAILED'
    original=tree(app.game)
    for action,state in [('install','enabled'),('disable','disabled'),('enable','enabled'),('uninstall','not_installed')]:
        data=app.call('mod.action',dict(installation_id=app.installation,release_id=app.release['release_id'],
            manifest_hash=app.release['manifest_hash'],action=action,idempotency_key=str(uuid4())))
        assert data.get('completed'),data
        assert current_state(app)==state
        assert (app.game/'Mods/suggestion.png').exists()==(state=='enabled')
    assert tree(app.game)==original and len(provider.requests)==0


def test_case043_close_after_tool_turn_cancels_child_and_preserves_game(app):
    before=tree(app.game)
    with ScriptedAIProvider([{'calls':[('get_installation_summary',{})]},{'mode':'stall'}]) as provider:
        configure(app,provider)
        task=start(app)
        provider.wait_requests(2)
        context=app.context
        manager=context.jobs
        app.close()
        assert manager.snapshot(task)['state']=='cancelled'
        assert not any(job.thread.is_alive() for job in manager._jobs.values())
        assert len(provider.requests)==2
    assert tree(app.game)==before
