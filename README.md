<div align="center">

![Manifest SDK Architecture](./docs/github-sdk.png)

# Manifest for Python

**Keep every API connection in your app up and running.**

[![CI](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/mnfst?label=PyPI)](https://pypi.org/project/mnfst/)
[![PyPI downloads](https://img.shields.io/pypi/dm/mnfst?label=PyPI%20downloads)](https://pypi.org/project/mnfst/)

</div>

## What is Manifest

Manifest lets you monitor all your API connections and make them more reliable.

* ⏰ **Stay ahead of breaking changes**: get warned before an API you depend on changes, so nothing breaks by surprise.
* 🎯 **Never lose a request to a bad call**: failed requests are fixed and sent again on the fly, before your users notice.
* 📡 **Know exactly how your APIs behave**: every call, every provider, every issue, live in one dashboard.

Works with internal APIs, external services and agent tools.

## How it works

![How the SDK works: every call Manifest does not heal is recorded in the background as metadata (method, URL, status, timing, no body); a failure Manifest can heal is sent with its error, patched, and retried once, returning a 200 OK](./docs/sdk-flow-diagram.png)

Every call your app makes is reported to Manifest as metadata only (method, URL without its query string, status and timing), in the background. A failure Manifest can heal is sent in full, so it can be repaired. [What is sent](docs/guide.md#data-sent-to-manifest).

## Prerequisites

- <a href="https://www.python.org/downloads/release/python-3100/" target="_blank">Python 3.10</a> or higher

## Get started

### Start with your agent

```
"Install Manifest in this app: https://dashboard.manifest.build/prompt-python.md"
```

[Read the prompt →](https://dashboard.manifest.build/prompt-python.md)

The prompt finds your entry point and stops to let you paste your key.

### Start with code

```sh
pip install mnfst
```

Run your app through the CLI, so Manifest loads before the first request:

```sh
export MNFST_KEY='your-project-key'
mnfst run uvicorn main:app
```

`mnfst run` prefixes any command — `mnfst run gunicorn app:app`, `mnfst run celery -A tasks worker`. Or call `manifest()` yourself, once at startup:

```python
from mnfst import manifest

manifest()  # Once, at startup.
# Keep making your API calls as usual.
```

Use the source-level call when there is no command to prefix: AWS Lambda, a notebook, or a start command owned by a host dashboard.

## Setup

1. Create a project in your [Manifest dashboard](https://dashboard.manifest.build) and copy its project key.
2. Set the key as an environment variable:

```sh
export MNFST_KEY='your-project-key'
```

Load the SDK with `mnfst run`, or call `manifest()` once at startup, before your first request. Self-healing is enabled by default in your project settings.

Check the install at any time with [`mnfst doctor`](docs/guide.md#verifying-the-installation).

## Try it

Send a request that would normally fail. Manifest catches it, repairs it, and retries:

```python
import httpx
from mnfst import manifest

manifest(on_heal=lambda e: print(f"Healed: {e.heal_status}, Response: {e.replay_status_code}"))

res = httpx.post(
    "https://api.example.com/orders",
    json={"limit": 500},  # Invalid? Manifest fixes it and retries.
)
print(res.status_code)  # See the 200 OK response.
```

Check your [Manifest dashboard](https://dashboard.manifest.build) to see all repairs and insights.

## More

[Configuration, limits & development](docs/guide.md) · [API contract](CONTRACT.md) · [Node.js SDK](https://github.com/mnfst/manifest-node) · [Website](https://manifest.build)
