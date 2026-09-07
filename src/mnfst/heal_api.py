"""The SDK's own calls to Phoenix. Everything here fails soft: a heal that
cannot complete returns None and the caller serves the original response."""
from __future__ import annotations

import contextvars
import threading
import queue
import logging
import anyio
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .config import HEAL_TIMEOUT_SECONDS, Config
from .version import VERSION
from .gate import bounded_json

logger = logging.getLogger("mnfst")
_HEAL_SLOTS = threading.BoundedSemaphore(8)
MAX_HEAL_RESPONSE = 1_048_576

def _bounded_call(call):
    if not _HEAL_SLOTS.acquire(blocking=False):
        return None
    result = queue.Queue(maxsize=1)
    def run():
        try:
            result.put(call())
        except Exception:
            result.put(None)
        finally:
            _HEAL_SLOTS.release()
    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        _HEAL_SLOTS.release()
        return None
    try:
        return result.get(timeout=HEAL_TIMEOUT_SECONDS)
    except queue.Empty:
        return None


internal_call = contextvars.ContextVar("mnfst_internal_call", default=False)

DISABLED_BACKOFF_SECONDS = 300
MAX_INFLIGHT_REPORTS = 64

# An attempt Phoenix opened that we never replayed (strip mode, or nothing
# replayable came back). Reported so the attempt ledger closes instead of
# holding open an answer that will never arrive.
NOT_ATTEMPTED = "replay_not_attempted"


@dataclass
class HealEvent:
    url: str
    status_code: int
    heal_status: str
    replay_status_code: Optional[int]
    heal_ms: int
    operations: Optional[list]


def _headers(config: Config) -> dict:
    headers = {
        "user-agent": f"mnfst-python/{VERSION}",
        "x-mnfst-source": "python-sdk",
    }
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    return headers


class _Disable:
    """Shared kill-switch backoff state."""

    def __init__(self) -> None:
        self._disabled_until = 0.0

    def enabled(self) -> bool:
        return time.monotonic() >= self._disabled_until

    def trip(self) -> None:
        self._disabled_until = time.monotonic() + DISABLED_BACKOFF_SECONDS


def _is_app_disabled(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    try:
        body = response.json()
        return isinstance(body, dict) and (body.get("error") == "project_disabled" or body.get("status") == "app_disabled")
    except ValueError:
        return False


class HealApi:
    def __init__(self, config: Config, transport: Optional[httpx.BaseTransport] = None):
        self._client = httpx.Client(base_url=config.base_url, timeout=HEAL_TIMEOUT_SECONDS,
                                    transport=transport, headers=_headers(config))
        self._disable = _Disable()
        self._pending: list[threading.Thread] = []
        self._pending_lock = threading.Lock()
        self.report_failures = 0
        self.reports_dropped = 0

    @property
    def _disabled_until(self) -> float:
        return self._disable._disabled_until

    @_disabled_until.setter
    def _disabled_until(self, value: float) -> None:
        self._disable._disabled_until = value

    def healing_enabled(self) -> bool:
        return self._disable.enabled()

    def heal(self, payload: dict) -> Optional[dict]:
        return _bounded_call(lambda: self._heal(payload))

    def _heal(self, payload: dict) -> Optional[dict]:
        if not self._disable.enabled():
            return None
        token = internal_call.set(True)
        try:
            # The post() call stays inside the try: serializing a pathological
            # payload can raise, and that must fail open like any other error.
            if not bounded_json(payload):
                return None
            with self._client.stream("POST", "/v1/heal", json=payload) as streamed:
                data = bytearray()
                for chunk in streamed.iter_bytes():
                    if len(data) + len(chunk) > MAX_HEAL_RESPONSE:
                        return None
                    data.extend(chunk)
                response = httpx.Response(streamed.status_code, content=bytes(data))
            if _is_app_disabled(response):
                self._disable.trip()
                return None
            if response.status_code != 200:
                return None
            result = response.json()
            return result if isinstance(result, dict) else None
        except Exception:
            return None
        finally:
            internal_call.reset(token)

    def report_outcome(self, heal_attempt_id: str, retry_status_code: int,
                       error: Any = None, truncated: bool = False) -> None:
        if retry_status_code == 0:
            body = {"failure": {"kind": "not_attempted" if error == NOT_ATTEMPTED else "transport_error", "message": error or NOT_ATTEMPTED}}
        else:
            body = {"response": {"statusCode": retry_status_code}}
            if error is not None:
                body["response"].update(body=error, truncated=truncated)

        def _send() -> None:
            internal_call.set(True)
            try:
                response = self._client.patch(f"/v1/heal-attempts/{heal_attempt_id}", json=body)
                response.raise_for_status()
            except Exception:
                with self._pending_lock:
                    self.report_failures += 1
                logger.warning("Outcome report failed; attempt remains unconfirmed")

        thread = threading.Thread(target=_send, daemon=True)
        with self._pending_lock:
            # Reports are fire-and-forget, so nothing else ever clears this in
            # a long-running process — sweep the finished ones as we go, and
            # under a flood drop the report rather than spawn without bound:
            # a lost outcome costs one learning signal, not the host process.
            self._pending = [t for t in self._pending if t.is_alive()]
            if len(self._pending) >= MAX_INFLIGHT_REPORTS:
                self.reports_dropped += 1
                logger.warning("Outcome report capacity reached; report dropped")
                return
            self._pending.append(thread)
            thread.start()

    def join_pending_reports(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        with self._pending_lock:
            pending = list(self._pending)
        for thread in pending:
            thread.join(max(0, deadline - time.monotonic()))


class AsyncHealApi:
    def __init__(self, config: Config, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._client = httpx.AsyncClient(base_url=config.base_url, timeout=HEAL_TIMEOUT_SECONDS,
                                         transport=transport, headers=_headers(config))
        self._sync = HealApi(config)  # outcome reports go out on threads either way
        self._disable = self._sync._disable

    def healing_enabled(self) -> bool:
        return self._disable.enabled()

    async def heal(self, payload: dict) -> Optional[dict]:
        try:
            with anyio.fail_after(HEAL_TIMEOUT_SECONDS):
                return await self._heal(payload)
        except TimeoutError:
            return None

    async def _heal(self, payload: dict) -> Optional[dict]:
        if not self._disable.enabled():
            return None
        token = internal_call.set(True)
        try:
            # Same fail-open envelope as the sync client, serialization included.
            if not bounded_json(payload):
                return None
            async with self._client.stream("POST", "/v1/heal", json=payload) as streamed:
                data = bytearray()
                async for chunk in streamed.aiter_bytes():
                    if len(data) + len(chunk) > MAX_HEAL_RESPONSE:
                        return None
                    data.extend(chunk)
                response = httpx.Response(streamed.status_code, content=bytes(data))
            if _is_app_disabled(response):
                self._disable.trip()
                return None
            if response.status_code != 200:
                return None
            result = response.json()
            return result if isinstance(result, dict) else None
        except Exception:
            return None
        finally:
            internal_call.reset(token)

    def report_outcome(self, heal_attempt_id: str, retry_status_code: int,
                       error: Any = None, truncated: bool = False) -> None:
        self._sync.report_outcome(heal_attempt_id, retry_status_code, error, truncated)

    def join_pending_reports(self, timeout: float = 5.0) -> None:
        self._sync.join_pending_reports(timeout)
