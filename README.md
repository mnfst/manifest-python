# Manifest for Python

[![CI](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mnfst/manifest-python/actions/workflows/ci.yml)

Repair failed JSON API requests automatically. Works with `httpx` and `requests`, for everyday APIs and LLMs alike.

```python
from mnfst import manifest

manifest()
# Keep making your API calls as usual.
```

Your API rejects a request → Manifest finds a repair → the SDK retries once, locally.

## Setup

### 1. Install

Requires **Python 3.10+**:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install mnfst
```

On Windows, activate with `.venv\Scripts\activate` instead. The package and import name are **mnfst**. `httpx` is included; install `requests` separately if you use it.

### 2. Connect your project

Create a project in your Manifest dashboard and copy the project key shown during setup. In **Project Settings**, turn **Autofix** on to enable repairs.

```sh
export MNFST_KEY='your-project-key'
```

The SDK defaults to `https://api.manifest.build`. For a local app running on port 5310, also set:

```sh
export MNFST_URL='http://127.0.0.1:5310'
```

Your server must support the [SDK API contract](CONTRACT.md). The local app must already be running.

### 3. Initialize before your requests

Call `manifest()` once at startup. Save this as `example.py`, replacing the example endpoint and payload with your own:

```python
import httpx
from mnfst import manifest, flush

manifest(
    on_heal=lambda event: print(
        "[manifest]", event.heal_status, event.replay_status_code
    )
)

try:
    response = httpx.post(
        "https://api.example.com/orders",
        json={"limit": 500},
    )
    print(response.status_code, response.text)
finally:
    flush(timeout=5)
```

Run it with `python example.py`. For an API that rejects `limit: 500` and has a matching repair, Manifest can retry with a valid limit. Repairs depend on the API error and available patches.

The same initialization covers `httpx.AsyncClient` and `requests` calls using their standard transports.

## Check that it works

Send a JSON request that your test API rejects with **400, 404 or 422**. Check the failure in your project's dashboard and the `on_heal` callback for the repair result. A successful request alone does not contact Manifest. `flush()` lets a short script wait for outcome reports before exiting.

## What to expect

- **One retry.** Manifest returns a repair; the SDK sends the corrected request directly to your API.
- **Original error if healing is unavailable.** A heal call can add up to 60 seconds. If a retry returns an HTTP response, that response reaches your application.
- **Sync and async.** Standard `httpx` transports and `requests` adapters are covered process-wide. Custom transports and `aiohttp` are not intercepted.
- **Retry semantics still matter.** Use idempotency keys where needed; a repeated request can repeat side effects.

## Privacy

Manifest receives failed request URLs, headers, JSON bodies and error responses. Known credential fields are masked or withheld, but nested secrets, prompts and business data can still be sent. Enable it only for traffic you permit your Manifest server to process and store.

## More

[Configuration, limits & development](docs/guide.md) · [API contract](CONTRACT.md) · [Node.js SDK](https://github.com/mnfst/manifest-node)
