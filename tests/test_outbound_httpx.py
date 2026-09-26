import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

import httpx
import pytest

from mnfst.bodies import is_form

from mnfst.config import resolve_config
from mnfst.heal_api import AsyncHealApi, HealApi
from mnfst.outbound import _Retry, _rebuild, install_outbound, uninstall_outbound
from tests.helpers import wait_for
from tests.stub_phoenix import StubPhoenix

BIG_ERROR_CHUNKS = 8  # 128 KiB, twice the capture cap


class Provider:
    """400s any JSON body containing 'temperature', 200s otherwise.

    Extra routes: /v1/stream chunk-streams a 200; /v1/flaky 400s the first
    request then hangs up on the retry (so the replay raises); /v1/gzip 400s
    gzip-encoded; /v1/big 400s with a chunked body larger than the capture cap."""

    def start(self):
        provider = self
        self.requests: list = []
        self.received: list = []  # (route, headers, parsed body) per JSON POST
        self.gets: list = []  # (route, headers, raw body) per GET

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length) if length else b""
                route, _, query = self.path.partition("?")
                provider.gets.append((route, dict(self.headers), raw))
                if route == "/v1/discover":  # a CDN-fronted read: page is clamped to 500
                    page = int(dict(parse_qsl(query)).get("page", 1))
                    if page > 500:
                        return self._reply(400, json.dumps(
                            {"error": {"message": "page must be at most 500"}}).encode())
                    return self._reply(200, json.dumps({"ok": True, "page": page}).encode())
                data = json.dumps({"error": {"message": "unknown path"}}).encode()
                self.send_response(404)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length) if length else b""
                route = self.path.split("?", 1)[0]
                provider.requests.append((route, raw))
                if route == "/v1/stream":
                    return self._stream()
                if route == "/v1/flaky":
                    return self._flaky(raw)
                if route == "/v1/gzip":
                    return self._gzip_error()
                if route == "/v1/big":
                    return self._big_error()
                if route.startswith("/v1/status/"):  # replies with the status asked for
                    return self._reply(int(route.rsplit("/", 1)[1]),
                                       json.dumps({"error": {"message": "as requested"}}).encode())
                if is_form(self.headers.get("content-type")):
                    # a form provider reads fields, not JSON -- the shape the
                    # SDK must replay in rather than JSON-encoding over it
                    body = dict(parse_qsl(raw.decode("utf-8", "replace"),
                                          keep_blank_values=True))
                else:
                    try:
                        body = json.loads(raw or b"{}")
                    except ValueError:
                        body = {}
                provider.received.append((route, dict(self.headers), body))
                error = None
                if route == "/v1/old":
                    return self._reply(404, json.dumps({"error": {"message": "moved"}}).encode())
                if route == "/v1/conflict":
                    return self._reply(409, json.dumps({"error": {"message": "conflict"}}).encode())
                if "x-bad" in self.headers:
                    error = "unsupported header x-bad"
                elif isinstance(body, dict) and body.get("needs_beta") and \
                        self.headers.get("x-beta") != "on":
                    error = "beta header required"
                elif isinstance(body, dict) and "temperature" in body:
                    error = "temperature unsupported"
                elif isinstance(body, list) and "bad" in body:
                    error = "bad item"
                if error:
                    self._reply(400, json.dumps({"error": {"message": error}}).encode())
                else:
                    self._reply(200, json.dumps({"ok": True, "received": body}).encode())

            def _reply(self, status, data):
                self.send_response(status)
                if status >= 400:
                    self.send_header("set-cookie", "error_session=retained; Path=/")
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _stream(self):
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                for index in range(3):
                    chunk = f"chunk{index}".encode()
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def _gzip_error(self):
                data = gzip.compress(json.dumps(
                    {"error": {"message": "temperature unsupported"}}).encode())
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.send_header("content-encoding", "gzip")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _big_error(self):
                # a chunked 400 larger than the capture cap
                self.send_response(400)
                self.send_header("content-type", "text/plain")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                for _ in range(BIG_ERROR_CHUNKS):
                    chunk = b"x" * 16384
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def _flaky(self, raw):
                if len([p for p, _ in provider.requests if p == "/v1/flaky"]) == 1:
                    data = json.dumps({"error": {"message": "temperature unsupported"}}).encode()
                    return self._reply(400, data)
                # the replay: hang up mid-response so the retry raises
                self.close_connection = True
                self.wfile.close()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self

    def stop(self):
        if self._server is None:
            return  # a test may have stopped it mid-flight; teardown repeats
        self._server.shutdown()
        self._server.server_close()
        self._server = None


