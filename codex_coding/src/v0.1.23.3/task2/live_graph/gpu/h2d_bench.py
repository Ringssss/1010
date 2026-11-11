"""H2D microbenchmark: how fast can this platform actually move host bytes into HBM?

Run inside a GPU container. Measures pinned and pageable host buffers separately, one-shot and
double-buffered, at the sizes a model restore actually uses.

Why: a 19 GiB model restore through my executor measured 1.2 GB/s while a pure broadcast of the
same 19 GiB hit 45.4 GB/s. Either the host->device copy is far slower than the 46-47 GB/s that the
frozen node27 profile reports for H2D, or the executor serialises copies against collectives. This
tells which.
"""
import argparse
import json
import time

import torch

MiB = 1 << 20


def bgps(nbytes, dt):
    return round(nbytes / dt / 1e9, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gib", type=float, default=4.0)
    ap.add_argument("--chunk-mib", type=int, default=1024)
    ap.add_argument("--gpu", type=int, default=0)
    a = ap.parse_args()

    torch.cuda.set_device(a.gpu)
    dev = torch.device("cuda", a.gpu)
    n = int(a.gib * 2**30)
    chunk = a.chunk_mib * MiB
    out = {}

    for kind in ("pinned", "pageable"):
        host = (torch.empty(n, dtype=torch.uint8, pin_memory=True) if kind == "pinned"
                else torch.empty(n, dtype=torch.uint8))
        host.fill_(5)
        staging = torch.empty(chunk, dtype=torch.uint8, device=dev)

        # one-shot: a single copy of the whole buffer
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        staging[:min(chunk, n)].copy_(host[:min(chunk, n)], non_blocking=False)
        torch.cuda.synchronize()
        one = time.perf_counter() - t0

        # chunked, blocking: what a naive restore loop does
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for off in range(0, n, chunk):
            m = min(chunk, n - off)
            staging[:m].copy_(host[off:off + m], non_blocking=False)
            torch.cuda.synchronize()
        blocked = time.perf_counter() - t0

        # chunked, async on one stream: ordering kept, host not blocked
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for off in range(0, n, chunk):
            m = min(chunk, n - off)
            staging[:m].copy_(host[off:off + m], non_blocking=True)
        torch.cuda.synchronize()
        async_ = time.perf_counter() - t0

        # chunked, double-buffered
        s2 = torch.empty(chunk, dtype=torch.uint8, device=dev)
        bufs = [staging, s2]
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        evs = [None, None]
        k = 0
        for off in range(0, n, chunk):
            m = min(chunk, n - off)
            if evs[k] is not None:
                evs[k].synchronize()
            e = torch.cuda.Event()
            with torch.cuda.stream(torch.cuda.Stream(device=dev)):
                pass
            bufs[k][:m].copy_(host[off:off + m], non_blocking=True)
            e.record()
            evs[k] = e
            k ^= 1
        torch.cuda.synchronize()
        dbuf = time.perf_counter() - t0

        out[kind] = {
            "one_chunk_GBps": bgps(min(chunk, n), one),
            "chunked_blocking_GBps": bgps(n, blocked),
            "chunked_async_GBps": bgps(n, async_),
            "chunked_double_buffer_GBps": bgps(n, dbuf),
        }
        print(json.dumps({"kind": kind, **out[kind]}), flush=True)
        del host, staging, s2, bufs
        torch.cuda.empty_cache()

    print(json.dumps({"summary": out}), flush=True)


if __name__ == "__main__":
    main()
