"""CASE-015/026: typed synthetic facts through the production graph algorithm."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID

import pytest

from backend.profiles import graph

FIXTURES = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))


def uid(number):
    return str(UUID(int=number))


def component(number, *, version='1.0', assembly=None, assembly_version='1.0.0.0', digest=None, content_ids=None, capabilities=None):
    return dict(schema_version=1, component_id=uid(number), release_id=uid(10000+number),
                artifact_hash=digest or f'{number:064x}', file_name=f'Mod{number}.dll',
                assembly_name=assembly or f'Mod{number}', assembly_version=assembly_version,
                melon_info={'name':f'Mod{number}'}, roles=['melon_mod'], content_ids=content_ids or [],
                version_raw=version, capabilities=capabilities or [])


def fingerprint(**changes):
    value = deepcopy(FIXTURES['RuntimeFingerprint'])
    value.update(loader_version='0.7.3', coverage='complete', exe_hash='1'*64, game_assembly_hash='2'*64, metadata_hash='3'*64)
    value.update(changes)
    return value


def relation(number, source, target=None, *, kind='hard_dependency', confidence='confirmed', source_kind='author_manifest', scope=True, requirement=None, capability=None):
    return dict(schema_version=1, relation_id=uid(20000+number), from_id=uid(source),
                to_id=uid(target) if target is not None else None, capability=capability, kind=kind,
                requirement_raw=requirement, source={'kind':source_kind,'reference':f'manual-rule-{number}',
                    'observed_at':'2026-09-29T00:00:00Z'}, confidence=confidence,
                fingerprint_scope=fingerprint()['fingerprint_id'] if scope is True else scope)


def file(number, release, path, *, root='mods', digest=None):
    return dict(schema_version=1, file_entry_id=uid(30000+number), release_id=uid(10000+release),
                source_hash=digest or f'{release:064x}', target_root_kind=root, relative_path=path,
                lifecycle='immutable', ownership='shared_identical')


def evaluate(components=None, relations=None, files=None, selected=None, **kwargs):
    return graph.evaluate_graph(components=components or [], relations=relations or [], files=files or [],
        fingerprint=kwargs.pop('fingerprint', fingerprint()),
        selected_release_ids=[uid(10000+i) for i in (selected if selected is not None else [1])], **kwargs)


def codes(result):
    return {finding['code'] for finding in result['findings']}


def test_case015_confirmed_scoped_missing_hard_dependency_blocks_with_source():
    edge = relation(1,1,2)
    result = evaluate([component(1)], [edge])
    assert result['blocking'] and result['runtime_compatibility'] == 'unknown'
    finding = next(item for item in result['findings'] if item['code']=='HARD_DEPENDENCY_MISSING')
    assert finding['source'] == dict(edge['source'],schema_version=1)
    assert finding['scope']['fingerprint_id'] == edge['fingerprint_scope']
    assert finding['relation_ids'] == [edge['relation_id']]
    assert not result['enable_preview']['complete']


@pytest.mark.parametrize('changes', [dict(kind='soft_dependency'),dict(source_kind='heuristic'),
    dict(source_kind='runtime_log'),dict(confidence='inferred'),dict(confidence='unknown'),dict(scope=None),
    dict(scope=uid(900))])
def test_case015_soft_inferred_runtime_and_unscoped_never_become_hard(changes):
    result = evaluate([component(1)], [relation(1,1,2,**changes)])
    assert not result['blocking']
    assert result['enable_preview']['added_release_ids'] == []


def test_case015_assemblyref_even_mislabelled_hard_is_only_hint():
    edge = relation(1,1,2,source_kind='embedded_metadata',requirement='9.9.9')
    edge['source']['reference'] = 'metadata/AssemblyRef/SomeLibrary'
    result = evaluate([component(1)], [edge])
    assert not result['blocking'] and 'DEPENDENCY_UNCONFIRMED' in codes(result)


def test_case015_persistable_explicit_component_identity_missing_and_present():
    edge = relation(1,1,capability='component:' + uid(2))
    absent = evaluate([component(1)],[edge])
    assert absent['blocking'] and 'HARD_DEPENDENCY_MISSING' in codes(absent)
    present = evaluate([component(1),component(2)],[edge],selected=[1,2])
    assert not present['blocking'] and present['enable_preview']['complete']
    edge['capability'] = 'runtime-name-that-is-not-component-identity'
    unknown = evaluate([component(1)],[edge])
    assert not unknown['blocking'] and 'CAPABILITY_UNKNOWN' in codes(unknown)


def test_case015_no_assemblyref_does_not_erase_reflection_runtime_relation():
    edge = relation(1,1,2,kind='runtime_interaction',source_kind='runtime_log')
    result = evaluate([component(1),component(2)], [edge], selected=[1,2])
    assert 'RUNTIME_INTERACTION' in codes(result) and not result['blocking']
    assert result['findings'][0]['relation_ids'] == [edge['relation_id']]


@pytest.mark.parametrize('version,requirement,expected', [('1.2.3','>=1.2, <2',True),
    ('2.0','>=1.2, <2',False),('1.2','1.2.0',True),('1.2.3','^1.2',None),
    ('1.2-beta','>=1.0',None),('nightly','1.0',None),('1.2','1.*',None),('1.2',19,None)])
def test_case015_explicit_version_range_semantics(version, requirement, expected):
    assert graph.version_satisfies(version,requirement) is expected


def test_case015_conflicting_manual_ranges_remain_separate_evidence():
    edges = [relation(1,1,2,source_kind='user',requirement='>=2'),relation(2,1,2,source_kind='user',requirement='<2')]
    result = evaluate([component(1),component(2,version='2.0')],edges,selected=[1,2])
    assert result['blocking']
    conflict = next(item for item in result['findings'] if item['code']=='DEPENDENCY_VERSION_UNSATISFIED')
    assert conflict['relation_ids'] == [edges[1]['relation_id']]
    assert not result['enable_preview']['complete']


def test_case015_unknown_release_version_cannot_borrow_assembly_version():
    result = evaluate([component(1),component(2,version=None,assembly_version='19.0.0.0')],
                      [relation(1,1,2,requirement='>=19')], selected=[1,2])
    assert 'DEPENDENCY_VERSION_UNKNOWN' in codes(result) and not result['blocking']


def test_case015_capability_cannot_borrow_assembly_name_or_version():
    result = evaluate([component(1),component(2,assembly='ItemForge',assembly_version='19.0.0.0')],
        [relation(1,1,capability='itemforge.runtime_api',kind='capability_requirement',requirement=19)],selected=[1,2])
    assert 'CAPABILITY_UNKNOWN' in codes(result) and not result['blocking']
    assert not result['enable_preview']['complete'] and result['unknowns']


def provider(api, **changes):
    value = dict(name='itemforge.runtime_api',value=api,source={'kind':'author_manifest','reference':'explicit-api',
        'observed_at':'2026-09-29T00:00:00Z'},confidence='confirmed',fingerprint_scope=fingerprint()['fingerprint_id'])
    value.update(changes)
    return value


def test_case015_explicit_capability_satisfies_and_unique_provider_closure():
    result = evaluate([component(1),component(2,capabilities=[provider(19)])],
        [relation(1,1,kind='capability_requirement',capability='itemforge.runtime_api',requirement=19)])
    assert result['enable_preview']['added_release_ids'] == [uid(10002)]
    assert result['enable_preview']['complete']
    satisfied = evaluate([component(1),component(2,capabilities=[provider(19)])],
        [relation(1,1,kind='capability_requirement',capability='itemforge.runtime_api',requirement=19)],selected=[1,2])
    assert not satisfied['blocking'] and 'CAPABILITY_UNKNOWN' not in codes(satisfied)


def test_case015_capability_known_low_api_and_ambiguous_providers():
    edge = relation(1,1,kind='capability_requirement',capability='itemforge.runtime_api',requirement=19)
    low = evaluate([component(1),component(2,capabilities=[provider(18)])],[edge],selected=[1,2])
    assert low['blocking'] and 'CAPABILITY_VERSION_UNSATISFIED' in codes(low)
    ambiguous = evaluate([component(1),component(2,capabilities=[provider(19)]),component(3,capabilities=[provider(20)])],[edge])
    assert not ambiguous['blocking'] and not ambiguous['enable_preview']['complete']
    assert ambiguous['enable_preview']['added_release_ids'] == []


def test_case015_case026_disabling_one_capability_provider_preserves_satisfied_consumers():
    components = [component(1),component(2,capabilities=[provider(19)]),component(3,capabilities=[provider(20)])]
    edge = relation(1,1,kind='capability_requirement',capability='itemforge.runtime_api',requirement=19)
    one = evaluate(components,[edge],selected=[1,2,3],requested_disable=[uid(10002)])
    assert one['disable_preview']['affected_release_ids'] == []
    both = evaluate(components,[edge],selected=[1,2,3],requested_disable=[uid(10002),uid(10003)])
    assert both['disable_preview']['affected_release_ids'] == [uid(10001)]


def test_case015_case026_long_chain_transitive_closure_and_reverse_influence():
    count = 75
    components = [component(i) for i in range(1,count+1)]
    relations = [relation(i,i,i+1) for i in range(1,count)]
    result = evaluate(components,relations)
    assert result['enable_preview']['release_ids'] == [uid(10000+i) for i in range(1,count+1)]
    assert result['enable_preview']['complete']
    reverse = evaluate(components,relations,selected=list(range(1,count+1)),requested_disable=[uid(10000+count)])
    assert reverse['disable_preview']['affected_release_ids'] == [uid(10000+i) for i in range(1,count)]
    chain = next(item for item in reverse['disable_preview']['reason_chains'] if item['release_id']==uid(10001))
    assert len(chain['relation_ids']) == count-1


def test_case026_diamond_preserves_every_edge_and_deterministic_results():
    components = [component(i) for i in range(1,5)]
    relations = [relation(1,1,2),relation(2,1,3),relation(3,2,4),relation(4,3,4)]
    result = evaluate(components,relations,selected=[1,2,3,4],requested_disable=[uid(10004)])
    assert result['disable_preview']['affected_release_ids'] == [uid(10001),uid(10002),uid(10003)]
    assert {edge for item in result['disable_preview']['reason_chains'] for edge in item['relation_ids']} == {r['relation_id'] for r in relations}
    again = evaluate(list(reversed(components)),list(reversed(relations)),selected=[4,3,2,1],requested_disable=[uid(10004)])
    assert result == again


def test_case026_cycle_keeps_edges_and_satisfiable_selected_set():
    relations = [relation(1,1,2),relation(2,2,3),relation(3,3,1)]
    result = evaluate([component(1),component(2),component(3)],relations,selected=[1,2,3],requested_disable=[uid(10001)])
    assert not result['blocking'] and result['enable_preview']['complete']
    assert result['cycles'][0]['relation_ids'] == [r['relation_id'] for r in relations]
    assert result['disable_preview']['affected_release_ids'] == [uid(10002),uid(10003)]
    assert 'DEPENDENCY_CYCLE' in codes(result)


def test_case026_mutual_exclusion_requires_confirmed_applicable_source():
    components = [component(1),component(2)]
    assert evaluate(components,[relation(1,1,2,kind='mutual_exclusion')],selected=[1,2])['blocking']
    assert not evaluate(components,[relation(1,1,2,kind='mutual_exclusion',source_kind='heuristic')],selected=[1,2])['blocking']


def test_case026_enable_closure_candidate_has_its_own_file_conflict_review():
    result = evaluate([component(1),component(2),component(3)],[relation(1,1,2)],
                      files=[file(1,2,'shared.dat'),file(2,3,'shared.dat')],selected=[1,3])
    assert result['enable_preview']['complete']
    assert result['enable_preview']['added_release_ids'] == [uid(10002)]
    assert result['enable_preview']['candidate_blocking']
    assert 'TARGET_HASH_CONFLICT' in result['enable_preview']['candidate_finding_codes']
    assert 'candidate_plan_ready' not in result['enable_preview']


def test_case026_disable_candidate_removes_conflict_without_changing_inputs():
    files = [file(1,1,'shared.dat'),file(2,2,'shared.dat')]
    result = evaluate([component(1),component(2)],files=files,selected=[1,2],requested_disable=[uid(10002)])
    assert result['blocking'] and not result['disable_preview']['candidate_blocking']
    assert result['disable_preview']['release_ids'] == [uid(10002)]
    assert result['disable_preview']['affected_release_ids'] == []


@pytest.mark.parametrize('same_hash', [False,True])
def test_case026_windows_same_target_alias_case_and_unicode(same_hash):
    files = [file(1,1,'Mods/caf\u00e9.dll',root='game_root'),file(2,2,'CAFE\u0301.DLL',digest=f'{1 if same_hash else 2:064x}')]
    result = evaluate([],files=files,selected=[1,2])
    assert result['blocking'] is not same_hash
    assert ('TARGET_SHARED_IDENTICAL' if same_hash else 'TARGET_HASH_CONFLICT') in codes(result)


def test_case026_physical_duplicate_hash_does_not_create_component_conflict():
    content = [{'framework':'itemforge','type':'bundle','value':'same'}]
    result = evaluate([component(1,assembly='Shared',content_ids=content),
                       component(2,assembly='Shared',digest=f'{1:064x}',content_ids=content)],selected=[1,2])
    assert not result['blocking'] and not {'CONTENT_ID_COLLISION','ASSEMBLY_SHADOW_UNKNOWN','ML073_ASSEMBLY_SHADOW'} & codes(result)


def test_case026_content_ids_are_framework_and_type_namespaced():
    components = [component(1,content_ids=[{'framework':'itemforge','type':'bundle','value':'same'}]),
                  component(2,content_ids=[{'framework':'characterforge','type':'bundle','value':'same'}]),
                  component(3,content_ids=[{'framework':'itemforge','type':'item','value':'same'}])]
    assert 'CONTENT_ID_COLLISION' not in codes(evaluate(components,selected=[1,2,3]))
    components.append(component(4,content_ids=[{'framework':'itemforge','type':'bundle','value':'same'}]))
    result = evaluate(components,selected=[1,2,3,4])
    item = next(item for item in result['findings'] if item['code']=='CONTENT_ID_COLLISION')
    assert item['component_ids'] == [uid(1),uid(4)] and not result['blocking']


def test_case026_assembly_shadow_scoped_by_loader_options_and_stage():
    components = [component(1,assembly='Shared'),component(2,assembly='Shared',assembly_version='2.0.0.0')]
    files = [file(1,1,'one.dll'),file(2,2,'two.dll')]
    options = {'DisableSubFolderLoad':False,'DisableSubFolderManifest':False}
    supported = evaluate(components,files=files,selected=[1,2],loader_options=options)
    assert 'ML073_ASSEMBLY_SHADOW' in codes(supported) and not supported['blocking']
    unknown = evaluate(components,files=files,selected=[1,2])
    assert 'ASSEMBLY_SHADOW_UNKNOWN' in codes(unknown) and 'ML073_ASSEMBLY_SHADOW' not in codes(unknown)
    future = evaluate(components,files=files,selected=[1,2],loader_options=options,fingerprint=fingerprint(loader_version='0.8.0'))
    assert 'ML073_ASSEMBLY_SHADOW' not in codes(future) and future['unknowns']
    files[1]['target_root_kind'] = 'plugins'
    assert 'ML073_ASSEMBLY_SHADOW' not in codes(evaluate(components,files=files,selected=[1,2],loader_options=options))


def test_case026_loader_excluded_subfolder_does_not_enter_shadow_group():
    components = [component(1,assembly='Shared'),component(2,assembly='Shared',assembly_version='2.0.0.0')]
    files = [file(1,1,'one.dll'),file(2,2,'Disabled/two.dll')]
    result = evaluate(components,files=files,selected=[1,2],loader_options={'DisableSubFolderLoad':False,'DisableSubFolderManifest':False})
    assert not {'ML073_ASSEMBLY_SHADOW','ASSEMBLY_SHADOW_UNKNOWN'} & codes(result)


def test_case026_unknown_runtime_does_not_report_confirmed_compatibility():
    result = evaluate([component(1)],fingerprint=fingerprint(loader_version=None,coverage='partial'))
    assert not result['blocking'] and result['runtime_compatibility'] == 'unknown' and result['unknowns']
    assert result['dependency_coverage'] == 'stored_facts_only' and 'plan_ready' not in result


def test_case026_inputs_unchanged_and_display_order_not_a_graph_input():
    components, relations = [component(1),component(2)], [relation(1,1,2)]
    before = deepcopy([components,relations])
    evaluate(components,relations,selected=[1,2])
    assert [components,relations] == before
    with pytest.raises(TypeError):
        graph.evaluate_graph(components=components,relations=relations,files=[],fingerprint=fingerprint(),selected_release_ids=[],display_order=[uid(2),uid(1)])


@pytest.mark.parametrize('limit', ['MAX_COMPONENTS','MAX_RELATIONS','MAX_FILES','MAX_OUTPUT_BYTES','MAX_FINDINGS'])
def test_case026_limits_fail_closed_without_partial_success(monkeypatch,limit):
    monkeypatch.setattr(graph,limit,0)
    with pytest.raises(graph.GraphError,match='GRAPH_LIMIT_EXCEEDED'):
        evaluate([component(1)],[relation(1,1,2)],files=[file(1,1,'one.dll')])


def test_case026_cancelled_graph_propagates_without_result():
    def cancel():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        evaluate([component(1)],check=cancel)


def test_case026_outer_budget_value_error_preserves_limit_identity():
    class BudgetError(ValueError):
        pass
    def timeout():
        raise BudgetError('COMPATIBILITY_LIMIT')
    with pytest.raises(BudgetError):
        evaluate([component(1)],check=timeout)
