"""PS-01-04 real API and isolated parser over self-authored 45-file corpus."""
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from tests.integration.test_metadata_worker import BUILD
from tests.unit.test_installation_discovery import discover, hashes, request, wait
from tests.unit.test_installation_layout import make_installation


def call(api, command, **payload):
    response = api.dispatch(request(command, payload))
    assert response['ok'], response
    return response['data']


def wait_scan(api, snapshot):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = call(api, 'task.snapshot', task_id=snapshot['task_id'])
        if result['state'] in ('succeeded', 'failed', 'cancelled'):
            assert result['state'] == 'succeeded', result
            return result['data']
        time.sleep(.02)
    raise AssertionError('45-file synthetic inventory scan exceeded 120 seconds')


def compile_fixture(root, name, version='1.0.0.0', *, melon=True, resource=None):
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'Fixture.cs'
    prefix = 'using System; using System.Reflection;\n[assembly:AssemblyVersion("' + version + '")]\n'
    if melon:
        prefix += '[assembly:MelonLoader.MelonInfo(typeof(SyntheticPayload),"Self authored fixture","0.46D","Synthetic")]\n'
    source.write_text(prefix + '''public class SyntheticPayload { static SyntheticPayload(){throw new Exception("must never execute");} }
namespace MelonLoader { [AttributeUsage(AttributeTargets.Assembly)] public class MelonInfoAttribute:Attribute {
 public MelonInfoAttribute(Type t,string n,string v,string a){throw new Exception("must never instantiate");} } }''', encoding='utf-8')
    resources = []
    if resource:
        resource_name, body = resource
        path = root / resource_name
        path.write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
        resources.append((path, resource_name))
    return BUILD.compile_source(source, root / (name + '.dll'), library=True, resources=resources)


def manifest(framework, api, *, count=1, bundle='shared-bundle'):
    collection = 'Items' if framework == 'ItemForge' else 'Characters'
    return {'BundleId': bundle, 'RequiredRuntimeApi': api, 'BundleVersion': '0.46D',
            'GeneratorVersion': 'generator-local', collection: [{'Id': 'item-' + str(i)} for i in range(count)]}


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp('content-catalog-real-corpus')
    game = make_installation(root / '合成游戏 %')
    mods = game / 'Mods'
    mods.mkdir()
    loader_source = root / 'Loader.cs'
    loader_source.write_text('using System.Reflection; [assembly:AssemblyInformationalVersion("0.7.3")] public class SyntheticLoader {}', encoding='utf-8')
    BUILD.compile_source(loader_source, game / 'MelonLoader/net6/MelonLoader.dll', library=True)
    for index in range(42):
        name = 'Unique' + str(index).zfill(2)
        resource = None
        if index == 0:
            resource = ('ItemForge.items.json', manifest('ItemForge', 5))
        elif index == 1:
            resource = ('CharacterForge.characters.json', manifest('CharacterForge', 12))
        artifact = compile_fixture(root / 'sources' / name, name, resource=resource)
        shutil.copyfile(artifact, mods / (name + '.dll'))
    for version, filename in [('1.0.0.0', 'ShadowLow.dll'), ('2.0.0.0', 'ShadowHigh.dll')]:
        artifact = compile_fixture(root / 'sources' / filename, 'SharedAssembly', version)
        shutil.copyfile(artifact, mods / filename)
    artifact = compile_fixture(root / 'sources/ContentOnly', 'ContentOnly', melon=False,
                               resource=('ItemForge.items.json', manifest('ItemForge', 19, count=21, bundle='21-static-items')))
    shutil.copyfile(artifact, mods / 'ContentOnly.dll')
    before = hashes(game)
    context = DesktopContext(resolve_paths(str(root / 'isolated-data')))
    context.initialize()
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    try:
        candidate = discover(api)
        selected = wait(api, call(api, 'installation.select', candidate_id=candidate['candidate_id']))
        assert selected['state'] == 'succeeded'
        identity = selected['data']['installation']['installation_id']
        result = wait_scan(api, call(api, 'inventory.scan', installation_id=identity, force=False))
        assert result['status'] == 'complete', result
        page = call(api, 'inventory.list', installation_id=identity, limit=50)
        yield dict(root=root, game=game, context=context, api=api, identity=identity,
                   generation=result['generation'], rows=page['entries'], before=before)
    finally:
        context.close()
    assert hashes(game) == before


def detail(corpus, filename):
    row = next(row for row in corpus['rows'] if row['relative_path'] == 'Mods/' + filename)
    return call(corpus['api'], 'inventory.detail', installation_id=corpus['identity'],
                entry_id=row['entry_id'], generation=corpus['generation'])


def graph_all(corpus):
    edges, offset = [], 0
    while True:
        page = call(corpus['api'], 'inventory.graph', installation_id=corpus['identity'],
                    generation=corpus['generation'], limit=20, offset=offset)
        edges.extend(page['relations'])
        if page['next_offset'] is None:
            assert len(edges) == page['total']
            return page, edges
        assert page['next_offset'] > offset
        offset = page['next_offset']


