import time


def wait_for(predicate, timeout=3.0):
    """Poll predicate until true or timeout. Test-only: outcome reports are
    fire-and-forget, so tests observe the result instead of draining them."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False
