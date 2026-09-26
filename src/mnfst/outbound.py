"""Auto-instrumentation of outbound HTTP clients (httpx, httpx2, requests and aiohttp).
Patches at TRANSPORT level: one hook per client library, so every client —
including ones created before manifest() ran — is covered, and redirects,
retries and streaming stay the client's business. The internal_call guard
keeps the SDK's own Phoenix calls out of the loop.

Per captured failure (CONTRACT §1): capture → heal → apply the server's
healedRequest (url / headers / body) → retry once → report the outcome.
"""
from __future__ import annotations

import atexit
import platform
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

import anyio
import httpx

from .bodies import content_type_of, encode_request_body, parse_request_body
from .config import Config
from .url_filter import is_excluded
from .gate import should_capture
from .heal_api import NOT_ATTEMPTED, NOT_SENT, AsyncHealApi, HealApi, HealEvent, internal_call
from .merge import merge_healed_body
from .response_capture import capture_aiohttp, capture_httpx, capture_httpx_async, capture_requests
from .tracking import CallBuffer
from .wire import capped_response_body, heal_payload, safe_error_text, tracked_url, traveling_body

# Methods a retry may carry no body for.
_BODYLESS = ("GET", "HEAD", "DELETE", "OPTIONS")

_installed = False
_installed_config: Optional[Config] = None
_originals: dict = {}
_tracker: Optional[CallBuffer] = None
EXIT_FLUSH_SECONDS = 2.0


def installed_config() -> Optional[Config]:
    """The config the process is instrumented with, or None. Patching is
    process-global and one-shot, so a second manifest() with different options
    cannot take effect — the entry point warns instead of silently ignoring."""
    return _installed_config


# --- tracked calls -------------------------------------------------------------

def _track(method: str, url: Any, status_code: int, started_at: float, elapsed_ms: int) -> None:
    """Record a call that is not being healed. An in-memory append: never
    blocks on the network, never reads the response, never raises."""
    tracker = _tracker
    if tracker is None:
        return
    try:
        reported = tracked_url(str(url))
        verb = (method or "GET").upper()
        # The server refuses a whole batch over one out-of-range record.
        if (reported is None or len(reported) > 4096 or len(verb) > 16
                or not 100 <= int(status_code) <= 599):
            return
        tracker.record({
            "traceId": uuid.uuid4().hex, "method": verb, "url": reported,
            "statusCode": int(status_code), "responseTimeMs": int(elapsed_ms),
            "occurredAt": datetime.fromtimestamp(started_at, timezone.utc).isoformat(),
        })
    except Exception:
        pass


def _excluded(config: Config, url: Any) -> bool:
    """A call kept out of Manifest by the allowlist or denylist: never healed, never tracked."""
    try:
        return is_excluded(config.allowlist, config.denylist, str(url))
    except Exception:
        return False


def _flush_at_exit() -> None:
    tracker = _tracker
    if tracker is not None:
        try:
            tracker.flush(EXIT_FLUSH_SECONDS)
        except Exception:
            pass


# --- the loop, client-agnostic ---------------------------------------------

class _Capture:
    """Everything the SDK knows about one failing call."""

    def __init__(self, method: str, url: str, headers: Mapping[str, Any],
                 content: Optional[bytes], status_code: int, raw_response: bytes,
                 response_time_ms: int, incomplete: bool = False):
        self.method = method or "GET"
        self.url = url
        self.headers = headers
        self.content = content  # None = the body could not be read (streamed)
        self.content_type = content_type_of(headers)
        # replayable is False when bytes were sent that could not be parsed
        # into a structure we can re-encode -- reported, never retried.
        self.body, self.replayable = parse_request_body(content, self.content_type)
        response_body, truncated = capped_response_body(raw_response)
        self.payload = heal_payload(
            trace_id=uuid.uuid4().hex, method=self.method, url=url, headers=headers,
            body=self.body, status_code=status_code, response_body=response_body,
            truncated=truncated or incomplete, response_time_ms=response_time_ms)
        self.started = time.monotonic()


class _Retry:
    """A rebuilt request: the original with the server's changes applied."""

    def __init__(self, url: str, headers: dict, content: Optional[bytes]):
        self.url = url
        self.headers = headers
        self.content = content


def _healed_request(result: Optional[dict]) -> Optional[dict]:
    if not result or result.get("status") not in ("patched", "unverified"):
        return None
    healed = result.get("healedRequest")
    if not isinstance(healed, dict) or not any(k in healed for k in ("url", "headers", "body")):
        return None
    return healed