def test_case011_real_45_files_44_melon_43_candidates(corpus):
    rows = corpus['rows']
    assert len(rows) == 45 and all(row['status'] == 'managed' for row in rows)
    analyses = {row['relative_path']: detail(corpus, Path(row['relative_path']).name)['analysis'] for row in rows}
    melon = sorted(path for path, analysis in analyses.items() if analysis['melon_info'] is not None)
    candidates = sorted(row['relative_path'] for row in rows if row['prediction']['status'] == 'candidate')
    assert len(melon) == 44 and len(candidates) == 43
    assert 'Mods/ContentOnly.dll' not in melon and 'Mods/ShadowLow.dll' not in candidates
    assert 'Mods/ShadowHigh.dll' in candidates
    assert all(analysis['registration_status'] == 'unknown' for analysis in analyses.values())
    report = {'fixture_source': 'Self-authored C# sources compiled offline; no game/mod binary copied',
              'physical_count': 45, 'melon_count': 44, 'predicted_candidate_count': 43,
              'physical_paths': sorted(analyses), 'melon_paths': melon, 'candidate_paths': candidates,
              'historical_observation': 'not_used', 'runtime_registration': 'not_run',
              'before': corpus['before'], 'after': hashes(corpus['game']), 'analyses': analyses}
    assert report['before'] == report['after']
    (corpus['root'] / 'case011-45-44-43.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def test_three_content_roles_and_framework_namespaces(corpus):
    plain = detail(corpus, 'ContentOnly.dll')
    dual = detail(corpus, 'Unique00.dll')
    character = detail(corpus, 'Unique01.dll')
    assert plain['parser_status'] == 'managed'
    assert plain['analysis']['roles'] == ['itemforge_content']
    assert set(dual['analysis']['roles']) == {'melon_mod', 'itemforge_content'}
    assert set(character['analysis']['roles']) == {'melon_mod', 'characterforge_content'}
    for component, minimum in [(plain, 19), (dual, 5), (character, 12)]:
        analysis = component['analysis']
        assert analysis['registration_status'] == 'unknown'
        assert analysis['manifests'][0]['status'] == 'valid'
        assert analysis['manifests'][0]['bundle_version_raw'] == '0.46D'
        requirement = analysis['requirements'][0]
        assert requirement['minimum'] == minimum
        assert requirement['source']['kind'] == 'embedded_metadata'
        assert 'RequiredRuntimeApi' in requirement['source']['reference']
    assert dual['analysis']['manifests'][0]['bundle_id'] == character['analysis']['manifests'][0]['bundle_id']
    page, edges = graph_all(corpus)
    assert page['node_count'] == 45 and page['coverage'] == 'complete'
    conflicts = [edge for edge in edges if edge['kind'] == 'content_id_conflict']
    # Both ItemForge packages intentionally contain item-0; that same-framework
    # collision must remain visible. The identically named CharacterForge IDs
    # and cross-framework shared-bundle must never be merged into that conflict.
    assert len(conflicts) == 1
    assert {conflicts[0]['from_id'], conflicts[0]['to_id']} == {plain['entry_id'], dual['entry_id']}
    assert conflicts[0]['namespace'] == {'framework': 'itemforge', 'type': 'item'}


def test_21_static_items_paged_without_claiming_registration(corpus):
    component = detail(corpus, 'ContentOnly.dll')
    entry = component['entry_id']
    first = call(corpus['api'], 'inventory.contents', installation_id=corpus['identity'], entry_id=entry,
                 generation=corpus['generation'], offset=0, limit=20)
    second = call(corpus['api'], 'inventory.contents', installation_id=corpus['identity'], entry_id=entry,
                  generation=corpus['generation'], offset=first['next_offset'], limit=20)
    ids = first['entries'] + second['entries']
    assert first['total'] == 22 and second['next_offset'] is None  # bundle plus 21 items
    assert len([item for item in ids if item['type'] == 'item']) == 21
    assert {item['framework'] for item in ids} == {'itemforge'}
    assert component['analysis']['registration_status'] == 'unknown'
    assert component['prediction']['status'] == 'excluded'  # no direct Melon entry, still valid content


def test_graph_keeps_missing_runtime_requirements_and_shadow_provenance(corpus):
    page, edges = graph_all(corpus)
    requirements = [edge for edge in edges if edge['kind'] == 'capability_requirement']
    assert sorted(edge['minimum'] for edge in requirements) == [5, 12, 19]
    for edge in requirements:
        assert edge['resolution'] == 'missing' and edge['compatibility'] == 'unknown'
        assert edge['source']['kind'] == 'embedded_metadata' and edge['confidence'] == 'confirmed'
        assert edge['fingerprint_scope']['generation'] == corpus['generation']
    shadows = [edge for edge in edges if edge['kind'] == 'assembly_shadow']
    assert len(shadows) == 1
    assert shadows[0]['source']['kind'] == 'loader_rule'
    assert shadows[0]['registration_status'] == 'unknown'
    references = [edge for edge in edges if edge['kind'] == 'soft_dependency']
    assert references and all(edge['requirement_semantics'] == 'compile_reference_only' for edge in references)
    assert page['plan_validation'] == 'not_run'
    (corpus['root'] / 'catalog-graph.json').write_text(json.dumps({'page': page, 'relations': edges}, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('command,extra', [('inventory.contents', {'limit': 21}), ('inventory.graph', {'offset': -1}), ('inventory.detail', {'entry_id': str(uuid4())}), ('inventory.detail', {'path': 'C:/unapproved'})])
def test_catalog_bridge_rejects_unapproved_or_invalid_inputs(corpus, command, extra):
    payload = dict(installation_id=corpus['identity'])
    if command != 'inventory.graph':
        payload['entry_id'] = corpus['rows'][0]['entry_id']
    payload.update(extra)
    assert not corpus['api'].dispatch(request(command, payload))['ok']
