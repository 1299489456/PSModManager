"""PS-04-01: real closed desktop bridge, localhost SSE and owned credentials.

All game/library resources and provider traffic are synthetic. Provider captures
and fake keys stay in memory; no external AI service is contacted.
"""
import json
import time
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.integration.harness.ai_provider import SyntheticAIProvider
from tests.integration.harness.transaction_recovery import request


class MemoryCredentials:
    def __init__(self, *, fail_write=False):
        self.values = {}
        self.calls = []
        self.fail_write = fail_write

    def get_password(self, service, account):
        self.calls.append(('get', service, account))
        return self.values.get((service, account))

    def set_password(self, service, account, key):
        self.calls.append(('set', service, account))
        if self.fail_write:
            raise OSError('credential storage denied ' + key)
        self.values[service, account] = key

    def delete_password(self, service, account):
        self.calls.append(('delete', service, account))
        self.values.pop((service, account), None)


@pytest.mark.parametrize('text', ['token=owned-sensitive-value', 'password: owned-sensitive-value',
                                 'api_key="owned-sensitive-value"'])
def test_repeated_card_and_history_redaction_preserves_hidden_marker(text):
    from backend.ai.redaction import redact
    once=redact(text)
    assert 'owned-sensitive-value' not in once
    assert redact(once)==once


def open_context(root, backend=None):
    context = DesktopContext(resolve_paths(str(root)))
    if backend is not None:
        context.ai_credentials_backend = backend
    context.initialize()
    context.api = DesktopApi(context)
    return context


@pytest.fixture
def active(tmp_path_factory):
    context = open_context(tmp_path_factory.mktemp('ai') / 'data', MemoryCredentials())
    try:
        yield context
    finally:
        context.close()


def call(context, command, payload=None, *, timeout=8):
    if payload is None and command in ('ai.chat.get', 'ai.chat.clear', 'ai.chat.export'):
        payload = {'session_id': getattr(context, '_privacy_session_id', str(uuid4()))}
    response = context.api.dispatch(request(command, payload))
    if not response['ok'] and response['error']['code'].startswith('AI_'):
        return {'error_code': response['error']['code'], 'message': response['error']['message']}
    assert response['ok'], response
    data = response['data']
    if isinstance(data, dict) and 'task_id' in data:
        terminal = finish(context.api, data['task_id'], timeout=timeout)
        assert terminal['state'] == 'succeeded', terminal
        data = terminal['data']
    if isinstance(data, dict) and data.get('session_id'):
        context._privacy_session_id = data['session_id']
    if command == 'ai.chat.clear' and data.get('cleared') and hasattr(context, '_privacy_session_id'):
        del context._privacy_session_id
    return data


