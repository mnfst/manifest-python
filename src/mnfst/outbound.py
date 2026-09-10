"""Auto-instrumentation of outbound HTTP clients (httpx and requests).
Patches at TRANSPORT level: one hook per client library, so every client —
including ones created before manifest() ran — is covered, and redirects,
retries and streaming stay the client's business. The internal_call guard
keeps the SDK's own Phoenix calls out of the loop.

Per captured failure (CONTRACT §1): capture → heal → apply the server's
healedRequest (url / headers / body) → retry once → report the outcome.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

import httpx

from .bodies import content_type_of, encode_request_body, parse_request_body
from .config import Config
from .gate import should_capture
from .heal_api import NOT_ATTEMPTED, AsyncHealApi, HealApi, HealEvent, internal_call
from .merge import merge_healed_body
from .response_capture import capture_httpx, capture_httpx_async, capture_requests
from .wire import capped_response_body, heal_payload, safe_error_text, traveling_body

# Methods a retry may carry no body for.
_BODYLESS = ("GET", "HEAD", "DELETE", "OPTIONS")

_installed = False
_installed_config: Optional[Config] = None
_originals: dict = {}
_reporters: list = []


def flush(timeout: float = 5.0) -> None:
    """Wait up to timeout seconds total for outstanding outcome reports."""
    deadline = time.monotonic() + max(0, timeout)
    for api in _reporters:
        api.join_pending_reports(max(0, deadline - time.monotonic()))


def installed_config() -> Optional[Config]:
    """The config the process is instrumented with, or None. Patching is
    process-global and one-shot, so a second manifest() with different options
    cannot take effect — the entry point warns instead of silently ignoring."""
    return _installed_config


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
        content: Optional[bytes] = encode_request_body(merged, capture.content_type)
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


# --- httpx -------------------------------------------------------------------

def _safe_request_content(request: httpx.Request) -> Optional[bytes]:
    """A streamed or iterator request body raises RequestNotRead on `.content`.
    Capture without it; a retry then needs the server to supply a body."""
    try:
        content = request.content
    except Exception:
        return None
    return content if isinstance(content, bytes) else None


def _rebuild(request: httpx.Request, retry: _Retry) -> httpx.Request:
    # Extensions carry the per-request timeout (and the caller's trace hooks);
    # dropping them would silently retry under the client default.
    return httpx.Request(request.method, retry.url, headers=retry.headers,
                         content=retry.content, extensions=dict(request.extensions))


def install_outbound(config: Config, heal_api: Optional[HealApi] = None,
                     async_heal_api: Optional[AsyncHealApi] = None) -> None:
    global _installed, _installed_config
    if _installed:
        return
    sync_api = heal_api or HealApi(config)
    async_api = async_heal_api or AsyncHealApi(config)
    _installed = True
    _installed_config = config
    _reporters[:] = [sync_api, async_api]

    _originals["httpx_sync"] = httpx.HTTPTransport.handle_request
    _originals["httpx_async"] = httpx.AsyncHTTPTransport.handle_async_request

    def patched_sync(self, request: httpx.Request) -> httpx.Response:
        original = _originals["httpx_sync"]
        started = time.monotonic()
        response = original(self, request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if internal_call.get():
            return response
        try:
            # Status decides capture, before the response body is touched — a
            # successful streamed response is never read here.
            if not should_capture(response.status_code) or not sync_api.healing_enabled():
                return response
            try:
                response, raw = capture_httpx(response)
            except Exception:
                return response
            capture = _Capture(request.method, str(request.url), request.headers,
                               _safe_request_content(request), response.status_code,
                               raw, elapsed_ms, response.extensions.get("mnfst_capture_incomplete", False))
            result = sync_api.heal(capture.payload)
            retry = _decide(config, sync_api, capture, result)
            if retry is None:
                return response
            try:
                retried = original(self, _rebuild(request, retry))
                retry_body, truncated = None, False
                if retried.status_code >= 400:
                    retried, raw = capture_httpx(retried)
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

    async def patched_async(self, request: httpx.Request) -> httpx.Response:
        original = _originals["httpx_async"]
        started = time.monotonic()
        response = await original(self, request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if internal_call.get():
            return response
        try:
            if not should_capture(response.status_code) or not async_api.healing_enabled():
                return response
            try:
                response, raw = await capture_httpx_async(response)
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
                retried = await original(self, _rebuild(request, retry))
                retry_body, truncated = None, False
                if retried.status_code >= 400:
                    retried, raw = await capture_httpx_async(retried)
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

    httpx.HTTPTransport.handle_request = patched_sync
    httpx.AsyncHTTPTransport.handle_async_request = patched_async
    install_requests(config, sync_api)


def uninstall_outbound() -> None:
    global _installed, _installed_config
    if not _installed:
        return
    httpx.HTTPTransport.handle_request = _originals["httpx_sync"]
    httpx.AsyncHTTPTransport.handle_async_request = _originals["httpx_async"]
    uninstall_requests()
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
        started = time.monotonic()
        response = original(self, request, stream=stream, timeout=timeout,
                            verify=verify, cert=cert, proxies=proxies)
        # `.elapsed` is only stamped by Session.send, after the adapter
        # returns — so time the call here instead.
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if internal_call.get():
            return response
        try:
            if not should_capture(response.status_code) or not heal_api.healing_enabled():
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
