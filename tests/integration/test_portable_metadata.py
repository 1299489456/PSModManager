"""Actual frozen console host and bundled parser, with no developer PATH."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import pytest
import win32api
import win32job

from backend.scanning.worker_client import decode_response
from tests.integration.test_content_catalog import compile_fixture


def test_frozen_console_host_and_self_contained_parser(tmp_path_factory):
    configured = os.environ.get('PSMM_PORTABLE_ROOT')
    if not configured:
        pytest.skip('Select the actual portable product with PSMM_PORTABLE_ROOT')
    bundle = Path(configured).resolve()
    root = tmp_path_factory.mktemp('w')
    source = compile_fixture(root/'source', 'FrozenMetadataFixture')
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    stage = root/'stage'
    stage.mkdir()
    helper = bundle/'_internal/tools/PSModManager.Metadata/bin'
    for file in helper.iterdir():
        assert file.is_file() and not file.is_symlink()
        shutil.copyfile(file, stage/file.name)
    shutil.copyfile(source, stage/'input.dll')
    request = json.dumps(dict(protocol_version=1,input_path=str(stage/'input.dll'))).encode()
    payload = dict(argv=[str(stage/'PSModManager.Metadata.exe'),'--require-isolation'],
        request=base64.b64encode(request).decode(),stage=str(stage),timeout=8)
    env = os.environ.copy()
    env['PATH'] = str(Path(env['SYSTEMROOT'])/'System32')
    env['DOTNET_ROOT'] = str(root/'missing-dotnet')
    env['DOTNET_MULTILEVEL_LOOKUP'] = '0'
    for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV'):
        env.pop(key,None)
    assert shutil.which('python',path=env['PATH']) is None
    assert shutil.which('dotnet',path=env['PATH']) is None
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    job = win32job.CreateJobObject(None,'')
    limits = win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limits)
    process = None
    result = dict(status='FAIL',developer_commands_visible=False,system_dotnet_still_installed=True)
    try:
        process = subprocess.Popen([str(bundle/'PSModManager.WorkerHost.exe')],cwd=root,env=env,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            startupinfo=startup,creationflags=subprocess.CREATE_NEW_CONSOLE)
        # Broker cannot create its worker until this controlling message arrives.
        win32job.AssignProcessToJobObject(job,int(process._handle))
        streams=[bytearray(),bytearray()]
        def drain(stream,target):
            while block:=stream.read(8192):
                target.extend(block)
        readers=[threading.Thread(target=drain,args=(pipe,target),daemon=True)
                 for pipe,target in zip((process.stdout,process.stderr),streams)]
        for reader in readers:
            reader.start()
        process.stdin.write(json.dumps(payload).encode()+b'\n')
        process.stdin.flush()
        process.wait(timeout=25)
        for reader in readers:
            reader.join(2)
            assert not reader.is_alive()
        result.update(exit_code=process.returncode,stderr=streams[1].decode('utf-8',errors='replace'))
        assert process.returncode==0,result
        observed=json.loads(streams[0])
        result['worker']=observed
        decoded=decode_response(observed)
        assert decoded['status']=='managed',decoded
        assert decoded['report']['assembly_name']=='FrozenMetadataFixture',decoded
        assert hashlib.sha256(source.read_bytes()).hexdigest()==expected
        assert not (root/'source/executed.txt').exists()
        deadline=time.monotonic()+3
        while win32job.QueryInformationJobObject(job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses'] and time.monotonic()<deadline:
            time.sleep(.02)
        result['remaining_processes']=win32job.QueryInformationJobObject(job,win32job.JobObjectBasicAccountingInformation)['ActiveProcesses']
        assert result['remaining_processes']==0
        result['status']='PASS'
    finally:
        win32job.TerminateJobObject(job,124)
        if process is not None:
            process.wait(timeout=5)
            for stream in (process.stdin,process.stdout,process.stderr):
                stream.close()
        win32api.CloseHandle(job)
        (root/'frozen-metadata.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
