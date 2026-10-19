import copy
import json

import pytest

from backend.scanning.content_manifests import analyze, MAX_RESOURCE_BYTES

SHA = 'a' * 64
TIME = '2026-09-28T08:00:00Z'


def resource(framework='ItemForge', **changes):
    collection = 'Items' if framework == 'ItemForge' else 'Characters'
    data = dict(BundleId='same', RequiredRuntimeApi=19, BundleVersion='v1-local+原文',
                GeneratorVersion='not.semver', **{collection: [dict(Id='same')]})
    data.update(changes)
    name = framework + ('.items.json' if framework == 'ItemForge' else '.characters.json')
    return dict(name=name, embedded=True, json_text=json.dumps(data))


def report(*resources, melon=False):
    return dict(assembly_name='原文程序集', assembly_version='1.0.0.0', resources=list(resources), references=[],
                custom_attributes=[dict(type='MelonLoader.MelonInfoAttribute', decoded=True,
                    arguments=['Entry.Type', '名字', '0.5.x-preview', '作者', None])] if melon else [])


def run(value):
    original = copy.deepcopy(value)
    result = analyze(value, artifact_sha256=SHA, observed_at=TIME)
    assert value == original
    assert result['registration_status'] == 'unknown'
    return result


@pytest.mark.parametrize('api', [5, 12, 19])
def test_integer_api_versions_are_preserved(api):
    result = run(report(resource(RequiredRuntimeApi=api)))
    assert result['requirements'][0]['minimum'] == api
    assert result['requirements'][0]['requirement_raw'] == api
    assert result['manifests'][0]['bundle_version_raw'] == 'v1-local+原文'
    assert result['manifests'][0]['generator_version_raw'] == 'not.semver'


@pytest.mark.parametrize('framework,melon,expected', [
    ('ItemForge', False, ['itemforge_content']),
    ('CharacterForge', False, ['characterforge_content']),
    ('ItemForge', True, ['itemforge_content', 'melon_mod']),
    ('CharacterForge', True, ['characterforge_content', 'melon_mod']),
])
def test_roles_allow_content_only_and_dual_role(framework, melon, expected):
    result = run(report(resource(framework), melon=melon))
    assert result['roles'] == expected
    assert result['diagnostics'] == []


def test_plain_melon_and_unknown_are_not_content():
    assert run(report(melon=True))['roles'] == ['melon_mod']
    assert run(report())['roles'] == ['unknown']
    assert run(report(melon=True))['melon_info']['version_raw'] == '0.5.x-preview'


def test_same_ids_have_framework_and_type_namespaces_and_sources():
    result = run(report(resource(), resource('CharacterForge')))
    ids = result['content_ids']
    assert len({(item['framework'], item['type'], item['value']) for item in ids}) == 4
    assert result['roles'] == ['characterforge_content', 'itemforge_content']
    for entry in ids + result['requirements'] + result['manifests']:
        assert entry['source']['kind'] == 'embedded_metadata'
        assert entry['source']['reference'].startswith('artifact:' + SHA + '#resource:')
        assert entry['source']['observed_at'] == TIME
        assert entry['confidence'] == 'confirmed'


@pytest.mark.parametrize('text,code', [
    ('{broken', 'JSON_INVALID'), ('[]', 'MANIFEST_SCHEMA_INVALID'),
    ('{"BundleId":"x","BundleId":"y"}', 'DUPLICATE_JSON_KEY'),
    ('{"x":NaN}', 'NONFINITE_JSON'), ('[' * 30 + '0' + ']' * 30, 'JSON_DEPTH_LIMIT'),
])
def test_bad_json_does_not_destroy_other_manifest(text, code):
    bad = resource(); bad['json_text'] = text
    result = run(report(bad, resource('CharacterForge')))
    assert result['manifests'][0]['status'] == 'invalid'
    assert result['manifests'][1]['status'] == 'valid'
    assert result['roles'] == ['characterforge_content']
    assert result['diagnostics'][0]['code'] == code


@pytest.mark.parametrize('changes,code', [
    ({'RequiredRuntimeApi': True}, 'RUNTIME_API_INVALID'),
    ({'RequiredRuntimeApi': '19'}, 'RUNTIME_API_INVALID'),
    ({'RequiredRuntimeApi': -1}, 'RUNTIME_API_INVALID'),
    ({'RequiredRuntimeApi': 2147483648}, 'RUNTIME_API_INVALID'),
    ({'BundleVersion': 1}, 'INVALID_STRING'),
    ({'BundleId': 'a' * 257}, 'INVALID_STRING'),
])
def test_invalid_schema_is_explicit(changes, code):
    result = run(report(resource(**changes)))
    assert result['roles'] == ['unknown']
    assert result['requirements'] == []
    assert result['content_ids'] == []
    assert result['diagnostics'][0]['code'] == code


