"""Tracked calls: one metadata record per call that is not healed, whatever
its status, shipped to `POST /v1/requests` in batches off the caller's thread.

`record()` is an in-memory append under a short lock and nothing else. One
daemon worker thread sends: when `flush_at` calls are waiting or every
`interval` seconds, at most once per `min_gap` seconds, 500 calls per batch.
Past MAX_BUFFER the newest call is dropped, so a burst never grows memory. A
batch whose send raises is retried once, then dropped.
"""
from __future__ import annotations

import collections
import os
import threading
import time
from typing import Callable

MAX_BUFFER = 5000
MAX_BATCH = 500


class CallBuffer:
    def __init__(self, send: Callable[[list], None], interval: float = 5.0,
                 flush_at: int = 500, min_gap: float = 1.0) -> None:
        self._send = send
        self._interval = interval
        self._flush_at = flush_at
        self._min_gap = min_gap
        self._last_sent = float("-inf")
        self._pid = None

    def record(self, call: dict) -> None:
        self._ensure_worker()
        with self._lock:
            if len(self._queue) >= MAX_BUFFER:
                return
            self._queue.append(call)
            full = len(self._queue) >= self._flush_at
        if full:
            self._wake.set()

    def size(self) -> int:
        self._ensure_worker()
        with self._lock:
            return len(self._queue)

    def flush(self, timeout: float) -> None:
        """Send everything buffered now; wait at most `timeout` seconds."""
        if self.size() == 0:
            return
        self._drained.clear()
        self._wake.set()
        self._drained.wait(timeout)

    def _ensure_worker(self) -> None:
        # A forked child (gunicorn --preload) inherits the parent's buffer but
        # not its thread, and possibly a lock held mid-append: start clean.
        if self._pid == os.getpid():
            return
        self._pid = os.getpid()
        self._queue: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._drained = threading.Event()
        threading.Thread(target=self._run, name="mnfst-tracking", daemon=True).start()

    def _run(self) -> None:
        while True:
            self._wake.wait(self._interval)
            self._wake.clear()
            self._drain()
            self._drained.set()

    def _drain(self) -> None:
        while True:
            with self._lock:
                batch = [self._queue.popleft() for _ in range(min(MAX_BATCH, len(self._queue)))]
            if not batch:
                return
            for _ in range(2):  # one retry, then the batch is dropped
                wait = self._last_sent + self._min_gap - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last_sent = time.monotonic()
                try:
                    self._send(batch)
                    break
                except Exception:
                    pass
