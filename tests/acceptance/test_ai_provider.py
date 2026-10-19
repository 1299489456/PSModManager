"""CASE-044 optional authorized real-provider check; disabled by default.

PSMM_AI_LIVE_ENABLE=1 is set only after concrete endpoint/model/fixed-text
authorization. Merely having application settings or credentials never opts in.
This test sends one fixed synthetic question via the real DesktopApi/Gateway.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from urllib.parse import urlsplit

import pytest

from backend.ai.gateway import normalize_url
from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.ai.redaction import redact

ROOT=Path(__file__).resolve().parents[2]
QUESTION='请用一句中文说明：Mod 文件安装完成，不代表游戏玩法兼容。只解释，不请求或修改任何文件。'


def dispatch(api,command,payload):
    response=api.dispatch(dict(schema_version=1,request_id=str(uuid4()),command=command,payload=payload))
    if not response['ok']:
        raise AssertionError('AI_LIVE_ENTRY_FAILED:'+response['error']['code'])
    return response['data']


@pytest.mark.skipif(os.environ.get('PSMM_AI_LIVE_ENABLE')!='1',reason='ai-live NOT_RUN: concrete provider/fixed-text authorization not supplied')
def test_case044_authorized_provider_fixed_text_through_actual_api(tmp_path):
    assert os.environ.get('PSMM_VERIFY_PROFILE')=='ai-live','Use the dedicated ai-live profile'
    assert tmp_path.resolve().is_relative_to(ROOT/'docs/development/evidence/PS-04-05'),'Use this task owned --basetemp'
    base_url=normalize_url(os.environ.get('PSMM_AI_LIVE_URL',''))
    assert urlsplit(base_url).scheme=='https','Real provider requires HTTPS'
    model=os.environ.get('PSMM_AI_LIVE_MODEL','')
    assert model.strip(),'An explicit model is required'
    context=DesktopContext(resolve_paths(str(tmp_path/'private-data')))
    context.initialize()
    api=DesktopApi(context)
    credential=(os.environ.get('PSMM_AI_LIVE_KEY') or '').strip()
    report=dict(status='FAIL',profile='ai-live',endpoint=redact(base_url,[credential] if credential else []),
        model=redact(model,[credential] if credential else []),
        send_scope='One fixed synthetic question; no tools, game, logs, packages, DLLs, saves or prior history',
        request_budget=dict(seconds=30,output_bytes=2000,provider_calls=1),cost='UNKNOWN')
    try:
        # Session-only key; never read the user's application config or keyring account.
        payload=dict(provider='openai_compatible',base_url=base_url,model=model,remember=False)
        if credential:
            payload['api_key']=credential
        settings=dispatch(api,'ai.settings.save',payload)
        context.ai.limits=replace(context.ai.limits,request_seconds=30,output_bytes=2000,provider_calls=1)
        task=dispatch(api,'ai.chat.send',dict(question=QUESTION))
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            terminal=dispatch(api,'task.snapshot',dict(task_id=task['task_id']))
            if terminal['state'] in {'succeeded','failed','cancelled'}:
                break
            time.sleep(.05)
        else:
            dispatch(api,'task.cancel',dict(task_id=task['task_id']))
            raise AssertionError('AI_LIVE_WAIT_EXCEEDED')
        data=terminal.get('data') or {}
        if credential and credential in json.dumps([settings,terminal],ensure_ascii=False):
            raise AssertionError('AI_LIVE_SECRET_EXPOSED')
        report.update(task_state=terminal['state'],error_code=data.get('error_code'),
            response_bytes=len(data.get('text','').encode('utf-8')))
        assert terminal['state']=='succeeded' and data.get('completed') and not data.get('error_code'),'AI_LIVE_REQUEST_NOT_COMPLETED'
        assert data.get('text') and data.get('suggestions')==[],'AI_LIVE_RESPONSE_INVALID'
        report.update(status='PASS',response_text=data['text'])
    finally:
        manager=context.jobs
        context.close()
        report['cleanup']=dict(active_workers=sum(job.thread.is_alive() for job in manager._jobs.values()),
                               subscriptions=len(manager._subscriptions))
        if any(report['cleanup'].values()):
            report['status']='FAIL'
        (tmp_path/'ai-provider-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    assert report['status']=='PASS','AI_LIVE_CLEANUP_FAILED'
