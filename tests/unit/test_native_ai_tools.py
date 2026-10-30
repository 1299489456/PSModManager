"""Native AI projections and review-only suggestions; no game or provider writes."""
import copy
import json
import time
import threading
from types import SimpleNamespace

import pytest

from backend.ai.budgets import RequestBudget
from backend.ai.desktop_runtime import DesktopAssistant
from backend.ai.gateway import AIError
from backend.ai.plan_proposals import NATIVE_TOOL_NAME, PlanProposals, schema
from backend.ai.tool_policy import NATIVE_REGISTRY, ToolScope, parameters, schemas
from backend.ai.tools import ReadOnlyTools


INSTALLATION_ID = '11111111-1111-4111-8111-111111111111'
SCOPE = ToolScope(INSTALLATION_ID)


class ForbiddenLegacy:
    paths = None

    def __getattr__(self, key):
        raise AssertionError(f'native AI must not read or execute legacy {key}')


class NativeFixture:
    def __init__(self):
        self._lock = threading.RLock()
        self.calls = []
        self.value = dict(revision='native-revision', enabled_ids=['item_details', 'CyberElectronics'],
            paths={'persistent_root': 'C:/nonexistent-owned-test-native-data'},
            runtime=dict(state='stopped', command_line='private-command'),
            log=dict(source='Player.log', status='available', source_modified_at='2026-10-28', path='C:/private/Player.log'), mods=[
                dict(id='item_details', name='物品详情', version='0.4.1', installed=True,
                    target_enabled=True, source='workshop', workshop_id='3828868413',
                    prerequisites=[], capabilities=[], approval=dict(state='approved', scan_status='historical'),
                    run=dict(state='running', audit_lines=[23], running_entry_count=1,
                        events=[dict(kind='startup_audit', line=23)], trust_state='private-trust', log_path='C:/private/Player.log')),
                dict(id='CyberElectronics', name='电子产品扩展', version='1.4.31', installed=True,
                    target_enabled=True, source='workshop', workshop_id='3828902092',
                    prerequisites=['item_details'], capabilities=['files'],
                    approval=dict(state='approved', scan_status='historical', fingerprint='private-trust'),
                    run=dict(state='not_running', evidence=[], log_path='C:/private/Player.log')),
            ])

    def snapshot(self, installation):
        self.calls.append('snapshot')
        assert installation['installation_id'] == INSTALLATION_ID
        return copy.deepcopy(self.value)

    def detail(self, installation, mod_id):
        self.calls.append('detail')
        row = copy.deepcopy(next(row for row in self.value['mods'] if row['id'] == mod_id))
        row.update(author='Custom Mod', files=[dict(relative_path='Assemblies/Mod.dll', size=100)],
            sources=[dict(source='workshop', workshop_id='3828902092', active=True, folder_path='C:/private')])
        return dict(revision=self.value['revision'], mod=row)

    def config_read(self, installation, mod_id):
        self.calls.append('config_read')
        return dict(id=mod_id, revision='config-revision', exists=True, path='C:/private/config.json', entries=[
            dict(key='ShowPrices', value='true'), dict(key='SteamPassword', value='private-password'),
            dict(key='AccessToken', value='private-token'), dict(key='Server', value='https://user:pass@host.invalid/path?token=private-query&lang=zh'),
            dict(key='Title', value='owned-key-secret'), dict(key='Cookie', value='private-cookie'),
        ])

    def validate(self, installation, enabled_ids):
        self.calls.append('validate')
        return dict(enabled_ids=enabled_ids, sorted_ids=enabled_ids, issues=[], blocking=[], approval_required_ids=[])

    def __getattr__(self, key):
        raise AssertionError(f'native AI may not call {key}')


@pytest.fixture
def tools():
    installations = SimpleNamespace(list=lambda: dict(selected_installation_id=INSTALLATION_ID,
        installations=[dict(installation_id=INSTALLATION_ID)]))
    native = NativeFixture()
    return ReadOnlyTools(installations, ForbiddenLegacy(), ForbiddenLegacy(), ForbiddenLegacy(), ForbiddenLegacy(),
        native=native, installation_resolver=lambda identity: dict(installation_id=identity,
            canonical_root='C:/private/game', runtime_kind='mono', app_id='4348910'))


def execute(tools, name, data=None, secrets=()):
    return json.loads(tools.execute(name, json.dumps(data or {}), SCOPE, lambda: None, secrets))['data']


