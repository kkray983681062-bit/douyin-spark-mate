from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest


@pytest.fixture
def chat_server():
    state = SimpleNamespace(requests=0, gate=None, gate_expired=False)
    content = (Path(__file__).with_name('fixtures')/'chat.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/':
                self.send_error(404)
                return
            state.requests += 1
            if state.gate and not state.gate.wait(4):
                state.gate_expired = True
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f'http://127.0.0.1:{server.server_port}/'
    yield state
    if state.gate:
        state.gate.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
