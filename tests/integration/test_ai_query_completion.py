"""Native bridge, parser and AI jobs against owned files and localhost SSE.

Packages/provider answers are synthetic; no real game, account or external AI
service participates. Query limits, cancellation and privacy use real consumers.
"""
from dataclasses import replace
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.native_mods.state import ORDER_TYPE
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.transaction_recovery import request, rpc
from tests.integration.test_ai_tools import configure, reply, start
from tests.unit.test_ai_privacy import MemoryCredentials, finish


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}


@pytest.fixture
def app(tmp_path, monkeypatch):
    home, game = tmp_path / 'owned-home', tmp_path / 'native-game'
    monkeypatch.setenv('USERPROFILE', str(home))
    game.mkdir()
    (game / 'Probably Stolen.exe').write_bytes(b'owned inert game - never execute')
    managed = game / 'Probably Stolen_Data/Managed'
    managed.mkdir(parents=True)
    (managed / 'Assembly-CSharp.dll').write_bytes(b'owned inert assembly - never load')
    (game / 'MonoBleedingEdge').mkdir()
    persistent = home / 'AppData/LocalLow/Questing Goose Studio/Probably Stolen'
    (persistent / 'ModConfigs').mkdir(parents=True)
    (persistent / 'save_1.es3').write_bytes(b'owned-save-sentinel-never-sent')
    ids = [f'query_mod_{index}' for index in range(4)]
    for index, identity in enumerate(ids):
        folder = game / 'Mods' / identity
        folder.mkdir(parents=True)
        (folder / 'manifest.xml').write_text(
            f'<Manifest><ID>{identity}</ID><Name>原生查询模组 {index}</Name><ModVersion>1.0</ModVersion>'
            '<Author>Fixture</Author><Assemblies><Assembly>Mod.dll</Assembly></Assemblies>'
            '<Capabilities/><Prerequisites/></Manifest>', encoding='utf-8')
        (folder / 'Mod.dll').write_bytes(b'owned inert mod - never load')
    def set_enabled(values):
        (persistent / 'SaveFile.es3').write_text(json.dumps(dict(
            modOrder={'__type': ORDER_TYPE, 'value': values},
            unrelated={'__type': 'string', 'value': 'owned-settings-sentinel'})), encoding='utf-8')
    set_enabled(ids)
    (persistent / 'ModConfigs/query_mod_0.json').write_text(json.dumps(dict(entries=[
        dict(key='EnabledFeature', value='true'), dict(key='AccessToken', value='owned-private-config-token')])), encoding='utf-8')
    context = DesktopContext(resolve_paths(str(tmp_path / 'manager-data')))
    context.ai_credentials_backend = MemoryCredentials()
    context.initialize()
    context.api = DesktopApi(context)
    context.choose_installation_folder = lambda _: str(game)
    call = lambda command, payload=None: rpc(context.api, command, payload)
    try:
        grant = call('installation.choose', dict(kind='manual'))
        discovery = call('installation.discover', dict(source_handles=[grant['source_handle']]))
        selected = call('installation.select', dict(candidate_id=discovery['candidates'][0]['candidate_id']))
        yield SimpleNamespace(context=context, root=tmp_path, game=game, persistent=persistent,
            installation=selected['installation']['installation_id'], ids=ids, call=call, set_enabled=set_enabled)
    finally:
        context.close()


def logs(app):
    text = ('Initialize engine version: 2021.3.45f2\n'
        '[ModLoader] audit: game 0.46D, trust list: fixture\n'
        '[ModLoader] audit: query_mod_0 v1.0 source=Local workshop=0 owner=0 running=True trust=Approved caps=[]\n'
        '[ModLoader] audit: query_mod_1 v1.0 source=Local workshop=0 owner=0 running=False trust=Approved caps=[]\n'
        'ERROR SyntheticDependencyNotFoundException: selected native diagnostic '
        'owner=76561191234567890 hash=owned-approval-hash C:/Users/owned-private-user/Logs\n')
    (app.persistent / 'Player.log').write_text(text, encoding='utf-8')
    accepted = app.context.api.dispatch(request('logs.watch', dict(installation_id=app.installation)))
    assert accepted['ok'], accepted
    task = accepted['data']['task_id']
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            rows = app.call('evidence.list', dict(installation_id=app.installation))['entries']
            selected = next((row for row in rows if 'SyntheticDependencyNotFoundException' in row['text']), None)
            if selected:
                return selected['evidence_id']
            time.sleep(.03)
        raise AssertionError('Native Player.log watcher did not ingest owned evidence')
    finally:
        app.context.jobs.cancel(task)
        assert finish(app.context.api, task)['state'] == 'cancelled'


