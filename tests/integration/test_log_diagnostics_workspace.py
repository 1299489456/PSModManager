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
