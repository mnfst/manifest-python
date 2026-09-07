"""Settle a healed body against the one the caller sent (CONTRACT §4).

Objects merge: the healed object decides every key it names, a key it
omits is dropped (deletion-by-omission is how a field gets removed), and
keys that never travelled — the credential-named ones withheld from the
wire — are restored from the caller's copy unless the healed object names
them. Anything else (arrays, scalars, a body that changed type) is replaced
wholesale.
"""
from __future__ import annotations

from typing import Any


def merge_healed_body(original: Any, traveled: Any, healed: Any) -> Any:
    if not isinstance(original, dict) or not isinstance(healed, dict):
        return healed
    merged = dict(healed)
    traveled_keys = traveled.keys() if isinstance(traveled, dict) else ()
    for key, value in original.items():
        if key not in traveled_keys and key not in merged:
            merged[key] = value
    return merged