def test_native_registry_uses_manifest_ids_and_removes_old_package_contract():
    assert {row['function']['name'] for row in schemas(SCOPE, native=True)} == set(NATIVE_REGISTRY) - {'read_selected_log'}
    assert 'get_package' not in NATIVE_REGISTRY
    assert parameters('get_mod_config', '{"mod_id":"probablystolen.workshop.uploader"}', SCOPE, native=True)['mod_id'] == 'probablystolen.workshop.uploader'
    assert [row['function']['name'] for row in schema(SCOPE, workspace=True, native=True)] == [NATIVE_TOOL_NAME]


@pytest.mark.parametrize('mod_id', ['../secret', '.hidden', 'C:/private', 'space id', '汉字', 'x' * 65, ''])
def test_native_tool_identity_is_not_an_arbitrary_file_path(mod_id):
    with pytest.raises(AIError, match='AI_TOOL_ARGUMENTS'):
        parameters('get_mod_config', json.dumps(dict(mod_id=mod_id)), SCOPE, native=True)


def test_native_states_remain_separate_and_paginated(tools):
    data = execute(tools, 'get_mod_workspace', dict(limit=1))
    assert data['next_offset'] == 1 and data['total'] == 2
    assert data['revision'] == 'native-revision' and data['runtime'] == {'state': 'stopped'}
    assert data['items'][0]['run']['state'] == 'running'  # Log evidence, despite stopped process.
    second = execute(tools, 'get_installed_mods', dict(offset=1))['items'][0]
    assert second['installed'] and second['target_enabled']
    assert second['approval']['state'] == 'approved' and second['run']['state'] == 'not_running'
    assert not data['runtime_verified'] and data['order_policy'] == 'saved_order_not_initialization_order'
    assert execute(tools, 'search_mods', dict(query='Cyber'))['items'][0]['id'] == 'CyberElectronics'
    assert execute(tools, 'get_installation_summary')['runtime_kind'] == 'unknown'


def test_config_preserves_string_values_unknown_types_and_redacts_key_value_credentials(tools):
    data = execute(tools, 'get_mod_config', dict(mod_id='item_details'), secrets=('owned-key-secret',))
    values = {row['key']: row for row in data['entries']}
    assert values['ShowPrices']['value'] == 'true'
    assert all(row['type'] == 'unknown' for row in data['entries'])
    assert values['SteamPassword']['redacted'] and values['Cookie']['redacted']
    serialized = json.dumps(data)
    for secret in ('private-password', 'private-token', 'private-query', 'owned-key-secret', 'private-cookie', 'user:pass'):
        assert secret not in serialized
    assert 'lang=zh' in values['Server']['value']
    assert 'path' not in data


def test_native_detail_dependencies_and_runtime_do_not_expose_trust_or_paths(tools):
    detail = execute(tools, 'get_mod_detail', dict(mod_id='CyberElectronics'))
    assert detail['mod']['capabilities'] == ['files']
    assert detail['mod']['files'] == [dict(relative_path='Assemblies/Mod.dll', size=100)]
    assert 'private' not in json.dumps(detail)
    deps = execute(tools, 'get_mod_dependencies', dict(mod_id='item_details'))
    assert deps['required_by'] == ['CyberElectronics'] and not deps['applied']
    run = execute(tools, 'get_runtime_reconciliation')
    assert run['log'] == dict(source='Player.log', status='available', source_modified_at='2026-10-28')
    assert run['items'][0]['run']['events'] == [dict(kind='startup_audit', line=23)]
    assert 'private' not in json.dumps(run)


@pytest.mark.parametrize('name,data', [('get_package', {}), ('mod.action', {}), ('config.save', {}),
    ('get_mod_config', {'mod_id': 'item_details', 'approved': True}), ('get_mod_config', {'adapter_id': 'pswallpaper'})])
def test_native_tools_reject_old_entrypoints_writes_and_approval_fields(tools, name, data):
    with pytest.raises(AIError):
        execute(tools, name, data)


def test_scope_change_or_missing_resolver_never_reads_native_data(tools):
    tools.installations.list = lambda: dict(selected_installation_id=None, installations=[])
    with pytest.raises(AIError, match='AI_TOOL_SCOPE'):
        execute(tools, 'get_mod_workspace')
    assert tools.native.calls == []


def test_unreadable_native_state_remains_visible_and_cannot_create_suggestions(tools):
    tools.native.value.update(ready=False, issues=[dict(code='NATIVE_STATE_FORMAT', severity='error', path='C:/private/state')])
    data = execute(tools, 'get_mod_workspace')
    assert not data['ready'] and data['issues'] == [dict(code='NATIVE_STATE_FORMAT', severity='error')]
    with pytest.raises(AIError, match='AI_TOOL_UNAVAILABLE'):
        PlanProposals(tools).prepare(json.dumps(dict(mod_id='item_details', revision='native-revision',
            action='disable', reason='review')), SCOPE, [], lambda: None, name=NATIVE_TOOL_NAME)


