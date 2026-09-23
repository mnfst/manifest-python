import threading
import time

from mnfst.tracking import CallBuffer


def call(i):
    return {"traceId": f"t{i}", "method": "GET", "url": "https://a.com/x",
            "statusCode": 200, "responseTimeMs": 1, "occurredAt": "1970-01-01T00:00:00Z"}


def test_sends_at_500_in_one_batch():
    sent = []
    buf = CallBuffer(sent.append, interval=60, min_gap=0)
    for i in range(500):
        buf.record(call(i))
    buf.flush(timeout=2)
    assert [len(b) for b in sent] == [500]


def test_drops_past_5000_without_blocking():
    gate = threading.Event()
    buf = CallBuffer(lambda b: gate.wait(), interval=60)  # the send hangs
    started = time.monotonic()
    for i in range(50_000):
        buf.record(call(i))
    assert time.monotonic() - started < 1.0
    assert buf.size() <= 5000
    gate.set()


def test_never_sends_twice_within_min_gap():
    at = []
    buf = CallBuffer(lambda b: at.append(time.monotonic()), interval=60, min_gap=0.2)
    for i in range(2000):  # four batches' worth
        buf.record(call(i))
    buf.flush(timeout=5)
    assert len(at) == 4
    assert all(b - a >= 0.19 for a, b in zip(at, at[1:]))


def test_failed_batch_is_retried_once_then_dropped():
    calls = []

    def boom(batch):
        calls.append(len(batch))
        raise RuntimeError("down")

    buf = CallBuffer(boom, interval=60, min_gap=0)
    for i in range(500):
        buf.record(call(i))
    buf.flush(timeout=2)  # must not raise
    assert calls == [500, 500]
    assert buf.size() == 0


def test_flush_splits_into_batches_of_500():
    sent = []
    buf = CallBuffer(sent.append, interval=60, flush_at=10_000, min_gap=0)
    for i in range(900):
        buf.record(call(i))
    buf.flush(timeout=2)
    assert [len(b) for b in sent] == [500, 400]


def test_interval_sends_a_small_batch_without_waiting_for_500():
    sent = []
    buf = CallBuffer(sent.append, interval=0.05, min_gap=0)
    buf.record(call(1))
    deadline = time.monotonic() + 2
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [len(b) for b in sent] == [1]


def test_flush_with_nothing_buffered_returns_at_once():
    buf = CallBuffer(lambda b: None, interval=60)
    started = time.monotonic()
    buf.flush(timeout=2)
    assert time.monotonic() - started < 0.1
