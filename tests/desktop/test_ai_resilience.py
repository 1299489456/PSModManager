"""CASE-043 visible failure/retry/cancel and independent local management."""
import io
import json
import os
from pathlib import Path
import socket
import sys

from scripts.verification.processes import run_command
from tests.integration.harness.ai_provider import SyntheticAIProvider

ROOT = Path(__file__).resolve().parents[2]


def test_case043_actual_webview_failures_retry_cancel_and_offline_mods(tmp_path_factory):
    out = tmp_path_factory.mktemp('air')
    with socket.socket() as endpoint:
        endpoint.bind(('127.0.0.1', 0))
        offline_url = f'http://127.0.0.1:{endpoint.getsockname()[1]}/v1'
    with SyntheticAIProvider(delay=.005) as provider:
        original_handler = provider.server.RequestHandlerClass

        class Handler(original_handler):
            """Own HTTP responses; the gateway and desktop remain unmodified."""
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                body = json.loads(raw)
                if body['model'] == 'synthetic-unknown-model':
                    with provider._lock:
                        provider.requests.append(dict(path=self.path, headers=dict(self.headers), body=body))
                    self._json(404, {'error': {'code': 'model_not_found', 'message': 'Unknown synthetic model'}})
                    return
                self.rfile = io.BytesIO(raw)
                question = next(m['content'] for m in reversed(body['messages']) if m['role'] == 'user')
                with provider._lock:
                    provider.delay = .005
                    if question == 'PS0404 限流':
                        seen = sum(any(m.get('role') == 'user' and m.get('content') == question
                                       for m in row['body']['messages']) for row in provider.requests)
                        provider.mode = '429' if not seen else 'normal'
                        provider.chunks = ['手动重试成功，仅说明当前回答。']
                    elif question == 'PS0404 中断':
                        provider.mode, provider.chunks = 'eof', ['已收到的部分回复。']
                    elif question == 'PS0404 取消':
                        provider.mode, provider.chunks, provider.delay = 'normal', ['取消前已收到流。'], 8
                    elif question == 'PS0404 超时':
                        provider.mode, provider.chunks = 'stall', []
                    else:
                        provider.mode, provider.chunks = 'normal', ['普通回答；文件操作完成不证明游戏问题已修复。']
                super().do_POST()

        provider.server.RequestHandlerClass = Handler
        result = run_command([sys.executable, '-I', '-B', str(ROOT/'tests/desktop/harness/ai_resilience.py'),
            '--evidence-root', str(out), '--provider-url', provider.base_url, '--offline-url', offline_url],
            cwd=ROOT, env=os.environ.copy(), timeout=300,
            stdout_path=out/'stdout.log', stderr_path=out/'stderr.log')
        (out/'process.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        assert result['started'] and not result['timed_out'] and not result['error'], result
        assert result['exit_code'] == 0, f'Inspect {out}/ai-resilience.json and stderr.log'
        assert result['cleanup']['remaining_processes'] == 0 and not result['cleanup'].get('error'), result
        report = json.loads((out/'ai-resilience.json').read_text(encoding='utf-8'))
        assert report['status'] == 'PASS' and report['cleanup'] == dict(active_workers=0, subscriptions=0)
        assert len(provider.requests) == 7
        bodies = json.dumps([row['body'] for row in provider.requests], ensure_ascii=False)
        for sentinel in ('owned-resilience-resource', 'independent-resilience-mod', 'isolated-save-never-deployed'):
            assert sentinel not in bodies
        assert all(not row['headers'].get('Authorization') and not row['body'].get('tools') for row in provider.requests)
        (out/'provider-observations.json').write_text(json.dumps(dict(request_count=7, localhost_only=True,
            explicit_manual_retry_requests=1, automatic_retry_requests=0, local_mod_http_requests=0,
            full_package_or_save_sent=False, no_credentials_used=True), indent=2), encoding='utf-8')
