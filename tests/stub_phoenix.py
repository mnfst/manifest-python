"""Stdlib-only stub of the Phoenix /v1 contract (spec §4). Doubles as the
local dev server: `python -m tests.stub_phoenix`."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional


class StubPhoenix:
    def __init__(self, result: Optional[dict] = None):
        self.result = result
        self.heals: list[dict] = []
        self.hellos: list[dict] = []
        self.outcomes: list[tuple[str, dict]] = []
        self.disabled = False
        # Fired when a heal request arrives — the one moment a test can act
        # between the original call and the replay.
        self.on_heal_request = None
        self._server: Optional[ThreadingHTTPServer] = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "StubPhoenix":
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _read_json(self) -> dict:
                length = int(self.headers.get("content-length", 0))
                return json.loads(self.rfile.read(length) or b"{}")

            def _reply(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path == "/v1/hello":
                    stub.hellos.append(self._read_json())
                    return self._reply(200, {"status": "ok"})
                if self.path != "/v1/heal":
                    return self._reply(404, {"error": "not_found"})
                if stub.disabled:
                    return self._reply(403, {"error": "project_disabled"})
                stub.heals.append(self._read_json())
                if stub.on_heal_request:
                    stub.on_heal_request()
                result = stub.result or {"status": "no_patch", "issueId": "stub-issue"}
                self._reply(200, result)

            def do_PATCH(self):
                prefix = "/v1/heal-attempts/"
                if not self.path.startswith(prefix):
                    return self._reply(404, {"error": "not_found"})
                body = self._read_json()
                if set(body) not in ({"response"}, {"failure"}):
                    return self._reply(400, {"error": "invalid_outcome"})
                if "response" in body and not 200 <= body["response"].get("statusCode", 0) <= 599:
                    return self._reply(400, {"error": "invalid_status"})
                stub.outcomes.append((self.path[len(prefix):], body))
                self._reply(200, {"healAttemptId": self.path[len(prefix):],
                                  "status": "succeeded"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "StubPhoenix":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


if __name__ == "__main__":
    stub = StubPhoenix().start()
    print(f"stub phoenix at {stub.url} (no_patch for everything)")
    threading.Event().wait()
