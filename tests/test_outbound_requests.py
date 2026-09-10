import inspect

import pytest
import requests

from mnfst.bodies import is_form
from mnfst.outbound import uninstall_outbound
from tests.test_outbound_httpx import build_rig, header, wait_for


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


def test_unhealed_response_preserves_session_cookies(rig):
    provider, stub = rig
    stub.result = {"status": "no_patch"}
    with requests.Session() as client:
        response = client.post(provider.url + "/v1/generate", json={"temperature": 1})
        assert response.status_code == 400
        assert client.cookies.get("error_session") == "retained"


# --- form-urlencoded through the requests adapter ---

def test_requests_form_body_is_healed_and_replayed_as_a_form(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m"}}}
    response = requests.post(f"{provider.url}/v1/generate",
                             data={"model": "m", "temperature": "0.2"})
    assert response.status_code == 200
    assert stub.heals[0]["request"]["body"] == {"model": "m", "temperature": "0.2"}
    route, raw = provider.requests[-1]
    assert raw == b"model=m"
    headers = provider.received[-1][1]
    assert is_form(header(headers, "content-type"))
    assert header(headers, "content-length") == str(len(raw))


def test_patched_send_matches_httpadapter_signature(rig):
    """CacheControlAdapter.send calls super().send with positional extras."""
    assert list(inspect.signature(requests.adapters.HTTPAdapter.send).parameters) == [
        "self", "request", "stream", "timeout", "verify", "cert", "proxies",
    ]


def test_requests_adapter_send_accepts_positional_extras(rig):
    provider, stub = rig
    session = requests.Session()
    prepared = session.prepare_request(
        requests.Request("POST", f"{provider.url}/v1/generate", json={"model": "m"}))
    response = session.get_adapter(prepared.url).send(
        prepared, False, None, True, None, None)
    assert response.status_code == 200
    assert stub.heals == []


def test_requests_unparseable_form_body_is_not_replayed(rig):
    provider, stub = rig
    response = requests.post(f"{provider.url}/v1/generate", data=b"temperature=%GG",
                             headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400
    assert stub.heals[0]["request"]["body"] is None
    assert len([r for r in provider.requests if r[0] == "/v1/generate"]) == 1
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "not_attempted"
