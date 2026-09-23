"""Tracked calls: one metadata record per call that is not healed, whatever
its status, shipped to `POST /v1/requests` in batches off the caller's thread.

`record()` is an in-memory append under a short lock and nothing else. One
daemon worker thread sends: when `flush_at` calls are waiting or every
`interval` seconds, at most once per `min_gap` seconds, 500 calls per batch.
Past MAX_BUFFER the newest call is dropped, so a burst never grows memory. A
batch whose send raises is retried once, then dropped.

Fork-safe: a forked child (gunicorn --preload) gets a fresh, empty buffer, a
fresh lock and its own worker the first time it records, never the parent's.
"""
from __future__ import annotations

import collections
import os
import threading
import time
from typing import Callable

MAX_BUFFER = 5000
MAX_BATCH = 500

# Guards starting a worker. Re-created in a forked child, where a copy held by
# a parent thread at fork time would never be released.
_start_lock = threading.Lock()


def _reset_start_lock() -> None:
    global _start_lock
    _start_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_start_lock)


class CallBuffer:
    def __init__(self, send: Callable[[list], None], interval: float = 5.0,
                 flush_at: int = 500, min_gap: float = 1.0) -> None:
        self._send = send
        self._interval = interval
        self._flush_at = flush_at
        self._min_gap = min_gap
        self._last_sent = float("-inf")
        self._pid = None
        self._fresh_state()

    def _fresh_state(self) -> None:
        self._queue: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._drained = threading.Event()
        self._sending = False

    def record(self, call: dict) -> None:
        if not self._ensure_worker():
            return
        with self._lock:
            if len(self._queue) >= MAX_BUFFER:
                return
            self._queue.append(call)
            full = len(self._queue) >= self._flush_at
        if full:
            self._wake.set()

    def size(self) -> int:
        if self._pid != os.getpid():
            return 0  # nothing recorded in this process yet
        with self._lock:
            return len(self._queue)

    def flush(self, timeout: float) -> None:
        """Send everything buffered now; wait at most `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while self.size() > 0 or self._sending_here():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._drained.clear()
            self._wake.set()
            self._drained.wait(remaining)

    def _sending_here(self) -> bool:
        return self._pid == os.getpid() and self._sending

    def _ensure_worker(self) -> bool:
        pid = os.getpid()
        if self._pid == pid:
            return True
        with _start_lock:
            if self._pid == pid:
                return True
            self._fresh_state()
            try:
                threading.Thread(target=self._run, name="mnfst-tracking", daemon=True).start()
            except Exception:
                return False  # no worker, no recording: never a buffer nobody drains
            self._pid = pid
            return True

    def _run(self) -> None:
        while True:
            self._wake.wait(self._interval)
            self._wake.clear()
            self._drain()
            self._drained.set()

    def _drain(self) -> None:
        while True:
            # Wait out the gap before taking the batch, so calls that arrive
            # meanwhile ride in it instead of waiting for the next one.
            wait = self._last_sent + self._min_gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            with self._lock:
                batch = [self._queue.popleft() for _ in range(min(MAX_BATCH, len(self._queue)))]
                self._sending = bool(batch)
            if not batch:
                return
            try:
                for attempt in range(2):  # one retry, then the batch is dropped
                    if attempt:
                        wait = self._last_sent + self._min_gap - time.monotonic()
                        if wait > 0:
                            time.sleep(wait)
                    self._last_sent = time.monotonic()
                    try:
                        self._send(batch)
                        break
                    except Exception:
                        pass
            finally:
                self._sending = False
