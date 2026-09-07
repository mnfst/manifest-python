# Manifest Python SDK

Manifest repairs eligible failed JSON API requests made through `httpx` (sync or async) and `requests`. It sends failure context to your Manifest project, applies the returned repair, and retries once using your original credentials.

```python
from mnfst import manifest

manifest(key="your-project-key")

# Your existing httpx / requests calls follow.
```

The distribution and import name are **mnfst**. The repository is **manifest-python**. There is no `autofix` module or compatibility alias.

## Install from source

Python 3.10 or later:

```sh
pip install git+https://github.com/mnfst/manifest-python.git
```

The package has not yet been released on PyPI. Install `requests` separately if you use it.

## Configuration

Call `manifest()` once at startup. Arguments override environment variables:

| Argument | Environment | Default |
| --- | --- | --- |
| `key` | `MNFST_KEY` | Missing key disables the SDK with a warning |
| `url` | `MNFST_URL` | `https://api.manifest.build` |
| `on_heal` | — | Optional callback receiving a `HealEvent` |

Use `url="http://127.0.0.1:5310"` with a local Manifest app. The hosted default requires a deployed, compatible app. Changing configuration after initialization requires a process restart.

```python
from mnfst import manifest, flush

manifest(on_heal=lambda event: print(event.heal_status, event.replay_status_code))
# Before a short-lived process exits:
flush(timeout=5)
```

Reports run in background threads. `flush` waits for outstanding reports within one total timeout; it does not guarantee delivery. Normal interpreter exit allows two seconds to flush. Failed or dropped reports emit warnings on the `mnfst` logger. Abrupt termination can lose reports.

## Behavior and limits

- Standard httpx transports and requests adapters are instrumented process-wide, including existing clients. Custom transports, aiohttp, browsers and other languages are not covered.
- Eligible HTTP failures are sent to Manifest; successful calls, authentication failures, rate limits and server errors pass through. A network failure before an HTTP response also passes through.
- Manifest selects repairs. The SDK retries at most once per captured failure. The original error response is returned if healing is unavailable, no repair can be applied, or the retry has a transport error.
- A successful streaming retry remains streamed. Error capture reads a bounded prefix and preserves the original response bytes for the caller. Error reads use the caller's read timeout; healing adds up to 60 seconds, and the retry uses the caller's timeout.
- The sync heal worker pool permits eight concurrent calls. Excess calls fail open. Timed-out workers can continue in the background within that bound. Outcome reporting permits 64 concurrent reports per reporter.
- Request JSON is limited to 256 KiB and depth 64. Multipart, binary, streamed, oversized and invalid JSON bodies travel as `null`; they are not generally repairable. Response metadata is limited to 64 KiB; truncated errors are reported without retry. Gzip and deflate error prefixes are decoded within that limit; unsupported content encodings provide no body evidence.
- Retries can repeat side effects. Use APIs with safe retry semantics and caller-managed idempotency keys. Existing credentials and idempotency headers are retained unless explicitly changed by the repair. URL repairs must stay on the same origin.

## Data sent to Manifest

Failed request URLs, headers, JSON bodies and error responses are sent to the configured server. Known credential names in query parameters and headers are masked. Credential-named **top-level** request body fields are withheld and restored for the retry.

This is not general data-loss prevention: nested fields, arbitrary secret names, personal data, prompts and response bodies may still contain sensitive content. Only enable it for traffic you permit Manifest to process and store. The server does not receive the original credential values masked by the SDK.

## Development

```sh
pip install -e '.[dev]'
pytest -q
# Optional: point only at a disposable app (creates a test customer/project).
MNFST_TEST_APP_URL=http://127.0.0.1:5310 pytest -q tests/test_live_app.py
```

CI tests Python 3.10, 3.13 and 3.14 and builds the wheel. The live app test runs locally because the app repository is private; cross-repository CI needs separate checkout credentials. Validated against app commit `9ea359279577f99e4058b7600c75889b1a2c5881`.

See [CONTRACT.md](CONTRACT.md) for the wire protocol. Transport failures require the app's explicit `failure` outcome support; they must never be reported as HTTP success.

Adapted from [guillaumegay13/autofix-python](https://github.com/guillaumegay13/autofix-python), source commit `9a82d8235a037392826f982626f49422f3828213`.
