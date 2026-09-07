from mnfst.wire import (ERROR_TEXT_CAP, RESPONSE_BODY_CAP, capped_response_body, heal_payload,
                          safe_error_text, safe_url)


def test_safe_url_keeps_query_masks_secrets_drops_fragment():
    assert safe_url("https://api.stripe.com/v1/charges?limit=5&api-key=sk_123#frag") == \
        "https://api.stripe.com/v1/charges?limit=5&api-key=REDACTED"
    assert safe_url("/workouts?debug=1") == "/workouts?debug=1"
    assert safe_url("/workouts") == "/workouts"


def test_safe_url_strips_userinfo():
    assert safe_url("https://user:sk_live_abc@api.x.com/v1/c?a=1") == "https://api.x.com/v1/c?a=1"


def test_secret_param_matching_is_normalized():
    assert safe_url("/p?X-Api-Key=abc") == "/p?X-Api-Key=REDACTED"
    assert safe_url("/p?apiKey=abc") == "/p?apiKey=REDACTED"
    assert safe_url("/p?sessionId=abc") == "/p?sessionId=REDACTED"


def test_secret_param_coverage():
    for name in ("bearer", "jwt", "id_token", "idToken", "auth_token", "authToken",
                 "pwd", "passwd", "private_key", "X-Private-Key"):
        assert safe_url(f"/p?{name}=abc") == f"/p?{name}=REDACTED", name


def test_safe_error_text_masks_urls_and_credentials():
    # the shape requests uses: a rooted path with a query, no scheme
    text = safe_error_text(RuntimeError(
        "HTTPConnectionPool(host='x', port=80): Max retries exceeded with url: "
        "/v1/generate?api_key=sk_live_SECRET"))
    assert "sk_live_SECRET" not in text
    assert "REDACTED" in text

    # the shape httpx uses: a whole absolute URL, userinfo included
    text = safe_error_text(RuntimeError(
        "connect failed for https://u:pw@api.x.com/v1/c?token=sk_live_SECRET&limit=5"))
    assert "sk_live_SECRET" not in text and "u:pw@" not in text
    assert "REDACTED" in text and "limit=5" in text  # non-secrets survive

    # a bare assignment the URL shapes miss
    assert "sk_live_SECRET" not in safe_error_text(
        RuntimeError("bad request: client_secret=sk_live_SECRET"))


def test_safe_error_text_is_capped_and_never_raises():
    assert len(safe_error_text(RuntimeError("x" * 5000))) == ERROR_TEXT_CAP

    class Hostile(Exception):
        def __str__(self):
            raise ValueError("no string for you")

    assert safe_error_text(Hostile()) == "Hostile"


def test_capped_response_body_holds_the_cap_on_the_wire():
    # every byte decodes to a 3-byte replacement char: the input fits the cap,
    # the decoded text does not, and it is the text that travels.
    body, truncated = capped_response_body(b"\xff" * RESPONSE_BODY_CAP)
    assert isinstance(body, str)
    assert len(body.encode("utf-8")) <= RESPONSE_BODY_CAP
    assert truncated is True


def test_capped_response_body_json_and_text():
    body, truncated = capped_response_body(b'{"detail": []}')
    assert body == {"detail": []} and truncated is False
    body, truncated = capped_response_body(b"x" * (RESPONSE_BODY_CAP + 10))
    assert isinstance(body, str) and len(body.encode()) <= RESPONSE_BODY_CAP and truncated is True


def test_heal_payload_body_may_be_none():
    p = heal_payload(trace_id="t", method="GET", url="/users/42", headers={}, body=None,
                     status_code=404, response_body={"detail": "nope"}, truncated=False,
                     response_time_ms=3)
    assert p["request"]["body"] is None
    assert p["request"]["method"] == "GET"


def test_heal_payload_shape():
    p = heal_payload(trace_id="t1", method="POST", url="https://h/p?q=1",
                     headers={"Content-Type": "application/json", "Authorization": "Bearer sk"},
                     body={"a": 1}, status_code=422, response_body={"detail": []},
                     truncated=False, response_time_ms=12)
    assert p == {
        "traceId": "t1",
        # query strings travel by design (only credential-named values are masked);
        # headers travel lowercased with credential values masked
        "request": {"method": "POST", "url": "https://h/p?q=1",
                    "headers": {"content-type": "application/json", "authorization": "REDACTED"},
                    "body": {"a": 1}},
        "response": {"statusCode": 422, "body": {"detail": []}, "truncated": False},
        "responseTimeMs": 12,
    }


def test_safe_headers_masks_by_root_and_lowercases():
    from mnfst.wire import safe_headers
    out = safe_headers({"Authorization": "Bearer x", "Proxy-Authorization": "y", "Cookie": "c=1",
                        "X-Goog-Api-Key": "k", "X-Amz-Security-Token": "t",
                        "Content-Type": "application/json", "Stripe-Version": "2024-06-20",
                        "User-Agent": "openai-python/1.3"})
    assert out["authorization"] == "REDACTED"
    assert out["proxy-authorization"] == "REDACTED"
    assert out["cookie"] == "REDACTED"
    assert out["x-goog-api-key"] == "REDACTED"
    assert out["x-amz-security-token"] == "REDACTED"
    assert out["content-type"] == "application/json"
    assert out["stripe-version"] == "2024-06-20"
    assert out["user-agent"] == "openai-python/1.3"


def test_traveling_body_withholds_top_level_credentials_only():
    from mnfst.wire import traveling_body
    body = {"model": "m", "client_secret": "s", "apiKey": "k", "nested": {"token": "keep"}}
    assert traveling_body(body) == {"model": "m", "nested": {"token": "keep"}}
    assert traveling_body(["a", "b"]) == ["a", "b"]
    assert traveling_body(None) is None
