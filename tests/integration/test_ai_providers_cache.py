"""Actual provider routes, tasks and persistent cache; localhost AI only."""
import json

import pytest

from tests.integration.harness.ai_provider import SyntheticAIProvider
from tests.integration.harness.transaction_recovery import request
from tests.unit.test_ai_privacy import active, call, configured, finish, open_context


def add(context, provider, name='翻译服务', **extra):
    result = call(context, 'ai.providers.save', dict(name=name, base_url=provider.base_url,
        model='translation-model', remember=False, **extra))
    assert 'saved_provider_id' in result, result
    return result['saved_provider_id']


def bind(context, purpose, identity):
    return call(context, 'ai.providers.bind', dict(purpose=purpose, provider_id=identity))


def detail(monkeypatch, context, **extra):
    value = dict(workshop_id='3828868413', title='Item Details', description='Show more item details.',
                 description_truncated=False) | extra
    monkeypatch.setattr(context.workshop, 'translation_detail', lambda *args, **kwargs: value)
    monkeypatch.setattr(context.workshop, 'cached_detail', lambda *args: value)
    return value


def translate(context, *, force_refresh=False, workshop_id='3828868413'):
    response = context.api.dispatch(request('workshop.translate', dict(workshop_id=workshop_id, force_refresh=force_refresh)))
    assert response['ok'], response
    return response['data']['task_id']


def lookup(context, workshop_id='3828868413'):
    return call(context, 'ai.translation.cache', dict(workshop_id=workshop_id))


def test_legacy_profile_migration_preserves_vault_record_and_endpoint_key_isolation(active):
    config = dict(provider='openai_compatible', base_url='https://synthetic.invalid/v1', model='old-model')
    active.preferences.update('ai', config)
    old_account = active.ai.credentials.account(config)
    active.ai.credentials.set(config, 'owned-old-saved-key', True)
    settings = call(active, 'ai.providers.list')
    assert settings['bindings'] == dict(analysis='default', translation='default')
    assert settings['providers'][0]['model'] == 'old-model'
    assert settings['providers'][0]['key_status'] == 'saved'
    assert active.ai.credentials.account(active.ai._config()) == old_account
    second = call(active, 'ai.providers.save', dict(name='Other account', **config,
        api_key='owned-second-saved-key', remember=True))['saved_provider_id']
    alternate = active.ai._config(provider_id=second)
    assert active.ai.credentials.account(alternate) != old_account
    assert active.ai.credentials.get(alternate)[0] == 'owned-second-saved-key'
    assert active.ai.credentials.get(active.ai._config())[0] == 'owned-old-saved-key'
    public = json.dumps(call(active, 'ai.providers.list')) + active.paths.config.read_text(encoding='utf-8')
    assert 'owned-old-saved-key' not in public and 'owned-second-saved-key' not in public


def test_provider_crud_bindings_and_secrets_survive_reopen_without_requests(active):
    with SyntheticAIProvider() as provider:
        configured(active, provider)
        identity = add(active, provider, api_key='owned-session-key')
        assert call(active, 'ai.providers.delete', dict(provider_id='default'))['error_code'] == 'AI_PROVIDER_IN_USE'
        bind(active, 'translation', identity)
        result = call(active, 'ai.providers.save', dict(provider_id=identity, name='Renamed',
            base_url=provider.base_url, model='translation-model', remember=False))
        assert next(row for row in result['providers'] if row['id'] == identity)['key_status'] == 'session'
        assert provider.requests == []
        root, credentials = active.paths.root, active.ai.credentials._backend
        active.close()
        reopened = open_context(root, credentials)
        try:
            result = call(reopened, 'ai.providers.list')
            assert result['bindings']['translation'] == identity
            assert next(row for row in result['providers'] if row['id'] == identity)['key_status'] == 'missing'
            bind(reopened, 'translation', 'default')
            assert len(call(reopened, 'ai.providers.delete', dict(provider_id=identity))['providers']) == 1
        finally:
            reopened.close()