def build_rig(**options):
    provider = Provider().start()
    stub = StubPhoenix().start()
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m"}}}  # temperature omitted -> dropped
    config = resolve_config(api_key="mnfx_k", url=stub.url, **options)
    install_outbound(config, HealApi(config), AsyncHealApi(config))
    return provider, stub


@pytest.fixture
def rig():
    provider, stub = build_rig()
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


@pytest.fixture
def strip_rig():
    provider, stub = build_rig(send_bodies=False)
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


def header(received_headers, name):
    """Header lookup by name: httpx sends them lowercased, requests does not."""
    return next((value for key, value in received_headers.items()
                 if key.lower() == name), None)


def test_sync_client_heals(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(stub.heals) == 1
    heal = stub.heals[0]
    assert heal["request"]["url"] == f"{provider.url}/v1/generate"
    assert heal["response"]["statusCode"] == 400


def test_sync_client_untouched_on_success(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/generate", json={"model": "m"})
    assert response.status_code == 200
    assert stub.heals == []


def test_no_patch_returns_original_400(rig):
    provider, stub = rig
    stub.result = None  # no_patch
    response = httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 1})
    assert response.status_code == 400


@pytest.mark.anyio
async def test_async_client_heals(rig):
    provider, stub = rig
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{provider.url}/v1/generate",
                                     json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200


def test_phoenix_calls_not_intercepted(rig):
    provider, stub = rig
    # a heal triggers SDK-internal calls to stub phoenix; if those were
    # intercepted we'd recurse. One heal recorded means the guard held.
    httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert len(stub.heals) == 1


# --- C1: a streamed 200 must reach the caller with its stream unconsumed ---
@pytest.mark.anyio
async def test_async_streamed_200_is_not_preread(rig):
    provider, stub = rig
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", f"{provider.url}/v1/stream",
                                 json={"model": "m"}) as response:
            assert response.status_code == 200
            # the SDK must not have buffered the body out from under the caller
            assert response.is_stream_consumed is False
            chunks = [chunk async for chunk in response.aiter_bytes()]
    assert b"".join(chunks) == b"chunk0chunk1chunk2"
    assert stub.heals == []


