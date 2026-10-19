"""Owned localhost HTTP/SSE provider for actual desktop AI consumers.

This server never contacts an external host and never logs authorization or
request text. Tests inspect captures in memory only.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time


class SyntheticAIProvider:
    def __init__(self, *, mode='normal', chunks=None, delay=.01):
        self.mode = mode
        self.chunks = ['合成', '回答'] if chunks is None else list(chunks)
        self.delay = delay
        self.requests = []
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
                    if not 0 < length <= 65536:
                        self.send_error(413)
                        return
                    raw = self.rfile.read(length)
                    body = json.loads(raw)
                    with provider._lock:
                        provider.requests.append({'path': self.path,
                                                  'headers': dict(self.headers),
                                                  'body': body})
                        mode = provider.mode
                        chunks = list(provider.chunks)
                    question = body.get('messages', [{}])[-1].get('content', '')
                    for candidate in ('401', '429', '500', 'malformed', 'eof', 'stall'):
                        if '[[' + candidate + ']]' in question:
                            mode = candidate
                            break
                    if self.path != '/v1/chat/completions':
                        self._json(404, {'error': {'message': 'unexpected synthetic endpoint'}})
                        return
                    if mode in ('401', '429', '500'):
                        self._json(int(mode), {'error': {'message': 'provider body must never echo ' + self.headers.get('Authorization', '')}})
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    if mode == 'stall':
                        self.wfile.flush()
                        provider._release.wait(8)
                        self.close_connection = True
                        return
                    if mode == 'malformed':
                        self.wfile.write(b'data: {this is not JSON}\n\n')
                        self.wfile.flush()
                        self.close_connection = True
                        return
                    for text in chunks:
                        event = {'id': 'synthetic', 'choices': [{'index': 0, 'delta': {'content': text}}]}
                        self.wfile.write(('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode('utf-8'))
                        self.wfile.flush()
                        if provider.delay:
                            time.sleep(provider.delay)
                    if mode != 'eof':
                        finish = {'id': 'synthetic', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}
                        self.wfile.write(('data: ' + json.dumps(finish) + '\n\n').encode('utf-8'))
                        self.wfile.write(b'data: [DONE]\n\n')
                        self.wfile.flush()
                    self.close_connection = True
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # The real consumer may cancel and close the socket.
                    self.close_connection = True

            def _json(self, code, value):
                raw = json.dumps(value).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(raw)
                self.wfile.flush()
                self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name='synthetic-ai-http', daemon=True)
        self.base_url = 'http://127.0.0.1:' + str(self.server.server_port) + '/v1'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self._release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def set_response(self, *, mode='normal', chunks=None):
        with self._lock:
            self.mode = mode
            if chunks is not None:
                self.chunks = list(chunks)

    def wait_requests(self, count=1, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.requests) >= count:
                    return self.requests[count - 1]
            time.sleep(.01)
        raise AssertionError('Synthetic provider did not receive request')
