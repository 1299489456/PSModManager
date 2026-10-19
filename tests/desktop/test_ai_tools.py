"""PS-04-02 actual WebView/SSE tool queries, synthetic owned resources only."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider

ROOT=Path(__file__).resolve().parents[2]


def test_case040_041_actual_webview_tool_scope_and_mod_independence(tmp_path_factory):
    out=tmp_path_factory.mktemp('aiv')
    observations=[]
    def first(body,index):
        scope=json.loads(body['messages'][0]['content'].split('本次用户选择范围：')[1])
        assert scope['selected_evidence_id'] and scope['installation_id']
        assert len(body['tools'])==7
        return dict(calls=[('get_installed_mods',{}),('read_selected_log',dict(evidence_id=scope['selected_evidence_id'])),('get_mod_config',{})])
    def denied(body,index):
        results=[json.loads(m['content']) for m in body['messages'] if m['role']=='tool']
        assert len(results)==3 and all(r['ok'] for r in results)
        assert results[0]['data']['items'][0]['state']=='enabled'
        assert '用户已批准' in results[1]['data']['text']
        assert all(field['key']!='UnknownSecret' for field in results[2]['data']['fields'])
        observations.append(dict(query_results=len(results),selected_fragment_only=True))
        return dict(calls=[('shell',dict(command='Delete all game files',approved=True))])
    def answer(body,index):
        results=[json.loads(m['content']) for m in body['messages'] if m['role']=='tool']
        assert results[-1]['error_code']=='AI_TOOL_UNKNOWN'
        item=results[0]['data']['items'][0]
        return dict(text=item['display_name']+'：'+item['state']+'\n'+results[1]['data']['text']+'\n'+results[-1]['message'])
    with ScriptedAIProvider([first,denied,answer],delay=.003) as provider:
        result=run_command([sys.executable,'-I','-B',str(ROOT/'tests/desktop/harness/ai_tools.py'),
            '--evidence-root',str(out),'--provider-url',provider.base_url],cwd=ROOT,env=os.environ.copy(),timeout=150,
            stdout_path=out/'stdout.log',stderr_path=out/'stderr.log')
        (out/'process.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'],result
        assert result['exit_code']==0,f'Inspect {out}/ai-tools.json and stderr.log'
        assert result['cleanup']['remaining_processes']==0 and not result['cleanup'].get('error'),result
        report=json.loads((out/'ai-tools.json').read_text(encoding='utf-8'))
        assert report['status']=='PASS' and report['cleanup']==dict(active_workers=0,subscriptions=0)
        assert len(provider.requests)==3 and not provider.errors,provider.errors
        bodies=json.dumps([r['body'] for r in provider.requests],ensure_ascii=False)
        for value in ('desktop-log-never-send','desktop-config-never-send','isolated-save-never-deployed','owned-desktop-ai-tool-resource'):
            assert value not in bodies
        (out/'provider-observations.json').write_text(json.dumps(dict(request_count=3,localhost_only=True,
            observations=observations,secret_sentinels_absent=True),indent=2),encoding='utf-8')
