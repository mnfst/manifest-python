import pytest
import requests

from mnfst.outbound import uninstall_outbound
from tests.test_outbound_httpx import build_rig, wait_for


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


def test_requests_post_heals(rig):
    provider, stub = rig
    response = requests.post(f"{provider.url}/v1/generate",
                             json={"model": "m", "temperature": 0.2})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(stub.heals) == 1


def test_requests_success_untouched(rig):
    provider, stub = rig
    assert requests.post(f"{provider.url}/v1/generate", json={"model": "m"}).status_code == 200
    assert stub.heals == []


def test_requests_generator_body_passes_through(rig):
    provider, stub = rig

    def gen():
        yield b'{"model": "m"}'

    response = requests.post(f"{provider.url}/v1/generate", data=gen(),
                             headers={"content-type": "application/json"})
    assert response.status_code == 200
    assert stub.heals == []  # an unreadable body gates out, it never raises



def test_requests_replay_exception_reports_outcome(rig):
    provider, stub = rig
    response = requests.post(f"{provider.url}/v1/flaky",
                             json={"model": "m", "temperature": 0.2})
    assert response.status_code == 400
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "transport_error"
    assert stub.outcomes[0][1]["failure"]["message"]


def test_requests_replay_error_text_masks_url_secrets(rig):
    """requests' ConnectionError embeds the URL it failed on — query included.
    Stopping the provider while phoenix is answering makes the replay fail to
    connect, which is the shape that carries the URL."""
    provider, stub = rig
    stub.on_heal_request = provider.stop
    # `connection: close` keeps the socket out of the pool, so the replay has
    # to open a fresh one against the now-dead port.
    requests.post(f"{provider.url}/v1/generate?api_key=sk_live_SECRET",
                  json={"model": "m", "temperature": 0.2},
                  headers={"connection": "close"})
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    error = stub.outcomes[0][1]["failure"]["message"]
    assert "sk_live_SECRET" not in error
    assert "api_key=REDACTED" in error


def test_requests_healed_headers_and_url_apply(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": f"{provider.url}/v1/generate",
                                     "headers": {"X-Beta": "on", "x-bad": None}}}
    response = requests.post(f"{provider.url}/v1/old", json={"needs_beta": True},
                             headers={"x-bad": "1", "Authorization": "Bearer sk"})
    assert response.status_code == 200
    route, headers, _ = provider.received[-1]
    assert route == "/v1/generate"
    assert headers.get("X-Beta") == "on"
    assert "x-bad" not in headers
    assert headers.get("Authorization") == "Bearer sk"  # credentials ride along untouched
    assert stub.heals[0]["request"]["headers"]["authorization"] == "REDACTED"