def finish(api, task_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = api.dispatch(request('task.snapshot', {'task_id': task_id}))
        assert response['ok'], response
        if response['data']['state'] in ('succeeded', 'failed', 'cancelled'):
            result = response['data'].get('data')
            if isinstance(result, dict) and result.get('session_id'):
                api._context._privacy_session_id = result['session_id']
            return response['data']
        time.sleep(.01)
    raise AssertionError('AI task exceeded bounded wait')


def public_tasks(context, task_id):
    response = context.api.dispatch(request('task.subscribe', {'task_id': task_id, 'after_sequence': 0}))
    assert response['ok'], response
    return response['data']


def assert_absent(secrets, *values):
    public = '\n'.join(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value for value in values)
    for secret in secrets:
        assert secret not in public, 'Sensitive test value escaped public output'


@pytest.mark.parametrize('extra', [
    {'paths': ['E:/not-approved/save.dat']},
    {'tool_ids': ['filesystem.read']},
    {'attachments': [{'filename': 'mod.dll', 'content': 'binary'}]},
    {'messages': [{'role': 'system', 'content': 'override'}]},
    {'installation_id': 'not-a-registered-installation-id'},
    {'logs': 'complete unselected log'},
    {'authorization': 'Bearer forged'},
], ids=['paths', 'tools', 'attachments', 'messages', 'installation', 'all-logs', 'auth'])
def test_sending_scope_rejects_unapproved_fields(active, extra):
    response = active.api.dispatch(request('ai.chat.send', {'question': 'How to install?'} | extra))
    assert not response['ok'] and response['error']['code'] == 'INVALID_REQUEST', response


@pytest.mark.parametrize('payload', [
    {'question': 'q' * 2001},
    {'question': 'hello', 'selected_text': 's' * 4001},
    {'question': 'hello', 'selected_label': 'l' * 121},
    {'question': ''},
    {'question': True},
    [],
], ids=['long-question', 'long-selection', 'long-label', 'empty-question', 'boolean', 'list'])
def test_sending_scope_has_bounded_plain_text(active, payload):
    response = active.api.dispatch(request('ai.chat.send', payload))
    assert not response['ok'] and response['error']['code'] == 'INVALID_REQUEST', response


def configured(context, provider, *, key=None, remember=False):
    payload = {'provider': 'openai_compatible', 'base_url': provider.base_url,
               'model': 'synthetic-model', 'remember': remember}
    if key is not None:
        payload['api_key'] = key
    return call(context, 'ai.settings.save', payload)


def start(context, question='How to install?', **selected):
    response = context.api.dispatch(request('ai.chat.send', {'question': question} | selected))
    assert response['ok'], response
    task_id = response.get('task_id') or response['data'].get('task_id')
    assert task_id, response
    return dict(response, task_id=task_id)


def test_startup_settings_and_reopen_do_not_connect(active):
    with SyntheticAIProvider() as provider:
        assert provider.requests == []
        initial = call(active, 'ai.settings.get')
        assert 'api_key' not in initial
        saved = configured(active, provider, key='owned-no-network-key')
        assert 'error_code' not in saved, saved
        call(active, 'ai.settings.get')
        assert provider.requests == []
        root = active.paths.root
        active.close()
        reopened = open_context(root, MemoryCredentials())
        try:
            loaded = call(reopened, 'ai.settings.get')
            assert loaded['base_url'] == provider.base_url
            assert provider.requests == []
        finally:
            reopened.close()


def test_missing_configuration_and_remote_key_are_clear_local_errors(active, monkeypatch):
    result = call(active, 'ai.chat.send', {'question': 'hello'})
    assert result.get('error_code') and result.get('message'), result
    monkeypatch.setattr(active.ai.gateway, 'stream', lambda *args: pytest.fail('Missing external key must not start HTTP'))
    call(active, 'ai.settings.save', {'provider': 'openai_compatible',
        'base_url': 'https://synthetic.invalid/v1', 'model': 'synthetic-model', 'remember': False})
    result = call(active, 'ai.chat.send', {'question': 'hello'})
    assert result.get('error_code') == 'AI_NOT_CONFIGURED' and result.get('message'), result


def test_actual_sse_progress_and_multi_turn_only_transmit_current_scope(active):
    key = 'owned-stream-key-' + uuid4().hex
    with SyntheticAIProvider(delay=.03) as provider:
        configured(active, provider, key=key)
        first = start(active, 'Explain this line', selected_text='Selected line only', selected_label='Manual selection')
        end = finish(active.api, first['task_id'])
        assert end['state'] == 'succeeded' and not end['data'].get('error_code'), end
        assert end['data']['text'] == '合成回答'
        events = public_tasks(active, first['task_id'])
        progress = [event for event in events['events'] if event['type'] == 'progress' and (event.get('data') or {}).get('text')]
        assert progress, events
        second = call(active, 'ai.chat.send', {'question': 'Second question', 'session_id': end['data']['session_id']})
        assert second['text'] == '合成回答'
        assert len(provider.requests) == 2
        first_body, second_body = [item['body'] for item in provider.requests]
        for item in provider.requests:
            assert item['path'] == '/v1/chat/completions'
            assert item['headers']['Authorization'] == 'Bearer ' + key
            assert set(item['body']) <= {'model', 'stream', 'messages'}
            assert item['body']['stream'] is True and item['body']['model'] == 'synthetic-model'
            assert all(set(message) == {'role', 'content'} for message in item['body']['messages'])
            assert_absent([key], item['body'])
        assert 'Selected line only' in json.dumps(first_body)
        assert 'Second question' not in json.dumps(first_body)
        assert '合成回答' in json.dumps(second_body, ensure_ascii=False)
        assert 'Second question' in json.dumps(second_body)
        assert all(message['role'] in ('system', 'user', 'assistant') for message in second_body['messages'])
        assert_absent([key], end, events, second, call(active, 'ai.chat.get'), call(active, 'ai.chat.export'),
                      call(active, 'ai.settings.get'), active.paths.config.read_text(encoding='utf-8'),
                      active.paths.log.read_text(encoding='utf-8'))


@pytest.mark.parametrize('mode', ['401', '429', '500', 'malformed', 'eof'],
                         ids=['unauthorized', 'rate-limit', 'server-error', 'malformed-sse', 'truncated-sse'])
def test_actual_provider_errors_are_clear_and_never_echo_key(active, mode):
    key = 'owned-error-key-' + uuid4().hex
    with SyntheticAIProvider(mode=mode) as provider:
        configured(active, provider, key=key)
        response = start(active)
        terminal = finish(active.api, response['task_id'])
        assert terminal['state'] == 'succeeded', terminal
        error = terminal['data']
        assert error.get('error_code') and error.get('message'), error
        assert len(provider.requests) == 1
        assert_absent([key], terminal, public_tasks(active, response['task_id']), call(active, 'ai.chat.get'),
                      call(active, 'ai.chat.export'), active.paths.log.read_text(encoding='utf-8'))


def test_manual_selection_question_response_and_export_are_redacted(active):
    key = 'owned-secret-key-' + uuid4().hex
    bearer = 'bearer-test-secret-' + uuid4().hex
    basic = 'dXNlcjpzb21lLXNlY3JldC1wYXNzd29yZA=='
    proxy = 'proxy-pass-' + uuid4().hex
    query = 'query-secret-' + uuid4().hex
    text = ('selected diagnostic line\nAuthorization: Bearer ' + bearer
            + '\nProxy-Authorization: Basic ' + basic
            + '\nhttps://synthetic-user:' + proxy + '@proxy.example/path?token=' + query
            + '&lang=zh\nknown key=' + key)
    with SyntheticAIProvider(chunks=[text]) as provider:
        configured(active, provider, key=key)
        result = call(active, 'ai.chat.send', {'question': 'Explain ' + key,
            'selected_text': text, 'selected_label': 'Selected ' + key})
        assert not result.get('error_code'), result
        outgoing = provider.requests[0]['body']
        assert 'selected diagnostic line' in json.dumps(outgoing)
        provider.set_response(chunks=['Next response'])
        followup = call(active, 'ai.chat.send', {'question': 'Continue explanation', 'session_id': result['session_id']})
        assert not followup.get('error_code'), followup
        assert_absent([key, bearer, basic, proxy, query], outgoing, result,
                      provider.requests[-1]['body'], followup,
                      call(active, 'ai.chat.get'), call(active, 'ai.chat.export'),
                      active.paths.log.read_text(encoding='utf-8'))


def test_known_key_split_across_response_chunks_never_leaks_prefix(active):
    key = 'owned-chunk-key-' + uuid4().hex
    midpoint = len(key) // 2
    first_half, second_half = key[:midpoint], key[midpoint:]
    with SyntheticAIProvider(chunks=['Before ', first_half, second_half, ' after'], delay=.03) as provider:
        configured(active, provider, key=key)
        response = start(active)
        end = finish(active.api, response['task_id'])
        assert end['state'] == 'succeeded' and not end['data'].get('error_code'), end
        public = public_tasks(active, response['task_id'])
        assert_absent([key, first_half, second_half], end, public,
                      call(active, 'ai.chat.get'), call(active, 'ai.chat.export'))
        assert 'Before' in end['data']['text'] and 'after' in end['data']['text']


def test_settings_keep_theme_unknown_fields_and_never_write_plain_key(active):
    config = json.loads(active.paths.config.read_text(encoding='utf-8'))
    config['future_extension'] = {'keep': [1, 2, 3]}
    config['theme_id'] = 'graphite-ash'
    active.paths.config.write_text(json.dumps(config), encoding='utf-8')
    key = 'owned-config-key-' + uuid4().hex
    with SyntheticAIProvider() as provider:
        result = configured(active, provider, key=key)
        assert not result.get('error_code'), result
        after = json.loads(active.paths.config.read_text(encoding='utf-8'))
        assert after['future_extension'] == config['future_extension']
        assert after['theme_id'] == 'graphite-ash'
        assert call(active, 'app.theme.get')['theme_id'] == 'graphite-ash'
        assert_absent([key], after, call(active, 'ai.settings.get'))
        assert provider.requests == []


def test_failed_keyring_save_keeps_current_memory_and_explains_session_scope(tmp_path_factory):
    backend = MemoryCredentials(fail_write=True)
    context = open_context(tmp_path_factory.mktemp('ai') / 'data', backend)
    key = 'owned-denied-key-' + uuid4().hex
    try:
        with SyntheticAIProvider() as provider:
            saved = configured(context, provider, key=key, remember=True)
            assert saved.get('message'), saved
            assert 'session' in json.dumps(saved).lower(), saved
            result = call(context, 'ai.chat.send', {'question': 'hello'})
            assert result['text'] == '合成回答', result
            assert provider.requests[0]['headers']['Authorization'] == 'Bearer ' + key
            assert backend.values == {}
            assert_absent([key], saved, call(context, 'ai.settings.get'), call(context, 'ai.chat.export'),
                          context.paths.config.read_text(encoding='utf-8'), context.paths.log.read_text(encoding='utf-8'))
    finally:
        context.close()


def test_memory_key_is_not_saved_and_session_is_unavailable_after_reopen(tmp_path_factory):
    backend = MemoryCredentials()
    root = tmp_path_factory.mktemp('ai') / 'data'
    context = open_context(root, backend)
    try:
        with SyntheticAIProvider() as provider:
            configured(context, provider, key='owned-memory-key')
            sent = call(context, 'ai.chat.send', {'question': 'hello'})
            assert not any(call_[0] == 'set' for call_ in backend.calls)
            context.close()
            context = open_context(root, backend)
            history = call(context, 'ai.chat.get', {'session_id': sent['session_id']})
            assert history.get('error_code') == 'AI_SESSION_NOT_FOUND', history
            result = call(context, 'ai.chat.send', {'question': 'after reopen'})
            assert result['text'] == '合成回答', result
            assert 'Authorization' not in provider.requests[-1]['headers']
            assert len(provider.requests) == 2
    finally:
        context.close()


def test_chat_clear_removes_history_from_next_outgoing_request(active):
    with SyntheticAIProvider() as provider:
        configured(active, provider, key='owned-clear-key')
        call(active, 'ai.chat.send', {'question': 'This history must be cleared'})
        call(active, 'ai.chat.clear')
        assert not call(active, 'ai.chat.get').get('messages')
        call(active, 'ai.chat.send', {'question': 'Fresh question'})
        body = json.dumps(provider.requests[-1]['body'])
        assert 'Fresh question' in body and 'This history must be cleared' not in body


def test_explicit_connection_test_connects_once_without_changing_chat(active):
    with SyntheticAIProvider() as provider:
        configured(active, provider, key='owned-test-connection-key')
        assert provider.requests == []
        call(active, 'ai.chat.send', {'question': 'Existing conversation'})
        before = call(active, 'ai.chat.get')
        count = len(provider.requests)
        result = call(active, 'ai.test')
        assert not result.get('error_code'), result
        assert len(provider.requests) == count + 1
        assert call(active, 'ai.chat.get') == before


def test_actual_cancel_stops_stalled_request_and_task_is_cancelled(active):
    with SyntheticAIProvider(mode='stall') as provider:
        configured(active, provider, key='owned-cancel-key')
        response = start(active)
        provider.wait_requests()
        start_time = time.monotonic()
        cancelled = active.api.dispatch(request('task.cancel', {'task_id': response['task_id']}))
        assert cancelled['ok'], cancelled
        end = finish(active.api, response['task_id'], timeout=3)
        assert end['state'] == 'cancelled', end
        assert time.monotonic() - start_time < 3
        assert len(provider.requests) == 1


def test_context_close_stops_stalled_http_within_job_shutdown_limit(active):
    with SyntheticAIProvider(mode='stall') as provider:
        configured(active, provider, key='owned-close-key')
        response = start(active)
        provider.wait_requests()
        manager = active.jobs
        start_time = time.monotonic()
        active.close()
        assert time.monotonic() - start_time < 3
        assert manager.snapshot(response['task_id'])['state'] == 'cancelled'


def test_key_accounts_are_independent_for_data_roots_and_provider_endpoints(tmp_path_factory):
    backend = MemoryCredentials()
    first = open_context(tmp_path_factory.mktemp('ai') / 'data', backend)
    second = open_context(tmp_path_factory.mktemp('ai') / 'data', backend)
    key = 'owned-scope-key-' + uuid4().hex
    try:
        with SyntheticAIProvider() as provider, SyntheticAIProvider() as other:
            configured(first, provider, key=key, remember=True)
            assert len(backend.values) == 1
            configured(second, provider)
            missing = call(second, 'ai.chat.send', {'question': 'No shared key'})
            assert missing['text'] == '合成回答', missing
            assert 'Authorization' not in provider.requests[-1]['headers']
            configured(first, other)
            missing = call(first, 'ai.chat.send', {'question': 'No key migration'})
            assert missing['text'] == '合成回答', missing
            assert 'Authorization' not in other.requests[-1]['headers']
            assert all(service == 'PSModManager' for _, service, _ in backend.calls)
            accounts = {account for _, _, account in backend.calls}
            assert len(accounts) >= 3
            assert all('RimCrow' not in account and str(first.paths.root) not in account for account in accounts)
    finally:
        first.close()
        second.close()


def test_real_win_vault_owned_key_survives_reopen_and_clear(tmp_path_factory, monkeypatch):
    from keyring.backends.Windows import WinVaultKeyring
    original_write = WinVaultKeyring._set_password
    original_delete = WinVaultKeyring._delete_password
    written = []
    deleted = []

    def record_write(self, target, account, value):
        # The standard Windows backend moves a pre-existing bare-service value.
        # Product code must operate on our scoped target directly instead.
        assert target != 'PSModManager' and target.endswith('@PSModManager')
        written.append(target)
        return original_write(self, target, account, value)

    def record_delete(self, target):
        assert target in written
        deleted.append(target)
        return original_delete(self, target)

    monkeypatch.setattr(WinVaultKeyring, '_set_password', record_write)
    monkeypatch.setattr(WinVaultKeyring, '_delete_password', record_delete)
    root = tmp_path_factory.mktemp('ai') / 'data'
    context = open_context(root)
    key = 'owned-native-key-' + uuid4().hex
    try:
        with SyntheticAIProvider() as provider:
            saved = configured(context, provider, key=key, remember=True)
            assert not saved.get('error_code'), saved
            assert written, 'Native Windows credential writer was not reached'
            assert_absent([key], saved, context.paths.config.read_text(encoding='utf-8'))
            context.close()
            context = open_context(root)
            result = call(context, 'ai.chat.send', {'question': 'After saved-key reopen'})
            assert result['text'] == '合成回答', result
            assert provider.requests[-1]['headers']['Authorization'] == 'Bearer ' + key
            cleared = call(context, 'ai.credentials.clear')
            assert not cleared.get('error_code') and deleted, cleared
            assert all(WinVaultKeyring()._read_credential(target) is None for target in written)
            context.close()
            context = open_context(root)
            result = call(context, 'ai.chat.send', {'question': 'After key deletion'})
            assert result['text'] == '合成回答', result
            assert 'Authorization' not in provider.requests[-1]['headers']
            assert len(provider.requests) == 2
    finally:
        context.close()
        # The test only deletes targets it itself created, even after failure.
        for target in set(written):
            original_delete(WinVaultKeyring(), target)


def test_provider_failure_does_not_block_actual_basic_mod_actions_or_upload_library(tmp_path_factory):
    from tests.integration.harness.transaction_recovery import TransactionFixture
    fixture = TransactionFixture(tmp_path_factory.mktemp('ai'))
    release = fixture.publish({'Mods/selected.png': b'opaque-library-file-not-question'}, 'Unselected library metadata')
    try:
        with SyntheticAIProvider(mode='500') as provider:
            configured(fixture.context, provider, key='owned-unrelated-key')
            failure = fixture.call('ai.chat.send', {'question': 'Why failed?'})
            assert failure.get('error_code') and failure.get('message'), failure
            outgoing = json.dumps(provider.requests[0]['body'])
            assert_absent(['opaque-library-file-not-question', 'Unselected library metadata',
                           'isolated-save-never-deployed', str(fixture.game), str(fixture.data)], outgoing)
            for action, state in [('install', 'enabled'), ('disable', 'disabled'),
                                  ('enable', 'enabled'), ('uninstall', 'not_installed')]:
                result = fixture.call('mod.action', {'installation_id': fixture.installation,
                    'release_id': release['release_id'], 'manifest_hash': release['manifest_hash'],
                    'action': action, 'idempotency_key': str(uuid4())})
                assert result.get('completed') and result['state'] == state, result
                assert (fixture.game / 'Mods/selected.png').exists() == (state == 'enabled')
            assert len(provider.requests) == 1
            fixture.assert_sentinels()
    finally:
        fixture.close()


def test_disconnected_local_endpoint_is_explained_without_raw_exception(active):
    with SyntheticAIProvider() as provider:
        configured(active, provider, key='owned-offline-key')
    result = call(active, 'ai.chat.send', {'question': 'Disconnected localhost'})
    assert result.get('error_code') and result.get('message'), result
    assert_absent(['owned-offline-key', 'Traceback', 'ConnectionRefusedError'], result, call(active, 'ai.chat.export'))
