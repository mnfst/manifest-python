# SDK guide

[← Quick start](../README.md)

## Installation

Requires Python 3.10+. In a virtual environment:

```sh
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install mnfst
```

The package and import name are both `mnfst`. `httpx` is installed as a dependency; install `requests` separately if you use it.

## Configuration

Call `manifest()` once at startup. Arguments override environment variables:

| Argument | Environment | Default |
| --- | --- | --- |
| `key` | `MNFST_KEY` | Missing key disables the SDK with a warning |
| `url` | `MNFST_URL` | `https://api.manifest.build` |
| `on_heal` | — | Optional callback receiving a `HealEvent` |
| `allowlist` | `MNFST_ALLOWLIST` | Every call eligible ([entries](../README.md#choosing-which-calls-reach-manifest)) |
| `denylist` | `MNFST_DENYLIST` | None excluded |

Use `url="http://127.0.0.1:5310"` with a local Manifest app, which must already be running and support the [SDK API contract](../CONTRACT.md). The hosted default requires a deployed, compatible app. Changing configuration after initialization requires a process restart.

```python
from mnfst import manifest

manifest(on_heal=lambda event: print(event.heal_status, event.replay_status_code))
```

Outcome reports run in background threads and are best effort. Failed or dropped reports emit warnings on the `mnfst` logger. Abrupt termination can lose reports.

## Running your app

`pip install mnfst` also installs an `mnfst` command. Prefix your start command with `mnfst run` and the SDK loads before the app's first request:

```sh
mnfst run uvicorn main:app
mnfst run gunicorn app:app
mnfst run celery -A tasks worker
```

`mnfst run` requires `MNFST_KEY`. Without it the command still runs, uninstrumented, with one warning. It prepends a bootstrap directory to `PYTHONPATH` and execs the real command, so the interpreter imports the SDK at startup — the same preload as `ddtrace-run`. The prefix applies to the command's child processes too, and it takes precedence over any other `sitecustomize` on `PYTHONPATH`.

Use the source-level call when there is no command to prefix: AWS Lambda, a notebook, or a start command owned by a host dashboard you cannot edit. It is also visible in git, which a dashboard setting is not.

## Verifying the installation

Run the built-in checks:

```sh
mnfst doctor
```

```
  ✅ SDK installed          mnfst 1.0.0
  ✅ MNFST_KEY set          mnfst_pr…DZDw
  ✅ Key valid              project "My project"
  ✅ Preload active         the SDK runs in this process
```

It verifies the SDK imports, the key is readable (never printed in full), the key is accepted by a single authenticated round trip, and `manifest()` ran in the process that makes the calls. A rejected or unreachable key is otherwise indistinguishable from a healthy install: both are silence. Run it through `mnfst run` — `mnfst run mnfst doctor` — for the preload check to see the same startup path as your app. The command exits non-zero if any check fails.

Then send a JSON or `application/x-www-form-urlencoded` request that your test API rejects with 400, 404, 422 or any other request-side 4xx. The failure appears in your [project's dashboard](https://dashboard.manifest.build), and the `on_heal` callback reports the repair result. A successful request alone does not contact Manifest. Outcome reports are asynchronous, so a short-lived script may exit before the report is delivered.

## Behavior and limits

- Standard httpx and httpx2 transports and requests adapters are instrumented process-wide, including existing clients. Custom transports, aiohttp, browsers and other languages are not covered.
- Any 4xx is sent to Manifest for healing except 401, 402, 403 and 429. Successful calls, those four, and every 5xx are never healed: authentication, billing, rate limiting and server faults are not repaired by editing the request. They are tracked as metadata only (see "Data sent to Manifest"). A network failure before an HTTP response passes through untracked.
- Manifest selects repairs. The SDK retries at most once per captured failure. The original error response is returned if healing is unavailable, no repair can be applied, or the retry has a transport error.
- A successful streaming retry remains streamed. Error capture reads a bounded prefix and preserves the original response bytes for the caller. Error reads use the caller's read timeout; healing adds up to 60 seconds, and the retry uses the caller's timeout.
- The sync heal worker pool permits eight concurrent calls. Excess calls fail open. Timed-out workers can continue in the background within that bound. Outcome reporting permits 64 concurrent reports per reporter.
- JSON and `application/x-www-form-urlencoded` request bodies are parsed, including nested form keys such as `line_items[0][price]`. Both are limited to 256 KiB and depth 64. Multipart, binary, streamed, oversized and invalid bodies travel as `null`; they are not generally repairable. A form retry is re-encoded from the parsed structure, so a repeated key such as `expand=a&expand=b` returns as `expand[0]=a&expand[1]=b`. Response metadata is limited to 64 KiB; truncated errors are reported without retry. Gzip and deflate error prefixes are decoded within that limit; unsupported content encodings provide no body evidence.
- Retries can repeat side effects. Use APIs with safe retry semantics and caller-managed idempotency keys. Existing credentials and idempotency headers are retained unless explicitly changed by the repair. URL repairs must stay on the same origin.

## Data sent to Manifest

**Every call (metadata only).** For each call that is not healed, whatever its status, the SDK sends its method, URL without the query string, userinfo or fragment, status code, response time and time of the call. No headers and no bodies. Calls are batched and sent from a background thread, at most once per second; recording one never slows the call. Calls still buffered when a serverless runtime freezes the process can be lost.

**Healable failures (full capture).** Failed request URLs, headers, JSON or form-urlencoded bodies, and error responses are sent to the configured server. Known credential names in query parameters and headers are masked. Credential-named **top-level** request body fields are withheld and restored for the retry.

This is not general data-loss prevention: nested fields, arbitrary secret names, personal data, prompts and response bodies may still contain sensitive content. Only enable it for traffic you permit Manifest to process and store. The server does not receive the original credential values masked by the SDK.

## Development

```sh
pip install -e '.[dev]'
pytest -q
# Optional: point only at a disposable app (creates a test customer/project).
MNFST_TEST_APP_URL=http://127.0.0.1:5310 pytest -q tests/test_live_app.py
```

CI tests Python 3.10, 3.13 and 3.14 and builds the wheel. The live app test runs locally because the app repository is private; cross-repository CI needs separate checkout credentials. Validated against app commit `9ea359279577f99e4058b7600c75889b1a2c5881`.

Use conventional commit titles for pull requests. `feat:` prepares a minor version, `fix:` prepares a patch version, and `!` or `BREAKING CHANGE:` prepares a major version. GitHub keeps one rolling `chore: release …` pull request; PyPI publishing starts only when that release pull request is merged.

See [CONTRACT.md](../CONTRACT.md) for the wire protocol. Transport failures require the app's explicit `failure` outcome support; they must never be reported as HTTP success.

Adapted from [guillaumegay13/autofix-python](https://github.com/guillaumegay13/autofix-python), source commit `9a82d8235a037392826f982626f49422f3828213`.
