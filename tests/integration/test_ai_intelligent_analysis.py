"""20261016 regressions: real localhost SSE, full sessions and external Mods."""
from dataclasses import replace
import json
import time

import pytest

from backend.ai.budgets import AIError, RequestBudget
from backend.ai.gateway import Gateway
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.transaction_recovery import request, tree
from tests.integration.test_ai_plan_handoff import app
from tests.integration.test_ai_tools import configure, reply, start, tool_results
from tests.unit.test_ai_privacy import active, finish


@pytest.mark.parametrize('text,fragment_size', [('分析建议'*2000,4096),('x'*1800,1),('中文'*20000,40000)],ids=['chinese-24k','1800-frames','single-event-120k'])
def test_default_gateway_accepts_normal_chinese_many_frames_and_large_event(text,fragment_size):
    with ScriptedAIProvider([{'text':text}],fragment_size=fragment_size,delay=0) as provider:
        output=Gateway().stream(dict(base_url=provider.base_url,model='synthetic'),None,
            [dict(role='user',content='分析日志')],lambda:None,lambda *_:None)
        assert output==text


@pytest.mark.parametrize('field,code', [('frames','AI_STREAM_LIMIT'),('wire_bytes','AI_STREAM_LIMIT'),('output_bytes','AI_OUTPUT_LIMIT')])
def test_frame_wire_and_content_budgets_are_distinct(field,code):
    from backend.ai.budgets import BudgetLimits
    limit=replace(BudgetLimits(),**{field:8})
    received=[]
    with ScriptedAIProvider([{'text':'x'*30}],fragment_size=1,delay=0) as provider:
        with pytest.raises(AIError) as error:
            Gateway().stream(dict(base_url=provider.base_url,model='synthetic'),None,
                [dict(role='user',content='分析')],lambda:None,lambda text,*_:received.append(text),budget=RequestBudget(limit))
        assert error.value.code==code
        if field!='wire_bytes':
            assert received[-1]=='x'*8


def test_large_session_survives_job_payload_limit_and_can_follow_up(active):
    text='证据：这是较长的中文日志分析。\n'*3000
    with ScriptedAIProvider([{'text':text},{'text':'继续给出最小排查步骤。'}],fragment_size=300,delay=0) as provider:
        active.ai.save(dict(provider='openai_compatible',base_url=provider.base_url,model='synthetic'))
        task=active.api.dispatch(request('ai.chat.send',dict(question='分析日志')))['data']['task_id']
        terminal=finish(active.api,task,timeout=12)
        assert terminal['state']=='succeeded',terminal
        data=terminal['data']
        assert data['completed'] and data['full_text_in_session'] and not data['text']
        session=active.ai.get(data['session_id'])
        assert session['messages'][-1]['content']==text
        events=active.jobs.subscribe(task)['events']
        assert any(event.get('data',{}).get('text_delta') for event in events if isinstance(event.get('data'),dict))
        assert all(len(json.dumps(event,ensure_ascii=False).encode('utf-8'))<32768 for event in events)
        task=active.api.dispatch(request('ai.chat.send',dict(question='继续',session_id=data['session_id'])))['data']['task_id']
        resumed=finish(active.api,task)['data']
        assert resumed.get('completed') and '继续' in resumed['text'],resumed
        assert len(json.dumps(provider.requests[-1]['body'],ensure_ascii=False).encode('utf-8'))<60000
        assert '[此前回复仅保留末尾' in json.dumps(provider.requests[-1]['body'],ensure_ascii=False)
        assert text in active.ai.export(data['session_id'])['text']


def test_model_length_is_not_display_limit_and_continuation_preserves_answer(app):
    with ScriptedAIProvider([{'text':'已查到首个异常，接下来','mode':'length'},{'text':'核对 Mod 依赖版本。'}],delay=0) as provider:
        configure(app,provider)
        first=reply(app)
        assert first['error_code']=='AI_PROVIDER_TRUNCATED' and first['can_continue']
        assert first['text']=='已查到首个异常，接下来'
        assert app.context.ai.get(first['session_id'])['incomplete']
        assert len(provider.requests)==1
        second=reply(app,session_id=first['session_id'],question='从刚才中断处继续')
        assert second['completed']
        history=app.context.ai.get(first['session_id'])
        assert history['messages'][1]['content']==first['text'] and not history['incomplete']


