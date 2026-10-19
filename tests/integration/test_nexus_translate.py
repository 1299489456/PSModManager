"""Mod page translation through the actual DesktopApi and a localhost provider."""
import json

from tests.integration.harness.ai_provider import SyntheticAIProvider
from tests.integration.harness.transaction_recovery import request
from tests.unit.test_ai_privacy import active, configured, finish


def events(context, task_id):
    response = context.api.dispatch(request('task.subscribe', {'task_id': task_id, 'after_sequence': 0}))
    assert response['ok'], response
    return [event for event in response['data']['events'] if event.get('type') == 'progress' and event.get('stage') == 'translate']


def test_translation_sends_only_foreign_pieces_and_publishes_each_piece(active):
    long_line = 'Press F8 to open the panel. ' * 120
    active.nexus.translation_source = lambda mod_id, check: [
        ('summary', 'Sort your loot automatically.'),
        ('description', '[b]功能特性[/b] 这是已经写好的中文说明。\n' + long_line)]
    with SyntheticAIProvider(chunks=['译文']) as provider:
        configured(active, provider)
        started = active.api.dispatch(request('nexus.translate', {'mod_id': 81}))
        assert started['ok'], started
        terminal = finish(active.api, started['data']['task_id'])
        assert terminal['state'] == 'succeeded' and terminal['data']['completed'], terminal
        pieces = [event['data'] for event in events(active, started['data']['task_id'])]
        assert [(row['part'], row['index']) for row in pieces] == [('summary', 0), ('description', 0), ('description', 1), ('description', 2)]
        assert pieces[1]['text'].startswith('[b]功能特性[/b]')  # already Chinese: kept, not sent
        sent = [row['body']['messages'] for row in provider.requests]
        assert len(sent) == 3 and all(messages[0]['role'] == 'system' and 'BBCode' in messages[0]['content'] for messages in sent)
        assert not any('这是已经写好的中文说明' in messages[1]['content'] for messages in sent)
        assert ''.join(row['sep'] + row['text'] for row in pieces[1:]).count('译文') == 2


def test_translation_without_ai_settings_is_refused_before_any_request(active):
    called = []
    active.nexus.translation_source = lambda mod_id, check: called.append(mod_id) or []
    response = active.api.dispatch(request('nexus.translate', {'mod_id': 81}))
    assert not response['ok'] and response['error']['code'] == 'AI_NOT_CONFIGURED'
    assert called == []
