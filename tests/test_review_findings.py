"""Regressions from the SDK review: filtering, retry framing, capture deadline, fail-open install."""
import json
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from mnfst import outbound, response_capture
from mnfst.config import resolve_config
from mnfst.outbound import _Capture, _apply, _rebuild, install_outbound, uninstall_outbound
from tests.helpers import wait_for
from tests.test_outbound_httpx import build_rig


@pytest.fixture
def denied_target():
    provider, stub = build_rig(denylist="127.0.0.1/v1/generate")
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


def test_a_patch_never_moves_the_retry_onto_a_denied_route(denied_target):
    provider, stub = denied_target
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": f"{provider.url}/v1/generate"}}
    response = httpx.post(f"{provider.url}/v1/old", json={"model": "m"})
    assert response.status_code == 404
    assert len(stub.heals) == 1
    assert [r[0] for r in provider.received] == ["/v1/old"]
    assert wait_for(lambda: len(stub.outcomes) == 1)


def test_a_retry_with_a_new_body_drops_chunked_framing():
    capture = _Capture("POST", "https://api.example.com/v1/x",
                       {"content-type": "application/json", "transfer-encoding": "chunked"},
                       b'{"model": "m", "temperature": 2}', 400, b"{}", 5)
    retry = _apply(capture, {"body": {"model": "m"}})
    assert retry is not None
    assert "transfer-encoding" not in {name.lower() for name in retry.headers}
    rebuilt = _rebuild(httpx.Request("POST", capture.url), retry)
    assert "transfer-encoding" not in rebuilt.headers
    assert rebuilt.headers["content-length"] == str(len(retry.content))


class _Trickle(BaseHTTPRequestHandler):
    """A 400 whose body arrives one byte at a time, far slower than any capture should wait."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        try:
            for _ in range(40):
                self.wfile.write(b"1\r\n \r\n")
                self.wfile.flush()
                time.sleep(0.1)
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass


def test_capturing_a_trickling_failure_stops_at_its_deadline(monkeypatch):
    monkeypatch.setattr(response_capture, "CAPTURE_DEADLINE_SECONDS", 0.3)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    provider, stub = build_rig()
    stub.result = {"status": "no_patch"}
    try:
        started = time.monotonic()
        with httpx.Client() as client:
            with client.stream("POST", f"http://127.0.0.1:{server.server_address[1]}/x",
                               json={"temperature": 2}) as response:
                handed_back = time.monotonic() - started
                assert response.status_code == 400
        assert handed_back < 2.0
        assert len(stub.heals) == 1
        assert stub.heals[0]["response"]["truncated"] is True
    finally:
        uninstall_outbound()
        stub.stop()
        provider.stop()
        server.shutdown()
        server.server_close()


def test_an_install_failure_leaves_the_app_running_uninstrumented(monkeypatch):
    original = httpx.HTTPTransport.handle_request
    monkeypatch.setenv("HTTPS_PROXY", "bad://proxy")
    config = resolve_config(api_key="mnfx_k", url="https://manifest.example")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        install_outbound(config)
    assert any("install failed" in str(w.message) for w in caught)
    assert outbound.installed_config() is None
    assert httpx.HTTPTransport.handle_request is original
