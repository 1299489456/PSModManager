"""Real SQLite projections: scope, pagination and incomplete graph safety."""
import json
from uuid import uuid4
import pytest

from backend.app.composition import DesktopContext
from backend.app.api import DesktopApi
from backend.app.paths import resolve_paths
from backend.contracts import ENTITY_MODELS
from backend.storage.inventory_repository import InventoryRepository
from tests.integration.test_inventory_repository import FIXTURE, entry, complete
from tests.unit.test_installation_discovery import request


@pytest.fixture
def catalog(tmp_path):
    context=DesktopContext(resolve_paths(str(tmp_path/'data')));context.initialize()
    for kind in ('RuntimeFingerprint','Installation'):
        context.store.put(ENTITY_MODELS[kind].model_validate(FIXTURE[kind]))
    context.api=DesktopApi(context)
    try:yield context,InventoryRepository(context.store),FIXTURE['Installation']['installation_id']
    finally:context.close()


def content_row(path='Mods/a.dll', count=2):
    row=entry(path);row.update(status='managed',assembly_name='SyntheticPack',assembly_version='0.46D')
    row['metadata']={'assembly_name':'SyntheticPack','assembly_version':'0.46D','references':[],
        'resources':[{'name':'ItemForge.items.json','embedded':True,
         'json_text':json.dumps({'BundleId':'synthetic','RequiredRuntimeApi':19,'Items':[{'Id':str(i)} for i in range(count)]})}]}
    return row


def test_graph_partial_scope_does_not_claim_missing_runtime(catalog,monkeypatch):
    context,repo,identity=catalog
    complete(repo,identity,[content_row(),entry('Mods/z.dll')])
    monkeypatch.setattr('backend.catalog.service.MAX_GRAPH_ENTRIES',1)
    graph=context.api.dispatch(request('inventory.graph',{'installation_id':identity}))
    assert graph['ok'],graph
    assert graph['data']['coverage']=='partial' and graph['data']['truncated']
    assert graph['data']['relations'][0]['resolution']=='unknown'
    assert graph['data']['plan_validation']=='not_run'


def test_detail_and_contents_pin_generation_and_do_not_mutate_inventory(catalog):
    context,repo,identity=catalog
    generation=complete(repo,identity,[content_row(count=40)])
    row=repo.list(identity)['entries'][0]
    payload=dict(installation_id=identity,entry_id=row['entry_id'],generation=generation)
    first=context.api.dispatch(request('inventory.contents',payload|{'limit':20}))['data']
    complete(repo,identity,[content_row(count=1)])
    second=context.api.dispatch(request('inventory.contents',payload|{'limit':20,'offset':20}))['data']
    assert first['total']==second['total']==41 and second['generation']==generation
    detail=context.api.dispatch(request('inventory.detail',payload))['data']
    assert detail['analysis']['content_id_count']==41 and len(detail['analysis']['content_ids'])==16
    assert detail['analysis']['registration_status']=='unknown'
    assert len(json.dumps(detail).encode())<48*1024
    assert repo.entry(identity,generation,row['entry_id'])['metadata']==row['metadata']


def test_unknown_entry_id_cannot_be_used_as_path(catalog):
    context,repo,identity=catalog
    complete(repo,identity,[content_row()])
    result=context.api.dispatch(request('inventory.detail',dict(installation_id=identity,entry_id=str(uuid4()))))
    assert not result['ok'] and result['error']['code']=='INVENTORY_ENTRY_NOT_FOUND'


def test_invalid_resource_preserves_inventory_but_marks_graph_partial(catalog):
    context,repo,identity=catalog
    row=content_row();row['metadata']['resources'][0]['json_text']='{'
    generation=complete(repo,identity,[row])
    graph=context.api.dispatch(request('inventory.graph',{'installation_id':identity}))['data']
    assert graph['coverage']=='partial' and graph['diagnostics']
    assert repo.summary(identity,generation)['status']=='complete'


def test_graph_not_run_has_no_fabricated_relation(catalog):
    context,_,identity=catalog
    result=context.api.dispatch(request('inventory.graph',{'installation_id':identity}))
    assert result['ok'] and result['data']['coverage']=='not_run' and result['data']['relations']==[]