def test_native_read_preserves_cancellation_and_releases_service_lock(tools, monkeypatch):
    def interrupted(installation):
        raise InterruptedError('cancelled native read')
    monkeypatch.setattr(tools.native, 'snapshot', interrupted)
    with pytest.raises(InterruptedError):
        execute(tools, 'get_mod_workspace')
    acquired = []
    def other_thread():
        if tools.native._lock.acquire(timeout=.1):
            acquired.append(True)
            tools.native._lock.release()
    worker = threading.Thread(target=other_thread)
    worker.start(); worker.join()
    assert acquired == [True]


def test_suggestion_only_navigates_and_cannot_be_reused_after_state_change(tools):
    proposals, pending = PlanProposals(tools), []
    args = dict(mod_id='CyberElectronics', revision='native-revision', action='disable', reason='核对扩展模组的加载记录。')
    value = json.loads(proposals.prepare(json.dumps(args), SCOPE, pending, lambda: None, name=NATIVE_TOOL_NAME))
    assert value['data']['executed'] is False and value['data']['suggestion']['kind'] == 'native_mod'
    result = proposals.resolve(pending[0], SCOPE)
    assert result == dict(kind='native_mod', installation_id=INSTALLATION_ID, mod_id='CyberElectronics',
        action='disable', revision='native-revision', navigation=dict(page='mods', mod_id='CyberElectronics'))
    assert tools.native.calls == ['snapshot', 'snapshot']
    assert tools.native.value['enabled_ids'] == ['item_details', 'CyberElectronics']
    tools.native.value['revision'] = 'changed'
    with pytest.raises(AIError, match='AI_SUGGESTION_STALE'):
        proposals.resolve(pending[0], SCOPE)


def test_native_mode_blocks_legacy_suggestions_including_preexisting_cards(tools):
    proposals = PlanProposals(tools)
    for name in ('suggest_mod_action', 'suggest_workspace_action'):
        with pytest.raises(AIError, match='AI_TOOL_UNKNOWN'):
            proposals.prepare('{}', SCOPE, [], lambda: None, name=name)
    old = dict(installation_id=INSTALLATION_ID, expires_at=time.monotonic()+600, kind='workspace')
    with pytest.raises(AIError, match='AI_SUGGESTION_STALE'):
        proposals.resolve(old, SCOPE)
    assert tools.native.calls == []


def test_native_suggestion_metadata_is_redacted_even_after_credentials_clear(tools):
    tools.native.value['mods'][0]['name'] = 'owned-key-secret'
    pending = []
    proposals = PlanProposals(tools)
    proposals.prepare(json.dumps(dict(mod_id='item_details', revision='native-revision', action='disable',
        reason='owned-key-secret')), SCOPE, pending, lambda: None, ('owned-key-secret',), name=NATIVE_TOOL_NAME)
    assert 'owned-key-secret' not in json.dumps(proposals.view(pending[0]))


def test_native_local_query_timeout_preserves_evidence_and_finishes_without_more_disk_queries(tools, monkeypatch):
    assistant = object.__new__(DesktopAssistant)
    assistant.tools, assistant.proposals = tools, PlanProposals(tools)
    assistant.credentials = SimpleNamespace(secrets=[])
    calls = []
    def stream(config, key, messages, check, emit, **options):
        calls.append(copy.deepcopy(messages))
        options['budget'].provider(messages)
        if options.get('tools'):
            offered = {item['function']['name'] for item in options['tools']}
            assert NATIVE_TOOL_NAME in offered and 'suggest_mod_action' not in offered
            return SimpleNamespace(text='已有日志已保留。', tool_calls=[
                dict(id='native-timeout', function=dict(name='get_mod_workspace', arguments='{}')),
                dict(id='skip-after-timeout', function=dict(name='get_runtime_reconciliation', arguments='{}'))])
        results = [json.loads(row['content']) for row in messages if row['role'] == 'tool']
        assert [row['error_code'] for row in results] == ['AI_TOOL_TIMEOUT', 'AI_TOOL_SKIPPED']
        return '本地状态尚未取得；依据已有日志继续排查。'
    assistant.gateway = SimpleNamespace(stream=stream)
    monkeypatch.setattr('backend.ai.tools.TOOL_SECONDS', -1)
    state = {}
    text = assistant._query_rounds({}, None, [dict(role='system', content='read-only'), dict(role='user', content='diagnose')],
        SCOPE, lambda: None, lambda *args: None, SimpleNamespace(progress=lambda *args: None),
        'session', (), RequestBudget(), [], state)
    assert '依据已有日志' in text and '已有日志已保留' in text and '超时' in state['notice']
    assert len(calls) == 2 and tools.native.calls == []
