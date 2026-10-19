"""Diagnostic consumer boundaries against the real product SQLite database."""
import hashlib
from uuid import uuid4

import pytest

from tests.integration.test_catalog_queries import catalog, content_row
from tests.integration.test_inventory_repository import complete
from tests.unit.test_installation_discovery import request


def query(context, installation_id, **kwargs):
    response=context.api.dispatch(request('diagnostics.explain',dict(installation_id=installation_id,**kwargs)))
    assert response['ok'],response
    return response['data']


def test_not_run_is_not_healthy_or_deployment_approval(catalog):
    context,_,identity=catalog
    result=query(context,identity)
    assert result['inventory_status']=='not_run' and result['coverage']=='partial'
    assert result['deployment_verification']=='not_run'
    assert all(not row.get('block') for row in result['diagnostics'])


def test_scoped_snapshot_is_deterministic_and_database_readonly(catalog):
    context,repo,identity=catalog
    generation=complete(repo,identity,[content_row()])
    before=hashlib.sha256(context.paths.database.read_bytes()).hexdigest()
    first=query(context,identity)
    second=query(context,identity,generation=generation,snapshot_id=first['snapshot_id'])
    assert first==second
    assert hashlib.sha256(context.paths.database.read_bytes()).hexdigest()==before
    assert first['freshness']=='stored_observation'


def test_new_generation_invalidates_default_snapshot_but_pinned_history_survives(catalog):
    context,repo,identity=catalog
    generation=complete(repo,identity,[content_row()])
    first=query(context,identity)
    complete(repo,identity,[content_row(count=4)])
    response=context.api.dispatch(request('diagnostics.explain',dict(installation_id=identity,snapshot_id=first['snapshot_id'])))
    assert not response['ok'] and response['error']['code']=='DIAGNOSTIC_SNAPSHOT_CHANGED'
    # A pinned old inventory remains queryable; the response also reports latest_attempt.
    pinned=query(context,identity,generation=generation)
    assert pinned['generation']==generation and pinned['last_attempt']==generation+1


def test_inventory_limit_explicitly_partial_not_false_complete(catalog,monkeypatch):
    context,repo,identity=catalog
    complete(repo,identity,[content_row(),content_row('Mods/b.dll')])
    monkeypatch.setattr('backend.diagnostics.explain.MAX_ENTRIES',1)
    result=query(context,identity)
    assert result['coverage']=='partial' and result['inventory_truncated']
    assert all(not row.get('block') for row in result['diagnostics'])


def test_component_priority_keeps_asset_folders_from_hiding_dll_diagnostics(catalog,monkeypatch):
    context,repo,identity=catalog
    asset=content_row('Mods/a/textures/image.png')
    asset.update(metadata=None,managed=None,has_melon_info=None,status='unknown')
    component=content_row('Mods/z-content.dll')
    complete(repo,identity,[asset,component])
    monkeypatch.setattr('backend.diagnostics.explain.MAX_ENTRIES',1)
    result=query(context,identity)
    assert result['inventory_truncated'] and result['coverage']=='partial'
    assert any(d['rule_id']=='IF-CONTENT' for d in result['diagnostics'])


def test_bounded_pages_keep_one_snapshot_and_all_diagnostics(catalog):
    import json
    context,repo,identity=catalog
    complete(repo,identity,[content_row(f'Mods/item-{n:03}.dll') for n in range(40)])
    first=query(context,identity,limit=3)
    assert first['total']==40 and first['next_offset']==3
    rows=list(first['diagnostics']);page=first
    while page['next_offset'] is not None:
        page=query(context,identity,generation=first['generation'],limit=3,
                   snapshot_id=first['snapshot_id'],offset=page['next_offset'])
        assert page['snapshot_id']==first['snapshot_id']
        assert len(json.dumps(page,ensure_ascii=False).encode())<48*1024
        rows.extend(page['diagnostics'])
    assert len({r['diagnostic_id'] for r in rows})==40


def test_log_limit_is_partial_and_never_claims_collection_complete(catalog,monkeypatch):
    from backend.diagnostics.evidence import EvidenceRepository
    from backend.diagnostics.sessions import SessionTracker
    context,repo,identity=catalog
    complete(repo,identity,[content_row()])
    logs=EvidenceRepository(context.store)
    source=logs.register_source(identity,'MelonLoader/Latest.log','latest')
    epoch=str(uuid4())
    records=[dict(epoch=epoch,byte_start=n*4,byte_end=n*4+4,raw=b'abc\n',text='abc\n',
                  complete_line=True,fragment=False,decode_errors=False) for n in range(2)]
    logs.ingest(source,{},SessionTracker(source).classify(records))
    monkeypatch.setattr('backend.diagnostics.explain.MAX_LOGS',1)
    result=query(context,identity)
    assert result['log_truncated'] and result['coverage']=='partial'
    assert result['coverage_scope']=='stored_inputs_only' and result['log_collection_coverage']=='unknown'


@pytest.mark.parametrize('payload',[
    {'installation_id':str(uuid4())}, {'session_id':str(uuid4())}, {'generation':999},
    {'limit':21}, {'offset':-1}, {'offset':1}, {'snapshot_id':'bad'}, {'path':'C:/not-allowed'},
    {'fingerprint':{'loader_version':'0.7.3'}}, {'rules':[]}, {'evidence':[]},
])
def test_unknown_scope_and_frontend_rule_injection_rejected(catalog,payload):
    context,_,identity=catalog
    response=context.api.dispatch(request('diagnostics.explain',dict(installation_id=identity)|payload))
    assert not response['ok'] and response['error']['code'] in {
        'INVALID_REQUEST','INSTALLATION_NOT_FOUND','DIAGNOSTIC_SESSION_NOT_FOUND','INVENTORY_SCAN_NOT_FOUND'}
