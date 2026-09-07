from mnfst.gate import REQUEST_BODY_LIMIT, parse_json_body, should_capture


def test_capture_is_request_side_failures_only():
    for status in (400, 404, 422):
        assert should_capture(status)
    for status in (200, 201, 204, 301, 302, 401, 402, 403, 429, 500, 502):
        assert not should_capture(status)  # auth/billing/rate-limit/5xx: noise


def test_any_json_body_travels():
    assert parse_json_body(b'{"reps": "10"}') == {"reps": "10"}
    assert parse_json_body(b"[1,2]") == [1, 2]  # arrays: bulk endpoints
    assert parse_json_body(b'"just a string"') == "just a string"
    assert parse_json_body(b"not json") is None
    assert parse_json_body(None) is None
    assert parse_json_body(b"") is None


def test_body_size_limit():
    big = b'{"k": "' + b"x" * REQUEST_BODY_LIMIT + b'"}'
    assert parse_json_body(big) is None


def test_hostile_depth_fails_open():
    deep = b"[" * 20000 + b"]" * 20000
    assert parse_json_body(deep) is None  # RecursionError swallowed
