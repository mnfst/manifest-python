import json
import time

import httpx
import pytest

from mnfst.config import resolve_config
from mnfst.heal_api import DISABLED_BACKOFF_SECONDS, AsyncHealApi, HealApi
from tests.helpers import wait_for

CFG = resolve_config(api_key="mnfx_test_k", url="http://phoenix.test")
PAYLOAD = {"traceId": "t1", "request": {}, "response": {"statusCode": 422}}
RESULT = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
          "healedRequest": {"body": {"reps": 10}}, "operations": []}


def capture(responder):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responder(request)

    return seen, httpx.MockTransport(handler)


def test_heal_success_sends_auth_and_returns_result():
    seen, transport = capture(lambda r: httpx.Response(200, json=RESULT))
    api = HealApi(CFG, transport=transport)
    assert api.heal(PAYLOAD) == RESULT
    req = seen[0]
    assert req.url == "http://phoenix.test/v1/heal"
    assert req.headers["authorization"] == "Bearer mnfx_test_k"
    assert req.headers["user-agent"].startswith("mnfst-python/")
    assert json.loads(req.content) == PAYLOAD


def test_heal_returns_none_on_error_status_and_exception():
    _, transport = capture(lambda r: httpx.Response(500))
    assert HealApi(CFG, transport=transport).heal(PAYLOAD) is None

    def boom(request):
        raise httpx.ConnectError("down")

    assert HealApi(CFG, transport=httpx.MockTransport(boom)).heal(PAYLOAD) is None


def test_heal_returns_none_on_unserializable_payload():
    # Serialization happens inside the fail-open try: a payload too deep for the
    # JSON encoder must degrade to None, never raise into the caller's request.
    seen, transport = capture(lambda r: httpx.Response(200, json=RESULT))
    api = HealApi(CFG, transport=transport)

    deep: dict = {}
    cursor = deep
    for _ in range(10000):
        cursor["next"] = {}
        cursor = cursor["next"]

    assert api.heal(deep) is None
    assert seen == []  # never reached the wire

    cyclic: dict = {}
    cyclic["self"] = cyclic
    assert api.heal(cyclic) is None


def test_app_disabled_backs_off():
    seen, transport = capture(
        lambda r: httpx.Response(403, json={"error": "project_disabled"}))
    api = HealApi(CFG, transport=transport)
    assert api.heal(PAYLOAD) is None
    assert api.healing_enabled() is False
    assert api.heal(PAYLOAD) is None
    assert len(seen) == 1  # second call never hit the wire
    api._disabled_until = time.monotonic() - 1  # backoff expiry re-enables
    assert api.healing_enabled() is True
    assert DISABLED_BACKOFF_SECONDS == 300


def test_hello_announces_the_install():
    seen, transport = capture(lambda r: httpx.Response(200, json={"status": "ok"}))
    api = HealApi(CFG, transport=transport)
    # Returns immediately: a handshake must never delay startup.
    assert api.hello("python-3.12.0") is None
    assert wait_for(lambda: seen)
    req = seen[0]
    assert req.method == "POST"
    assert req.url == "http://phoenix.test/v1/hello"
    assert req.headers["authorization"] == "Bearer mnfx_test_k"
    assert req.headers["user-agent"].startswith("mnfst-python/")
    # Carries the runtime and nothing else — identity rides in the user-agent.
    assert json.loads(req.content) == {"runtime": "python-3.12.0"}


def test_a_failing_hello_is_swallowed():
    # An app that cannot reach Manifest must still boot cleanly: no raise, and
    # no warning printed on every boot.
    def boom(request):
        raise httpx.ConnectError("down")

    api = HealApi(CFG, transport=httpx.MockTransport(boom))
    assert api.hello("python-3.12.0") is None


def test_report_outcome_fire_and_forget():
    seen, transport = capture(lambda r: httpx.Response(200, json={}))
    api = HealApi(CFG, transport=transport)
    api.report_outcome("a1", 200)
    assert wait_for(lambda: seen)
    req = seen[0]
    assert req.method == "PATCH"
    assert req.url == "http://phoenix.test/v1/heal-attempts/a1"
    assert json.loads(req.content) == {"response": {"statusCode": 200}}


def test_report_outcome_carries_the_replay_error():
    seen, transport = capture(lambda r: httpx.Response(200, json={}))
    api = HealApi(CFG, transport=transport)
    api.report_outcome("a1", 0, "ConnectError: down")
    assert wait_for(lambda: seen)
    assert json.loads(seen[0].content) == {"failure": {"kind": "transport_error", "message": "ConnectError: down"}}


def test_report_outcome_prunes_finished_threads():
    # fire-and-forget reports must not pile up for the life of the process
    _, transport = capture(lambda r: httpx.Response(200, json={}))
    api = HealApi(CFG, transport=transport)
    for index in range(25):
        api.report_outcome(f"a{index}", 200)
    for thread in list(api._pending):
        thread.join(timeout=2)
    api.report_outcome("a-last", 200)
    assert len(api._pending) == 1  # the 25 finished ones were swept


@pytest.mark.anyio
async def test_async_heal():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=RESULT)

    api = AsyncHealApi(CFG, transport=httpx.MockTransport(handler))
    assert await api.heal(PAYLOAD) == RESULT
    assert seen[0].url == "http://phoenix.test/v1/heal"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_outcome_reports_are_bounded_under_a_flood(monkeypatch):
    import threading
    from mnfst import heal_api as heal_api_module

    gate = threading.Event()

    def slow(request):
        gate.wait(5)
        return httpx.Response(200, json={})

    monkeypatch.setattr(heal_api_module, "MAX_INFLIGHT_REPORTS", 2)
    api = HealApi(CFG, transport=httpx.MockTransport(slow))
    for i in range(10):
        api.report_outcome(f"a{i}", 200)
    assert len(api._pending) <= 2  # the rest were dropped, not queued forever
    gate.set()
    for thread in list(api._pending):
        thread.join(timeout=2)


def test_send_requests_raises_only_when_a_retry_could_help():
    from mnfst.config import resolve_config
    from mnfst.heal_api import HealApi
    from tests.stub_phoenix import StubPhoenix
    call = {"traceId": "t", "method": "GET", "url": "https://a.com/x", "statusCode": 200,
            "responseTimeMs": 1, "occurredAt": "1970-01-01T00:00:00+00:00"}
    with StubPhoenix() as stub:
        api = HealApi(resolve_config(api_key="mnfx_k", url=stub.url))
        for status in (202, 400, 401, 404):
            stub.requests_status = status
            api.send_requests([call])
        for status in (429, 500, 503):
            stub.requests_status = status
            with pytest.raises(RuntimeError):
                api.send_requests([call])
        stub.requests_status = 403  # answered with {"error": "project_disabled"}
        api.send_requests([call])
        assert api.healing_enabled() is False
