"""Bounded polling of atomically published model exports on a shared handoff directory."""
from pathlib import Path
import time


def wait_for_exports(directory, ranks=2, timeout=600, poll_s=0.01):
    if not 0 < poll_s <= 1 or ranks < 1 or timeout <= 0:
        raise ValueError("invalid handoff wait settings")
    paths = [Path(directory) / f"rank{rank}.pkl" for rank in range(ranks)]
    started = time.monotonic()
    checks = 0
    while True:
        checks += 1
        if all(p.exists() for p in paths):
            return {"wait_s": time.monotonic()-started, "poll_s": poll_s, "checks": checks}
        remaining = timeout-(time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError(f"model exports not ready: {directory}")
        time.sleep(min(poll_s, remaining))
