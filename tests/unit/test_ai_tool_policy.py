"""PS-04-02 closed parameter validation; no external model or file operations."""
import json
from uuid import uuid4

import pytest

from backend.ai.gateway import AIError
from backend.ai.tool_policy import REGISTRY, ToolScope, parameters, schemas


SCOPE = ToolScope(str(uuid4()), str(uuid4()))
PACKAGE = dict(release_id=str(uuid4()), manifest_hash='a' * 64)


@pytest.mark.parametrize('name,arguments', [
    ('get_installation_summary', {}), ('search_mods', {}),
    ('search_mods', dict(query='壁纸', offset=3, limit=5)),
    ('get_package', PACKAGE), ('read_selected_log', dict(evidence_id=SCOPE.selected_evidence_id)),
    ('get_installed_mods', {}), ('get_mod_config', {}), ('get_mod_config', dict(adapter_id='pswallpaper')),
])
def test_registered_readonly_tool_arguments(name, arguments):
    result = parameters(name, json.dumps(arguments), SCOPE)
    assert all(result[key] == value for key, value in arguments.items())


@pytest.mark.parametrize('name', list(REGISTRY))
def test_no_installation_scope_denies_every_tool(name):
    with pytest.raises(AIError) as error:
        parameters(name, '{}', ToolScope())
    assert error.value.code == 'AI_TOOL_SCOPE'


@pytest.mark.parametrize('name', ['shell', 'filesystem.read', 'mod.action', 'plan.apply', 'config.save', 'delete_files'])
def test_write_shell_and_free_path_tools_are_not_registered(name):
    with pytest.raises(AIError) as error:
        parameters(name, '{"path":"C:/save.dat","approved":true}', SCOPE)
    assert error.value.code == 'AI_TOOL_UNKNOWN'


@pytest.mark.parametrize('name,arguments', [
    ('get_installation_summary', {'installation_id': str(uuid4())}),
    ('get_installation_summary', {'approved': True}),
    ('search_mods', {'path': 'C:/other-game'}),
    ('search_mods', {'offset': True}), ('search_mods', {'limit': False}),
    ('search_mods', {'offset': '0'}), ('search_mods', {'limit': 21}),
    ('search_mods', {'offset': -1}), ('search_mods', {'offset': 2001}),
    ('search_mods', {'query': 'x' * 101}),
    ('get_package', PACKAGE | {'release_id': '../Mods/mod.dll'}),
    ('get_package', PACKAGE | {'manifest_hash': 'bad'}),
    ('get_package', PACKAGE | {'command': 'delete'}),
    ('read_selected_log', {'evidence_id': 'C:/private.log'}),
    ('get_mod_config', {'adapter_id': 'savegame'}),
    ('get_mod_config', {'adapter_id': 'pswallpaper', 'changes': {'LogToFile': False}}),
])
def test_closed_fields_types_and_ranges(name, arguments):
    with pytest.raises(AIError) as error:
        parameters(name, json.dumps(arguments), SCOPE)
    assert error.value.code == 'AI_TOOL_ARGUMENTS'


@pytest.mark.parametrize('arguments', ['[]', 'null', 'true', '"text"', '{broken}',
    '{"limit":1,"limit":20}', '{"query":NaN}', '{"query":Infinity}',
    '{"query":"' + '界' * 1400 + '"}', None, {}])
def test_json_is_one_bounded_object_without_duplicates_or_nonfinite_values(arguments):
    with pytest.raises(AIError) as error:
        parameters('search_mods', arguments, SCOPE)
    assert error.value.code == 'AI_TOOL_ARGUMENTS'


@pytest.mark.parametrize('scope,evidence_id', [
    (ToolScope(SCOPE.installation_id), SCOPE.selected_evidence_id),
    (SCOPE, str(uuid4())),
])
def test_only_the_explicitly_selected_evidence_is_authorized(scope, evidence_id):
    with pytest.raises(AIError) as error:
        parameters('read_selected_log', json.dumps(dict(evidence_id=evidence_id)), scope)
    assert error.value.code == 'AI_TOOL_SCOPE'


def test_schema_list_matches_the_request_scope_and_contains_no_writes():
    assert schemas(ToolScope()) == []
    basic = schemas(ToolScope(SCOPE.installation_id))
    selected = schemas(SCOPE)
    assert {row['function']['name'] for row in basic} == set(REGISTRY) - {'read_selected_log'}
    assert {row['function']['name'] for row in selected} == set(REGISTRY)
    for row in selected:
        assert row['type'] == 'function'
        assert row['function']['parameters']['additionalProperties'] is False
