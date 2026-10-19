"""Synthetic boundaries only; no authorization to open any actual installation."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from scripts.verification.readonly import (REQUIRED_FILES,TREES,EVIDENCE_FIELDS,validate_authorization,
    load_authorization,load_historical_baseline,snapshot_scope,snapshot_authorized,compare_sets,parse_loader_hashes,ReadonlyAudit)

PROJECT=Path(__file__).resolve().parents[2]


def auth(root):
    return dict(task_id='PS-01-07',profile='real-readonly',
        resources=dict(installation_root=str(root),trees=list(TREES),files=list(REQUIRED_FILES),logs=[],evidence_fields=list(EVIDENCE_FIELDS)),
        operations=['read'],network=False,upload=False,budget=dict(max_files=5000,max_bytes=1024**3,max_seconds=120),
        valid_until='2099-01-01T00:00:00Z',excluded_data=['saves','credentials','personal_data'])


def test_no_authorization_or_wrong_profile_never_touches_target(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('Must reject before any filesystem read')
    monkeypatch.setattr(Path,'read_text',forbidden)
    monkeypatch.setattr(Path,'stat',forbidden)
    for env in ({},{'PSMM_VERIFY_PROFILE':'synthetic','PSMM_READONLY_AUTHORIZATION_FILE':str(tmp_path/'forbidden.json')},
                {'PSMM_VERIFY_PROFILE':'real-readonly'}):
        with pytest.raises(ValueError): load_authorization(env)


@pytest.mark.parametrize('field,value',[('network',True),('upload',True),('operations',['read','write']),
    ('operations',['read','startgame']),('excluded_data',[]),('valid_until','2020-01-01T00:00:00Z'),
    ('valid_until','2099-01-01T00:00:00'),('profile','synthetic'),('task_id','PS-01-08')])
def test_authorization_rejects_widened_or_expired_scope(tmp_path,field,value):
    grant=auth(tmp_path);grant[field]=value
    with pytest.raises(ValueError):validate_authorization(grant)


def test_authorization_explicit_resource_and_evidence_fields(tmp_path):
    grant=auth(tmp_path)
    assert validate_authorization(grant)==grant
    for name,value in [('installation_root','relative/game'),('logs',['../secret.log']),('files',[]),
                       ('trees',['Mods']),('evidence_fields',['raw_dll'])]:
        changed=deepcopy(grant);changed['resources'][name]=value
        with pytest.raises(ValueError):validate_authorization(changed)


def test_steam_shape_uses_only_explicit_external_manifests(tmp_path):
    game=tmp_path/'steamapps/common/game'
    grant=auth(game)
    validate_authorization(grant)  # Host disables manual manifest probing for this scope.
    manifests=[tmp_path/'steamapps'/f'appmanifest_{app}.acf' for app in ('4349200','4348910')]
    grant['resources']['steam_manifests']=[str(p) for p in manifests]
    validate_authorization(grant)
    game.mkdir(parents=True)
    manifests[0].write_bytes(b'explicit authorized manifest')
    result=snapshot_authorized(grant)
    assert result['steam_manifests']['files'][0]['relative_path']=='appmanifest_4349200.acf'
    assert result['steam_manifests']['absent']==['appmanifest_4348910.acf']


def test_log_watch_guard_denies_unlisted_and_new_archives(tmp_path):
    game=tmp_path/'game';logs=game/'MelonLoader/Logs';logs.mkdir(parents=True)
    allowed=logs/'allowed.log';allowed.write_bytes(b'allowed')
    grant=auth(game);grant['resources']['logs']=['MelonLoader/Logs/allowed.log']
    guard=ReadonlyAudit(grant)
    with pytest.raises(ValueError,match='ENUMERATION'):guard.check_watch_scope()
    grant['resources']['log_enumeration']=['MelonLoader/Logs'];guard=ReadonlyAudit(grant)
    guard.check_watch_scope()
    guard('open',(str(allowed),'rb',os.O_RDONLY))
    added=logs/'new.log';added.write_bytes(b'not granted')
    with pytest.raises(ValueError,match='NOT_AUTHORIZED'):guard.check_watch_scope()
    with pytest.raises(PermissionError):guard('open',(str(added),'rb',os.O_RDONLY))
    with pytest.raises(PermissionError):guard('open',(str(allowed),'wb',os.O_WRONLY))


def test_root_only_manual_discovery_select_and_use_never_probe_external_manifest(tmp_path,monkeypatch):
    from backend.app.api import DesktopApi
    from backend.app.composition import DesktopContext
    from backend.app.paths import resolve_paths
    from tests.unit.test_installation_layout import make_installation
    from tests.unit.test_installation_discovery import discover,request,wait
    game=make_installation(tmp_path/'steamapps/common/game')
    def forbidden(*args,**kwargs):raise AssertionError('External Steam manifest was not authorized')
    monkeypatch.setattr('backend.games.probably_stolen.discovery._manifest',forbidden)
    context=DesktopContext(resolve_paths(str(tmp_path/'data')));context.initialize()
    context.installations.allow_manual_manifest_probe=False
    context.choose_installation_folder=lambda kind:str(game)
    api=DesktopApi(context);context.api=api
    try:
        candidate=discover(api)
        assert candidate['channel']=='unknown' and candidate['app_id'] is None and candidate['build_id'] is None
        response=api.dispatch(request('installation.select',dict(candidate_id=candidate['candidate_id'])))
        selected=wait(api,response['data'])
        assert selected['state']=='succeeded'
        installation=selected['data']['installation']
        assert installation['channel']=='unknown'
        response=api.dispatch(request('installation.use',dict(installation_id=installation['installation_id'])))
        assert wait(api,response['data'])['data']['installation']['channel']=='unknown'
    finally:context.close()


def test_historical_baseline_retains_original_hashes_and_raw_gap():
    baseline=load_historical_baseline(PROJECT)
    assert baseline['historical_date']=='2026-09-24'
    assert len(baseline['physical'])==45 and len(baseline['loaded_reported'])==43
    assert len(baseline['loaded_raw'])==6
    assert baseline['gaps']==['historical_raw_43_loader_hashes_missing']
    assert not baseline['real_environment_verified']
    missing={r['relative_path'] for r in baseline['physical']}-{r['relative_path'] for r in baseline['loaded_reported']}
    assert missing=={'Mods/PSItemPack_SanmuShiwushechipinPack1.dll','Mods/RatColonyOptimizer.dll'}


def test_full_scope_hashes_and_drift_are_not_forced_to_45(tmp_path):
    for name in ('Mods/a.dll','Mods/nested/unknown.bin','Plugins/p.dll','UserLibs/u.dll','MelonLoader/Latest.log'):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(name.encode())
    (tmp_path/'Saves').mkdir();(tmp_path/'Saves/never-read.sav').write_bytes(b'not in registered scope')
    before=snapshot_scope(tmp_path,files=['MelonLoader/Latest.log'])
    assert len(before['files'])==5 and before==snapshot_scope(tmp_path,files=['MelonLoader/Latest.log'])
    (tmp_path/'Mods/a.dll').write_bytes(b'changed')
    (tmp_path/'Mods/new.dll').write_bytes(b'new')
    (tmp_path/'Plugins/p.dll').unlink()
    after=snapshot_scope(tmp_path,files=['MelonLoader/Latest.log'])
    diff=compare_sets(before['files'],after['files'])
    assert diff['added']==['mods/new.dll'] and diff['removed']==['plugins/p.dll']
    assert diff['changed'][0]['relative_path']=='mods/a.dll'


def test_hash_snapshot_refuses_links_and_read_budget(tmp_path):
    (tmp_path/'Mods').mkdir();(tmp_path/'Mods/a.dll').write_bytes(b'1234')
    with pytest.raises(ValueError,match='BUDGET'):snapshot_scope(tmp_path,files=[],max_bytes=2)
    os.link(tmp_path/'Mods/a.dll',tmp_path/'Mods/alias.dll')
    with pytest.raises(ValueError,match='HARDLINK|NON_REGULAR'):snapshot_scope(tmp_path,files=[])


def test_hash_snapshot_refuses_junction_before_target_read(tmp_path):
    root=tmp_path/'game'; root.mkdir()
    external=tmp_path/'outside'; external.mkdir()
    (external/'private.dll').write_bytes(b'never read this target')
    subprocess.run(['cmd','/c','mklink','/J',str(root/'Mods'),str(external)],check=True,capture_output=True)
    with pytest.raises(ValueError,match='REPARSE'):snapshot_scope(root,files=[])


def test_loader_parser_requires_each_raw_hash_and_rejects_escape():
    digest=hashlib.sha256(b'x').hexdigest()
    text=f"[time] Melon Assembly loaded: '.\\Mods\\Chinese %.dll'\n[time] SHA256 Hash: '{digest.upper()}'\n"
    assert parse_loader_hashes(text)==[dict(relative_path='Mods/Chinese %.dll',sha256=digest)]
    assert parse_loader_hashes("43 Mods loaded.\n")==[]
    with pytest.raises(ValueError):parse_loader_hashes(text.replace('.\\Mods\\Chinese %.dll','..\\private.dll'))
