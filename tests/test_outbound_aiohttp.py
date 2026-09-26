"""aiohttp through the real patch: the SDK rides aiohttp's client middleware
chain, innermost, on every session — including ones made before install."""
import json
import time

import aiohttp
import pytest

from mnfst import outbound
from mnfst.bodies import is_form
from mnfst.outbound import uninstall_outbound
from mnfst.wire import RESPONSE_BODY_CAP
from tests.helpers import wait_for
from tests.test_outbound_httpx import BIG_ERROR_CHUNKS, build_rig, header

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def rig():
    provider, stub = build_rig()
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


@pytest.fixture
def denied():
    provider, stub = build_rig(denylist="127.0.0.1")
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


async def post(url, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.post(url, **kwargs) as response:
            return response.status, await response.read()


async def test_json_post_heals(rig):
    provider, stub = rig
    status, body = await post(f"{provider.url}/v1/generate",
                              json={"model": "m", "temperature": 0.2})
    assert status == 200
    assert json.loads(body)["received"] == {"model": "m"}
    assert len(stub.heals) == 1
    heal = stub.heals[0]
    assert heal["request"]["method"] == "POST"
    assert heal["request"]["body"] == {"model": "m", "temperature": 0.2}
    assert heal["response"]["statusCode"] == 400
    assert "temperature unsupported" in json.dumps(heal["response"]["body"])


async def test_success_is_untouched(rig):
    provider, stub = rig
    status, _ = await post(f"{provider.url}/v1/generate", json={"model": "m"})
    assert status == 200
    assert stub.heals == []


async def test_streamed_200_reaches_the_caller_unread(rig):
    provider, stub = rig
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{provider.url}/v1/stream", json={"model": "m"}) as response:
            chunks = [chunk async for chunk in response.content.iter_any()]
    assert b"".join(chunks) == b"chunk0chunk1chunk2"
    assert stub.heals == []


async def test_unhealed_failure_body_is_still_readable_every_way(rig):
    provider, stub = rig
    stub.result = None  # no_patch
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{provider.url}/v1/generate",
                                json={"temperature": 1}) as response:
            assert response.status == 400
            assert (await response.json())["error"]["message"] == "temperature unsupported"
        async with session.post(f"{provider.url}/v1/generate",
                                json={"temperature": 1}) as response:
            streamed = b"".join([chunk async for chunk in response.content.iter_any()])
            assert json.loads(streamed)["error"]["message"] == "temperature unsupported"
    assert len(stub.heals) == 2


async def test_a_session_made_before_install_is_covered():
    uninstall_outbound()  # the rig installs; build the session first
    async with aiohttp.ClientSession() as session:
        provider, stub = build_rig()
        try:
            async with session.post(f"{provider.url}/v1/generate",
                                    json={"model": "m", "temperature": 0.2}) as response:
                assert response.status == 200
            assert len(stub.heals) == 1
        finally:
            uninstall_outbound()
            stub.stop()
            provider.stop()


async def test_caller_middlewares_run_outside_the_heal(rig):
    provider, stub = rig
    seen = []

    async def watcher(request, handler):
        response = await handler(request)
        seen.append(response.status)
        return response

    async with aiohttp.ClientSession(middlewares=(watcher,)) as session:
        async with session.post(f"{provider.url}/v1/generate",
                                json={"model": "m", "temperature": 0.2}) as response:
            assert response.status == 200
        # a per-request override replaces the session's middlewares, not ours
        async with session.post(f"{provider.url}/v1/generate", middlewares=(),
                                json={"model": "m", "temperature": 0.2}) as response:
            assert response.status == 200
    assert seen == [200]  # the caller's middleware only ever sees the healed call
    assert len(stub.heals) == 2


async def test_raise_for_status_sees_the_healed_status(rig):
    provider, stub = rig
    async with aiohttp.ClientSession(raise_for_status=True) as session:
        async with session.post(f"{provider.url}/v1/generate",
                                json={"model": "m", "temperature": 0.2}) as response:
            assert response.status == 200


async def test_healed_headers_and_url_apply(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": f"{provider.url}/v1/generate",
                                     "headers": {"X-Beta": "on", "x-bad": None}}}
    status, _ = await post(f"{provider.url}/v1/old", json={"needs_beta": True},
                           headers={"x-bad": "1", "Authorization": "Bearer sk"})
    assert status == 200
    route, headers, body = provider.received[-1]
    assert route == "/v1/generate"
    assert header(headers, "x-beta") == "on"
    assert header(headers, "x-bad") is None
    assert header(headers, "authorization") == "Bearer sk"
    assert body == {"needs_beta": True}
    assert stub.heals[0]["request"]["headers"]["authorization"] == "REDACTED"


