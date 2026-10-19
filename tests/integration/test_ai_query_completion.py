"""Query limits finish with evidence-backed analysis instead of failing chat."""
from dataclasses import replace
import json

import pytest

from tests.integration.harness.adaptive_analysis_provider import AdaptiveDiagnosticScenario
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.transaction_recovery import request,tree
from tests.integration.test_ai_plan_handoff import app
from tests.integration.test_ai_tools import configure,logs,reply,start
from tests.unit.test_ai_privacy import finish


def external_mods(app):
    for index in range(4):
        (app.game/f'Mods/AnalysisExternal{index}.dll').write_bytes(b'owned-query-completion-mod')


def test_adaptive_log_environment_and_pagination_finish_with_known_evidence(app):
    external_mods(app)
    evidence=logs(app,'[00:00:01] ERROR synthetic dependency missing\n')[0]['evidence_id']
    before=tree(app.game)
    scenario=AdaptiveDiagnosticScenario()
    with ScriptedAIProvider([scenario]*15,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=reply(app,selected_evidence_id=evidence)
        assert result['completed'] and 'error_code' not in result,result
        assert '最终分析' in result['text'] and '尚未查询' in result['text']
        assert result['query_notice'] and len(provider.requests)==4
        assert 'tools' not in provider.requests[-1]['body']
        rows=scenario.results(provider.requests[-1]['body'])
        assert [name for name,_ in rows]==['read_selected_log','get_installation_summary','get_mod_workspace','get_mod_workspace']
        assert app.context.ai.get(result['session_id'])['state']=='idle'
        assert tree(app.game)==before


@pytest.mark.parametrize('field,value', [('provider_calls',1),('tool_batches',1),('tool_calls',1)])
def test_small_query_budget_is_a_normal_summary_not_terminal_error(app,field,value):
    external_mods(app)
    app.context.ai.limits=replace(app.context.ai.limits,**{field:value})
    scenario=AdaptiveDiagnosticScenario()
    with ScriptedAIProvider([scenario]*12,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        assert result['completed'] and 'error_code' not in result,result
        assert 'tools' not in provider.requests[-1]['body']
        assert len(provider.requests)<=app.context.ai.limits.provider_calls
        assert result['query_notice']


def test_repeated_queries_are_cached_and_finish_early(app,monkeypatch):
    calls=[]
    execute=app.context.ai.tools.execute
    def observed(name,*args,**kwargs):
        calls.append(name)
        return execute(name,*args,**kwargs)
    monkeypatch.setattr(app.context.ai.tools,'execute',observed)
    scenario=AdaptiveDiagnosticScenario(repeat=True)
    before=tree(app.game)
    with ScriptedAIProvider([scenario]*15,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        assert result['completed'] and len(provider.requests)==3
        assert calls==['get_installation_summary']
        assert tree(app.game)==before


def test_completed_summary_keeps_safe_suggestion_and_followup_sees_real_state(app):
    external_mods(app)
    before=tree(app.game)
    scenario=AdaptiveDiagnosticScenario(suggest=True)
    with ScriptedAIProvider([scenario]*15,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        assert result['completed'] and len(result['suggestions'])==1
        assert '最终分析' in result['text'] and '须用户点击' in result['text']
        assert tree(app.game)==before
        card=result['suggestions'][0]
        resolution=app.call('ai.suggestion.resolve',dict(session_id=result['session_id'],suggestion_id=card['suggestion_id']))
        assert tree(app.game)==before
        saved=app.call('mod.workspace.save',resolution['request'])
        assert saved['completed']
        assert next(row for row in saved['workspace']['items'] if row['id']==card['workspace_id'])['state']=='disabled'
        result2=reply(app,session_id=result['session_id'],question='[FOLLOWUP] 重新核对当前状态')
        assert result2['completed'] and 'disabled' in result2['text']
        app.assert_sentinels()


def test_provider_ignoring_removed_tools_keeps_receipt_and_never_executes_more(app,monkeypatch):
    scenario=AdaptiveDiagnosticScenario(repeat=True,ignore_stop=True)
    calls=[]
    execute=app.context.ai.tools.execute
    def observed(name,*args,**kwargs):
        calls.append(name)
        return execute(name,*args,**kwargs)
    monkeypatch.setattr(app.context.ai.tools,'execute',observed)
    with ScriptedAIProvider([scenario]*15,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        assert result['completed'] and '未生成最终分析' in result['text']
        assert '服务未' in result['query_notice']
        assert calls==['get_installation_summary'] and len(provider.requests)==3


def test_cancel_during_reserved_summary_preserves_reply_and_releases_worker(app):
    scenario=AdaptiveDiagnosticScenario(repeat=True)
    def turn(body,index):
        return dict(text='已收到的最终分析片段。',mode='stall_after_text') if not body.get('tools') else scenario(body,index)
    with ScriptedAIProvider([turn]*12,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        task=start(app)
        provider.wait_requests(3)
        import time
        end=time.monotonic()+3
        while time.monotonic()<end:
            snapshot=app.context.jobs.snapshot(task)
            if (snapshot.get('data') or {}).get('text'):
                break
            time.sleep(.01)
        app.context.jobs.cancel(task)
        assert finish(app.context.api,task)['state']=='cancelled'
        session=app.context.ai.get(snapshot['data']['session_id'])
        assert session['messages'][-1]['content']=='已收到的最终分析片段。' and session['incomplete']
        assert not any(row.thread.is_alive() for row in app.context.jobs._jobs.values())


def test_tool_context_pressure_still_produces_a_bounded_final_analysis(app):
    for index in range(15):
        (app.game/(f'Mods/External{index}-'+('N'*60)+'.dll')).write_bytes(b'owned-context-pressure')
    app.context.ai.limits=replace(app.context.ai.limits,input_bytes=9000)
    def turn(body,index):
        if body.get('tools'):
            return dict(calls=[('get_mod_workspace',dict(limit=20))])
        assert '部分工具返回仅保留片段' in body['messages'][0]['content']
        return dict(text='已根据日志和部分 Mod 查询结果分析；清单不完整，剩余部分保持未知。')
    with ScriptedAIProvider([turn]*8,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        assert result['completed'] and 'error_code' not in result,result
        assert len(provider.requests)==2
        assert len(json.dumps(provider.requests[-1]['body']['messages'],ensure_ascii=False).encode('utf-8'))<=9000


def test_model_length_during_reserved_final_analysis_can_continue(app):
    scenario=AdaptiveDiagnosticScenario(repeat=True)
    summaries=[]
    def turn(body,index):
        if body.get('tools'):
            return scenario(body,index)
        summaries.append(index)
        return dict(text='已有证据显示依赖问题，还需要',mode='length') if len(summaries)==1 else dict(text='继续分析完成：先核对依赖版本。')
    with ScriptedAIProvider([turn]*12,fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        first=reply(app)
        assert first['error_code']=='AI_PROVIDER_TRUNCATED' and first['can_continue']
        second=reply(app,session_id=first['session_id'],question='请继续上次中断的分析')
        assert second['completed'] and '继续分析完成' in second['text']
        assert app.context.ai.get(first['session_id'])['messages'][1]['content']==first['text']


def test_minimal_probe_uses_only_new_fixture_and_localhost_config():
    from pathlib import Path
    from uuid import uuid4
    from tests.integration.harness.ai_minimal_probe import MINIMAL_LOG,run_minimal_probe
    scenario=AdaptiveDiagnosticScenario(suggest=True)
    root=Path(__file__).resolve().parents[2]/'docs/development/evidence/20261016-followup-fixes/ai'/('minimal-localhost-'+str(uuid4()))
    root.mkdir(parents=True)
    with ScriptedAIProvider([scenario]*12,fragment_size=4096,delay=0) as provider:
        result=run_minimal_probe(root,dict(base_url=provider.base_url,model='synthetic-minimal'))
        assert result['ok'] and result['text']
        assert set(result['tool_names'])>={'get_installation_summary','get_mod_workspace'}
        sent=json.dumps([row['body'] for row in provider.requests],ensure_ascii=False)
        assert MINIMAL_LOG.splitlines()[-1] in sent
        assert 'owned-synthetic-probe-placeholder' not in sent
        assert str(root) not in sent
        assert all(not row['headers'].get('Authorization') for row in provider.requests)


def test_normal_analysis_text_before_suggestion_is_not_replaced_by_short_final(app):
    detail='## 详细分析\n关键异常 SyntheticDependencyNotFoundException：应核对依赖程序集与游戏构建。\n'+('这段是正常 assistant.content 分析，不是 reasoning。\n'*65)
    summary='建议已生成，请点击确认后复测。'
    def detailed(body,index):
        rows=AdaptiveDiagnosticScenario.results(body)
        package=next(value['data']['items'][0] for name,value in rows if name=='search_mods')
        return dict(text=detail,calls=[('suggest_mod_action',dict(release_id=package['release_id'],
            manifest_hash=package['manifest_hash'],action='install',reason='在合成环境安装此包验证建议闭环。'))])
    before=tree(app.game)
    with ScriptedAIProvider([dict(text='先核对当前环境和包库。',calls=[('get_installation_summary',{}),('search_mods',{})]),
        detailed,dict(text=summary)],fragment_size=300,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        assert result['completed'] and detail in result['text'] and summary in result['text']
        assert result['text'].startswith('先核对当前环境和包库。\n\n')
        session=app.context.ai.get(result['session_id'])
        assert session['messages'][-1]['content']==result['text']
        assert 'SyntheticDependencyNotFoundException' in app.context.ai.export(result['session_id'])['text']
        assert len(result['suggestions'])==1 and tree(app.game)==before
        card=result['suggestions'][0]
        resolution=app.call('ai.suggestion.resolve',dict(session_id=result['session_id'],suggestion_id=card['suggestion_id']))
        assert app.call('mod.action',resolution['request'])['completed']
        app.assert_sentinels()


def test_provider_batch_over_four_is_bounded_then_summarized_not_failed(app,monkeypatch):
    calls=[]
    execute=app.context.ai.tools.execute
    def observed(name,*args,**kwargs):
        calls.append(name)
        return execute(name,*args,**kwargs)
    monkeypatch.setattr(app.context.ai.tools,'execute',observed)
    queries=[('get_installation_summary',{}),('get_mod_workspace',{}),('get_installed_mods',{}),
             ('search_mods',{}),('get_mod_config',{}),('search_mods',dict(query='other'))]
    with ScriptedAIProvider([dict(calls=queries),dict(text='已取得四项证据，其余查询未执行；现在给出分析。')],fragment_size=4096,delay=0) as provider:
        configure(app,provider)
        result=reply(app)
        assert result['completed'] and 'error_code' not in result,result
        assert len(calls)==4 and len(provider.requests)==2
        rows=AdaptiveDiagnosticScenario.results(provider.requests[-1]['body'])
        assert sum(value.get('error_code')=='AI_TOOL_LIMIT' for _,value in rows)==2
        assert 'tools' not in provider.requests[-1]['body']
