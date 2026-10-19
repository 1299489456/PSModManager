"""Actual app dispatch -> job -> isolated C# helper; synthetic inputs only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.scanning import worker_client as client
from tests.unit.test_installation_discovery import discover, request, wait, hashes
from tests.unit.test_installation_layout import make_installation
from tests.integration.test_metadata_sandbox import probe

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('metadata_build_integration',ROOT/'tools/PSModManager.Metadata/build.py')
BUILD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(BUILD)


@pytest.fixture(scope='module')
def dll(tmp_path_factory):
    root = tmp_path_factory.mktemp('metadata-input')
    source = root/'Original.cs'
    source.write_text('''using System; using System.IO; using System.Reflection;
[assembly:AssemblyVersion("2.3.4.5")]
[assembly:MelonLoader.MelonInfo(typeof(Payload),"Perks display","0.46D","Synthetic")]
public class Payload { static Payload(){ throw new Exception("must not run"); } }
namespace MelonLoader { [AttributeUsage(AttributeTargets.Assembly)] public class MelonInfoAttribute:Attribute {
 public MelonInfoAttribute(Type t,string n,string v,string a){File.WriteAllText(@"MARKER","BAD");}
} }'''.replace('MARKER',str(root/'executed.txt')),encoding='utf-8')
    return BUILD.compile_source(source,root/'JacksonPerks.dll',library=True)


@pytest.fixture
def game(tmp_path,dll):
    root = tmp_path/'synthetic-game'; make_installation(root)
    (root/'Mods').mkdir(); shutil.copyfile(dll,root/'Mods/WagesPerks.dll')
    return root


def test_real_inventory_consumer_preserves_identity_sources_and_continues(game,tmp_path,dll):
    (game/'Mods/Bad.dll').write_bytes(b'broken PE')
    original = hashes(game)
    context = DesktopContext(resolve_paths(str(tmp_path/'data'))); context.initialize()
    context.choose_installation_folder = lambda kind:str(game)
    api = DesktopApi(context); context.api=api
    try:
        candidate=discover(api)
        chosen=wait(api,api.dispatch(request('installation.select',{'candidate_id':candidate['candidate_id']}))['data'])
        installation_id=chosen['data']['installation']['installation_id']
        response=api.dispatch(request('inventory.inspect',{'installation_id':installation_id,'files':['Mods/Bad.dll','Mods/WagesPerks.dll']}))
        assert response['ok'] and response['task_id']
        completed=wait(api,response['data']); assert completed['state']=='succeeded', completed
        result=completed['data']; assert not result['complete_inventory']
        bad,good=result['files']; assert bad['status']=='corrupt'
        assert good['status']=='managed',good
        assert good['relative_path']=='Mods/WagesPerks.dll'
        assert good['assembly_name']=='JacksonPerks' and good['assembly_version']=='2.3.4.5'
        report=json.loads((context.paths.root/'metadata/reports'/(good['metadata_report_id']+'.json')).read_text(encoding='utf-8'))
        attribute=next(a for a in report['metadata']['custom_attributes'] if a['type']=='MelonLoader.MelonInfoAttribute')
        assert attribute['arguments'][1:3]==['Perks display','0.46D']
        assert report['process']['appcontainer'] and report['process']['capabilities']==0
        assert report['process']['profile_deleted'] and report['process']['cleanup_remaining']==0
        assert not list((context.paths.root/'metadata/staging').iterdir())
        assert not (dll.parent/'executed.txt').exists()
        (tmp_path/'consumer-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    finally: context.close()
    assert hashes(game)==original


def test_pythonw_scan_preview_and_adopt_real_service_chain(game, tmp_path):
    """Console-based tests used to miss the GUI host's worker startup failure."""
    output = tmp_path / 'gui-scan-adopt.json'
    command = [str(Path(sys.executable).with_name('pythonw.exe')), '-I', '-B',
               str(Path(__file__).with_name('gui_metadata_host.py')), 'scan-adopt', str(game), str(output)]
    process = subprocess.run(command, timeout=60, creationflags=subprocess.DETACHED_PROCESS)
    report = json.loads(output.read_text(encoding='utf-8'))
    assert process.returncode == 0, report
    assert report['console_window'] == 0
    assert report['scan']['status'] == 'complete' and report['scan']['error_count'] == 0
    assert report['preview']['coverage'] == 'complete'
    assert report['adoption']['deployment_generation'] is None
    assert report['library']['releases'][0]['integrity'] == 'verified'
    assert report['generations'] == []
    assert report['source_before'] == report['source_after']