def test_cancel_preserves_received_text_in_session_not_only_last_event(app):
    with ScriptedAIProvider([{'text':'取消前已经收到的分析。','mode':'stall_after_text'}],fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        task=start(app)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=app.context.jobs.snapshot(task)
            if (value.get('data') or {}).get('text'):
                break
            time.sleep(.01)
        assert value['data']['text']=='取消前已经收到的分析。'
        app.context.api.dispatch(request('task.cancel',dict(task_id=task)))
        assert finish(app.context.api,task)['state']=='cancelled'
        session=app.context.ai.get(value['data']['session_id'])
        assert session['state']=='cancelled' and session['incomplete']
        assert session['messages'][-1]['content']=='取消前已经收到的分析。'
        assert len(provider.requests)==1


def workspace_suggestion(body,index,verb='disable'):
    result=tool_results(body)[-1]['data']
    row=next(item for item in result['items'] if item['display_name']=='ExternalAnalysis')
    return {'calls':[('suggest_workspace_action',dict(workspace_id=row['id'],snapshot_id=result['snapshot_id'],
        action=verb,reason='单独停用复现，其他 Mod 保持不变。'))]}


def test_external_mod_query_suggestion_confirm_disable_then_enable(app):
    external=app.game/'Mods/ExternalAnalysis.dll'
    external.write_bytes(b'owned-synthetic-external-mod')
    baseline=tree(app.game)
    turns=[{'calls':[('get_mod_workspace',{})]},workspace_suggestion,{'text':'请确认停用 ExternalAnalysis 以隔离问题。'}]
    with ScriptedAIProvider(turns,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        card=result['suggestions'][0]
        assert card['kind']=='workspace' and card['display_name']=='ExternalAnalysis'
        assert tree(app.game)==baseline
        resolution=app.call('ai.suggestion.resolve',dict(session_id=result['session_id'],suggestion_id=card['suggestion_id']))
        assert resolution['kind']=='workspace' and tree(app.game)==baseline
        applied=app.call('mod.workspace.save',resolution['request'])
        assert applied['completed'] and not external.exists()
        row=next(item for item in applied['workspace']['items'] if item['id']==card['workspace_id'])
        assert row['state']=='disabled' and row['release_id']
        workspace=applied['workspace']
        enabled=app.call('mod.workspace.save',dict(installation_id=app.installation,snapshot_id=workspace['snapshot_id'],
            enabled_ids=workspace['enabled_ids']+[card['workspace_id']],idempotency_key='11111111-1111-4111-8111-111111111111'))
        assert enabled['completed'] and tree(app.game)==baseline
        assert len(provider.requests)==3
        prompt=provider.requests[0]['body']['messages'][0]['content']
        for term in ('深空当铺','IL2CPP','MelonPriorityAttribute','Demo','Playtest','事实','suggest_workspace_action'):
            assert term in prompt
        app.assert_sentinels()


def test_external_suggestion_rejects_changed_snapshot_before_write(app):
    (app.game/'Mods/ExternalAnalysis.dll').write_bytes(b'owned-synthetic-external-mod')
    with ScriptedAIProvider([{'calls':[('get_mod_workspace',{})]},workspace_suggestion,{'text':'确认后停用。'}],delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        card=result['suggestions'][0]
        (app.game/'Mods/ExternalAnalysis.dll').write_bytes(b'changed-after-analysis')
        baseline=tree(app.game)
        response=app.context.api.dispatch(request('ai.suggestion.resolve',dict(session_id=result['session_id'],suggestion_id=card['suggestion_id'])))
        assert not response['ok'] and response['error']['code']=='AI_SUGGESTION_STALE'
        assert tree(app.game)==baseline


def test_query_turn_keeps_stream_offset_when_final_reply_extends_interim_text(app):
    prefix='已取得的证据。'*1000
    final=prefix+'进一步分析结果。'*1000
    with ScriptedAIProvider([{'text':prefix,'calls':[('get_installation_summary',{})]},{'text':final}],fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        task=start(app)
        terminal=finish(app.context.api,task)
        assert terminal['data']['completed']
        visible=''
        for event in app.context.jobs.subscribe(task)['events']:
            data=event.get('data') or {}
            if isinstance(data.get('text'),str) and not data.get('full_text_in_session'):
                visible=data['text']
            if 'text_delta' in data:
                assert data['text_offset']==len(visible.encode('utf-16-le'))//2
                visible+=data['text_delta']
        assert visible==prefix+'\n\n'+final
