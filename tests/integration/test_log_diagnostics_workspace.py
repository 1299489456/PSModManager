"""Actual local snapshots -> desktop AI gateway -> local synthetic provider."""
import json
from uuid import uuid4

import pytest

from backend.ai.diagnostics_api import diagnostic_commands
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.transaction_recovery import TransactionFixture, request
from tests.integration.test_ai_tools import configure
from tests.unit.test_ai_privacy import MemoryCredentials, finish


@pytest.fixture
def app(tmp_path):
    fixture = TransactionFixture(tmp_path)
    fixture.context.ai.credentials._backend = MemoryCredentials()
    if 'ai.diagnostics.sources' not in fixture.context.api._commands:
        fixture.context.api._commands.update(diagnostic_commands(fixture.context))
    (fixture.game / 'MelonLoader/Latest.log').write_text(
        '[12:00:00] Game started\n[12:00:01] ERROR ModAlpha dependency missing\n'
        'token=do-not-send-password\nC:\\Users\\PrivatePerson\\AppData\\test\n', encoding='utf-8')
    yield fixture
    fixture.close()


def sources(app, installation=True):
    return app.call('ai.diagnostics.sources', {'installation_id': app.installation} if installation else {})['sources']


def preview(app, *, include_context=True, manager=False):
    rows = sources(app)
    chosen = [row['source_id'] for row in rows if row['default'] and (manager or row['kind'] == 'game')]
    return app.call('ai.diagnostics.preview', dict(installation_id=app.installation, source_ids=chosen, include_context=include_context))


def send(app, snapshot, question='分析错误', session_id=None):
    payload = dict(snapshot_id=snapshot['snapshot_id'], question=question)
    if session_id:
        payload['session_id'] = session_id
    response = app.context.api.dispatch(request('ai.diagnostics.send', payload))
    assert response['ok'], response
    terminal = finish(app.context.api, response['data']['task_id'], timeout=12)
    assert terminal['state'] == 'succeeded', terminal
    return terminal['data']


def test_open_preview_no_network_and_original_readable_with_redaction(app):
    with ScriptedAIProvider([{'text': 'not called'}]) as provider:
        configure(app, provider, key='saved-preview-key')
        result = preview(app, manager=True)
        assert provider.requests == []
        assert len(result['logs']) == 2
        log = next(row for row in result['logs'] if 'Latest.log' in row['relative_path'])
        assert 'ModAlpha dependency missing' in log['text']
        assert 'do-not-send-password' not in log['text']
        assert 'PrivatePerson' not in log['text']
        assert '[已隐藏]' in log['text']
        assert result['context']['installation']['game'] == 'Probably Stolen'
        assert result['context']['installation']['runtime_verified'] is False
        assert result['redacted'] is True


def test_analysis_really_calls_provider_and_followup_reuses_visible_snapshot(app):
    with ScriptedAIProvider([{'text': 'Latest.log 第 2 行显示缺少依赖。'}, {'text': '先检查 ModAlpha 的依赖版本。'}]) as provider:
        configure(app, provider)
        snapshot = preview(app)
        # Changes after preview must not replace the material the user agreed to send.
        (app.game / 'MelonLoader/Latest.log').write_text('new unselected content\n', encoding='utf-8')
        first = send(app, snapshot)
        assert first['completed'] and '缺少依赖' in first['text']
        second = send(app, snapshot, '具体怎么检查？', first['session_id'])
        assert second['completed'] and second['session_id'] == first['session_id']
        assert len(provider.requests) == 2
        text = json.dumps([row['body'] for row in provider.requests], ensure_ascii=False)
        assert 'ModAlpha dependency missing' in text
        assert '2: [12:00:01] ERROR' in text
        assert 'new unselected content' not in text
        assert 'do-not-send-password' not in text
        assert 'PrivatePerson' not in text
        assert 'get_installed_mods' in text
        assert '具体怎么检查' in text
        assert all('snapshot_id' not in row['body'] for row in provider.requests)