def _apply(capture: _Capture, healed: dict) -> Optional[_Retry]:
    """CONTRACT §4: url replaces; headers set/replace, null removes; body
    merges (objects) or replaces. Returns None when the retry cannot be built
    (the original body was unreadable or unparseable, or nothing to send)."""
    url = healed.get("url") or capture.url
    if not _same_origin(url, capture.url):
        return None  # a URL heal may move the path, never the host: the
                     # retry carries the caller's credentials (CONTRACT §4)
    headers = {k: v for k, v in capture.headers.items() if k.lower() != "content-length"}
    for name, value in (healed.get("headers") or {}).items():
        headers = {k: v for k, v in headers.items() if k.lower() != str(name).lower()}
        if value is not None:
            headers[str(name)] = str(value)
    if "body" in healed:
        if not capture.replayable:
            return None  # we could not read what was sent; never invent a replay
        merged = merge_healed_body(capture.body, traveling_body(capture.body), healed["body"])
        if merged is None and capture.method not in _BODYLESS:
            return None
        # A form request is replayed as a form, not as JSON under its own
        # content type; an unencodable body raises and closes the attempt.
        # No body survives the merge on a bodyless method: send none, rather
        # than encoding None into a literal b"null" that CDNs reject.
        content: Optional[bytes] = (None if merged is None else
                                    encode_request_body(merged, capture.content_type))
    else:
        content = capture.content
        if content is None and capture.method not in _BODYLESS:
            return None
    return _Retry(url, headers, content)


def _same_origin(url: str, original: str) -> bool:
    try:
        a, b = urlsplit(str(url)), urlsplit(str(original))
    except Exception:
        return False
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def _report(api, result: Optional[dict], retry_status: int,
            error: Any = None, truncated: bool = False) -> None:
    attempt_id = (result or {}).get("healAttemptId")
    if attempt_id:
        try:
            api.report_outcome(attempt_id, retry_status, error, truncated)
        except Exception:
            pass


def _emit(config: Config, capture: _Capture, result: Optional[dict],
          retry_status: Optional[int], replay_attempted: bool = True) -> None:
    if not config.on_heal:
        return
    try:
        status = (result or {}).get("status") or "heal_unreachable"
        if (replay_attempted and result and retry_status is None
                and status in ("patched", "unverified")):
            status = "replay_failed"
        config.on_heal(HealEvent(
            url=capture.payload["request"]["url"],
            status_code=capture.payload["response"]["statusCode"],
            heal_status=status, replay_status_code=retry_status,
            heal_ms=int((time.monotonic() - capture.started) * 1000),
            operations=(result or {}).get("operations")))
    except Exception:
        pass


def _decide(config: Config, api, capture: _Capture, result: Optional[dict]) -> Optional[_Retry]:
    """Turn a heal result into a retry, or close the attempt and return None."""
    healed = None if capture.payload["response"]["truncated"] else _healed_request(result)
    try:
        retry = _apply(capture, healed) if healed else None
    except Exception:
        retry = None
    if retry is None:
        _report(api, result, 0, NOT_ATTEMPTED)
        _emit(config, capture, result, None, replay_attempted=False)
    return retry


# --- httpx and httpx2 ----------------------------------------------------------
# httpx2 is the same API published under a second import name; both are
# patched when present, so a client from either package is covered.

def _httpx_modules() -> list:
    modules = [httpx]
    try:
        import httpx2
    except ImportError:
        return modules
    modules.append(httpx2)
    return modules


def _safe_request_content(request) -> Optional[bytes]:
    """A streamed or iterator request body raises RequestNotRead on `.content`.
    Capture without it; a retry then needs the server to supply a body."""
    try:
        content = request.content
    except Exception:
        return None
    return content if isinstance(content, bytes) else None


def _rebuild(request, retry: _Retry, mod=httpx):
    # Extensions carry the per-request timeout (and the caller's trace hooks);
    # dropping them would silently retry under the client default.
    return mod.Request(request.method, retry.url, headers=retry.headers,
                       content=retry.content, extensions=dict(request.extensions))


