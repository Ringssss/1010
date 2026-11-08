"""Bounded FIFO consumption and reader-first cleanup for existing keeper save."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import os
import time


def export_fifo_to_file(fifo, destination, expected_bytes, producer, timeout=1800):
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        fcntl.fcntl(reader, fcntl.F_SETPIPE_SZ, 1 << 20)
    except PermissionError:
        pass
    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    digest = hashlib.sha256()
    written = 0
    deadline = time.monotonic() + timeout
    try:
        with destination.open("xb", buffering=8 << 20) as stream:
            future = pool.submit(producer)
            while written < expected_bytes:
                if time.monotonic() >= deadline:
                    raise TimeoutError("FIFO export deadline exceeded")
                try:
                    block = os.read(reader, min(64 << 20, expected_bytes-written))
                except BlockingIOError:
                    block = b""
                if block:
                    stream.write(block)
                    digest.update(block)
                    written += len(block)
                elif future.done():
                    future.result()
                    raise EOFError("keeper export ended before the complete rank")
                else:
                    time.sleep(.005)
            saved = future.result(timeout=max(.01, deadline-time.monotonic()))
        return digest.hexdigest(), saved
    finally:
        # Closing the reader unblocks a producer in write() with EPIPE on consumer failure.
        os.close(reader)
        fifo.unlink()
        if future is not None:
            try:
                future.result(timeout=10)
            except Exception:
                pass
        pool.shutdown(wait=False)
