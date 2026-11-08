"""Two bounded staging slots with explicit copy/collective completion dependencies."""
import time


class DoubleBufferCopier:
    def __init__(self, device, buffers, broadcast, land, trace=False):
        import torch
        if len(buffers) != 2 or buffers[0].numel() != buffers[1].numel():
            raise ValueError("two equally sized buffers required")
        self.torch, self.device, self.buffers = torch, device, buffers
        self.broadcast, self.land, self.trace = broadcast, land, trace
        self.copy = torch.cuda.Stream(device)
        self.comm = torch.cuda.Stream(device)
        origin = torch.cuda.current_stream(device)
        self.copy.wait_stream(origin); self.comm.wait_stream(origin)
        self.ready = [torch.cuda.Event(), torch.cuda.Event()]
        self.free = [torch.cuda.Event(), torch.cuda.Event()]
        self.busy = [False, False]
        self.reference = torch.cuda.Event(enable_timing=True) if trace else None
        if trace:
            self.reference.record(origin)
            self.copy.wait_event(self.reference); self.comm.wait_event(self.reference)

    def synchronize(self):
        errors = []
        for stream in (self.copy, self.comm):
            try: stream.synchronize()
            except BaseException as exc: errors.append(repr(exc))
        if errors: raise RuntimeError(f"pipeline drain failed: {errors}")

    def move(self, op, source, target, sender, receiver, fail_after_chunks=None):
        torch = self.torch
        capacity = self.buffers[0].numel()
        chunks, host_wait_s, events = 0, 0.0, []
        def timed(stream, kind, pos, count, fn):
            begin = torch.cuda.Event(enable_timing=True) if self.trace else None
            end = torch.cuda.Event(enable_timing=True) if self.trace else None
            with torch.cuda.stream(stream):
                if begin is not None: begin.record(stream)
                fn()
                if end is not None: end.record(stream)
            if begin is not None: events.append((kind, pos, count, begin, end))
        try:
            for pos in range(0, op["n"], capacity):
                if fail_after_chunks is not None and chunks >= fail_after_chunks:
                    raise RuntimeError("injected failure after bounded pipeline submissions")
                k = chunks % 2
                if self.busy[k]:
                    # Bound host submissions and prevent reuse until the last consumer is done.
                    stamp = time.monotonic(); self.free[k].synchronize()
                    host_wait_s += time.monotonic()-stamp
                n = min(capacity, op["n"]-pos)
                buf = self.buffers[k][:n]
                view = source[op["source_offset"]+pos:op["source_offset"]+pos+n] if sender else None
                if sender and receiver:
                    timed(self.copy, "copy", pos, n, lambda: self.land(target, op["off"]+pos, view))
                    self.free[k].record(self.copy)
                elif sender:
                    timed(self.copy, "copy", pos, n, lambda: buf.copy_(view, non_blocking=True))
                    self.ready[k].record(self.copy)
                    self.comm.wait_event(self.ready[k])
                    timed(self.comm, "network", pos, n, lambda: self.broadcast(buf, op))
                    self.free[k].record(self.comm)
                else:
                    timed(self.comm, "network", pos, n, lambda: self.broadcast(buf, op))
                    self.ready[k].record(self.comm)
                    self.copy.wait_event(self.ready[k])
                    timed(self.copy, "copy", pos, n, lambda: self.land(target, op["off"]+pos, buf))
                    self.free[k].record(self.copy)
                self.busy[k] = True
                chunks += 1
            self.synchronize()
        except BaseException as exc:
            try: self.synchronize()
            except BaseException as drain:
                raise RuntimeError(f"pipeline failed: {exc}; drain failed: {drain}") from exc
            raise
        trace = [{"kind": kind, "offset": pos, "n": count,
                  "begin_ms": self.reference.elapsed_time(begin), "end_ms": self.reference.elapsed_time(end)}
                 for kind, pos, count, begin, end in events]
        return {"chunks": chunks, "host_slot_wait_s": host_wait_s, "max_slots": 2, "trace": trace,
                "trace_scope": "same-GPU event intervals; overlapping intervals do not prove simultaneous DMA-engine use"}
