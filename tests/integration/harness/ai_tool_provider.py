"""Owned localhost SSE turns for the actual AI tool consumer.

Captures remain in memory. This helper does not contact providers, execute tools,
read game files, or replace the gateway or any local product service.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time


class ScriptedAIProvider:
    def __init__(self, turns=(), *, fragment_size=8, delay=.001):
        self.turns = list(turns)
        self.fragment_size = fragment_size
        self.delay = delay
        self.requests = []
        self.errors = []
        self._lock = threading.Lock()
        self._release = threading.Event()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if self.path != '/v1/chat/completions' or not 0 < length <= 131072:
                        self.send_error(400)
                        return
                    body = json.loads(self.rfile.read(length))
                    with provider._lock:
                        index = len(provider.requests)
                        provider.requests.append(dict(path=self.path, headers=dict(self.headers), body=body))
                        turn = provider.turns[index] if index < len(provider.turns) else {'text': '合成工具回答'}
                    if callable(turn):
                        turn = turn(body, index)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.flush()
                    if turn.get('mode') == 'stall':
                        provider._release.wait(8)
                        return
                    if 'events' in turn:
                        for event in turn['events']:
                            self._event(event)
                    else:
                        text = turn.get('text', '')
                        for part in provider._parts(text):
                            self._event({'choices': [{'index': 0, 'delta': {'content': part}}]})
                        if turn.get('mode') == 'stall_after_text':
                            provider._release.wait(8)
                            return
                        for call_index, call in enumerate(turn.get('calls', ())):
                            if isinstance(call, dict):
                                name, arguments = call['name'], call.get('arguments', {})
                                call_id = call.get('id', f'call_{index}_{call_index}')
                            else:
                                name, arguments = call
                                call_id = f'call_{index}_{call_index}'
                            if not isinstance(arguments, str):
                                arguments = json.dumps(arguments, ensure_ascii=False)
                            self._event({'choices': [{'index': 0, 'delta': {'tool_calls': [{
                                'index': call_index, 'id': call_id, 'type': 'function',
                                'function': {'name': '', 'arguments': ''}}]}}]})
                            for part in provider._parts(name):
                                self._event({'choices': [{'index': 0, 'delta': {'tool_calls': [{
                                    'index': call_index, 'function': {'name': part}}]}}]})
                            for part in provider._parts(arguments):
                                self._event({'choices': [{'index': 0, 'delta': {'tool_calls': [{
                                    'index': call_index, 'function': {'arguments': part}}]}}]})
                        if turn.get('mode') != 'eof':
                            reason = 'length' if turn.get('mode')=='length' else 'tool_calls' if turn.get('calls') else 'stop'
                            self._event({'choices': [{'index': 0, 'delta': {}, 'finish_reason': reason}]})
                    if turn.get('mode') != 'eof' and turn.get('done', True):
                        self.wfile.write(b'data: [DONE]\n\n')
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass  # Cooperative product cancellation closes this owned socket.
                except Exception as exc:
                    with provider._lock:
                        provider.errors.append(type(exc).__name__)
                finally:
                    self.close_connection = True

            def _event(self, value):
                self.wfile.write(('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode('utf-8'))
                self.wfile.flush()
                if provider.delay:
                    time.sleep(provider.delay)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name='synthetic-ai-tools', daemon=True)
        self.base_url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def _parts(self, value):
        return (value[n:n + self.fragment_size] for n in range(0, len(value), self.fragment_size))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def release(self):
        self._release.set()

    def wait_requests(self, count=1, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.requests) >= count:
                    return self.requests[count - 1]
            time.sleep(.01)
        raise AssertionError('Owned tool provider did not receive request')