def test_manager_only_analysis_does_not_need_selected_game_or_tools(app):
    with ScriptedAIProvider([{'text': '管理器日志中启动成功。'}]) as provider:
        configure(app, provider)
        manager = sources(app, installation=False)[0]
        snapshot = app.call('ai.diagnostics.preview', dict(source_ids=[manager['source_id']], include_context=False))
        result = send(app, snapshot)
        assert result['completed']
        assert 'tools' not in provider.requests[0]['body']
        assert snapshot['context'] == {}


@pytest.mark.parametrize('command,payload', [
    ('ai.diagnostics.sources', {'path': 'C:/private/log.txt'}),
    ('ai.diagnostics.preview', {'source_ids': [], 'path': 'C:/private/log.txt'}),
    ('ai.diagnostics.preview', {'source_ids': [str(uuid4())] * 4}),
    ('ai.diagnostics.send', {'snapshot_id': str(uuid4()), 'question': 'hello', 'selected_text': 'forged logs'}),
])
def test_closed_routes_reject_client_paths_or_forged_logs(app, command, payload):
    result = app.context.api.dispatch(request(command, payload))
    assert not result['ok'] and result['error']['code'] == 'INVALID_REQUEST', result


def test_unknown_source_and_missing_snapshot_are_explicit_errors(app):
    result = app.context.api.dispatch(request('ai.diagnostics.preview', {'source_ids': [str(uuid4())]}))
    assert not result['ok'] and result['error']['code'] == 'AI_LOG_SOURCE_INVALID'
    result = app.context.api.dispatch(request('ai.diagnostics.send', {'snapshot_id': str(uuid4()), 'question': 'hello'}))
    assert not result['ok'] and result['error']['code'] == 'AI_DIAGNOSTIC_EXPIRED'


def test_empty_unconfigured_and_changed_snapshot_do_not_claim_analysis(app):
    snapshot = preview(app)
    result = app.context.api.dispatch(request('ai.diagnostics.send', {'snapshot_id': snapshot['snapshot_id'], 'question': 'hello'}))
    assert not result['ok'] and result['error']['code'] == 'AI_NOT_CONFIGURED'
    with ScriptedAIProvider([{'text': 'done'}]) as provider:
        configure(app, provider)
        first = send(app, snapshot)
        changed = preview(app)
        result = app.context.api.dispatch(request('ai.diagnostics.send', {'snapshot_id': changed['snapshot_id'], 'session_id': first['session_id'], 'question': 'next'}))
        assert not result['ok'] and result['error']['code'] == 'AI_DIAGNOSTIC_CHANGED'
        assert len(provider.requests) == 1
        (app.game / 'MelonLoader/Latest.log').write_text('', encoding='utf-8')
        empty = preview(app)
        result = app.context.api.dispatch(request('ai.diagnostics.send', {'snapshot_id': empty['snapshot_id'], 'question': 'hello'}))
        assert not result['ok'] and result['error']['code'] == 'AI_LOG_EMPTY'
        assert len(provider.requests) == 1


def test_large_utf8_log_is_bounded_numbered_and_marked_truncated(app):
    (app.game / 'MelonLoader/Latest.log').write_text('很早的错误不在本次片段内\n' + '信息\n' * 5000 + '最后一行错误\n', encoding='utf-8')
    snapshot = preview(app, include_context=False)
    log = snapshot['logs'][0]
    assert log['truncated'] and len(log['text'].encode('utf-8')) <= 8192 and log['line_count'] <= 240
    assert '最后一行错误' in log['text'] and log['selection_mode'] == 'error_context'
    with ScriptedAIProvider([{'text': '分析末尾片段'}]) as provider:
        configure(app, provider)
        assert send(app, snapshot)['completed']
        assert '资料已截断' in json.dumps(provider.requests[0]['body'], ensure_ascii=False)


