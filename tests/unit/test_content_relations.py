from copy import deepcopy

import pytest

from backend.catalog.relations import build_graph


TIME = '2026-09-28T08:00:00Z'


def source(kind='embedded_metadata', reference='Mods/content.dll#ItemForge.items.json'):
    return dict(kind=kind, reference=reference, observed_at=TIME)


def entry(name, **changes):
    row = dict(entry_id=name, relative_path=f'Mods/{name}.dll', sha256='a' * 64,
               status='managed', presence='present', metadata={'references': []},
               assembly_name=name, assembly_version='1.0.0.0', analysis={})
    row.update(changes)
    return row


def graph(rows, declarations=()):
    return build_graph(rows, generation=7, installation_id='installation', observed_at=TIME,
                       declarations=declarations)


def requirement(api=19):
    return dict(framework='itemforge', capability='itemforge.runtime_api', minimum=api,
                requirement_raw=api, source=source(), confidence='confirmed')


def content(framework='itemforge', type='bundle', value='same'):
    return dict(framework=framework, type=type, value=value, source=source(), confidence='confirmed')


def test_assembly_reference_is_soft_compile_hint_not_minimum_compatibility():
    rows = [entry('Consumer', metadata={'references': [{'name': 'Library', 'version': '5.0.0.0'}]}),
            entry('Library', assembly_version='1.0.0.0')]
    edge, = graph(rows)['relations']
    assert edge['kind'] == 'soft_dependency' and edge['confidence'] == 'inferred'
    assert edge['requirement_raw'] == '5.0.0.0' and 'minimum' not in edge
    assert edge['compatibility'] == 'unknown' and edge['resolution'] == 'candidate'
    assert edge['to_id'] == 'Library' and edge['source']['kind'] == 'embedded_metadata'


def test_missing_assembly_reference_and_empty_refs_do_not_claim_dependency_completeness():
    result = graph([entry('Consumer', metadata={'references': [{'name': 'External', 'version': '1.0'}]}), entry('Empty')])
    assert result['relations'][0]['resolution'] == 'unresolved'
    assert result['relations'][0]['capability'] == 'assembly:External'
    assert all(n['dependency_coverage'] == 'unknown' for n in result['nodes'])


@pytest.mark.parametrize('api', [5, 12, 19])
def test_api_integer_kept_separate_and_named_runtime_does_not_prove_support(api):
    result = graph([entry('Content', analysis={'requirements': [requirement(api)]}),
                    entry('ItemForgeRuntime', assembly_version='99.0.0.0')])
    edge, = result['relations']
    assert edge['minimum'] == edge['requirement_raw'] == api
    assert edge['candidate_ids'] == ['ItemForgeRuntime']
    assert edge['resolution'] == 'candidate' and edge['compatibility'] == 'unknown'
    assert result['plan_blocking_evaluated'] is False


def test_missing_runtime_and_unparsed_dll_are_different():
    row = entry('Content', analysis={'requirements': [requirement()]})
    assert graph([row])['relations'][0]['resolution'] == 'missing'
    unknown = entry('Opaque', status='hash_only', assembly_name=None)
    assert graph([row, unknown])['relations'][0]['resolution'] == 'unknown'


def test_missing_inventory_rows_neither_provide_runtime_nor_conflict():
    rows = [entry('Content', analysis={'requirements': [requirement()], 'content_ids': [content()]}),
            entry('ItemForgeRuntime', presence='missing', analysis={'content_ids': [content()]})]
    result = graph(rows)
    assert len(result['nodes']) == 2
    assert len(result['relations']) == 1 and result['relations'][0]['resolution'] == 'missing'


def test_content_id_namespace_is_framework_and_type():
    rows = [entry('A', analysis={'content_ids': [content()]}),
            entry('B', analysis={'content_ids': [content('characterforge')]}),
            entry('C', analysis={'content_ids': [content(type='item')]}),
            entry('D', analysis={'content_ids': [content()]})]
    edge, = graph(rows)['relations']
    assert edge['kind'] == 'content_id_conflict' and edge['from_id'] == 'A' and edge['to_id'] == 'D'
    assert edge['namespace'] == {'framework': 'itemforge', 'type': 'bundle'}
    assert edge['runtime_effect'] == 'unknown' and edge['target_source'] == source()


def test_dual_roles_registration_and_unknown_prediction_preserved():
    prediction = dict(status='unknown', reasons=['equal_version'], rule_version=None)
    node, = graph([entry('A', analysis={'roles': ['melon_mod', 'itemforge_content', 'melon_mod']}, prediction=prediction)])['nodes']
    assert node['roles'] == ['itemforge_content', 'melon_mod']
    assert node['prediction'] == prediction and node['registration_status'] == 'unknown'


