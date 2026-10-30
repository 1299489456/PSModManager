"""Native log notebook through real fixed sources, SQLite evidence and AI projections.

Only isolated temporary directories and minimal sanitized native statements are used.
No desktop composition, game process, credentials or network provider is started.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.ai.game_prompt import SYSTEM_PROMPT
from backend.ai.gateway import AIError
from backend.ai.log_diagnostics import LogDiagnostics, clean_log_text
from backend.ai.tools import ReadOnlyTools
from backend.app.paths import AppPaths
from backend.contracts import ENTITY_MODELS
from backend.diagnostics.evidence import EvidenceRepository
from backend.diagnostics.native_log import read_runtime_summary
from backend.diagnostics.service import LogService
from backend.games.probably_stolen.layout import identity
from backend.storage import Store
from tests.unit.test_native_log import HEADER, audit


@pytest.fixture
def notebook(tmp_path):
    game=tmp_path/'game'; data=tmp_path/'native-data'; manager=tmp_path/'manager'
    for folder in (game,data,manager/'logs'):
        folder.mkdir(parents=True)
    (game/'MelonLoader').mkdir()
    (game/'MelonLoader/Latest.log').write_text('obsolete loader must not be selected\n')
    current=data/'Player.log'
    current.write_text(HEADER+audit('one')+audit('two',False)
        + "[ModLoader] 'two' enabled=True (restart required)\n"
        + 'System.InvalidOperationException: fixture failure\n at Mod.Init()\n'
        + 'token=fixture-secret\nC:\\Users\\FixtureUser\\AppData\\fixture\n',encoding='utf-8')
    (data/'Player-prev.log').write_text(HEADER+audit('obsolete'))
    (manager/'logs/psmodmanager.log').write_text('manager ready\n')
    paths=AppPaths(manager)
    contracts=json.loads((Path(__file__).parents[1]/'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    installation=dict(contracts['Installation'],canonical_root=str(game),file_identity=identity(game))
    store=Store(paths.database)
    store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(contracts['RuntimeFingerprint']))
    store.put(ENTITY_MODELS['Installation'].model_validate(installation))
    store.close()
    logs=LogService(paths,user_data_resolver=lambda _: data)
    ident=installation['installation_id']
    registry=SimpleNamespace(list=lambda:dict(selected_installation_id=ident,installations=[installation]))
    def snapshot(_):
        summary=read_runtime_summary(data,['one','two'],['one','two'])
        mods=[dict(id=i,name=i,version='1.0',source='workshop',target_enabled=True,
                   approval={'state':'approved'},run=summary['by_id'][i]) for i in ('one','two')]
        return dict(revision='fixture-revision',mods=mods,enabled_ids=['one','two'],
                    runtime={'state':'stopped'},runtime_reconciliation=summary)
    native=SimpleNamespace(snapshot=snapshot)
    tools=ReadOnlyTools(registry,None,None,logs,None,native=native,installation_resolver=lambda _: installation)
    sent=[]
    def send(request_id,question,**kwargs):
        sent.append(deepcopy(kwargs)); return {'task_id':'fixture-only-no-network'}
    ai=SimpleNamespace(tools=tools,_config=lambda:{'model':''},credentials=SimpleNamespace(secrets=()),send=send)
    context=SimpleNamespace(paths=paths,logs=logs,ai=ai,native_mods=native)
    value=SimpleNamespace(game=game,data=data,paths=paths,installation=installation,ident=ident,
                          logs=logs,diagnostics=LogDiagnostics(context),sent=sent,current=current)
    yield value
    logs.close()


def selected(notebook):
    sources=notebook.diagnostics.sources(notebook.ident)
    return next(s for s in sources['sources'] if s['relative_path']=='Player.log')


def test_formal_sources_and_runtime_count_are_independent(notebook):
    result=notebook.diagnostics.sources(notebook.ident)
    rows=result['sources']
    assert {r['relative_path'] for r in rows}=={'Player.log','Player-prev.log','logs/psmodmanager.log'}
    assert next(r for r in rows if r['relative_path']=='Player.log')['default'] is True
    assert next(r for r in rows if r['relative_path']=='Player-prev.log')['default'] is False
    assert result['runtime_reconciliation']['running_count']==1
    assert result['runtime_reconciliation']['configured_count']==2
    assert result['runtime_reconciliation']['pending_restart_count']==1
    serialized=json.dumps(result)
    assert str(notebook.data) not in serialized and str(notebook.game) not in serialized
    assert 'fixture-owner' not in serialized and 'fixture-fingerprint' not in serialized
    assert notebook.sent==[]


def test_preview_redacts_native_privacy_and_snapshot_stays_immutable(notebook):
    row=selected(notebook)
    result=notebook.diagnostics.preview(notebook.ident,[row['source_id']])
    assert notebook.sent==[]
    text=json.dumps(result,ensure_ascii=False)
    assert all(s not in text for s in ('fixture-owner','fixture-fingerprint','fixture-secret','FixtureUser'))
    assert 'Initialize engine version' in text and 'audit: one' in text
    assert result['runtime_reconciliation']['log']['running_count']==1
    notebook.current.write_text('replacement error must not be sent\n')
    notebook.diagnostics.send(str(uuid4()),result['snapshot_id'],'分析')
    sent=notebook.sent[0]['diagnostic']['prompt']
    assert 'replacement error must not be sent' not in sent
    assert 'fixture failure' in sent
    assert 'fixture-owner' not in sent
    assert '"running_count": 1' in sent


def test_no_context_and_manager_only_without_installation(notebook):
    sources=notebook.diagnostics.sources()
    assert sources['runtime_reconciliation'] is None
    assert len(sources['sources'])==1
    source=sources['sources'][0]['source_id']
    result=notebook.diagnostics.preview(None,[source],include_context=False)
    assert result['context']=={} and result['runtime_reconciliation'] is None
    notebook.diagnostics.send(str(uuid4()),result['snapshot_id'],'分析')
    assert notebook.sent[0]['installation_id'] is None


@pytest.mark.parametrize('encoding',['utf-8','utf-16'])
def test_tail_paging_no_lost_lines_and_sensitive_paths_hidden(notebook,encoding):
    text=''.join(f'line-{i:05d} 中文 repeated context\n' for i in range(2200))
    notebook.current.write_text(text,encoding=encoding)
    row=selected(notebook)
    chunks=[]; before=None
    for _ in range(20):
        page=notebook.diagnostics.read(notebook.ident,row['source_id'],before=before)
        chunks.insert(0,page['text'])
        before=page['previous_before']
        if before is None:break
    assert ''.join(chunks)==text


def test_game_file_freshness_uses_installation_not_native_data(notebook,monkeypatch):
    roots=[]
    def modified(root):
        roots.append(root);return notebook.current.stat().st_mtime+10
    monkeypatch.setattr('backend.ai.log_diagnostics.game_files_modified_at',modified)
    page=notebook.diagnostics.read(notebook.ident,selected(notebook)['source_id'])
    assert roots==[notebook.game]
    assert page['freshness']=='predates_game_files'


def test_native_startup_retained_outside_large_error_tail(notebook):
    notebook.current.write_text(HEADER+audit('one')+'noise\n'*60000+'System.Exception: final fixture error\n')
    result=notebook.diagnostics.preview(notebook.ident,[selected(notebook)['source_id']],include_context=False)
    log=result['logs'][0]
    assert 'final fixture error' in log['text']
    assert 'Initialize engine version' in log['startup_context']['text']
    assert 'audit: one' in log['startup_context']['text']
    assert len((log['text']+log['startup_context']['text']).encode())<=8192


def test_paths_and_hardlinked_sources_do_not_expand_permission(notebook,tmp_path):
    with pytest.raises(AIError,match='AI_LOG_SOURCE_INVALID'):
        notebook.diagnostics.read(notebook.ident,str(uuid4()))
    spec,_=notebook.logs.source_specs(notebook.installation,['UserData/test.log'])
    assert spec[-1]['root']==notebook.game
    with pytest.raises(ValueError):
        notebook.logs.source_specs(notebook.installation,['../Player.log'])
    import os
    os.link(notebook.current,tmp_path/'hardlinked.log')
    row=selected(notebook)
    assert row['status']=='unreadable'
    with pytest.raises(AIError,match='AI_LOG_UNREADABLE'):
        notebook.diagnostics.read(notebook.ident,row['source_id'])


def test_native_watcher_writes_player_evidence_and_session(notebook):
    class Task:
        cancelled=False
        def check_cancelled(self):pass
        def progress(self,*args,**kwargs):notebook.logs.close()
    result=notebook.logs._watch(notebook.installation,(),Task())
    assert result['evidence_count']>0
    assert {s['relative_path'] for s in notebook.logs.sources(notebook.ident)['sources']}=={'Player.log','Player-prev.log'}
    store=Store(notebook.paths.database)
    try:
        repo=EvidenceRepository(store)
        for relative,kind in [('Player.log','latest'),('Player-prev.log','archive')]:
            assert repo.register_source(notebook.ident,relative,kind)
        with pytest.raises(ValueError):
            repo.register_source(notebook.ident,'../Player.log','latest')
        text=' '.join(json.loads(r[0])['text'] for r in store.connection.execute('SELECT payload FROM log_evidence'))
        assert 'obsolete loader' not in text
        sessions=notebook.logs.sessions(notebook.ident)
        assert sessions['entries'] and all(s['generation_id'] is None for s in sessions['entries'])
    finally:store.close()


def test_prompt_preserves_native_execution_and_authority_boundaries():
    for term in ('Unity Mono','manifest.xml','IMod','get_runtime_reconciliation','unknown','partial','AI 当前只读','modTrust','Init'):
        assert term in SYSTEM_PROMPT
    assert '已支持的 Loader 是' not in SYSTEM_PROMPT
    assert 'suggest_workspace_action' not in SYSTEM_PROMPT


def test_shared_selected_evidence_filter_hides_native_account_and_roots():
    text=('owner=fixture-account hash=fixture-hash SteamID: 76561190000000000\n'
          'hash fixture-hash2, declared files\n'
          r'C:\Users\Fixture Person\AppData\example'+'\n'
          r'C:\Users\Fixture Person\AppData\PSMM\logs\diagnostic.log')
    clean=clean_log_text(text,(),r'C:\Users\Fixture Person\AppData\PSMM')
    assert all(value not in clean for value in ('fixture-account','fixture-hash','76561190000000000','Fixture Person'))
    assert '[管理器数据目录]' in clean


def test_native_context_trims_whole_ids_and_preserves_original_counts(notebook,monkeypatch):
    tools=notebook.diagnostics.context.ai.tools
    execute=tools.execute
    def expanded(name,*args,**kwargs):
        value=json.loads(execute(name,*args,**kwargs))
        if name in ('get_mod_workspace','get_runtime_reconciliation'):
            value['data']['enabled_ids']=[f'fixture_{i:03d}_'+'x'*48 for i in range(90)]
        return json.dumps(value)
    monkeypatch.setattr(tools,'execute',expanded)
    context,warnings=notebook.diagnostics._context(notebook.ident,())
    assert len(json.dumps(context,ensure_ascii=False).encode())<=6000
    assert context['mods']['enabled_ids_total']==90
    assert context['mods']['enabled_ids_truncated'] is True
    assert warnings
