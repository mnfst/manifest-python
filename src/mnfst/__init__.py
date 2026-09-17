"""manifest(): repair failing API requests on the fly, based on the API's error.

    from mnfst import manifest
    manifest()  # once, at startup

Instruments the process's HTTP clients (httpx and requests). When a call your
app makes fails, the failing request and the API's error go to Phoenix; if the
server returns a repaired body, the call is retried once. Successes are never
touched. The surface is three options: key, url, on_heal — everything that
is policy (which providers and endpoints get healed, and how hard the server
tries) is server-side configuration, editable in the https://dashboard.manifest.build.
"""
from __future__ import annotations

import warnings
from typing import Callable, Optional

from .config import resolve_config
from .heal_api import HealEvent
from .outbound import install_outbound, installed_config
from .version import VERSION

__all__ = ["manifest", "HealEvent", "VERSION"]


def manifest(*, key: Optional[str] = None, url: Optional[str] = None,
            on_heal: Optional[Callable] = None) -> None:
    config = resolve_config(api_key=key, url=url, on_heal=on_heal)
    if config.api_key is None:
        warnings.warn("mnfst: MNFST_KEY is not set; mnfst is disabled.",
                      stacklevel=2)
        return
    # Patching is process-global and one-shot. A second call with different
    # options cannot take effect, so say so instead of pretending.
    existing = installed_config()
    if existing is not None and existing != config:
        warnings.warn("mnfst: already installed with a different configuration; "
                      "reconfiguring requires a restart. "
                      "The first configuration stays in effect.", stacklevel=2)
    install_outbound(config)