def test_shadow_relation_requires_loader_rule_and_preserves_scope():
    rows = [entry('Old', shadowed_by=['Mods/New.dll'], prediction={'rule_version': 'melonloader-0.7.3'}),
            entry('New', sha256='b' * 64)]
    edge, = graph(rows)['relations']
    assert edge['kind'] == 'assembly_shadow' and edge['source']['kind'] == 'loader_rule'
    assert edge['fingerprint_scope'] == dict(installation_id='installation', generation=7,
                                            from_sha256='a' * 64, to_sha256='b' * 64)
    rows[0]['prediction'] = {'rule_version': None}
    assert graph(rows)['relations'] == []


@pytest.mark.parametrize('kind,origin,confidence', [('hard_dependency', 'author_manifest', 'confirmed'),
    ('soft_dependency', 'user', 'confirmed'), ('runtime_interaction', 'heuristic', 'inferred')])
def test_backend_declarations_preserve_original_evidence(kind, origin, confidence):
    declaration = dict(from_id='A', to_id='B', kind=kind, requirement_raw='raw ~= build 12',
                       source=source(origin), confidence=confidence)
    edge, = graph([entry('A'), entry('B')], [declaration])['relations']
    assert all(edge[k] == declaration[k] for k in declaration)
    assert edge['resolution'] == 'not_evaluated' and edge['declaration'] is True


def test_historical_21_registration_claim_is_separate_from_static_prediction():
    rows=[entry('Content',analysis={'roles':['itemforge_content'],'requirements':[requirement(5)]}),entry('Runtime')]
    historical=dict(from_id='Content',to_id='Runtime',kind='runtime_interaction',
                    requirement_raw='historical session: 21 content configurations registered',
                    source=source('runtime_log','synthetic-log/session-old/epoch-1/bytes-0-64'),confidence='confirmed')
    result=graph(rows,[historical])
    assert {r['source']['kind'] for r in result['relations']}=={'embedded_metadata','runtime_log'}
    assert all(n['registration_status']=='unknown' for n in result['nodes'])
    assert result['relations'][-1]['source']['reference']==historical['source']['reference']
    assert result['relations'][-1]['resolution']=='not_evaluated'


@pytest.mark.parametrize('change', [dict(source=source('heuristic'), confidence='confirmed'),
    dict(source=source('alien')), dict(resolution='compatible'), dict(minimum=19),
    dict(to_id='missing'), dict(capability='also-target'), dict(requirement_raw=True),
    dict(confidence='certain'), dict(kind='execute')])
def test_invalid_declarations_cannot_upgrade_or_inject_graph_facts(change):
    declaration = dict(from_id='A', to_id='B', kind='hard_dependency', requirement_raw='1.0',
                       source=source('author_manifest'), confidence='confirmed')
    declaration.update(change)
    with pytest.raises(ValueError):
        graph([entry('A'), entry('B')], [declaration])


@pytest.mark.parametrize('time', ['yesterday', '2026-09-28T08:00:00', '2026-09-28T08:00:00+08:00'])
def test_sources_require_utc_observation_time(time):
    declaration = dict(from_id='A', to_id='B', kind='hard_dependency', requirement_raw=None,
                       source=dict(source('user'), observed_at=time), confidence='confirmed')
    with pytest.raises(ValueError):
        graph([entry('A'), entry('B')], [declaration])


def test_deterministic_ids_and_input_not_mutated():
    rows = [entry('A', analysis={'requirements': [requirement()]})]
    before = deepcopy(rows)
    assert graph(rows) == graph(rows) and rows == before
    assert graph(rows)['relations'][0]['relation_id'] != build_graph(rows, generation=8,
        installation_id='installation', observed_at=TIME)['relations'][0]['relation_id']


def test_collision_explosion_is_bounded_and_reported_partial():
    result = graph([entry(str(i), analysis={'content_ids': [content()]}) for i in range(100)])
    assert len(result['relations']) == 4096 and result['coverage'] == 'partial'
    assert result['diagnostics'][-1]['code'] == 'GRAPH_RELATION_LIMIT'


def test_analysis_diagnostic_is_bound_to_generation_and_hash():
    result = graph([entry('A', analysis={'diagnostics': [{'code': 'INVALID_JSON'}]})])
    assert result['diagnostics'][0]['detail']['code'] == 'INVALID_JSON'
    assert result['diagnostics'][0]['fingerprint_scope']['generation'] == 7
