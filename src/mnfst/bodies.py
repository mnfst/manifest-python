"""Parse a request body by content type, and re-encode it for the retry.

JSON is the default. An `application/x-www-form-urlencoded` body parses into
structured values instead — nested keys (`line_items[0][price]`), repeated
keys and the `key[]` append form all become lists and dicts — so the server
sees the fields that caused the failure rather than a null body.

The retry is then re-encoded in the encoding the caller used: a form request
is never replayed as JSON under its original content type. Re-encoding runs
off the parsed structure, not the original bytes, so a repeated key comes
back indexed (`expand=a&expand=b` -> `expand[0]=a&expand[1]=b`); every
bracket-aware parser reads the two the same way.

Anything that cannot be parsed, or cannot be encoded back, is refused rather
than guessed at: a body the SDK does not understand is one it must not
replay.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, List, Mapping, Optional, Tuple
from urllib.parse import unquote_plus, urlencode

from .gate import REQUEST_BODY_LIMIT, bounded_json, parse_json_body

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

_MAX_FIELDS = 100_000
_MAX_DEPTH = 64
# List holes are padded, so an index alone could allocate unboundedly. Bound
# the digits here and the total padding below; both are needed.
_INDEX = re.compile(r"^(0|[1-9][0-9]{0,6})$")
_MAX_SLOTS = 100_000
# unquote_plus leaves a malformed escape ("%GG") untouched, which would make
# the re-encode silently differ from the bytes the caller sent. Reject it.
_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def is_form(content_type: Optional[str]) -> bool:
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() == FORM_CONTENT_TYPE


def _text(value: Any) -> str:
    return value.decode("latin-1") if isinstance(value, bytes) else str(value)


def content_type_of(headers: Mapping[str, Any]) -> Optional[str]:
    """The caller's content type, whatever mapping the client hands us. Keys
    arrive as str or bytes depending on the client; missing it here would send
    a form request down the JSON path, so match on both."""
    try:
        for name, value in headers.items():
            if _text(name).lower() == "content-type":
                return _text(value)
    except Exception:
        pass
    return None


def parse_request_body(body_bytes: Optional[bytes],
                       content_type: Optional[str]) -> Tuple[Any, bool]:
    """(body, replayable). `replayable` is False when bytes were sent that the
    SDK could not turn into a structure it can re-encode — those are reported
    but never retried, exactly as an oversized JSON body is."""
    if not is_form(content_type):
        return parse_json_body(body_bytes), True
    if not body_bytes:
        return None, True  # nothing was sent; the server may supply a body
    if len(body_bytes) > REQUEST_BODY_LIMIT:
        return None, False
    try:
        parsed = _parse_form(body_bytes.decode("utf-8"))
    except Exception:
        return None, False
    if parsed is None or not bounded_json(parsed):
        return None, False
    return parsed, True


def encode_request_body(body: Any, content_type: Optional[str]) -> bytes:
    """Re-encode a healed body in the caller's encoding. Raises ValueError
    when a form request cannot carry the value the server sent back."""
    if not is_form(content_type):
        return json.dumps(body).encode()
    if not isinstance(body, dict):
        raise ValueError("a form body must be an object")
    pairs: List[Tuple[str, str]] = []
    for name, child in body.items():
        _flatten(child, str(name), pairs, 1)
    return urlencode(pairs).encode()


# --- parse -------------------------------------------------------------------

def _decode(part: str) -> str:
    if _BAD_ESCAPE.search(part):
        raise ValueError("invalid percent-escape")
    return unquote_plus(part, errors="strict")


def _parse_form(text: str) -> Optional[dict]:
    body: dict = {}
    budget = [_MAX_SLOTS]
    fields = 0
    for pair in text.split("&"):
        if not pair:
            continue
        fields += 1
        if fields > _MAX_FIELDS:
            return None
        name, separator, raw = pair.partition("=")
        path = _path(_decode(name))
        if path is None:
            return None
        if not _put(body, path, _decode(raw) if separator else "", budget):
            return None
    return body


def _path(key: str) -> Optional[List[str]]:
    """`line_items[0][price]` -> ['line_items', '0', 'price']. None when the
    brackets do not pair up, which means we cannot round-trip the key."""
    bracket = key.find("[")
    root = key if bracket == -1 else key[:bracket]
    if not root or "]" in root:
        return None
    path = [root]
    offset = len(root)
    while offset < len(key):
        if key[offset] != "[":
            return None
        end = key.find("]", offset + 1)
        if end == -1:
            return None
        part = key[offset + 1:end]
        if "[" in part:
            return None
        path.append(part)
        offset = end + 1
    return path if len(path) <= _MAX_DEPTH else None


def _grow(target: list, position: int, budget: List[int]) -> bool:
    if position < len(target):
        return True
    needed = position + 1 - len(target)
    if needed > budget[0]:
        return False
    budget[0] -= needed
    target.extend([None] * needed)
    return True


def _place(container: Any, slot: Any, value: str) -> bool:
    """A key seen twice becomes a list, as every form parser does."""
    existing = container[slot] if isinstance(container, list) else container.get(slot)
    if existing is None:
        container[slot] = value
    elif isinstance(existing, str):
        container[slot] = [existing, value]
    elif isinstance(existing, list) and all(isinstance(item, str) for item in existing):
        existing.append(value)
    else:
        return False
    return True


def _put(root: dict, path: List[str], value: str, budget: List[int]) -> bool:
    current: Any = root
    for index, part in enumerate(path):
        last = index == len(path) - 1
        if isinstance(current, list):
            if part == "":
                slot = len(current)
            elif _INDEX.match(part):
                slot = int(part)
            else:
                return False  # a list cannot also be keyed by name
            if not _grow(current, slot, budget):
                return False
        else:
            if not part:
                return False
            slot = part
        if last:
            return _place(current, slot, value)
        nxt = path[index + 1]
        wants_list = nxt == "" or bool(_INDEX.match(nxt))
        existing = current.get(slot) if isinstance(current, dict) else current[slot]
        if existing is None:
            current[slot] = [] if wants_list else {}
        elif not isinstance(existing, (dict, list)) or isinstance(existing, list) != wants_list:
            return False
        current = current[slot]
    return True


# --- encode ------------------------------------------------------------------

def _flatten(value: Any, key: str, pairs: List[Tuple[str, str]], depth: int) -> None:
    if len(pairs) >= _MAX_FIELDS or depth > _MAX_DEPTH:
        raise ValueError("form body exceeds structural limits")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _flatten(item, f"{key}[{index}]", pairs, depth + 1)
    elif isinstance(value, dict):
        for name, child in value.items():
            _flatten(child, f"{key}[{name}]" if key else str(name), pairs, depth + 1)
    elif value is None:
        pairs.append((key, ""))
    elif isinstance(value, bool):
        # JSON spells these lowercase; str(True) would send "True".
        pairs.append((key, "true" if value else "false"))
    elif isinstance(value, int):
        pairs.append((key, str(value)))
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a form body cannot carry a non-finite number")
        pairs.append((key, repr(value)))
    elif isinstance(value, str):
        pairs.append((key, value))
    else:
        raise ValueError("unsupported value in a form body")