# --- C2: a streamed/iterator request body must not crash the call ---
def test_sync_iterator_request_body_passes_through(rig):  # noqa: E302
    provider, stub = rig

    def gen():
        yield b'{"model": "m"}'

    response = httpx.post(f"{provider.url}/v1/generate", content=gen(),
                          headers={"content-type": "application/json"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.anyio
async def test_async_iterator_request_body_passes_through(rig):
    provider, stub = rig

    async def gen():
        yield b'{"model": "m", "temperature": 0.2}'

    async with httpx.AsyncClient() as client:
        response = await client.post(f"{provider.url}/v1/generate", content=gen(),
                                     headers={"content-type": "application/json"})
    # the unreadable body gates the request out instead of raising
    assert response.status_code == 200
    assert stub.heals == []


# --- C4: strip mode observes but never replays ---


def test_crash_in_heal_branch_serves_the_original_response(rig, monkeypatch):
    provider, stub = rig

    def boom(*args, **kwargs):
        raise RuntimeError("merge exploded")

    monkeypatch.setattr("mnfst.outbound._apply", boom)
    response = httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "temperature unsupported"


# --- I3: the retry keeps the original request's extensions (timeout) ---
def test_rebuild_preserves_extensions():
    request = httpx.Request("POST", "http://h/p", json={"a": 1},
                            extensions={"timeout": {"connect": 1.5, "read": 2.5,
                                                    "write": 2.5, "pool": 2.5}})
    rebuilt = _rebuild(request, _Retry("http://h/p", dict(request.headers), b'{"a": 2}'))
    assert rebuilt.extensions["timeout"] == {"connect": 1.5, "read": 2.5,
                                             "write": 2.5, "pool": 2.5}
    assert rebuilt.extensions is not request.extensions
    assert rebuilt.content == b'{"a": 2}'


# --- I4: a replay that raises is still reported ---
def test_replay_exception_reports_outcome(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/flaky", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 400  # original served
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    attempt_id, body = stub.outcomes[0]
    assert attempt_id == "a1"
    assert body["failure"]["kind"] == "transport_error"
    assert body["failure"]["message"]


# --- N2: the reported error text must not carry credentials ----------------
def test_replay_error_text_carries_no_url_secret(rig):
    """httpx's own connection errors do not embed the URL, but nothing may
    assume that — the reported text goes through the masker regardless.
    The masking itself is pinned in test_wire.py."""
    provider, stub = rig
    httpx.post(f"{provider.url}/v1/flaky?api_key=sk_live_SECRET",
               json={"model": "m", "temperature": 0.2})
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert "sk_live_SECRET" not in stub.outcomes[0][1]["failure"]["message"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_failed_get_is_captured_but_never_retried(rig):
    """Capture is wide: a failed GET reaches Phoenix with body None. Replay is
    narrow: with nothing to edit, the original response is always served."""
    provider, stub = rig
    response = httpx.get(f"{provider.url}/v1/generate")  # provider: 404 on GET
    assert response.status_code == 404
    assert len(stub.heals) == 1
    heal = stub.heals[0]
    assert heal["request"]["method"] == "GET"
    assert heal["request"]["body"] is None
    assert heal["response"]["statusCode"] == 404


# --- CONTRACT rev 2: headers travel masked; healedRequest url/headers/body ---

def test_headers_travel_with_credentials_masked(rig):
    provider, stub = rig
    httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2},
               headers={"Authorization": "Bearer sk_live_1", "X-Api-Key": "k",
                        "Anthropic-Beta": "thinking-2025", "Idempotency-Key": "idem-1"})
    sent = stub.heals[0]["request"]["headers"]
    assert sent["authorization"] == "REDACTED"
    assert sent["x-api-key"] == "REDACTED"
    assert sent["anthropic-beta"] == "thinking-2025"   # diagnostic, travels
    assert sent["idempotency-key"] == "REDACTED"        # over-masked by root, presence kept


def test_healed_headers_are_set_and_removed_on_retry(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"headers": {"X-Beta": "on", "x-bad": None}}}
    response = httpx.post(f"{provider.url}/v1/generate", json={"needs_beta": True},
                          headers={"x-bad": "1"})
    assert response.status_code == 200
    _, headers, _ = provider.received[-1]
    assert headers.get("X-Beta") == "on"
    assert "x-bad" not in headers
    assert len(provider.received) == 2  # original + one retry


def test_healed_url_moves_the_call(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": f"{provider.url}/v1/generate"}}
    response = httpx.post(f"{provider.url}/v1/old", json={"model": "m"})
    assert response.status_code == 200
    assert [r[0] for r in provider.received] == ["/v1/old", "/v1/generate"]


def test_array_body_is_captured_and_replaced(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": ["good"]}}
    response = httpx.post(f"{provider.url}/v1/generate", json=["good", "bad"])
    assert response.status_code == 200
    assert stub.heals[0]["request"]["body"] == ["good", "bad"]
    assert provider.received[-1][2] == ["good"]


def test_body_credentials_are_withheld_and_restored(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m"}}}  # temperature dropped
    response = httpx.post(f"{provider.url}/v1/generate",
                          json={"model": "m", "client_secret": "s3", "temperature": 0.2})
    assert response.status_code == 200
    assert "client_secret" not in stub.heals[0]["request"]["body"]   # never left the process
    assert provider.received[-1][2] == {"model": "m", "client_secret": "s3"}  # back on retry


def test_unreadable_body_without_server_body_is_not_retried(rig):
    """A streamed upload is captured with body null. If the server's fix does
    not include a body, the retry cannot be rebuilt — serve the original."""
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"headers": {"x-bad": None}}}

    def chunks():
        yield b'{"model": "m"}'

    response = httpx.post(f"{provider.url}/v1/generate", content=chunks(),
                          headers={"content-type": "application/json", "x-bad": "1"})
    assert response.status_code == 400  # captured, retry impossible
    assert stub.heals[0]["request"]["body"] is None
    assert len(provider.received) == 1


def test_cross_origin_url_heal_is_refused(rig):
    """The retry carries the caller's credentials — a heal may move the path,
    never the host."""
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": "http://evil.invalid/v1/generate"}}
    response = httpx.post(f"{provider.url}/v1/old", json={"model": "m"},
                          headers={"Authorization": "Bearer sk"})
    assert response.status_code == 404  # original served, nothing sent elsewhere
    assert len(provider.received) == 1


# --- form-urlencoded: parsed on capture, replayed in the caller's encoding ---

def test_form_body_is_healed_and_replayed_as_a_form(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m"}}}
    response = httpx.post(f"{provider.url}/v1/generate",
                          data={"model": "m", "temperature": "0.2"})
    assert response.status_code == 200
    # the fields reached the server, not a null body
    assert stub.heals[0]["request"]["body"] == {"model": "m", "temperature": "0.2"}
    # and the replay went out as a form under its own content type
    route, raw = provider.requests[-1]
    assert raw == b"model=m"
    assert is_form(header(provider.received[-1][1], "content-type"))
    assert provider.received[-1][2] == {"model": "m"}


def test_nested_form_keys_survive_the_replay(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m",
                                              "line_items": [{"price": "price_123"}]}}}
    response = httpx.post(
        f"{provider.url}/v1/generate",
        content=b"model=m&temperature=0.2&line_items%5B0%5D%5Bprice%5D=price_000",
        headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 200
    assert stub.heals[0]["request"]["body"] == {
        "model": "m", "temperature": "0.2", "line_items": [{"price": "price_000"}]}
    assert provider.requests[-1][1] == b"model=m&line_items%5B0%5D%5Bprice%5D=price_123"


def test_unparseable_form_body_is_reported_but_never_replayed(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/generate", content=b"temperature=%GG",
                          headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400  # the original error, untouched
    assert stub.heals[0]["request"]["body"] is None
    assert len([r for r in provider.requests if r[0] == "/v1/generate"]) == 1
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "not_attempted"


def test_form_request_is_not_replayed_with_a_body_it_cannot_encode(rig):
    """The server answering a form request with a scalar body has nothing a
    form can carry — close the attempt rather than send JSON under a form
    content type."""
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": "model=m"}}
    response = httpx.post(f"{provider.url}/v1/generate",
                          data={"model": "m", "temperature": "0.2"})
    assert response.status_code == 400
    assert len([r for r in provider.requests if r[0] == "/v1/generate"]) == 1
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "not_attempted"


def test_json_requests_still_replay_as_json(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/generate",
                          json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    assert provider.requests[-1][1] == b'{"model": "m"}'
    assert header(provider.received[-1][1], "content-type") == "application/json"


# --- capture eligibility: any request-side 4xx, not just 400/404/422 ---

def test_a_409_is_captured_and_replayed(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/conflict", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 409  # the route always conflicts
    assert len(stub.heals) == 1
    assert stub.heals[0]["response"]["statusCode"] == 409
    # the heal was applied and replayed, so the route was hit twice
    assert len([r for r in provider.requests if r[0] == "/v1/conflict"]) == 2


def test_forbidden_statuses_never_reach_manifest(rig):
    provider, stub = rig
    for status in (401, 402, 403, 429, 500):
        response = httpx.post(f"{provider.url}/v1/status/{status}", json={"model": "m"})
        assert response.status_code == status
    assert stub.heals == []


def test_get_retry_with_a_query_only_patch_carries_no_body(rig):
    """A query-only patch heals the URL and sends `body: null`. The retry is a
    GET: it must go out bodyless, not with a four-byte "null" that CDNs reject."""
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": f"{provider.url}/v1/discover?page=500", "body": None}}
    response = httpx.get(f"{provider.url}/v1/discover?page=502")
    assert response.status_code == 200
    assert len(provider.gets) == 2
    _, headers, body = provider.gets[-1]
    assert body == b""
    assert header(headers, "content-length") is None