def test_unavailable_sandbox_degrades_hash_only(game,tmp_path,monkeypatch):
    monkeypatch.setattr(client,'run_sandbox',lambda *a,**k:dict(started=False,appcontainer=False,capabilities=None))
    result=client.MetadataWorker(tmp_path/'data').inspect(game,'Mods/WagesPerks.dll')
    assert result['status']=='hash_only' and result['error']=='SANDBOX_UNAVAILABLE'
    assert result['sha256']==hashlib.sha256((game/'Mods/WagesPerks.dll').read_bytes()).hexdigest()
    assert result['metadata'] is None
    assert not list((tmp_path/'data/metadata/staging').iterdir())


def test_missing_helper_degrades_hash_only(game,tmp_path):
    result=client.MetadataWorker(tmp_path/'data',helper=tmp_path/'missing').inspect(game,'Mods/WagesPerks.dll')
    assert result['status']=='hash_only' and result['error']=='HELPER_UNAVAILABLE'


def test_worker_source_changed_during_parse_is_not_current_metadata(game,tmp_path,monkeypatch):
    original=client.run_sandbox
    def drift(*args,**kwargs):
        result=original(*args,**kwargs)
        with (game/'Mods/WagesPerks.dll').open('ab') as f:f.write(b'external change')
        return result
    monkeypatch.setattr(client,'run_sandbox',drift)
    result=client.MetadataWorker(tmp_path/'data').inspect(game,'Mods/WagesPerks.dll')
    assert result['status']=='changed' and result['error']=='INPUT_CHANGED' and result['metadata'] is None


@pytest.mark.parametrize('relative',['../sentinel.dll','C:/outside.dll','Mods/../x.dll','Mods/x.dll:stream','Mods//x.dll','Mods/./x.dll','\\\\server\\x.dll'])
def test_parent_rejects_path_escape(game,tmp_path,relative):
    with pytest.raises(ValueError):client.MetadataWorker(tmp_path/'data').inspect(game,relative)