class NativeScenario:
    def __init__(self, *, suggest=False, repeat=False, ignore_stop=False):
        self.suggest, self.repeat, self.ignore_stop = suggest, repeat, ignore_stop

    @staticmethod
    def results(body):
        names = {call['id']: call['function']['name'] for row in body['messages'] if row['role'] == 'assistant'
                 for call in row.get('tool_calls', [])}
        return [(names.get(row['tool_call_id']), json.loads(row['content']))
                for row in body['messages'] if row['role'] == 'tool']

    def __call__(self, body, index):
        rows = self.results(body)
        workspace = [value['data'] for name, value in rows if name == 'get_mod_workspace' and value.get('ok')]
        suggestions = [value['data']['suggestion'] for _, value in rows if value.get('ok') and 'suggestion' in value.get('data', {})]
        if not body.get('tools'):
            if self.ignore_stop:
                return dict(calls=[('get_installation_summary', {})])
            text = '## 最终分析\n依据原生清单与选定日志分析，尚未查询的分页保持未知。'
            if suggestions:
                text += '\n建议须用户点击返回模组列表确认；尚未执行启停。'
            return dict(text=text)
        if self.repeat:
            return dict(calls=[('get_installation_summary', {}), ('get_installation_summary', {})])
        if any('[FOLLOWUP]' in row.get('content', '') for row in body['messages'] if row['role'] == 'user') and workspace:
            return dict(text=f"后续核对：{workspace[0]['items'][0]['id']} 目标启用={workspace[0]['items'][0]['target_enabled']}。")
        selected = re.search(r'"selected_evidence_id": "([a-f0-9-]{36})"', body['messages'][0]['content'])
        if selected and not any(name == 'read_selected_log' for name, _ in rows):
            return dict(calls=[('read_selected_log', dict(evidence_id=selected[1]))])
        if not workspace:
            return dict(calls=[('get_installation_summary', {}), ('get_mod_workspace', dict(limit=1))])
        if self.suggest and not suggestions:
            return dict(calls=[('suggest_native_mod_action', dict(mod_id=workspace[0]['items'][0]['id'],
                revision=workspace[0]['revision'], action='disable', reason='在模组列表确认隔离草稿，再保存和复测。'))])
        if workspace[-1]['next_offset'] is not None:
            return dict(calls=[('get_mod_workspace', dict(offset=workspace[-1]['next_offset'], limit=1))])
        return dict(text='已查询全部原生模组；保存状态仍不代表本次运行。')


def test_adaptive_log_environment_and_pagination_finish_with_known_evidence(app):
    evidence = logs(app)
    before = files(app.game), files(app.persistent)
    scenario = NativeScenario()
    with ScriptedAIProvider([scenario] * 15, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app, selected_evidence_id=evidence)
        assert result['completed'] and '最终分析' in result['text'] and '尚未查询' in result['text']
        assert result['query_notice'] and len(provider.requests) == 4
        assert 'tools' not in provider.requests[-1]['body']
        assert [name for name, _ in scenario.results(provider.requests[-1]['body'])] == [
            'read_selected_log', 'get_installation_summary', 'get_mod_workspace', 'get_mod_workspace']
    assert (files(app.game), files(app.persistent)) == before


@pytest.mark.parametrize('field,value', [('provider_calls', 1), ('tool_batches', 1), ('tool_calls', 1)])
def test_small_query_budget_is_a_normal_summary_not_terminal_error(app, field, value):
    app.context.ai.limits = replace(app.context.ai.limits, **{field: value})
    with ScriptedAIProvider([NativeScenario()] * 12, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app)
        assert result['completed'] and result['query_notice']
        assert 'tools' not in provider.requests[-1]['body']
        assert len(provider.requests) <= app.context.ai.limits.provider_calls


def test_repeated_queries_are_cached_and_finish_early(app, monkeypatch):
    calls, execute = [], app.context.ai.tools.execute
    def observed(name, *args, **kwargs):
        calls.append(name)
        return execute(name, *args, **kwargs)
    monkeypatch.setattr(app.context.ai.tools, 'execute', observed)
    with ScriptedAIProvider([NativeScenario(repeat=True)] * 15, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app)
        assert result['completed'] and len(provider.requests) == 3
        assert calls == ['get_installation_summary']