async def test_cross_origin_url_heal_is_refused(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": "http://evil.example/v1/generate"}}
    status, _ = await post(f"{provider.url}/v1/old", json={"model": "m"})
    assert status == 404
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "not_attempted"


async def test_form_body_is_healed_and_replayed_as_a_form(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m"}}}
    status, _ = await post(f"{provider.url}/v1/generate",
                           data={"model": "m", "temperature": "0.2"})
    assert status == 200
    assert stub.heals[0]["request"]["body"] == {"model": "m", "temperature": "0.2"}
    route, raw = provider.requests[-1]
    assert raw == b"model=m"
    headers = provider.received[-1][1]
    assert is_form(header(headers, "content-type"))
    assert header(headers, "content-length") == str(len(raw))


async def test_streamed_request_body_is_captured_without_being_consumed(rig):
    provider, stub = rig
    stub.result = None

    async def gen():
        yield b'{"model": "m"}'

    async with aiohttp.ClientSession() as session:
        # the provider does not read chunked uploads: close, so the unread
        # body is not taken for a second request
        async with session.post(f"{provider.url}/v1/old", data=gen(),
                                headers={"content-type": "application/json",
                                         "connection": "close"}) as response:
            assert response.status == 404
    assert stub.heals[0]["request"]["body"] is None


async def test_failed_get_is_captured_with_no_body(rig):
    provider, stub = rig
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{provider.url}/v1/generate") as response:
            assert response.status == 404
    assert len(stub.heals) == 1
    assert stub.heals[0]["request"]["method"] == "GET"
    assert stub.heals[0]["request"]["body"] is None


async def test_replay_exception_reports_outcome_and_serves_the_original(rig):
    provider, stub = rig
    status, body = await post(f"{provider.url}/v1/flaky",
                              json={"model": "m", "temperature": 0.2})
    assert status == 400
    assert json.loads(body)["error"]["message"] == "temperature unsupported"
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "transport_error"


async def test_crash_in_heal_branch_serves_the_original_response(rig, monkeypatch):
    provider, stub = rig

    def boom(*args, **kwargs):
        raise RuntimeError("merge exploded")

    monkeypatch.setattr("mnfst.outbound._apply", boom)
    status, body = await post(f"{provider.url}/v1/generate",
                              json={"model": "m", "temperature": 0.2})
    assert status == 400
    assert json.loads(body)["error"]["message"] == "temperature unsupported"


async def test_a_decompressed_error_body_is_captured_as_text(rig):
    provider, stub = rig
    stub.result = None
    status, body = await post(f"{provider.url}/v1/gzip", json={"model": "m"})
    assert status == 400
    assert json.loads(body)["error"]["message"] == "temperature unsupported"
    assert "temperature unsupported" in json.dumps(stub.heals[0]["response"]["body"])


async def test_an_error_body_over_the_cap_reaches_the_caller_whole(rig):
    provider, stub = rig
    status, body = await post(f"{provider.url}/v1/big", json={"model": "m"})
    assert status == 400
    assert body == b"x" * (16384 * BIG_ERROR_CHUNKS)
    assert len(body) > RESPONSE_BODY_CAP
    heal = stub.heals[0]
    assert heal["response"]["truncated"] is True
    assert len(provider.requests) == 1  # a truncated capture is never replayed


async def test_a_websocket_handshake_is_never_healed(rig):
    provider, stub = rig
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.WSServerHandshakeError):
            await session.ws_connect(f"{provider.url}/v1/generate")
    assert stub.heals == []


async def test_healing_stays_inside_the_callers_total_timeout(rig):
    """aiohttp's total timeout spans the middleware chain: a slow heal must
    give up in time to hand back the original failure, not a TimeoutError."""
    provider, stub = rig
    stub.on_heal_request = lambda: time.sleep(3)
    started = time.monotonic()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1)) as session:
        async with session.post(f"{provider.url}/v1/generate",
                                json={"model": "m", "temperature": 0.2}) as response:
            assert response.status == 400
            assert (await response.json())["error"]["message"] == "temperature unsupported"
    assert time.monotonic() - started < 1


async def test_a_per_request_timeout_bounds_healing_too(rig):
    provider, stub = rig
    stub.on_heal_request = lambda: time.sleep(3)
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{provider.url}/v1/generate", timeout=aiohttp.ClientTimeout(total=1),
                                json={"model": "m", "temperature": 0.2}) as response:
            assert response.status == 400


async def test_calls_are_tracked(rig):
    provider, stub = rig
    await post(f"{provider.url}/v1/generate", json={"model": "m"})
    outbound._tracker.flush(5)
    assert [call["statusCode"] for call in stub.tracked] == [200]
    assert stub.tracked[0]["method"] == "POST"


async def test_a_denied_host_is_neither_healed_nor_tracked(denied):
    provider, stub = denied
    status, _ = await post(f"{provider.url}/v1/generate",
                           json={"model": "m", "temperature": 0.2})
    assert status == 400
    outbound._tracker.flush(5)
    assert stub.heals == [] and stub.tracked == []


async def test_uninstall_restores_the_session():
    original = aiohttp.ClientSession._request
    provider, stub = build_rig()
    try:
        assert aiohttp.ClientSession._request is not original
    finally:
        uninstall_outbound()
        stub.stop()
        provider.stop()
    assert aiohttp.ClientSession._request is original
