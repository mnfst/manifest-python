"""urllib.request through the real patch: the SDK wraps the HTTP handlers'
do_open, one hop on the wire, before urllib turns a 4xx into an HTTPError."""
import gzip
import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from mnfst import outbound
from mnfst.outbound import uninstall_outbound
from mnfst.bodies import is_form
from mnfst.wire import RESPONSE_BODY_CAP
from tests.helpers import wait_for
from tests.test_outbound_httpx import BIG_ERROR_CHUNKS, build_rig, header


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


def post_json(url, body, headers=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
    return urllib.request.urlopen(request, timeout=5)


def test_json_post_heals(rig):
    provider, stub = rig
    with post_json(f"{provider.url}/v1/generate", {"model": "m", "temperature": 0.2}) as response:
        assert response.status == 200
        assert json.loads(response.read())["received"] == {"model": "m"}
    assert len(stub.heals) == 1
    heal = stub.heals[0]
    assert heal["request"]["method"] == "POST"
    assert heal["request"]["body"] == {"model": "m", "temperature": 0.2}
    assert heal["response"]["statusCode"] == 400
    assert "temperature unsupported" in json.dumps(heal["response"]["body"])


def test_success_is_untouched(rig):
    provider, stub = rig
    with post_json(f"{provider.url}/v1/generate", {"model": "m"}) as response:
        assert response.status == 200
    assert stub.heals == []


def test_streamed_200_reaches_the_caller_unread(rig):
    provider, stub = rig
    with post_json(f"{provider.url}/v1/stream", {"model": "m"}) as response:
        assert [response.read(6) for _ in range(3)] == [b"chunk0", b"chunk1", b"chunk2"]
    assert stub.heals == []


def test_unhealed_failure_raises_http_error_with_its_body(rig):
    provider, stub = rig
    stub.result = None  # no_patch
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(f"{provider.url}/v1/generate", {"temperature": 1})
    error = caught.value
    assert error.code == 400
    assert error.reason == "Bad Request"
    assert json.loads(error.read())["error"]["message"] == "temperature unsupported"
    assert len(stub.heals) == 1


def test_an_opener_without_error_processing_gets_the_raw_response(rig):
    provider, stub = rig
    stub.result = None
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.HTTPHandler())
    request = urllib.request.Request(f"{provider.url}/v1/generate",
                                     data=b'{"temperature": 1}',
                                     headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=5) as response:
        assert response.status == 400
        assert response.msg == "Bad Request"
        assert response.getheader("content-type") == "application/json"
        assert response.readline().startswith(b'{"error"')
    assert len(stub.heals) == 1


def test_healed_headers_and_url_apply(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": f"{provider.url}/v1/generate",
                                     "headers": {"X-Beta": "on", "x-bad": None}}}
    with post_json(f"{provider.url}/v1/old", {"needs_beta": True},
                   headers={"X-Bad": "1", "Authorization": "Bearer sk"}) as response:
        assert response.status == 200
        assert response.geturl() == f"{provider.url}/v1/generate"
    route, headers, body = provider.received[-1]
    assert route == "/v1/generate"
    assert header(headers, "x-beta") == "on"
    assert header(headers, "x-bad") is None
    assert header(headers, "authorization") == "Bearer sk"
    assert body == {"needs_beta": True}
    assert stub.heals[0]["request"]["headers"]["authorization"] == "REDACTED"


def test_a_replay_through_a_proxy_goes_through_the_proxy(rig):
    """The provider stands in for an http proxy: requests reach it in
    absolute form, and so must the retry."""
    provider, stub = rig
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": provider.url}))
    request = urllib.request.Request("http://api.example.test/v1/generate",
                                     data=b'{"model": "m", "temperature": 0.2}',
                                     headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=5) as response:
        assert response.status == 200
    assert [route for route, _ in provider.requests] == [
        "http://api.example.test/v1/generate"] * 2
    assert stub.heals[0]["request"]["url"] == "http://api.example.test/v1/generate"


def test_cross_origin_url_heal_is_refused(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"url": "http://evil.example/v1/generate"}}
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(f"{provider.url}/v1/old", {"model": "m"})
    assert caught.value.code == 404
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "not_attempted"