def test_running_translation_uses_start_snapshot_while_purpose_bindings_change(active, monkeypatch):
    detail(monkeypatch, active, description='Open this panel for details. ' * 130)
    with SyntheticAIProvider(chunks=['第一服务译文'], delay=.2) as first, SyntheticAIProvider(chunks=['第二服务译文']) as second:
        configured(active, first, key='owned-first-key')
        other = add(active, second, api_key='owned-second-key')
        task = translate(active)
        first.wait_requests()
        bind(active, 'translation', other)
        bind(active, 'analysis', other)
        result = finish(active.api, task)
        assert result['data']['completed'] and result['data']['provider_id'] == 'default'
        assert len(first.requests) == 3 and second.requests == []
        assert all(row['headers']['Authorization'] == 'Bearer owned-first-key' for row in first.requests)
        assert call(active, 'ai.chat.send', dict(question='New conversation'))['model'] == 'translation-model'
        assert second.requests[0]['headers']['Authorization'] == 'Bearer owned-second-key'


def test_analysis_can_switch_provider_between_turns_without_losing_conversation(active):
    with SyntheticAIProvider(chunks=['First answer']) as first, SyntheticAIProvider(chunks=['Second answer']) as second:
        configured(active, first)
        before = call(active, 'ai.chat.send', dict(question='First question'))
        other = add(active, second)
        bind(active, 'analysis', other)
        after = call(active, 'ai.chat.send', dict(question='Follow up', session_id=before['session_id']))
        assert after['session_id'] == before['session_id'] and after['text'] == 'Second answer'
        assert 'First answer' in json.dumps(second.requests[0]['body'])


def test_complete_translation_replays_after_restart_without_provider_access(active, monkeypatch):
    original = detail(monkeypatch, active)
    with SyntheticAIProvider(chunks=['保存译文']) as provider:
        configured(active, provider)
        first = finish(active.api, translate(active))
        assert first['data']['completed'] and first['data']['cached'] is False
        assert lookup(active)['translation'] == dict(title='保存译文', description='保存译文')
        assert len(provider.requests) == 2
    # The provider is now stopped. Cache playback must not try HTTP at all.
    root, credentials = active.paths.root, active.ai.credentials._backend
    active.close()
    reopened = open_context(root, credentials)
    monkeypatch.setattr(reopened.workshop, 'translation_detail', lambda *args, **kwargs: original)
    monkeypatch.setattr(reopened.workshop, 'cached_detail', lambda *args: original)
    try:
        assert lookup(reopened)['hit']
        task = translate(reopened)
        result = finish(reopened.api, task)
        assert result['data']['completed'] and result['data']['cached'] is True
        from tests.integration.test_workshop_translate import pieces
        assert [row['text'] for row in pieces(reopened, task)] == ['保存译文', '保存译文']
    finally:
        reopened.close()


def test_source_model_item_changes_and_explicit_refresh_invalidate_exact_cache(active, monkeypatch):
    original = detail(monkeypatch, active)
    with SyntheticAIProvider(chunks=['译文']) as provider:
        configured(active, provider)
        assert finish(active.api, translate(active))['data']['completed']
        assert finish(active.api, translate(active))['data']['cached']
        assert len(provider.requests) == 2
        assert not lookup(active, '3828868414')['hit']
        original['description'] += ' Version two.'
        assert not lookup(active)['hit']
        assert finish(active.api, translate(active))['data']['completed']
        assert len(provider.requests) == 4
        assert finish(active.api, translate(active, force_refresh=True))['data']['cached'] is False
        assert len(provider.requests) == 6
        call(active, 'ai.providers.save', dict(provider_id='default', name='Default',
            base_url=provider.base_url, model='new-model'))
        assert not lookup(active)['hit']
        assert finish(active.api, translate(active))['data']['completed']
        assert len(provider.requests) == 8


def test_concurrent_duplicate_translation_pays_once_and_replays_waiter(active, monkeypatch):
    detail(monkeypatch, active)
    with SyntheticAIProvider(chunks=['译文'], delay=.2) as provider:
        configured(active, provider)
        first, second = translate(active), translate(active)
        results = [finish(active.api, task)['data'] for task in (first, second)]
        assert sorted(row['cached'] for row in results) == [False, True]
        assert len(provider.requests) == 2


@pytest.mark.parametrize('mode', ['eof', 'stall'])
def test_failed_or_cancelled_translation_is_not_cached(active, monkeypatch, mode):
    detail(monkeypatch, active)
    with SyntheticAIProvider(mode=mode) as provider:
        configured(active, provider)
        task = translate(active)
        if mode == 'stall':
            provider.wait_requests()
            active.jobs.cancel(task)
        result = finish(active.api, task)
        assert result['state'] == 'cancelled' if mode == 'stall' else result['data']['error_code'] == 'AI_STREAM_INCOMPLETE'
        assert not lookup(active)['hit']
        assert not active.ai.translation_cache.path.exists()


