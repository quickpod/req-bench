"""Shared fixtures: a tiny local echo server and an isolated store directory.

Tests never touch the real network.  ``echo_server`` spins up an ``http.server``
on 127.0.0.1 in a background thread that echoes back the method, path, query,
headers and body as JSON, so round-trips are deterministic and fast.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _EchoHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the test output clean
        pass

    def _echo(self):
        parsed = urlparse(self.path)
        # /status/NNN -> reply with that status code
        if parsed.path.startswith("/status/"):
            try:
                code = int(parsed.path.rsplit("/", 1)[1])
            except ValueError:
                code = 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": code}).encode())
            return
        # /redirect -> 302 to /get
        if parsed.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/get")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        body = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
        payload = {
            "method": self.command,
            "path": parsed.path,
            "args": {k: v[0] for k, v in parse_qs(parsed.query).items()},
            "headers": {k: v for k, v in self.headers.items()},
            "body": body,
            "raw": raw,
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _echo


@pytest.fixture()
def echo_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


@pytest.fixture()
def store(tmp_path):
    """An isolated collections/history store directory."""
    return str(tmp_path / "store")


@pytest.fixture()
def dead_url():
    """A URL with nothing listening -- for connection-error tests."""
    return "http://127.0.0.1:9"  # discard port; refuses connections
