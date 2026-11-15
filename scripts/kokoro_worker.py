#!/usr/bin/env python3
"""Explicitly started loopback-only Kokoro experiment; no runtime downloads.

Run with the isolated environment's Python, local KOKORO_MODEL/KOKORO_VOICES,
and KOKORO_EXPERIMENT_ENABLED=1. The default port is 8877. Never import this
worker into Gunicorn: it owns one CPU model session and one preparation queue.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tts_kokoro import KokoroConfig, KokoroEngine, KokoroError, PreparationQueue


MAX_AUDIO_BYTES = 8000
MAX_PREPARE_BYTES = 2_500_000


class WorkerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine, preparation=None):
        if address[0] != '127.0.0.1':
            raise ValueError('The experimental worker may bind only 127.0.0.1')
        self.engine = engine
        self.preparation = preparation or PreparationQueue(engine, start=False)
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, WorkerHandler)
        self.preparation.start()

    def process_request(self, request, client_address):
        if not self.slots.acquire(False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release(); raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
        self.preparation.close()
        super().server_close()

    def handle_error(self, request, client_address):
        pass  # Never include request bodies or engine exceptions in a traceback.


class WorkerHandler(BaseHTTPRequestHandler):
    server_version = 'KokoroLocal/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        pass  # Request paths, bodies and private dictation text are never logged.

    def handle(self):
        try:
            super().handle()
        except (TimeoutError, socket.timeout, BrokenPipeError, ConnectionResetError):
            return

    def _origin(self):
        host = self.headers.get('Host', '')
        expected = {'127.0.0.1', 'localhost', '127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
        if host not in expected or self.headers.get('Origin'):
            raise KokoroError('Only local gateway requests are accepted.', 403, 'origin')

    def _send(self, status, body, content_type='application/json', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        for key, value in (extra or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _json(self, status, body):
        self._send(status, json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode())

    def _error(self, error):
        self._json(error.status, {'error': str(error), 'code': error.code})

    def _body(self, maximum):
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            raise KokoroError('A bounded Content-Length is required.', 400, 'invalid')
        if self.headers.get_content_type() != 'application/json':
            raise KokoroError('JSON content is required.', 400, 'invalid')
        raw_length = self.headers.get('Content-Length', '')
        if not raw_length.isascii() or not raw_length.isdigit() or not 0 < int(raw_length) <= maximum:
            raise KokoroError('Request body exceeds its safe bound.', 400, 'invalid')
        content = self.rfile.read(int(raw_length))
        if len(content) != int(raw_length):
            raise KokoroError('Request body is incomplete.', 400, 'invalid')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON field')
                result[key] = value
            return result
        try:
            result = json.loads(content.decode('utf-8'), object_pairs_hook=unique,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON number')))
        except (UnicodeError, ValueError, RecursionError):
            raise KokoroError('Request JSON is invalid.', 400, 'invalid') from None
        if not isinstance(result, dict):
            raise KokoroError('Request must be a JSON object.', 400, 'invalid')
        return result

    def do_GET(self):
        try:
            self._origin()
            if self.path == '/health':
                self._json(200, self.server.engine.health())
            elif self.path == '/status':
                self._json(200, self.server.preparation.status())
            else:
                raise KokoroError('Endpoint does not exist.', 404, 'missing')
        except KokoroError as error:
            self._error(error)

    def do_POST(self):
        try:
            self._origin()
            if self.path not in ('/audio', '/prepare'):
                raise KokoroError('Endpoint does not exist.', 404, 'missing')
            body = self._body(MAX_AUDIO_BYTES if self.path == '/audio' else MAX_PREPARE_BYTES)
            field = 'text' if self.path == '/audio' else 'texts'
            if not {'language', field} <= set(body) or set(body) - {'language', field, 'voice'}:
                raise KokoroError('Request fields are invalid.', 400, 'invalid')
            if self.path == '/audio':
                content, metadata = self.server.engine.audio(body['language'], body['text'], body.get('voice'))
                self._send(200, content, 'audio/wav', {
                    'X-TTS-Engine': 'kokoro', 'X-TTS-Cache': 'HIT' if metadata['cache_hit'] else 'MISS',
                    'Server-Timing': 'kokoro;dur=' + format(metadata['duration_ms'], '.1f'),
                })
            else:
                result = self.server.preparation.prepare(body['language'], body['texts'], body.get('voice'))
                self._json(200, result)
        except KokoroError as error:
            self._error(error)
        except (TimeoutError, socket.timeout):
            self._error(KokoroError('Request body timed out.', 408, 'timeout'))
        except Exception:
            self._error(KokoroError())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8877)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535')
    engine = KokoroEngine(KokoroConfig.from_env())
    with WorkerServer(('127.0.0.1', args.port), engine) as server:
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
