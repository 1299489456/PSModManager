"""CASE-042 real suggestion buttons, ordinary file effects, localhost only."""
import json
import os
from pathlib import Path
import sys

from scripts.verification.processes import run_command
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider

ROOT=Path(__file__).resolve().parents[2]


def test_case042_actual_webview_suggestion_buttons_and_ordinary_errors(tmp_path_factory):
    out=tmp_path_factory.mktemp('aiv')
    def turn(body,index):
        if body['messages'][-1]['role']=='tool':
            result=json.loads(body['messages'][-1]['content'])
            assert result['ok'] and result['data']['executed'] is False
            return dict(text='建议已生成，请按需点击动作按钮。')
        assert any(tool['function']['name']=='suggest_mod_action' for tool in body['tools'])
        user=next(m['content'] for m in reversed(body['messages']) if m['role']=='user')
        action=user.split('桌面建议 ')[-1]
        assert action in {'install','enable','disable'}
        release=json.loads((out/'release.json').read_text(encoding='utf-8'))
        return dict(calls=[('suggest_mod_action',dict(release_id=release['release_id'],manifest_hash=release['manifest_hash'],
            action=action,reason='普通建议 <script>window.__AISuggestionExecuted=true</script> token=desktop-action-secret'))])
    with ScriptedAIProvider([turn]*12,delay=.005) as provider:
        result=run_command([sys.executable,'-I','-B',str(ROOT/'tests/desktop/harness/ai_plan_handoff.py'),
            '--evidence-root',str(out),'--provider-url',provider.base_url],cwd=ROOT,env=os.environ.copy(),timeout=210,
            stdout_path=out/'stdout.log',stderr_path=out/'stderr.log')
        (out/'process.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'],result
        assert result['exit_code']==0,f'Inspect {out}/ai-handoff.json and stderr.log'
        assert result['cleanup']['remaining_processes']==0 and not result['cleanup'].get('error'),result
        report=json.loads((out/'ai-handoff.json').read_text(encoding='utf-8'))
        assert report['status']=='PASS' and report['cleanup']==dict(active_workers=0,subscriptions=0)
        assert len(provider.requests)==12 and not provider.errors,provider.errors
        bodies=json.dumps([row['body'] for row in provider.requests],ensure_ascii=False)
        for sentinel in ('desktop-action-secret','owned-desktop-suggestion-resource','independent-desktop-mod','isolated-save-never-deployed'):
            assert sentinel not in bodies
        (out/'provider-observations.json').write_text(json.dumps(dict(request_count=12,localhost_only=True,
            adoption_contacts_provider=False,full_package_or_save_sent=False,secret_sentinels_absent=True),indent=2),encoding='utf-8')
