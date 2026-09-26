"""manifest(): repair failing API requests on the fly, based on the API's error.

    from mnfst import manifest
    manifest()  # once, at startup

Instruments the process's HTTP clients (httpx and requests). When a call your
app makes fails with a healable error, the failing request and the API's error
go to Manifest; if the server returns a repaired body, the call is retried once.
Every other call, successes included, is only recorded as metadata (method, URL
without its query, status, timing) and sent in background batches: it is never
modified and never slowed. The surface is five options: key, url, on_heal, and
allowlist / denylist (default MNFST_ALLOWLIST / MNFST_DENYLIST, comma-separated), which
keep calls out of Manifest entirely: neither healed nor tracked. An entry is a domain
(stripe.com, subdomains included) or a domain with a path (stripe.com/v1/charges, whole
segments); the denylist wins. Everything that is policy (which
providers and endpoints get healed, and how hard the server tries) is server-side configuration, editable in the https://dashboard.manifest.build.
"""
from __future__ import annotations

import warnings
from typing import Callable, Iterable, Optional, Union

from .config import resolve_config
from .heal_api import HealEvent
from .outbound import install_outbound, installed_config
from .version import VERSION

__all__ = ["manifest", "HealEvent", "VERSION"]


def manifest(*, key: Optional[str] = None, url: Optional[str] = None,
            on_heal: Optional[Callable] = None,
            allowlist: Optional[Union[str, Iterable[str]]] = None,
            denylist: Optional[Union[str, Iterable[str]]] = None) -> None:
    config = resolve_config(api_key=key, url=url, on_heal=on_heal,
                            allowlist=allowlist, denylist=denylist)
    if config.ignored_entries:
        warnings.warn("mnfst: ignoring unreadable allowlist/denylist entries: "
                      + ", ".join(config.ignored_entries), stacklevel=2)
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
