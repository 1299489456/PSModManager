"""Cold local reads and timeout recovery through the real localhost AI gateway."""
import json
from types import SimpleNamespace

import pytest

from backend.ai import tools as tools_module
from backend.ai.tool_policy import ToolScope
from backend.storage.package_repository import PackageRepository
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.test_ai_tools import app, configure, tool_results
from tests.integration.test_log_diagnostics_workspace import preview, send


def test_search_filters_names_before_verifying_package_files(app, monkeypatch):
    verified = []
    original = PackageRepository.release
    def counted(repository, release_id, *args, **kwargs):
        verified.append(str(release_id))
        return original(repository, release_id, *args, **kwargs)
    monkeypatch.setattr(PackageRepository, 'release', counted)
    query = app.context.packages.search('不存在的包')
    assert query['items'] == [] and query['scanned'] == 2
    assert verified == []
    query = app.context.packages.search('壁纸')
    assert [item['release_id'] for item in query['items']] == [app.release['release_id']]
    assert query['items'][0]['integrity'] == 'verified'
    assert verified == [app.release['release_id']]
    # A matching corrupt package must still be reported as corrupt.
    with app.context.plans._store(lambda: None) as store:
        entry = store.connection.execute('SELECT source_hash FROM file_entries WHERE release_id=?',
                                        (app.release['release_id'],)).fetchone()
    path = app.context.packages.library / entry[0][:2] / entry[0]
    path.write_bytes(b'changed synthetic library bytes')
    assert app.context.packages.search('壁纸')['items'][0]['integrity'] == 'corrupt'


def test_sparse_search_reaches_later_pages_without_skipping_matches(app):
    for number in range(24):
        app.publish({f'Mods/synthetic-{number}.png': b'owned search fixture'}, f'查找目标 {number}')
    offset, found = 0, []
    while offset is not None:
        page = app.context.packages.search('查找目标', offset=offset, limit=3)
        assert page['scanned'] >= len(page['items'])
        found.extend(row['release_id'] for row in page['items'])
        assert page['next_offset'] is None or page['next_offset'] > offset
        offset = page['next_offset']
    assert len(found) == len(set(found)) == 24


def test_first_cold_query_over_old_ten_second_limit_completes_and_can_repeat(app, monkeypatch):
    (app.game / 'MelonLoader/Latest.log').write_text('ERROR SyntheticMod dependency missing\n', encoding='utf-8')
    snapshot = preview(app)
    # Advance only the tool's clock; do not replace services or the HTTP gateway.
    clock = [0.0]
    monkeypatch.setattr(tools_module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    search = app.context.packages.search
    calls = []
    def cold_once(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            clock[0] += 12
        return search(*args, **kwargs)
    monkeypatch.setattr(app.context.packages, 'search', cold_once)
    turns = [{'calls': [('search_mods', {'query':'壁纸'})]}, {'text':'首次查询完成。'},
             {'calls': [('search_mods', {'query':'壁纸'})]}, {'text':'再次查询完成。'}]
    with ScriptedAIProvider(turns) as provider:
        configure(app, provider)
        for _ in range(2):
            result = send(app, snapshot)
            assert result.get('completed') and not result['query_notice'], result
        assert len(calls) == 2 and len(provider.requests) == 4
        assert all(tool_results(provider.requests[index]['body'])[0]['ok'] for index in (1,3))


def test_query_timeout_finishes_selected_logs_and_skips_remaining_queries(app, monkeypatch):
    (app.game / 'MelonLoader/Latest.log').write_text('ERROR SyntheticMod dependency missing\n', encoding='utf-8')
    snapshot = preview(app)
    clock = [0.0]
    monkeypatch.setattr(tools_module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    search = app.context.packages.search
    searches = []
    def timeout_once(*args, **kwargs):
        searches.append(True)
        if len(searches) == 1:
            clock[0] += tools_module.TOOL_SECONDS + 1
        return search(*args, **kwargs)
    monkeypatch.setattr(app.context.packages, 'search', timeout_once)
    monkeypatch.setitem(app.context.ai.tools._handlers, 'get_installed_mods',
                        lambda *args: pytest.fail('No further disk query after a timeout'))
    turns = [dict(text='先根据日志核对依赖。', calls=[('get_installation_summary', {}),
                ('search_mods', {'query':'壁纸'}), ('get_installed_mods', {})]),
             dict(text='日志显示 SyntheticMod 缺少依赖。包库查询超时，版本尚未核实。'),
             dict(calls=[('search_mods', {'query':'壁纸'})]), dict(text='追问查询已完成。')]
    with ScriptedAIProvider(turns) as provider:
        configure(app, provider)
        result = send(app, snapshot)
        assert result.get('completed') and 'error_code' not in result, result
        assert '先根据日志' in result['text'] and '版本尚未核实' in result['text']
        assert '部分本地查询超时' in result['query_notice']
        final = provider.requests[1]['body']
        assert 'tools' not in final
        returned = tool_results(final)
        assert returned[0]['ok']
        assert returned[1]['error_code'] == 'AI_TOOL_TIMEOUT'
        assert returned[2]['error_code'] == 'AI_TOOL_SKIPPED'
        assert 'ERROR SyntheticMod dependency missing' in json.dumps(final, ensure_ascii=False)
        state = app.context.ai.get(result['session_id'])
        assert state['state'] == 'idle' and state['query_notice'] == result['query_notice']
        later = send(app, snapshot, '再查一下版本', result['session_id'])
        assert later.get('completed') and not later['query_notice']
        assert len(provider.requests) == 4


def test_local_tool_still_enforces_its_deadline(app, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(tools_module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def slow(scope, data, check):
        clock[0] += tools_module.TOOL_SECONDS + 1
        check()
    monkeypatch.setitem(app.context.ai.tools._handlers, 'get_installation_summary', slow)
    with pytest.raises(tools_module.AIError, match='AI_TOOL_TIMEOUT'):
        app.context.ai.tools.execute('get_installation_summary', '{}', ToolScope(app.installation), lambda: None)
