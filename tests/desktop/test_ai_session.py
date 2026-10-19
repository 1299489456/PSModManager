"""Actual optional AI tab, localhost SSE, owned Windows credential and reopening."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from backend.ai.credentials import NativeCredentials
from backend.app.identity import KEYRING_SERVICE
from scripts.verification.processes import run_command
from tests.integration.harness.ai_provider import SyntheticAIProvider

ROOT = Path(__file__).resolve().parents[2]
OWNED_KEY = 'desktop-owned-synthetic-ai-key-PS0401'
CHUNKS = ['合成', '回答 <script>window.__PSMMAIExecuted=true</script> ',
          'token=synthetic-response-token\nProxy-Authorization: Bearer synthetic-response-proxy']


def test_case039_actual_webview_optional_ai_and_native_credentials(tmp_path):
    with SyntheticAIProvider(chunks=CHUNKS, delay=.4) as provider:
        account_value = str((tmp_path / 'private-data').resolve()).casefold() + '\0openai_compatible\0' + provider.base_url
        account = 'ai-' + hashlib.sha256(account_value.encode('utf-8')).hexdigest()
        vault = NativeCredentials()
        assert vault.get_password(KEYRING_SERVICE, account) is None
        checkpoints, failures = [], []
        stop = threading.Event()

        def watch_checkpoints():
            for name in ('default-local', 'settings-saved'):
                marker = tmp_path / (name + '-checkpoint.json')
                while not marker.exists() and not stop.wait(.01):
                    pass
                if stop.is_set():
                    return
                count = len(provider.requests)
                checkpoints.append({'checkpoint': name, 'http_requests': count})
                if count:
                    failures.append('Unexpected request before explicit AI send/test at ' + name)
                (tmp_path / (name + '-ack.json')).write_text(json.dumps({'http_requests': count}), encoding='utf-8')

        watcher = threading.Thread(target=watch_checkpoints, daemon=True)
        watcher.start()
        try:
            for phase in ('first', 'reopen'):
                result = run_command([sys.executable, '-I', '-B', str(ROOT / 'tests/desktop/harness/ai_session.py'),
                    '--evidence-root', str(tmp_path), '--phase', phase, '--provider-url', provider.base_url],
                    cwd=ROOT, env=os.environ.copy(), timeout=150,
                    stdout_path=tmp_path / (phase + '-stdout.log'), stderr_path=tmp_path / (phase + '-stderr.log'))
                (tmp_path / (phase + '-process.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
                assert result['started'] and not result['timed_out'] and not result['error'], result
                assert result['exit_code'] == 0, f'Inspect {tmp_path}/{phase}-ai-session.json'
                assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
                report = json.loads((tmp_path / (phase + '-ai-session.json')).read_text(encoding='utf-8'))
                assert report['status'] == 'PASS' and report['app_exit_code'] == 0
                assert report['cleanup'] == {'active_workers': 0, 'subscriptions': 0}
                if phase == 'first':
                    assert vault.get_password(KEYRING_SERVICE, account) == OWNED_KEY
                    assert len(provider.requests) == 4
                else:
                    assert vault.get_password(KEYRING_SERVICE, account) is None
                    assert len(provider.requests) == 6
            assert not failures, failures
            assert checkpoints == [{'checkpoint': 'default-local', 'http_requests': 0},
                                   {'checkpoint': 'settings-saved', 'http_requests': 0}]
            requests = provider.requests
            assert requests[0]['body']['messages'] == [{'role': 'user', 'content': 'Reply only OK.'}]
            for index, item in enumerate(requests):
                assert item['path'] == '/v1/chat/completions'
                assert set(item['body']) == {'model', 'messages', 'stream'}
                assert item['body']['model'] == 'synthetic-text-model' and item['body']['stream'] is True
                assert item['headers'].get('Authorization') == 'Bearer ' + OWNED_KEY if index < 5 else 'Authorization' not in item['headers']
                serialized = json.dumps(item['body'], ensure_ascii=False)
                for secret in (OWNED_KEY, 'desktop-selected-token', 'desktop-selected-proxy', 'desktop-url-pass',
                               'desktop-query-token', 'synthetic-response-token', 'synthetic-response-proxy'):
                    assert secret not in serialized
                for sentinel in ('isolated-save-never-deployed', 'private-log-must-not-send', 'AiDesktopMod', 'DLL-BINARY-MUST-NOT-SEND'):
                    assert sentinel not in serialized
            question = requests[1]['body']['messages'][-1]['content']
            assert '解释这个报错。' in question and '框选片段' in question and '[已隐藏]' in question
            assert '[[401]]' in requests[2]['body']['messages'][-1]['content']
            assert '[[stall]]' in requests[3]['body']['messages'][-1]['content']
            (tmp_path / 'provider-observations.json').write_text(json.dumps({
                'profile': 'desktop-local', 'localhost_only': True, 'request_count': len(requests),
                'checkpoints': checkpoints, 'native_compound_credentials_cleared': True,
                'request_bodies_checked_in_memory': True, 'request_headers_checked_in_memory': True,
                'sent_scope': 'fixed connection test or question/selected text/recent conversation; no automatic local files',
            }, indent=2), encoding='utf-8')
        finally:
            stop.set()
            watcher.join(2)
            if vault.get_password(KEYRING_SERVICE, account) is not None:
                vault.delete_password(KEYRING_SERVICE, account)
            assert vault.get_password(KEYRING_SERVICE, account) is None
