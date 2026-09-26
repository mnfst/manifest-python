"""Excluded calls through the real patches: never healed, never tracked, untouched."""
import asyncio

import httpx
import pytest
import requests

from mnfst import outbound
from mnfst.outbound import uninstall_outbound
from tests.test_outbound_httpx import build_rig


@pytest.fixture
def denied():
    provider, stub = build_rig(denylist="127.0.0.1")
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


@pytest.fixture
def off_allowlist():
    provider, stub = build_rig(allowlist="example.com")
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


def test_a_denied_host_is_neither_healed_nor_tracked(denied):
    provider, stub = denied
    failed = httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2})
    ok = requests.post(f"{provider.url}/v1/generate", json={"model": "m"})

    async def call():
        async with httpx.AsyncClient() as client:
            return (await client.post(f"{provider.url}/v1/status/404", json={})).status_code

    assert failed.status_code == 400
    assert ok.status_code == 200
    assert asyncio.run(call()) == 404
    outbound._tracker.flush(5)
    assert stub.heals == []
    assert stub.tracked == []


def test_a_host_off_the_allowlist_is_neither_healed_nor_tracked(off_allowlist):
    provider, stub = off_allowlist
    assert requests.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2}).status_code == 400
    assert httpx.post(f"{provider.url}/v1/generate", json={"model": "m"}).status_code == 200
    outbound._tracker.flush(5)
    assert stub.heals == []
    assert stub.tracked == []


@pytest.fixture
def denied_route():
    provider, stub = build_rig(denylist="127.0.0.1/v1/generate")
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


def test_a_denied_route_is_skipped_and_the_rest_of_the_host_is_tracked(denied_route):
    provider, stub = denied_route
    assert httpx.post(f"{provider.url}/v1/generate", json={"model": "m", "temperature": 0.2}).status_code == 400
    assert httpx.post(f"{provider.url}/v1/status/429", json={}).status_code == 429
    outbound._tracker.flush(5)
    assert stub.heals == []
    assert [(c["url"].endswith("/v1/status/429"), c["statusCode"]) for c in stub.tracked] == [(True, 429)]
