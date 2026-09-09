# Manifest for Python

[![CI](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/mnfst?label=PyPI)](https://pypi.org/project/mnfst/)
[![PyPI downloads](https://img.shields.io/pypi/dm/mnfst?label=PyPI%20downloads)](https://pypi.org/project/mnfst/)

**An API rejects your request. Manifest fixes it and retries. You do nothing.**

```sh
pip install mnfst
```

```python
from mnfst import manifest

manifest()  # once, at startup
# Keep making your API calls as usual.
```

Works with `httpx` (sync and async) and `requests`, for JSON and form-urlencoded bodies. Python 3.10+. The import name is `mnfst`.

![How Manifest heals a failed request: a 400 reaches Manifest, drops to a patch from the knowledge base or the healing agents, and is retried once, returning a 200 OK](https://raw.githubusercontent.com/mnfst/manifest-python/main/docs/healing-diagram.svg)

## Setup

1. Create a project in your Manifest dashboard and copy its project key.
2. Turn on **Autofix** in **Project Settings**.
3. Set the key:

```sh
export MNFST_KEY='your-project-key'
```

Call `manifest()` once at startup, before your first request.

## See it work

```python
import httpx
from mnfst import manifest, flush

manifest(on_heal=lambda e: print("[manifest]", e.heal_status, e.replay_status_code))

res = httpx.post(
    "https://api.example.com/orders",
    json={"limit": 500},  # rejected? Manifest retries with a valid limit
)

flush(timeout=5)  # short scripts only: wait for reports before exiting
```

## Good to know

- **Retries repeat side effects.** Use idempotency keys on non-idempotent calls.
- **A heal adds up to 60 s** to a failed request. Successful requests are untouched and never contact Manifest.
- **Not intercepted:** custom `httpx` transports and `aiohttp`.
- **Privacy.** Failed URLs, headers, JSON or form-urlencoded bodies, and error responses are sent to Manifest. Known credentials are masked, but nested secrets and business data are not. Enable it only for traffic you allow Manifest to process.

## More

[Configuration, limits & development](docs/guide.md) · [API contract](CONTRACT.md) · [Node.js SDK](https://github.com/mnfst/manifest-node)
