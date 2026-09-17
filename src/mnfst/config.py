"""Options resolution: kwargs beat environment beats defaults.

The surface is deliberately tiny: credentials, server, and a local
observability hook. Everything that is policy — whether a given app,
provider, endpoint, or direction gets healed, and how long the server may
spend finding a fix — lives server-side, where it is editable in the
https://dashboard.manifest.build without a deploy. The SDK only replays when the server hands it
a healed body, so the server can enforce all of that with no client knob.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

HOSTED_URL = "https://api.manifest.build"

# Hard client-side cap on a heal round-trip. Not configuration: fail-open
# needs a deadline even when the server misbehaves. How long the server
# actually spends investigating is server-side policy under this bound.
HEAL_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class Config:
    api_key: Optional[str]
    base_url: str
    on_heal: Optional[Callable]


def resolve_config(api_key: Optional[str] = None, url: Optional[str] = None,
                   on_heal: Optional[Callable] = None) -> Config:
    return Config(
        api_key=api_key or os.environ.get("MNFST_KEY") or None,
        base_url=(url or os.environ.get("MNFST_URL") or HOSTED_URL).rstrip("/"),
        on_heal=on_heal,
    )