def test_form_body_is_healed_and_replayed_as_a_form(rig):
    provider, stub = rig
    stub.result = {"status": "patched", "issueId": "i1", "healAttemptId": "a1",
                   "healedRequest": {"body": {"model": "m"}}}
    data = urllib.parse.urlencode({"model": "m", "temperature": "0.2"}).encode()
    with urllib.request.urlopen(f"{provider.url}/v1/generate", data=data, timeout=5) as response:
        assert response.status == 200
    assert stub.heals[0]["request"]["body"] == {"model": "m", "temperature": "0.2"}
    route, raw = provider.requests[-1]
    assert raw == b"model=m"
    headers = provider.received[-1][1]
    assert is_form(header(headers, "content-type"))
    assert header(headers, "content-length") == str(len(raw))


def test_a_file_body_is_captured_without_being_read(rig):
    provider, stub = rig
    stub.result = None
    body = io.BytesIO(b'{"model": "m"}')
    request = urllib.request.Request(f"{provider.url}/v1/old", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Content-Length": "14"})
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(request, timeout=5)
    assert stub.heals[0]["request"]["body"] is None


def test_failed_get_is_captured_with_no_body(rig):
    provider, stub = rig
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f"{provider.url}/v1/generate", timeout=5)
    assert caught.value.code == 404
    assert stub.heals[0]["request"]["method"] == "GET"
    assert stub.heals[0]["request"]["body"] is None
    assert len(provider.gets) == 2  # the replay kept its method
    assert provider.requests == []


def test_replay_exception_reports_outcome_and_serves_the_original(rig):
    provider, stub = rig
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(f"{provider.url}/v1/flaky", {"model": "m", "temperature": 0.2})
    assert json.loads(caught.value.read())["error"]["message"] == "temperature unsupported"
    assert wait_for(lambda: stub.outcomes), "no outcome report arrived"
    assert stub.outcomes[0][1]["failure"]["kind"] == "transport_error"


def test_crash_in_heal_branch_serves_the_original_response(rig, monkeypatch):
    provider, stub = rig

    def boom(*args, **kwargs):
        raise RuntimeError("merge exploded")

    monkeypatch.setattr("mnfst.outbound._apply", boom)
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(f"{provider.url}/v1/generate", {"model": "m", "temperature": 0.2})
    assert json.loads(caught.value.read())["error"]["message"] == "temperature unsupported"


def test_a_gzip_error_body_is_decoded_for_manifest_and_left_raw_for_the_caller(rig):
    provider, stub = rig
    stub.result = None
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(f"{provider.url}/v1/gzip", {"model": "m"})
    body = gzip.decompress(caught.value.read())
    assert json.loads(body)["error"]["message"] == "temperature unsupported"
    assert "temperature unsupported" in json.dumps(stub.heals[0]["response"]["body"])


def test_an_error_body_over_the_cap_reaches_the_caller_whole(rig):
    provider, stub = rig
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(f"{provider.url}/v1/big", {"model": "m"})
    body = caught.value.read()
    assert body == b"x" * (16384 * BIG_ERROR_CHUNKS)
    assert len(body) > RESPONSE_BODY_CAP
    assert stub.heals[0]["response"]["truncated"] is True
    assert len(provider.requests) == 1  # a truncated capture is never replayed


def test_calls_are_tracked(rig):
    provider, stub = rig
    post_json(f"{provider.url}/v1/generate", {"model": "m"}).close()
    outbound._tracker.flush(5)
    assert [call["statusCode"] for call in stub.tracked] == [200]
    assert stub.tracked[0]["method"] == "POST"


def test_a_denied_host_is_neither_healed_nor_tracked(denied):
    provider, stub = denied
    with pytest.raises(urllib.error.HTTPError):
        post_json(f"{provider.url}/v1/generate", {"model": "m", "temperature": 0.2})
    outbound._tracker.flush(5)
    assert stub.heals == [] and stub.tracked == []


def test_uninstall_restores_the_handler():
    original = urllib.request.AbstractHTTPHandler.do_open
    provider, stub = build_rig()
    try:
        assert urllib.request.AbstractHTTPHandler.do_open is not original
    finally:
        uninstall_outbound()
        stub.stop()
        provider.stop()
    assert urllib.request.AbstractHTTPHandler.do_open is original
