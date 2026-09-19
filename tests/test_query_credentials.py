"""The SDK masks credential query parameters on the wire and the server never
serves credentials back, so the retry must put the caller's own values back."""
from urllib.parse import parse_qsl, urlsplit

from mnfst.outbound import _Capture, _apply


def _query(url: str) -> dict:
    return dict(parse_qsl(urlsplit(url).query))


def _capture(url: str) -> _Capture:
    return _Capture("GET", url, {}, None, 400, b'{"error":"limit"}', 1)


def test_restores_the_query_credentials_the_server_never_saw():
    capture = _capture("https://a.test/orders?key=AIza-live&limit=500&session=s1")
    retry = _apply(capture, {"url": "https://a.test/orders?limit=100&session=REDACTED"})
    assert retry is not None
    assert _query(retry.url) == {"limit": "100", "session": "s1", "key": "AIza-live"}


def test_a_healed_credential_value_wins():
    capture = _capture("https://a.test/orders?key=old")
    retry = _apply(capture, {"url": "https://a.test/orders?key=new"})
    assert retry is not None
    assert retry.url == "https://a.test/orders?key=new"


def test_a_mask_with_nothing_behind_it_is_dropped():
    capture = _capture("https://a.test/orders?limit=500")
    retry = _apply(capture, {"url": "https://a.test/orders?limit=100&token=REDACTED"})
    assert retry is not None
    assert retry.url == "https://a.test/orders?limit=100"


def test_a_header_served_under_the_mask_keeps_the_callers_value():
    capture = _Capture("POST", "https://a.test/orders", {"X-Signature": "sig-1"}, b"{}", 400, b"{}", 1)
    retry = _apply(capture, {"headers": {"x-signature": "REDACTED", "x-added": "yes"}})
    assert retry is not None
    assert retry.headers == {"X-Signature": "sig-1", "x-added": "yes"}