def test_truncated_source_is_not_a_complete_cache_entry(active, monkeypatch):
    detail(monkeypatch, active, description_truncated=True)
    with SyntheticAIProvider(chunks=['译文']) as provider:
        configured(active, provider)
        assert finish(active.api, translate(active))['data']['truncated']
        assert not lookup(active)['hit']


def test_corrupt_cache_does_not_silently_charge_to_replace_it(active, monkeypatch):
    detail(monkeypatch, active)
    with SyntheticAIProvider() as provider:
        configured(active, provider)
        active.ai.translation_cache.path.write_text('{broken', encoding='utf-8')
        result = finish(active.api, translate(active))
        assert result['data']['error_code'] == 'AI_TRANSLATION_CACHE_IO'
        assert provider.requests == []


def test_new_routes_reject_unapproved_fields(active):
    for command, payload in [
        ('ai.providers.list', dict(api_key='do-not-return')),
        ('ai.providers.bind', dict(purpose='unknown', provider_id='default')),
        ('ai.providers.clear_key', dict(provider_id='../elsewhere')),
        ('ai.translation.cache', dict(workshop_id='1', text='page supplied text')),
    ]:
        result = active.api.dispatch(request(command, payload))
        assert not result['ok'] and result['error']['code'] == 'INVALID_REQUEST'


def test_loading_source_cache_lookup_does_not_fetch_or_call_ai(active, monkeypatch):
    monkeypatch.setattr(active.workshop, 'cached_detail', lambda *args: None)
    monkeypatch.setattr(active.workshop, 'translation_detail', lambda *args, **kwargs: pytest.fail('lookup must not fetch'))
    assert lookup(active) == dict(hit=False, source_pending=True)


@pytest.mark.parametrize('flags', [dict(source_pending=True), dict(description_scope='summary')])
def test_summary_or_pending_source_never_spends_tokens_or_becomes_cache(active, monkeypatch, flags):
    detail(monkeypatch, active, **flags)
    with SyntheticAIProvider() as provider:
        configured(active, provider)
        result = finish(active.api, translate(active))
        assert result['data']['error_code'] == 'AI_TRANSLATION_SOURCE_PENDING'
        assert provider.requests == [] and not active.ai.translation_cache.path.exists()


def test_named_provider_connection_test_uses_selected_profile_without_rebinding(active):
    with SyntheticAIProvider() as first, SyntheticAIProvider() as second:
        configured(active, first)
        other = add(active, second)
        tested = call(active, 'ai.providers.test', dict(provider_id=other))
        assert tested['completed'] and tested['model'] == 'translation-model'
        assert len(second.requests) == 1 and first.requests == []
        assert call(active, 'ai.providers.list')['bindings'] == dict(analysis='default', translation='default')


def test_key_cannot_be_exposed_in_another_provider_label(active):
    with SyntheticAIProvider() as provider:
        configured(active, provider)
        add(active, provider, name='owned-secret-used-as-label')
        result = call(active, 'ai.providers.save', dict(name='Another', base_url=provider.base_url,
            model='different-model', api_key='owned-secret-used-as-label'))
        assert result['error_code'] == 'AI_SETTINGS_INVALID'
        assert provider.requests == []


def test_same_endpoint_model_profiles_have_separate_translation_caches(active, monkeypatch):
    detail(monkeypatch, active)
    with SyntheticAIProvider(chunks=['第一账号译文']) as provider:
        configured(active, provider, key='owned-first-account')
        other = call(active, 'ai.providers.save', dict(name='Second account', base_url=provider.base_url,
            model='synthetic-model', api_key='owned-second-account', remember=False))['saved_provider_id']
        assert finish(active.api, translate(active))['data']['completed']
        bind(active, 'translation', other)
        assert not lookup(active)['hit']
        provider.set_response(chunks=['第二账号译文'])
        assert finish(active.api, translate(active))['data']['cached'] is False
        assert lookup(active)['translation']['title'] == '第二账号译文'
        assert [row['headers']['Authorization'] for row in provider.requests] == [
            'Bearer owned-first-account', 'Bearer owned-first-account',
            'Bearer owned-second-account', 'Bearer owned-second-account']
        bind(active, 'translation', 'default')
        assert lookup(active)['translation']['title'] == '第一账号译文'
        assert finish(active.api, translate(active))['data']['cached'] and len(provider.requests) == 4
