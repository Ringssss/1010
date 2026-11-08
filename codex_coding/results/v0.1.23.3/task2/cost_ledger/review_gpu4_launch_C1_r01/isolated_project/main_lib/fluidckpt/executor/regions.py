"""Bounded per-path copy streams for byte-exact local DRAM materialization."""
from collections import deque
import threading
import time
import traceback


def execute_regions(catalog, ops, targets, gpu, parallel=True, chunk_bytes=64 << 20,
                    max_inflight=2, paced=False, fail_after_chunks=None, trace_enabled=False):
    import torch
    if type(chunk_bytes) is not int or not 0 < chunk_bytes <= 256 << 20:
        raise ValueError("invalid chunk size")
    if type(max_inflight) is not int or not 1 <= max_inflight <= 8:
        raise ValueError("invalid in-flight bound")
    by_path = {}
    for op in ops:
        if op["kind"] != "h2d":
            raise ValueError("regional executor currently supports local registered DRAM only")
        by_path.setdefault(op["path_id"], []).append(op)
    if not by_path or len(by_path) > 8:
        raise ValueError("between one and eight local paths required")
    torch.cuda.set_device(gpu)
    reference = torch.cuda.Event(enable_timing=True)
    reference.record(); reference.synchronize()
    start_ns = time.monotonic_ns()
    aborted = threading.Event()
    barrier = threading.Barrier(len(by_path)) if parallel else None
    results, errors = {}, []

    with catalog.acquire(ops) as leased:
        def path_worker(path, items):
            stream = None
            try:
                torch.cuda.set_device(gpu)
                stream = torch.cuda.Stream(gpu)
                pending, events = deque(), []
                bytes_by_source, submitted, chunks = {}, 0, 0
                rate = items[0].get("rate_GBps")
                if paced and (rate is None or rate <= 0):
                    raise ValueError("pacing requires a positive planned rate")
                if barrier:
                    barrier.wait(timeout=30)
                t0 = time.monotonic()
                for op in items:
                    entry = leased[op["replica_id"]]
                    for relative in range(0, op["n"], chunk_bytes):
                        if aborted.is_set():
                            raise RuntimeError("peer path failed")
                        if fail_after_chunks is not None and chunks >= fail_after_chunks:
                            raise RuntimeError("injected copy failure")
                        if len(pending) >= max_inflight:
                            pending.popleft().synchronize()
                        if paced:
                            delay = t0 + submitted / (rate * 1e9) - time.monotonic()
                            if delay > 0 and aborted.wait(delay):
                                raise RuntimeError("cancelled during pacing")
                        n = min(chunk_bytes, op["n"]-relative)
                        begin = torch.cuda.Event(enable_timing=True) if trace_enabled else None
                        end = torch.cuda.Event(enable_timing=trace_enabled)
                        submitted_ns = time.monotonic_ns()
                        with torch.cuda.stream(stream):
                            if begin is not None: begin.record(stream)
                            off = op["source_offset"]+relative
                            moved = targets[op["tp"]].scatter(op["off"]+relative, entry.tensor[off:off+n])
                            if moved != n:
                                raise RuntimeError(f"incomplete scatter: {moved}/{n}")
                            end.record(stream)
                        pending.append(end)
                        if trace_enabled:
                            events.append((begin, end, submitted_ns, op["off"]+relative, n, op["replica_id"]))
                        submitted += n; chunks += 1
                        bytes_by_source[op["replica_id"]] = bytes_by_source.get(op["replica_id"], 0)+n
                stream.synchronize()
                copy_end = time.monotonic()
                trace = [{"gpu_begin_ms": reference.elapsed_time(a), "gpu_end_ms": reference.elapsed_time(b),
                          "host_submit_ns": host, "logical_offset": off, "n": n, "replica_id": rid}
                         for a, b, host, off, n, rid in events]
                results[path] = {"bytes": submitted, "bytes_by_source": bytes_by_source,
                                 "chunks": chunks, "copy_and_wait_s": copy_end-t0,
                                 "trace_processing_s": time.monotonic()-copy_end,
                                 "trace": trace}
            except BaseException as exc:
                errors.append({"path": path, "error": repr(exc), "traceback": traceback.format_exc()})
                aborted.set()
                if barrier:
                    barrier.abort()
            finally:
                # Outstanding DMA must finish before leases are released, even after a failure.
                if stream is not None:
                    try:
                        stream.synchronize()
                    except BaseException as exc:
                        errors.append({"path": path, "synchronize_error": repr(exc)})

        if parallel:
            threads = [threading.Thread(target=path_worker, args=(p, items)) for p, items in by_path.items()]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        else:
            for path, items in by_path.items():
                path_worker(path, items)
                if errors:
                    break
        if errors:
            raise RuntimeError(f"regional load failed: {errors}")
    if sum(r["bytes"] for r in results.values()) != sum(op["n"] for op in ops):
        raise RuntimeError("executed byte count differs from plan")
    points = []
    for path, result in results.items():
        for event in result["trace"]:
            points.extend([(event["gpu_begin_ms"], 1, path), (event["gpu_end_ms"], -1, path)])
    active, previous, overlap = {}, 0.0, 0.0
    for stamp, direction, path in sorted(points):
        if sum(v > 0 for v in active.values()) > 1:
            overlap += stamp-previous
        active[path] = active.get(path, 0)+direction
        previous = stamp
    return {"parallel": parallel, "paced": paced, "trace_enabled": trace_enabled, "paths": results,
            "bytes": sum(r["bytes"] for r in results.values()), "gpu_path_overlap_ms": overlap,
            "total_s": (time.monotonic_ns()-start_ns)/1e9, "start_monotonic_ns": start_ns,
            "end_monotonic_ns": time.monotonic_ns(), "chunk_bytes": chunk_bytes,
            "max_inflight_per_path": max_inflight,
            "overlap_scope": "device-event intervals on one GPU; not proof of simultaneous copy-engine execution"}
