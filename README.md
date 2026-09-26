<div align="center">

![Manifest SDK Architecture](./docs/github-sdk.png)

# Manifest for Python

**The API resilience layer for your Python apps.**

[![CI](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/mnfst?label=PyPI)](https://pypi.org/project/mnfst/)
[![PyPI downloads](https://img.shields.io/pypi/dm/mnfst?label=PyPI%20downloads)](https://pypi.org/project/mnfst/)

</div>

## What is Manifest

Manifest is the API resilience layer for your apps and agents. It works with every API they call: external services, your internal APIs and MCP tools.

* 🗺️ **See every API your app depends on**, and how reliable each one is.
* 🎯 **Repair failed API requests on the fly**, so your app keeps working.
* 🛠️ **Know what to fix in your code**, with a prompt for your coding agent.

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

1. Create a project in your [Manifest dashboard](https://dashboard.manifest.build) and copy its project key.

2. Install the SDK:

   ```sh
   pip install mnfst
   ```

3. Set your key in the environment of your app, or in the project's `.env` file:

   ```sh
   export MNFST_KEY='your-project-key'
   ```

4. Run your app through the CLI, so Manifest loads before the first request:

   ```sh
   mnfst run uvicorn main:app
   ```

   `mnfst run` goes in front of any start command, such as `mnfst run gunicorn app:app` or `mnfst run celery -A tasks worker`. Where there is no command to prefix (AWS Lambda, a notebook, or a start command owned by a host dashboard), call `manifest()` yourself, once at startup, before your first request:

   ```python
   from mnfst import manifest

   manifest()  # Once, at startup.
   # Keep making your API calls as usual.
   ```

5. Check the install at any time with [`mnfst doctor`](docs/guide.md#verifying-the-installation).

## Try it

Send a request that fails with a 4xx error, such as a value the API rejects:

```python
import httpx
from mnfst import manifest

manifest(on_heal=lambda e: print(f"[manifest] {e.heal_status} {e.replay_status_code}"))

res = httpx.post(
    "https://api.example.com/orders",
    json={"limit": 500},  # rejected by the API
)
print(res.status_code)
```

The failed request appears in your [Manifest dashboard](https://dashboard.manifest.build), grouped with others like it in an issue. Once Manifest has a patch for that error, the next request that fails the same way is repaired and retried: `on_heal` reports `patched` or `unverified` with the retry's status code, and your app receives the answer to the retry.

## More

[Documentation](https://docs.manifest.build) · [Configuration, limits & development](docs/guide.md) · [API contract](CONTRACT.md) · [Node.js SDK](https://github.com/mnfst/manifest-node) · [PHP SDK](https://github.com/mnfst/manifest-php) · [Website](https://manifest.build)
