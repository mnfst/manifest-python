import pytest

from mnfst.gate import REQUEST_BODY_LIMIT, parse_json_body, should_capture


@pytest.mark.parametrize("status", [400, 404, 405, 409, 410, 413, 415, 422, 428, 451, 499])
def test_any_request_side_4xx_is_captured(status):
    assert should_capture(status)


@pytest.mark.parametrize("status", [200, 201, 204, 301, 302, 401, 402, 403, 429, 500, 502, 599])
def test_forbidden_and_non_failure_statuses_pass_through(status):
    # auth, billing, rate limits and server faults: editing the request
    # cannot fix any of them, so reporting them is noise
    assert not should_capture(status)


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