@pytest.mark.parametrize('changes,code', [
    ({'Items': [{'ID': 'not-assumed'}]}, 'CONTENT_ID_SCHEMA_UNKNOWN'),
    ({'Items': ['not-assumed']}, 'CONTENT_ID_SCHEMA_UNKNOWN'),
    ({'Items': None}, 'CONTENT_ID_SCHEMA_UNKNOWN'),
    ({'Items': [{'Id': 'x'}, {'Id': 'x'}]}, 'DUPLICATE_CONTENT_ID'),
    ({'Items': [{'Id': str(i)} for i in range(256)]}, 'CONTENT_ID_LIMIT'),
])
def test_unknown_content_schema_preserves_known_header(changes, code):
    result = run(report(resource(**changes)))
    assert result['roles'] == ['itemforge_content']
    assert result['requirements'][0]['minimum'] == 19
    assert result['manifests'][0]['status'] == 'partial'
    assert result['manifests'][0]['bundle_version_raw'] == 'v1-local+原文'
    assert [(i['type'], i['value']) for i in result['content_ids']] == [('bundle', 'same')]
    assert result['diagnostics'][0]['code'] == code


def test_optional_versions_remain_null():
    result = run(report(resource(BundleVersion=None, GeneratorVersion=None)))
    assert result['manifests'][0]['bundle_version_raw'] is None
    assert result['manifests'][0]['generator_version_raw'] is None


@pytest.mark.parametrize('mutation,code', [
    ({'json_text': ' ' * (MAX_RESOURCE_BYTES + 1)}, 'RESOURCE_BYTES_LIMIT'),
    ({'size': MAX_RESOURCE_BYTES + 1}, 'RESOURCE_BYTES_LIMIT'),
    ({'embedded': False}, 'RESOURCE_NOT_EMBEDDED'),
    ({'json_text': None}, 'JSON_UNAVAILABLE'),
])
def test_resource_limits(mutation, code):
    value = resource(); value.update(mutation)
    result = run(report(value))
    assert result['diagnostics'][0]['code'] == code
    assert result['requirements'] == []


def test_duplicate_resources_are_ambiguous_and_bounded():
    result = run(report(*([resource()] * 65)))
    assert len(result['manifests']) == 1
    assert result['roles'] == ['unknown']
    assert {d['code'] for d in result['diagnostics']} == {'RESOURCE_COUNT_LIMIT', 'DUPLICATE_RESOURCE'}


def test_exact_names_only_no_prefix_ownership_or_reference_guess():
    r = resource(); r['name'] = 'Prefix.ItemForge.items.json'
    metadata = report(r)
    metadata['assembly_name'] = 'PSItemPack_anything'
    metadata['references'] = [dict(name='ItemForge', version='19.0.0.0')]
    result = run(metadata)
    assert result['roles'] == ['unknown']
    assert result['requirements'] == []


def test_unknown_fields_diagnosed_without_executing_them():
    result = run(report(resource(Untrusted={'call': 'anything'}, Items=[dict(Id='x', Extra='ignored')])))
    assert result['roles'] == ['itemforge_content']
    assert {d['code'] for d in result['diagnostics']} == {'UNKNOWN_MANIFEST_FIELDS', 'UNKNOWN_CONTENT_FIELDS'}


def test_total_content_limit_and_diagnostics_bound():
    first = resource(Items=[dict(Id=str(i), Extra='ignored') for i in range(254)])
    result = run(report(first, resource('CharacterForge')))
    assert len(result['content_ids']) == 256
    assert result['manifests'][1]['status'] == 'partial'
    assert len(result['diagnostics']) == 32


def test_static_21_items_never_becomes_registration():
    result = run(report(resource(Items=[dict(Id=str(i)) for i in range(21)])))
    assert len([i for i in result['content_ids'] if i['type'] == 'item']) == 21
    assert result['registration_status'] == 'unknown'


def test_malformed_melon_info_does_not_erase_valid_content():
    metadata = report(resource(), melon=True)
    metadata['custom_attributes'][0]['decoded'] = False
    result = run(metadata)
    assert result['roles'] == ['itemforge_content']
    assert result['melon_info'] is None
    assert result['diagnostics'][0]['code'] == 'MELON_INFO_UNREADABLE'
