"""End to end through the public entry point: a real provider socket, a real
stub Phoenix socket, and a patched httpx client the calling code never knew
about — the whole v1 loop."""
import time

import httpx
import pytest

from mnfst import manifest
from mnfst.outbound import uninstall_outbound
from tests.stub_phoenix import StubPhoenix
from tests.test_outbound_httpx import Provider


@pytest.fixture
def rig():
    provider = Provider().start()
    stub = StubPhoenix().start()
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m"}}}  # temperature omitted -> dropped
    yield provider, stub
    uninstall_outbound()
    stub.stop()
    provider.stop()


def test_full_outbound_heal_loop(rig):
    provider, stub = rig
    manifest(key="mnfx_k", url=stub.url)

    response = httpx.post(f"{provider.url}/v1/generate",
                          json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    assert response.json()["ok"] is True

    heal = stub.heals[0]
    assert heal["request"]["method"] == "POST"
    assert heal["request"]["url"] == f"{provider.url}/v1/generate"
    assert heal["request"]["body"] == {"model": "m", "temperature": 0.2}
    assert heal["response"]["statusCode"] == 400

    deadline = time.time() + 2
    while not stub.outcomes and time.time() < deadline:
        time.sleep(0.05)
    assert stub.outcomes, "outcome report never arrived"
    assert stub.outcomes[0][0] == "a1"
    assert stub.outcomes[0][1]["response"]["statusCode"] == 200


def test_no_patch_serves_original_error(rig):
    provider, stub = rig
    stub.result = None  # no_patch
    manifest(key="mnfx_k", url=stub.url)
    response = httpx.post(f"{provider.url}/v1/generate",
                          json={"model": "m", "temperature": 0.2})
    assert response.status_code == 400  # the provider's own answer, byte for byte
    assert len(stub.heals) == 1
