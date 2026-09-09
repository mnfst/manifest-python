"""Form-urlencoded parsing and re-encoding (CONTRACT §3-4)."""
import pytest

from mnfst.bodies import content_type_of, encode_request_body, is_form, parse_request_body

FORM = "application/x-www-form-urlencoded"


def parse(raw: bytes, content_type: str = FORM):
    return parse_request_body(raw, content_type)


def test_content_type_match_ignores_case_and_charset():
    assert is_form("Application/X-Www-Form-Urlencoded; charset=UTF-8")
    assert is_form(FORM)
    assert not is_form("application/json")
    assert not is_form("multipart/form-data; boundary=x")
    assert not is_form(None)


def test_content_type_is_found_in_any_header_mapping():
    assert content_type_of({"Content-Type": FORM}) == FORM
    assert content_type_of({b"content-type": b"application/json"}) == "application/json"
    assert content_type_of({"accept": "*/*"}) is None


def test_nested_repeated_and_appended_keys_become_structure():
    body, replayable = parse(
        b"line_items%5B0%5D%5Bprice%5D=price_123&line_items%5B0%5D%5Bquantity%5D=2"
        b"&expand=customer&expand=invoice&tags%5B%5D=a&tags%5B%5D=b")
    assert replayable
    assert body == {
        "line_items": [{"price": "price_123", "quantity": "2"}],
        "expand": ["customer", "invoice"],
        "tags": ["a", "b"],
    }


def test_flat_fields_and_plus_encoding():
    body, replayable = parse(b"limit=500&q=a+b%2Bc&flag&blank=")
    assert replayable
    assert body == {"limit": "500", "q": "a b+c", "flag": "", "blank": ""}


@pytest.mark.parametrize("raw", [
    b"name=%GG",          # malformed percent-escape
    b"name=%FF",          # not UTF-8
    b"a%5Bb=1",           # unclosed bracket
    b"a=1&a%5Bb%5D=2",    # the same key used as both scalar and object
    b"%5Bx%5D=1",         # no root key
    b"limit=" + b"1" * 262_145,  # past the capture limit
])
def test_unparseable_bodies_are_reported_but_not_replayable(raw):
    assert parse(raw) == (None, False)


def test_absent_body_stays_replayable_so_the_server_may_supply_one():
    assert parse(b"") == (None, True)
    assert parse(None) == (None, True)


def test_json_bodies_are_untouched_by_the_form_path():
    assert parse(b'{"limit": 500}', "application/json") == ({"limit": 500}, True)
    # unparseable JSON keeps its long-standing behaviour: null body, still
    # replayable, because the server may send a whole body back
    assert parse(b"limit=500", "application/json") == (None, True)
    assert parse(b'{"limit": 500}', None) == ({"limit": 500}, True)


def test_structure_round_trips_through_the_encoder():
    raw = (b"line_items%5B0%5D%5Bprice%5D=price_123&expand=customer&expand=invoice")
    body, _ = parse(raw)
    wire = encode_request_body(body, FORM)
    assert wire == (b"line_items%5B0%5D%5Bprice%5D=price_123"
                    b"&expand%5B0%5D=customer&expand%5B1%5D=invoice")
    assert parse(wire)[0] == body  # stable: encoding again changes nothing


def test_encoder_spells_scalars_the_way_json_does():
    assert encode_request_body({"n": 100, "b": True, "off": False, "z": None}, FORM) \
        == b"n=100&b=true&off=false&z="


def test_encoder_refuses_bodies_a_form_cannot_carry():
    for body in ["plain", 42, None, ["a"]]:
        with pytest.raises(ValueError):
            encode_request_body(body, FORM)
    with pytest.raises(ValueError):
        encode_request_body({"n": float("inf")}, FORM)


def test_encoder_falls_back_to_json_off_the_form_path():
    assert encode_request_body({"a": 1}, "application/json") == b'{"a": 1}'
    assert encode_request_body(None, None) == b"null"


def test_index_padding_is_bounded():
    # A single huge index would otherwise allocate a list to match it.
    assert parse(b"a%5B9999999%5D=x") == (None, False)