def test_unselected_and_hardlinked_logs_never_read(app, tmp_path):
    import os
    archive = app.game / 'MelonLoader/Logs'
    archive.mkdir(exist_ok=True)
    (archive / 'old.log').write_text('unselected archive private content', encoding='utf-8')
    result = preview(app)
    assert 'unselected archive' not in json.dumps(result)
    private = tmp_path / 'private.log'
    private.write_text('hardlinked confidential log', encoding='utf-8')
    os.link(private, archive / 'linked.log')
    row = next(row for row in sources(app) if row['relative_path'].endswith('linked.log'))
    assert row['status'] == 'unreadable'
    result = app.context.api.dispatch(request('ai.diagnostics.preview', dict(installation_id=app.installation, source_ids=[row['source_id']])))
    assert not result['ok'] and result['error']['code'] == 'AI_LOG_UNREADABLE'
    assert 'hardlinked confidential' not in json.dumps(result)


def test_reader_can_page_back_through_entire_log_without_gaps(app):
    original = ''.join(f'日志行 {index:05d} 中文内容\n' for index in range(5000))
    (app.game / 'MelonLoader/Latest.log').write_text(original, encoding='utf-8')
    source_id = next(row['source_id'] for row in sources(app) if row['kind'] == 'game' and row['default'])
    pages, before = [], None
    while True:
        payload = dict(installation_id=app.installation, source_id=source_id)
        if before is not None:
            payload['before'] = before
        page = app.call('ai.diagnostics.read', payload)
        assert page['bytes_read'] <= 32768
        pages.insert(0, page['text'])
        if page['previous_before'] is None:
            break
        assert before is None or page['previous_before'] < before
        before = page['previous_before']
        assert len(pages) < 30
    assert len(pages) > 1
    assert ''.join(pages) == original


def test_ai_excerpt_finds_error_earlier_than_tail_reading_window(app):
    original = 'boot\nERROR dependency missing earlier\n  at ModAlpha.Load()\n' + 'later routine info\n' * 2000
    (app.game / 'MelonLoader/Latest.log').write_text(original, encoding='utf-8')
    snapshot = preview(app)
    log = snapshot['logs'][0]
    assert log['selection_mode'] == 'error_context'
    assert 'ERROR dependency missing earlier' in log['text']
    assert 'ModAlpha.Load' in log['text']
    assert log['error_matches'] == 1
    assert log['truncated']
    source_id = log['source_id']
    page = app.call('ai.diagnostics.read', dict(installation_id=app.installation, source_id=source_id))
    assert 'ERROR dependency missing earlier' not in page['text']


def test_context_includes_discovered_mods_before_package_adoption(app):
    (app.game / 'Mods/ExternalLogContext.dll').write_bytes(b'synthetic-unadopted-mod')
    result = preview(app)
    mods = result['context']['mods']
    assert any(row['display_name'] == 'ExternalLogContext' for row in mods['items'])
    assert mods['enabled_count'] >= 1
    assert mods['total'] >= 1


def test_utf16_log_reader_preserves_complete_lines(app):
    original = ''.join(f'UTF16 日志 {index}\n' for index in range(3000))
    (app.game / 'MelonLoader/Latest.log').write_text(original, encoding='utf-16')
    source_id = next(row['source_id'] for row in sources(app) if row['kind'] == 'game' and row['default'])
    pages, before = [], None
    while True:
        args = dict(installation_id=app.installation, source_id=source_id)
        if before is not None:
            args['before'] = before
        page = app.call('ai.diagnostics.read', args)
        pages.insert(0, page['text'])
        before = page['previous_before']
        if before is None:
            break
        assert len(pages) < 10
    assert ''.join(pages) == original


