"""Capture any method, but only request-side failures.

A 4xx is the server saying the request was at fault, so 4xx is the range
worth reporting and repairing — 409, 413, 415 and 451 describe a refused
request just as 400 and 422 do. Four are forbidden, along with every 5xx:
401/403 (auth), 402 (billing), 429 (rate limits) and server faults cannot
be fixed by editing the request, and reporting them is noise.

That status gate is the client's only eligibility rule. Whether a captured
failure gets retried is the server's call: the SDK retries when Phoenix
hands back a healed request (CONTRACT §4).
"""
from __future__ import annotations

import json
from typing import Any, Optional

FORBIDDEN_STATUSES = frozenset({401, 402, 403, 429})
REQUEST_BODY_LIMIT = 262144  # past this a body is a payload, not a form to repair


def should_capture(status: int) -> bool:
    """Any 4xx but the forbidden ones. The upper bound is what excludes 5xx."""
    return 400 <= status < 500 and status not in FORBIDDEN_STATUSES


def parse_json_body(body_bytes: Optional[bytes]) -> Any:
    """The request body as JSON — object, array or scalar — else None (absent,
    huge, not JSON). Fails open on anything: deep nesting raises
    RecursionError, and no parse failure may ever break the caller's request."""
    if not body_bytes or len(body_bytes) > REQUEST_BODY_LIMIT:
        return None
    try:
        parsed = json.loads(body_bytes)
        return parsed if bounded_json(parsed) else None
    except Exception:
        return None


def bounded_json(value: Any, max_depth: int = 64) -> bool:
    """Bound depth and work independently of interpreter recursion behavior."""
    stack = [(value, 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if depth > max_depth or visited > 100_000:
            return False
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    return True