def install_outbound(config: Config, heal_api: Optional[HealApi] = None,
                     async_heal_api: Optional[AsyncHealApi] = None) -> None:
    global _installed, _installed_config, _tracker
    if _installed:
        return
    sync_api = heal_api or HealApi(config)
    async_api = async_heal_api or AsyncHealApi(config)
    _installed = True
    _installed_config = config
    # Every call that is not healed, any status, as metadata. Sync and async
    # clients share one buffer; its worker sends through the sync client.
    _tracker = CallBuffer(sync_api.send_requests)
    atexit.unregister(_flush_at_exit)  # once, however many times install runs
    atexit.register(_flush_at_exit)
    for mod in _httpx_modules():
        _install_httpx(mod, config, sync_api, async_api)
    install_requests(config, sync_api)
    install_aiohttp(config, async_api)
    # Announce the install, so that silence stops being ambiguous: a healthy
    # app and a broken one are otherwise the same nothing on the dashboard.
    # Once per process, fire-and-forget — it must never delay startup, and a
    # failure is never warned about (the quiet dashboard IS the signal).
    sync_api.hello(f"python-{platform.python_version()}")


def _install_httpx(mod, config: Config, sync_api: HealApi, async_api: AsyncHealApi) -> None:
    sync_key, async_key = f"{mod.__name__}_sync", f"{mod.__name__}_async"
    _originals[sync_key] = mod.HTTPTransport.handle_request
    _originals[async_key] = mod.AsyncHTTPTransport.handle_async_request

    def patched_sync(self, request):
        original = _originals[sync_key]
        started_at = time.time()
        started = time.monotonic()
        response = original(self, request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if internal_call.get() or _excluded(config, request.url):
            return response
        try:
            # Status decides capture, before the response body is touched — a
            # successful streamed response is never read here.
            if not should_capture(response.status_code) or not sync_api.healing_enabled():
                _track(request.method, request.url, response.status_code, started_at, elapsed_ms)
                return response
            try:
                response, raw = capture_httpx(response, mod)
            except Exception:
                return response
            capture = _Capture(request.method, str(request.url), request.headers,
                               _safe_request_content(request), response.status_code,
                               raw, elapsed_ms, response.extensions.get("mnfst_capture_incomplete", False))
            result = sync_api.heal(capture.payload)
            if result is NOT_SENT:  # every heal slot busy: track it, never lose it
                _track(request.method, request.url, response.status_code, started_at, elapsed_ms)
                return response
            retry = _decide(config, sync_api, capture, result)
            if retry is None:
                return response
            try:
                retried = original(self, _rebuild(request, retry, mod))
                retry_body, truncated = None, False
                if retried.status_code >= 400:
                    retried, raw = capture_httpx(retried, mod)
                    retry_body, truncated = capped_response_body(raw)
                    truncated = truncated or retried.extensions.get("mnfst_capture_incomplete", False)
            except Exception as exc:
                _report(sync_api, result, 0, safe_error_text(exc))
                _emit(config, capture, result, None)
                return response
            _report(sync_api, result, retried.status_code, retry_body, truncated)
            _emit(config, capture, result, retried.status_code)
            try:
                response.close()
            except Exception:
                pass
            return retried
        except Exception:
            return response  # nothing in here may reach the caller

    async def patched_async(self, request):
        original = _originals[async_key]
        started_at = time.time()
        started = time.monotonic()
        response = await original(self, request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if internal_call.get() or _excluded(config, request.url):
            return response
        try:
            if not should_capture(response.status_code) or not async_api.healing_enabled():
                _track(request.method, request.url, response.status_code, started_at, elapsed_ms)
                return response
            try:
                response, raw = await capture_httpx_async(response, mod)
            except Exception:
                return response
            capture = _Capture(request.method, str(request.url), request.headers,
                               _safe_request_content(request), response.status_code,
                               raw, elapsed_ms, response.extensions.get("mnfst_capture_incomplete", False))
            result = await async_api.heal(capture.payload)
            retry = _decide(config, async_api, capture, result)
            if retry is None:
                return response
            try:
                retried = await original(self, _rebuild(request, retry, mod))
                retry_body, truncated = None, False
                if retried.status_code >= 400:
                    retried, raw = await capture_httpx_async(retried, mod)
                    retry_body, truncated = capped_response_body(raw)
                    truncated = truncated or retried.extensions.get("mnfst_capture_incomplete", False)
            except Exception as exc:
                _report(async_api, result, 0, safe_error_text(exc))
                _emit(config, capture, result, None)
                return response
            _report(async_api, result, retried.status_code, retry_body, truncated)
            _emit(config, capture, result, retried.status_code)
            try:
                await response.aclose()
            except Exception:
                pass
            return retried
        except Exception:
            return response

    mod.HTTPTransport.handle_request = patched_sync
    mod.AsyncHTTPTransport.handle_async_request = patched_async


def uninstall_outbound() -> None:
    global _installed, _installed_config, _tracker
    if not _installed:
        return
    _tracker = None
    for mod in _httpx_modules():
        sync_key, async_key = f"{mod.__name__}_sync", f"{mod.__name__}_async"
        if sync_key in _originals:
            mod.HTTPTransport.handle_request = _originals.pop(sync_key)
            mod.AsyncHTTPTransport.handle_async_request = _originals.pop(async_key)
    uninstall_requests()
    uninstall_aiohttp()
    _installed = False
    _installed_config = None


# --- requests ----------------------------------------------------------------

def install_requests(config: Config, heal_api: HealApi) -> None:
    try:
        import requests.adapters
    except ImportError:
        return  # requests is not a runtime dependency; nothing to patch
    if "requests_send" in _originals:
        return

    _originals["requests_send"] = requests.adapters.HTTPAdapter.send

    def patched_send(self, request, stream=False, timeout=None, verify=True,
                     cert=None, proxies=None):
        original = _originals["requests_send"]
        started_at = time.time()
        started = time.monotonic()
        response = original(self, request, stream=stream, timeout=timeout,
                            verify=verify, cert=cert, proxies=proxies)
        # `.elapsed` is only stamped by Session.send, after the adapter
        # returns — so time the call here instead.
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if internal_call.get() or _excluded(config, request.url):
            return response
        try:
            if not should_capture(response.status_code) or not heal_api.healing_enabled():
                _track(request.method, request.url, response.status_code, started_at, elapsed_ms)
                return response
            try:
                response, raw = capture_requests(response)
            except Exception:
                return response
            # A generator/file body is not bytes or str — capture without it,
            # so a streamed upload is never consumed.
            body = request.body
            content = body if isinstance(body, bytes) else \
                (body.encode() if isinstance(body, str) else None)
            capture = _Capture(request.method, request.url, request.headers, content,
                               response.status_code, raw, elapsed_ms, getattr(response, "_mnfst_capture_incomplete", False))
            result = heal_api.heal(capture.payload)
            if result is NOT_SENT:  # every heal slot busy: track it, never lose it
                _track(request.method, request.url, response.status_code, started_at, elapsed_ms)
                return response
            retry = _decide(config, heal_api, capture, result)
            if retry is None:
                return response
            try:
                rebuilt = request.copy()
                rebuilt.url = retry.url
                rebuilt.headers.clear()
                rebuilt.headers.update(retry.headers)
                rebuilt.body = retry.content
                if retry.content is not None:
                    rebuilt.headers["content-length"] = str(len(retry.content))
                retried = original(self, rebuilt, stream=stream, timeout=timeout,
                                   verify=verify, cert=cert, proxies=proxies)
                retry_body, truncated = None, False
                if retried.status_code >= 400:
                    retried, raw = capture_requests(retried)
                    retry_body, truncated = capped_response_body(raw)
                    truncated = truncated or getattr(retried, "_mnfst_capture_incomplete", False)
            except Exception as exc:
                _report(heal_api, result, 0, safe_error_text(exc))
                _emit(config, capture, result, None)
                return response
            _report(heal_api, result, retried.status_code, retry_body, truncated)
            _emit(config, capture, result, retried.status_code)
            try:
                response.close()
            except Exception:
                pass
            return retried
        except Exception:
            return response

    requests.adapters.HTTPAdapter.send = patched_send


def uninstall_requests() -> None:
    if "requests_send" not in _originals:
        return
    import requests.adapters
    requests.adapters.HTTPAdapter.send = _originals.pop("requests_send")


# --- aiohttp -------------------------------------------------------------------
# aiohttp has no transport to swap; its client middleware chain (3.13+) is
# the same seam. The SDK's middleware is appended innermost on every request,
# so it sees each hop on the wire, the caller's own middlewares and
# raise_for_status see the healed response, and sessions made before
# install are covered.

def install_aiohttp(config: Config, heal_api: AsyncHealApi) -> None:
    try:
        import aiohttp
        from aiohttp.streams import StreamReader
    except ImportError:
        return  # aiohttp is not a runtime dependency; nothing to patch
    if not (hasattr(StreamReader, "unread_data")
            and "total_compressed_bytes" in getattr(StreamReader, "__slots__", ())):
        return  # before 3.13 a capture cannot tell a decompressed body from a raw one
    if "aiohttp_request" in _originals:
        return

    _originals["aiohttp_request"] = aiohttp.ClientSession._request

    async def patched_request(self, *args, **kwargs):
        try:
            # A per-request `middlewares=` replaces the session's; ours rides either.
            chain = kwargs.get("middlewares")
            if chain is None:
                chain = getattr(self, "_middlewares", ())
            middleware = _aiohttp_middleware(config, heal_api, _aiohttp_deadline(self, kwargs))
            kwargs["middlewares"] = (*chain, middleware)
        except Exception:
            pass  # the call goes out unobserved rather than not at all
        return await _originals["aiohttp_request"](self, *args, **kwargs)

    aiohttp.ClientSession._request = patched_request


def uninstall_aiohttp() -> None:
    if "aiohttp_request" not in _originals:
        return
    import aiohttp
    aiohttp.ClientSession._request = _originals.pop("aiohttp_request")


def _aiohttp_deadline(session, kwargs: dict) -> Optional[float]:
    """When aiohttp's total timeout cancels this call, or None. That timeout
    spans the middleware chain, so healing has to fit inside it."""
    import aiohttp
    timeout = kwargs.get("timeout", session.timeout)
    if isinstance(timeout, aiohttp.ClientTimeout):
        total = timeout.total
    elif timeout is None or isinstance(timeout, (int, float)):
        total = timeout
    else:
        total = session.timeout.total  # aiohttp's own "not given" sentinel
    return anyio.current_time() + total if total else None


async def _heal_in_time(heal_api: AsyncHealApi, payload: dict,
                        deadline: Optional[float]) -> Optional[dict]:
    """Heal within half of what is left before the deadline, keeping the
    other half for the retry. Out of time is treated as no answer."""
    if deadline is None:
        return await heal_api.heal(payload)
    with anyio.move_on_after((deadline - anyio.current_time()) / 2):
        return await heal_api.heal(payload)
    return None


async def _aiohttp_request_content(request) -> Optional[bytes]:
    """In-memory bodies (bytes, str, json=, form fields) are read; files,
    multipart and async iterators are left unconsumed and captured as None."""
    from aiohttp.payload import BytesPayload
    body = request.body
    if isinstance(body, bytes):
        return body  # b"": no body at all
    if isinstance(body, BytesPayload):
        return await body.as_bytes()
    return None


async def _retarget(request, retry: _Retry) -> None:
    """Apply a retry to the request in place, as aiohttp middlewares do."""
    from multidict import CIMultiDict
    from yarl import URL
    request.url = URL(retry.url)  # same origin: _apply refuses anything else
    request.headers = CIMultiDict(retry.headers)
    await request.update_body(retry.content)


def _aiohttp_middleware(config: Config, heal_api: AsyncHealApi, deadline: Optional[float]):
    from multidict import CIMultiDict

    async def mnfst_middleware(request, handler):
        started_at = time.time()
        started = time.monotonic()
        response = await handler(request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if internal_call.get() or _excluded(config, request.url):
            return response
        try:
            # A failed websocket handshake is tracked, never replayed:
            # ws_connect owns that connection.
            if (not should_capture(response.status) or not heal_api.healing_enabled()
                    or "upgrade" in request.headers):
                _track(request.method, request.url, response.status, started_at, elapsed_ms)
                return response
            try:
                raw, incomplete = await capture_aiohttp(response)
            except Exception:
                return response
            capture = _Capture(request.method, str(request.url), CIMultiDict(request.headers),
                               await _aiohttp_request_content(request), response.status,
                               raw, elapsed_ms, incomplete)
            result = await _heal_in_time(heal_api, capture.payload, deadline)
            retry = _decide(config, heal_api, capture, result)
            if retry is None:
                return response
            try:
                await _retarget(request, retry)
                retried = await handler(request)
                retry_body, truncated = None, False
                if retried.status >= 400:
                    raw, incomplete = await capture_aiohttp(retried)
                    retry_body, truncated = capped_response_body(raw)
                    truncated = truncated or incomplete
            except Exception as exc:
                _report(heal_api, result, 0, safe_error_text(exc))
                _emit(config, capture, result, None)
                return response
            _report(heal_api, result, retried.status, retry_body, truncated)
            _emit(config, capture, result, retried.status)
            try:
                response.release()
            except Exception:
                pass
            return retried
        except Exception:
            return response  # nothing in here may reach the caller

    return mnfst_middleware
