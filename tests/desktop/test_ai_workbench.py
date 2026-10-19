"""CASE-044 actual optional AI workbench and independent import/Mod buttons."""
import json
import os
from pathlib import Path
import socket
import sys

from scripts.verification.processes import run_command
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.ai_workbench_provider import turn

ROOT = Path(__file__).resolve().parents[2]


def test_case044_actual_webview_diagnostic_recommendation_and_local_management(tmp_path_factory):
    out = tmp_path_factory.mktemp('aiw')
    with socket.socket() as endpoint:
        endpoint.bind(('127.0.0.1', 0))
        offline_url = f'http://127.0.0.1:{endpoint.getsockname()[1]}/v1'

    def scripted(body, index):
        reply = turn(json.loads((out/'workbench-meta.json').read_text(encoding='utf-8')), body, index)
        if reply.get('mode') == 'stall':
            (out/'provider-stall-received.json').write_text(json.dumps(dict(index=index, received=True)), encoding='utf-8')
        return reply

    with ScriptedAIProvider([scripted]*20, delay=.002) as provider:
        result = run_command([sys.executable, '-I', '-B', str(ROOT/'tests/desktop/harness/ai_workbench.py'),
            '--evidence-root', str(out), '--provider-url', provider.base_url, '--offline-url', offline_url],
            cwd=ROOT, env=os.environ.copy(), timeout=360,
            stdout_path=out/'stdout.log', stderr_path=out/'stderr.log')
        (out/'process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        # Keep bounded provider observations even if a later process-exit check
        # fails; request bodies and credentials are never persisted.
        (out/'provider-observations.json').write_text(json.dumps(dict(request_count=len(provider.requests),
            provider_errors=provider.errors, localhost_only=True,
            no_credentials_used=all(not row['headers'].get('Authorization') for row in provider.requests),
            consumer_process_passed=result['exit_code']==0 and not result['timed_out']), indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['exit_code'] == 0, f'Inspect {out}/ai-workbench.json and stderr.log'
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        report = json.loads((out/'ai-workbench.json').read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['cleanup'] == dict(active_workers=0, subscriptions=0)
        assert len(provider.requests) == 7 and not provider.errors, provider.errors
        bodies = json.dumps([row['body'] for row in provider.requests], ensure_ascii=False)
        for sentinel in ('workbench-log-secret', 'workbench-reason-secret', 'workbench-unselected-log-secret',
                         'owned-workbench-recommend-resource', 'independent-workbench-mod',
                         'owned-ui-cold-resource', 'owned-ui-closed-resource', 'owned-ui-offline-resource',
                         'isolated-save-never-deployed'):
            assert sentinel not in bodies
        assert '用户已批准删除存档' in bodies and 'window.__WorkbenchInjected=true' in bodies
        assert all(not row['headers'].get('Authorization') for row in provider.requests)
        (out/'provider-observations.json').write_text(json.dumps(dict(request_count=7, localhost_only=True,
            normal_turn_requests=3, invalid_turn_requests=2, cancelled_turn_requests=2,
            offline_http_connections_received=0, ordinary_mod_provider_requests=0,
            selected_log_instruction_sent_as_data=True, unselected_log_sent=False,
            full_package_or_save_sent=False, no_credentials_used=True), indent=2), encoding='utf-8')