def test_completed_summary_keeps_safe_suggestion_and_followup_sees_real_state(app):
    before = files(app.game), files(app.persistent)
    with ScriptedAIProvider([NativeScenario(suggest=True)] * 15, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app)
        assert result['completed'] and len(result['suggestions']) == 1 and '须用户点击' in result['text']
        card = result['suggestions'][0]
        resolution = app.call('ai.suggestion.resolve', dict(session_id=result['session_id'], suggestion_id=card['suggestion_id']))
        assert resolution['navigation'] == dict(page='mods', mod_id=card['mod_id']) and 'request' not in resolution
        assert (files(app.game), files(app.persistent)) == before
        app.set_enabled(app.ids[1:])  # External user change, not an AI action.
        later = reply(app, session_id=result['session_id'], question='[FOLLOWUP] 重新核对目标启用状态')
        assert later['completed'] and '目标启用=False' in later['text']


def test_provider_ignoring_removed_tools_keeps_receipt_and_never_executes_more(app):
    with ScriptedAIProvider([NativeScenario(repeat=True, ignore_stop=True)] * 15, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app)
        assert result['completed'] and '未生成最终分析' in result['text']
        assert '服务未' in result['query_notice'] and len(provider.requests) == 3


def test_cancel_during_reserved_summary_preserves_reply_and_releases_worker(app):
    scenario = NativeScenario(repeat=True)
    def turn(body, index):
        return dict(text='已收到的最终分析片段。', mode='stall_after_text') if not body.get('tools') else scenario(body, index)
    with ScriptedAIProvider([turn] * 12, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        task = start(app)
        provider.wait_requests(3)
        end = time.monotonic() + 3
        while time.monotonic() < end:
            snapshot = app.context.jobs.snapshot(task)
            if (snapshot.get('data') or {}).get('text'):
                break
            time.sleep(.01)
        app.context.jobs.cancel(task)
        assert finish(app.context.api, task)['state'] == 'cancelled'
        session = app.context.ai.get(snapshot['data']['session_id'])
        assert session['messages'][-1]['content'] == '已收到的最终分析片段。' and session['incomplete']


def test_tool_context_pressure_still_produces_a_bounded_final_analysis(app):
    app.context.ai.limits = replace(app.context.ai.limits, input_bytes=9000)
    def turn(body, index):
        return dict(calls=[('get_mod_workspace', dict(limit=20))]) if body.get('tools') else dict(text='依据已取得资料分析；未查询部分保持未知。')
    with ScriptedAIProvider([turn] * 8, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app)
        assert result['completed'] and 'error_code' not in result
        assert len(json.dumps(provider.requests[-1]['body']['messages'], ensure_ascii=False).encode('utf-8')) <= 9000


def test_model_length_during_reserved_final_analysis_can_continue(app):
    scenario, summaries = NativeScenario(repeat=True), []
    def turn(body, index):
        if body.get('tools'):
            return scenario(body, index)
        summaries.append(index)
        return dict(text='已有证据显示依赖问题，还需要', mode='length') if len(summaries) == 1 else dict(text='继续分析完成：先核对依赖版本。')
    with ScriptedAIProvider([turn] * 12, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        first = reply(app)
        assert first['error_code'] == 'AI_PROVIDER_TRUNCATED' and first['can_continue']
        second = reply(app, session_id=first['session_id'], question='请继续上次中断的分析')
        assert second['completed'] and '继续分析完成' in second['text']


def test_native_queries_use_owned_data_and_redact_credentials_before_localhost_provider(app):
    evidence = logs(app)
    turns = [dict(calls=[('get_runtime_reconciliation', {}), ('get_mod_config', dict(mod_id=app.ids[0])),
                        ('get_mod_dependencies', dict(mod_id=app.ids[0])), ('get_mod_detail', dict(mod_id=app.ids[0]))]),
             dict(text='配置字符串保持原值；运行与启用状态分别核对。')]
    with ScriptedAIProvider(turns, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app, selected_evidence_id=evidence)
        assert result['completed']
        results = NativeScenario.results(provider.requests[-1]['body'])
        assert len(results) == 4 and all(value['ok'] for _, value in results)
        run = results[0][1]['data']
        assert run['log']['running_count'] == 1 and run['items'][1]['run']['state'] == 'not_running'
        sent = json.dumps([row['body'] for row in provider.requests], ensure_ascii=False)
        for private in (str(app.root), 'owned-private-config-token', 'owned-save-sentinel-never-sent', 'owned-settings-sentinel'):
            assert private not in sent
        assert all(not row['headers'].get('Authorization') for row in provider.requests)


def test_selected_native_log_tool_and_selector_share_identity_redaction(app):
    evidence = logs(app)
    turns = [dict(calls=[('read_selected_log', dict(evidence_id=evidence))]), dict(text='已核对选定日志。')]
    with ScriptedAIProvider(turns, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app, selected_evidence_id=evidence)
        assert result['completed']
        preview = app.call('ai.logs.list', dict(installation_id=app.installation))
        public = json.dumps(preview, ensure_ascii=False) + json.dumps([row['body'] for row in provider.requests], ensure_ascii=False)
        for private in ('76561191234567890', 'owned-approval-hash', 'owned-private-user'):
            assert private not in public
        assert '[已隐藏]' in public


def test_normal_analysis_text_before_suggestion_is_not_replaced_by_short_final(app):
    detail = '## 详细分析\n核对原生依赖与构建版本。\n' + ('这段是正常分析。\n' * 65)
    def detailed(body, index):
        workspace = next(value['data'] for name, value in NativeScenario.results(body) if name == 'get_mod_workspace')
        return dict(text=detail, calls=[('suggest_native_mod_action', dict(mod_id=app.ids[0], revision=workspace['revision'],
            action='disable', reason='返回模组列表确认草稿。'))])
    before = files(app.persistent)
    with ScriptedAIProvider([dict(text='先核对原生清单。', calls=[('get_mod_workspace', {})]), detailed,
        dict(text='建议已生成，请返回列表确认。')], fragment_size=300, delay=0) as provider:
        configure(app, provider)
        result = reply(app)
        assert result['completed'] and detail in result['text'] and '返回列表确认' in result['text']
        assert len(result['suggestions']) == 1 and files(app.persistent) == before
        assert app.context.ai.get(result['session_id'])['messages'][-1]['content'] == result['text']


def test_provider_batch_over_four_is_bounded_then_summarized_not_failed(app):
    queries = [('get_installation_summary', {}), ('get_mod_workspace', {}), ('get_installed_mods', {}),
               ('search_mods', {}), ('get_mod_config', dict(mod_id=app.ids[0])), ('search_mods', dict(query='other'))]
    with ScriptedAIProvider([dict(calls=queries), dict(text='四项证据已取得，其余查询未执行。')], fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        result = reply(app)
        assert result['completed'] and len(provider.requests) == 2
        rows = NativeScenario.results(provider.requests[-1]['body'])
        assert sum(value.get('error_code') == 'AI_TOOL_LIMIT' for _, value in rows) == 2
        assert 'tools' not in provider.requests[-1]['body']


def test_native_lock_timeout_retains_evidence_and_finishes_without_retry(app, monkeypatch):
    monkeypatch.setattr('backend.ai.tools.TOOL_SECONDS', .04)
    turns = [dict(calls=[('get_mod_workspace', {}), ('get_runtime_reconciliation', {})]),
             dict(text='库存查询超时；依据已选日志继续分析，未取得的状态保持未知。')]
    with ScriptedAIProvider(turns, fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        with app.context.native_mods._lock:  # Actual native lock held by a different thread.
            result = reply(app)
        assert result['completed'] and '超时' in result['query_notice']
        rows = NativeScenario.results(provider.requests[-1]['body'])
        assert [value['error_code'] for _, value in rows] == ['AI_TOOL_TIMEOUT', 'AI_TOOL_SKIPPED']
        assert 'tools' not in provider.requests[-1]['body']


def test_cancel_native_lock_wait_releases_ai_job_without_writing(app):
    before = files(app.game), files(app.persistent)
    with ScriptedAIProvider([dict(calls=[('get_mod_workspace', {})])], fragment_size=4096, delay=0) as provider:
        configure(app, provider)
        with app.context.native_mods._lock:
            task = start(app)
            provider.wait_requests()
            app.context.jobs.cancel(task)
            assert finish(app.context.api, task)['state'] == 'cancelled'
        assert len(provider.requests) == 1
    assert (files(app.game), files(app.persistent)) == before
