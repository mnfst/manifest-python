"""Tracked calls through the real patches: every call that is not healed, any
status, is recorded as metadata and sent in batches to /v1/requests."""
import asyncio
import os
import subprocess
import sys
import time

import httpx
import pytest
import requests

from mnfst import outbound
from mnfst.outbound import uninstall_outbound
from tests.test_outbound_httpx import build_rig

KEYS = {"method", "occurredAt", "responseTimeMs", "statusCode", "traceId", "url"}


@pytest.fixture
def rig():
    provider, stub = build_rig()
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


def flush():
    outbound._tracker.flush(5)


def test_a_successful_call_is_tracked_without_its_query(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/generate?api_key=secret#frag", json={"model": "m"})
    assert response.json()["ok"] is True
    flush()
    assert stub.heals == []
    assert len(stub.tracked) == 1
    call = stub.tracked[0]
    assert set(call) == KEYS
    assert call["url"] == f"{provider.url}/v1/generate"
    assert (call["method"], call["statusCode"]) == ("POST", 200)


@pytest.mark.parametrize("status", [401, 503])
def test_non_healable_statuses_are_tracked_and_never_healed(rig, status):
    provider, stub = rig
    assert httpx.post(f"{provider.url}/v1/status/{status}", json={}).status_code == status
    flush()
    assert stub.heals == []
    assert [c["statusCode"] for c in stub.tracked] == [status]


def test_a_healable_failure_goes_to_heal_only(rig):
    provider, stub = rig
    response = httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    flush()
    assert len(stub.heals) == 1
    assert stub.tracked == []


def test_requests_and_async_httpx_are_tracked(rig):
    provider, stub = rig
    assert requests.post(f"{provider.url}/v1/generate", json={"model": "m"}).status_code == 200

    async def call():
        async with httpx.AsyncClient() as client:
            return (await client.post(f"{provider.url}/v1/status/404", json={})).status_code

    # 404 is healable, so it goes to /heal; 200 from requests is tracked.
    assert asyncio.run(call()) == 404
    flush()
    assert [c["statusCode"] for c in stub.tracked] == [200]


def test_the_sdk_never_tracks_its_own_calls(rig):
    provider, stub = rig
    httpx.post(f"{provider.url}/v1/generate", json={"model": "m"})
    flush()
    flush()
    assert len(stub.tracked) == 1
    assert all(not c["url"].startswith(stub.url) for c in stub.tracked)


def test_a_server_without_the_route_is_ignored_and_healing_still_works(rig):
    provider, stub = rig
    stub.requests_status = 404
    httpx.post(f"{provider.url}/v1/generate", json={"model": "m"})
    flush()
    healed = httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert healed.status_code == 200
    assert len(stub.heals) == 1


def test_no_call_waits_on_a_send_even_one_that_never_answers(rig):
    provider, stub = rig
    stub.requests_delay = 60.0  # Manifest accepts the batch and never answers
    url = f"{provider.url}/v1/generate"
    slowest = 0.0
    with httpx.Client() as client:
        for _ in range(600):  # crosses 500, so a send starts mid-loop
            started = time.monotonic()
            client.post(url, json={"model": "m"})
            slowest = max(slowest, time.monotonic() - started)
    assert slowest < 0.25, f"slowest call {slowest:.3f}s"


def test_a_healable_failure_that_cannot_be_sent_is_tracked(rig, monkeypatch):
    provider, stub = rig
    from mnfst.heal_api import NOT_SENT, HealApi
    monkeypatch.setattr(HealApi, "heal", lambda self, payload: NOT_SENT)  # all slots busy
    response = httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    assert response.status_code == 400
    flush()
    assert stub.heals == []
    assert [c["statusCode"] for c in stub.tracked] == [400]


def test_methods_are_upper_cased_and_out_of_range_records_never_sent(rig):
    provider, stub = rig
    started = time.time()
    outbound._track("patch", f"{provider.url}/x", 200, started, 1)
    outbound._track("X" * 17, f"{provider.url}/x", 200, started, 1)
    outbound._track("GET", f"{provider.url}/" + "a" * 4100, 200, started, 1)
    outbound._track("GET", "mailto:a@b.co", 200, started, 1)
    flush()
    assert [c["method"] for c in stub.tracked] == ["PATCH"]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_a_forked_worker_tracks_with_its_own_buffer_and_client(rig):
    provider, stub = rig
    httpx.post(f"{provider.url}/v1/generate", json={"model": "m"})  # buffered in the parent
    pid = os.fork()
    if pid == 0:  # the child: record, send, exit without running parent cleanup
        code = 1
        try:
            # macOS aborts a forked child that runs its system proxy lookup
            # (Objective-C fork safety); Linux, where preforking servers run,
            # does not. Skip the lookup here: it is not what this test is about.
            os.environ["NO_PROXY"] = "*"
            httpx.post(f"{provider.url}/v1/child", json={"model": "m"})
            outbound._tracker.flush(5)
            code = 0
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    assert status == 0
    flush()
    urls = sorted(c["url"] for c in stub.tracked)
    # The child sent only its own call; the parent's buffered call went once.
    assert urls == [f"{provider.url}/v1/child", f"{provider.url}/v1/generate"]


def test_a_script_that_ends_sends_its_tracked_calls(rig):
    provider, stub = rig
    script = (
        "import httpx\n"
        "from mnfst import manifest\n"
        f"manifest(key='mnfx_k', url='{stub.url}')\n"
        f"httpx.post('{provider.url}/v1/generate', json={{'model': 'm'}})\n"
    )
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-c", script], timeout=20,
                            env={**os.environ, "MNFST_KEY": "", "MNFST_URL": ""})
    assert result.returncode == 0
    assert time.monotonic() - started < 15
    assert [c["url"] for c in stub.tracked] == [f"{provider.url}/v1/generate"]
