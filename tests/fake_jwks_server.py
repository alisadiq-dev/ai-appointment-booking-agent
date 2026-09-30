"""A local HTTP server standing in for Supabase's JWKS endpoint."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeJwksServer:
    """A local HTTP server standing in for Supabase's /.well-known/jwks.json."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.mode = "ok"  # ok | error | garbage | slow
        self.hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.hits += 1
                if outer.mode == "slow":
                    time.sleep(1.0)
                if outer.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                    return
                body = (
                    b"<html>not json</html>"
                    if outer.mode == "garbage"
                    else (json.dumps(outer.document).encode())
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/auth/v1/.well-known/jwks.json"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