def test_size_limit_and_cancel_do_not_launch_worker(game,tmp_path,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('worker must not launch')
    monkeypatch.setattr(client,'run_sandbox',forbidden)
    with (game/'Mods/huge.dll').open('wb') as f:f.truncate(client.MAX_INPUT+1)
    worker=client.MetadataWorker(tmp_path/'data')
    assert worker.inspect(game,'Mods/huge.dll')['error']=='INPUT_LIMIT'
    assert worker.inspect(game,'Mods/WagesPerks.dll',cancelled=lambda:True)['status']=='cancelled'


@pytest.mark.parametrize('raw',['{}','[]','not-json','{"protocol_version":2,"status":"native","report":{},"error":null}'])
def test_parent_rejects_malformed_protocol(raw):
    process=dict(started=True,appcontainer=True,capabilities=0,status='exited',cleanup_remaining=0,
                 profile_deleted=True,desktop_closed=True,lpac_policy_applied=True,exit_code=0,stdout=raw)
    with pytest.raises(ValueError,match='PROTOCOL'):client.decode_response(process)


def test_bridge_does_not_accept_paths_or_unknown_installation(tmp_path):
    context=DesktopContext(resolve_paths(str(tmp_path/'data')));context.initialize();api=DesktopApi(context)
    try:
        for payload in ({'installation_id':str(uuid4()),'files':['Mods/a.dll'],'root':'C:/secret'},
                        {'installation_id':str(uuid4()),'files':['../secret.dll']}):
            response=api.dispatch(request('inventory.inspect',payload))
            assert not response['ok'] and response['error']['code']=='INVALID_REQUEST'
        response=api.dispatch(request('inventory.inspect',{'installation_id':str(uuid4()),'files':['Mods/a.dll']}))
        assert response['error']['code']=='INSTALLATION_NOT_FOUND'
    finally:context.close()


def test_network_preflight_failure_never_publishes_metadata(game,tmp_path,monkeypatch):
    value=dict(protocol_version=1,status='sandbox_unavailable',report=None,error='network_boundary_unproven')
    monkeypatch.setattr(client,'run_sandbox',lambda *a,**k:dict(started=True,appcontainer=True,capabilities=0,
        lpac_policy_applied=True,status='exited',cleanup_remaining=0,profile_deleted=True,desktop_closed=True,
        exit_code=2,stdout=json.dumps(value)))
    result=client.MetadataWorker(tmp_path/'data').inspect(game,'Mods/WagesPerks.dll')
    assert result['status']=='hash_only' and result['error']=='NETWORK_BOUNDARY_UNPROVEN'
    assert result['metadata'] is None and result['sha256']


def test_hardlink_input_and_data_root_overlap_refused(game,tmp_path):
    import os
    os.link(game/'Mods/WagesPerks.dll',game/'Mods/alias.dll')
    result=client.MetadataWorker(tmp_path/'data').inspect(game,'Mods/alias.dll')
    assert result['metadata'] is None and result['sha256'] is None
    with pytest.raises(ValueError,match='conflicts'):
        client.MetadataWorker(game/'private').inspect(game,'Mods/WagesPerks.dll')


@pytest.mark.parametrize('mode',['hang','crash'])
def test_actual_worker_fault_does_not_stop_next_inventory_file(game,tmp_path,probe,monkeypatch,mode):
    (game/'Mods/Fault.dll').write_bytes(b'owned worker fault')
    original=client.run_sandbox
    def inject(argv,request,**kwargs):
        stage=kwargs['stage']
        if (stage/'input.dll').read_bytes()==b'owned worker fault':
            for name in ('probe.dll','probe.runtimeconfig.json'):
                shutil.copyfile(probe[0]/name,stage/name)
            return original([probe[1],stage/'probe.dll'],json.dumps({'mode':mode}).encode(),
                            stage=stage,timeout=1,cancelled=kwargs['cancelled'])
        return original(argv,request,**kwargs)
    monkeypatch.setattr(client,'run_sandbox',inject)
    context=DesktopContext(resolve_paths(str(tmp_path/'data')));context.initialize()
    context.choose_installation_folder=lambda kind:str(game);api=DesktopApi(context)
    try:
        candidate=discover(api)
        chosen=wait(api,api.dispatch(request('installation.select',{'candidate_id':candidate['candidate_id']}))['data'])
        response=api.dispatch(request('inventory.inspect',{'installation_id':chosen['data']['installation']['installation_id'],
                                                           'files':['Mods/Fault.dll','Mods/WagesPerks.dll']}))
        completed=wait(api,response['data']);assert completed['state']=='succeeded',completed
        bad,good=completed['data']['files']
        assert bad['status']=='hash_only' and bad['error'] in ('TIMEOUT','WORKER_PROTOCOL_INVALID')
        assert good['status']=='managed' and good['assembly_name']=='JacksonPerks'
        report=json.loads((context.paths.root/'metadata/reports'/(bad['metadata_report_id']+'.json')).read_text(encoding='utf-8'))
        assert report['process']['cleanup_remaining']==0
        assert not list((context.paths.root/'metadata/staging').iterdir())
    finally:context.close()
