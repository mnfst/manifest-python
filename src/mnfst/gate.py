"""Capture any method, but only request-side failures.

400/404/422 are the statuses where the failure is the request's fault —
the only failures worth reporting and the only ones worth repairing.
401/403 (auth), 402 (billing), 429 (rate limits) and every 5xx are excluded
by design: editing the request cannot help, and reporting them is noise.

That status gate is the client's only eligibility rule. Whether a captured
failure gets retried is the server's call: the SDK retries when Phoenix
hands back a healed request (CONTRACT §4).
"""
from __future__ import annotations

import json
from typing import Any, Optional

GATED_STATUSES = frozenset({400, 404, 422})
REQUEST_BODY_LIMIT = 262144  # past this a body is a payload, not a form to repair


def should_capture(status: int) -> bool:
    return status in GATED_STATUSES


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