def test_old_log_warning_and_actual_file_time_reach_model(app):
    import os
    log_path = app.game / 'MelonLoader/Latest.log'
    os.utime(log_path, (1000, 1000))
    result = preview(app, include_context=False)
    log = result['logs'][0]
    assert log['freshness'] == 'predates_game_files'
    assert log['source_modified_at'].startswith('1970-01-01T00:16:40')
    assert log['source_modified_at'] != log['captured_at']
    assert '历史参考' in log['freshness_message']
    with ScriptedAIProvider([{'text': '这是更新前的历史日志，请先取得更新后的启动日志。'}]) as provider:
        configure(app, provider)
        assert send(app, result)['completed']
        sent = json.dumps(provider.requests[0]['body'], ensure_ascii=False)
        assert '1970-01-01T00:16:40' in sent and '可能来自更新前' in sent
    log_path.write_text('fresh log\n', encoding='utf-8')
    current = preview(app)['logs'][0]
    assert current['freshness'] == 'not_older_than_game_files'
    assert not current['freshness_message']


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_large_logs_keep_startup_outside_tail_window_and_respect_budget(app, encoding):
    header = ('[12:00:00] MelonLoader v0.7.3 Open-Beta\n'
              '[12:00:00] Game Version: 0.49D\n'
              '[12:00:00] Unity Version: 2021.3.45f2\n'
              '[12:00:00] [Il2CppAssemblyGenerator] Assembly generation needed.\n'
              '[12:00:00] [Il2CppAssemblyGenerator] token=do-not-send-password\n')
    text = header + 'routine 中文 line\n' * 25000 + '[12:01:00] ERROR newest failure\n'
    (app.game / 'MelonLoader/Latest.log').write_text(text, encoding=encoding)
    result = preview(app)
    log = result['logs'][0]
    startup = log['startup_context']
    assert 'Game Version: 0.49D' in startup['text']
    assert 'Unity Version: 2021.3.45f2' in startup['text']
    assert 'Assembly generation needed' in startup['text']
    assert 'do-not-send-password' not in startup['text']
    assert 'newest failure' in log['text']
    assert len((startup['text'] + log['text']).encode('utf-8')) <= 8192
    assert len(startup['line_numbers']) + log['line_count'] <= 240
    with ScriptedAIProvider([{'text': '按日志中的版本和生成阶段分析。'}]) as provider:
        configure(app, provider)
        assert send(app, result)['completed']
        sent = json.dumps(provider.requests[0]['body'], ensure_ascii=False)
        assert '文件开头' in sent and 'Game Version: 0.49D' in sent and 'newest failure' in sent
        assert 'do-not-send-password' not in sent


def test_error_flood_does_not_discard_boot_versions_and_generation(app):
    header = ('[12:00:00] MelonLoader v0.7.3 Open-Beta\n'
              '[12:00:00] Game Version: 0.49D\n'
              '[12:00:00] Unity Version: 2021.3.45f2\n'
              '[12:00:01] [Il2CppAssemblyGenerator] Assembly is up to date. No Generation Needed.\n')
    (app.game / 'MelonLoader/Latest.log').write_text(header + ('ERROR mod failure ' + 'x' * 300 + '\n') * 300, encoding='utf-8')
    log = preview(app)['logs'][0]
    assert log['startup_context'] is None
    for line in header.splitlines():
        assert line in log['text']
    assert len(log['text'].encode()) <= 8192 and log['line_count'] <= 240


def test_current_environment_does_not_reuse_stored_or_historical_versions(app, monkeypatch):
    from backend.games.probably_stolen import fingerprint
    (app.game / 'UnityPlayer.dll').write_bytes(b'synthetic metadata')
    monkeypatch.setattr(fingerprint, 'file_version', lambda p: '0.7.3' if p.name == 'MelonLoader.dll' else '2021.3.45f2')
    result = preview(app)['context']['installation']
    assert result['game_version'] is None
    assert result['unity_version'] == '2021.3.45f2'
    assert result['loader_version'] == '0.7.3'
    assert result['freshness'] == 'current_files' and result['runtime_verified'] is False


def test_manager_log_does_not_get_game_update_warning(app):
    result = preview(app, manager=True)
    log = next(row for row in result['logs'] if row['relative_path'] == 'logs/psmodmanager.log')
    assert log['freshness'] == 'unknown'
    assert log['game_files_modified_at'] is None and log['freshness_message'] == ''
