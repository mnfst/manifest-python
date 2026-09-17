"""Installs Manifest at interpreter startup for `mnfst run`.

`mnfst run` puts this directory first on PYTHONPATH; Python imports
`sitecustomize` automatically while initializing, so `manifest()` runs before
the app's first request. This is the same preload trick as `ddtrace-run`.

Deliberately small and fail-open: instrumenting must never stop the process
it exists to heal. The MNFST_KEY guard keeps unrelated child interpreters
(e.g. `pip` spawned by the app) quiet instead of warning with no key set.
"""
from __future__ import annotations

import os
import warnings

if os.environ.get("MNFST_KEY"):
    try:
        from mnfst import manifest

        manifest()
    except Exception as exc:  # pragma: no cover - defensive, never fatal
        warnings.warn(f"mnfst: bootstrap failed; SDK not installed: {exc!r}",
                      stacklevel=2)
