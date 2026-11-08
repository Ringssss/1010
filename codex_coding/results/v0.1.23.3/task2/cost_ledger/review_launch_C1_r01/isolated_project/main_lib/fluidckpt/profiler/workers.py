"""Persistent profiling workers: each runs one transfer task at a time.

Task kinds (all byte counts are per operation):
  h2d      {"numa": n, "gpu": g}        pinned host buffer on NUMA n -> HBM of g
  d2h      {"gpu": g, "numa": n}        HBM of g -> pinned host buffer on NUMA n
  p2p      {"src": i, "dst": j}         HBM i -> HBM j
  nvme     {"numa": n, "file_idx": k}   O_DIRECT read of local checkpoint files into NUMA n
  nvme2gpu {"numa": n, "gpu": g, "file_idx": k}  pipelined O_DIRECT -> pinned staging -> HBM
"""
import multiprocessing as mp
import os
import queue
import threading
import time

NVME_BLOCK = 16 << 20
DEFAULT_FILES_DIR = "/home/zhujianian/Qwen3.6-35B-A3B"


def list_files(directory=DEFAULT_FILES_DIR):
    fs = [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".safetensors")]
    return sorted(fs, key=os.path.getsize, reverse=True)


class DirectReader:
    """Sequential O_DIRECT reads that wrap around a rotating set of files."""

    def __init__(self, files, start_idx):
        self.files = files[start_idx:] + files[:start_idx]
        self.fi, self.off, self.fd, self.limit = -1, 0, None, 0
        self._next_file()

    def _next_file(self):
        if self.fd is not None:
            os.close(self.fd)
        self.fi = (self.fi + 1) % len(self.files)
        path = self.files[self.fi]
        self.fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
        self.limit = os.path.getsize(path) // NVME_BLOCK * NVME_BLOCK
        self.off = 0

    def read_into(self, mview, nbytes):
        done = 0
        while done < nbytes:
            if self.off >= self.limit:
                self._next_file()
            n = min(NVME_BLOCK, nbytes - done, self.limit - self.off)
            got = os.preadv(self.fd, [mview[done:done + n]], self.off)
            if got <= 0:
                raise IOError("short O_DIRECT read")
            done += got
            self.off += got
        return done

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Task:
    def __init__(self, spec, nbytes, files):
        import torch
        from fluidckpt.profiler.numa import HostBuffer
        self.spec, self.nbytes, self.kind = spec, nbytes, spec["kind"]
        self.host, self.reader, self.staging = None, None, []
        k = self.kind
        if k == "h2d":
            self.host = HostBuffer(nbytes, spec["numa"])
            self.dev = torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{spec['gpu']}")
            self.src, self.dst, self.device = self.host.tensor, self.dev, spec["gpu"]
        elif k == "d2h":
            self.host = HostBuffer(nbytes, spec["numa"])
            self.dev = torch.ones(nbytes, dtype=torch.uint8, device=f"cuda:{spec['gpu']}")
            self.src, self.dst, self.device = self.dev, self.host.tensor, spec["gpu"]
        elif k == "p2p":
            self.src = torch.ones(nbytes, dtype=torch.uint8, device=f"cuda:{spec['src']}")
            self.dst = torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{spec['dst']}")
            self.device = spec["src"]
        elif k == "nvme":
            self.host = HostBuffer(nbytes, spec["numa"], register=False)
            self.reader = DirectReader(files, spec.get("file_idx", 0))
            self.mview = memoryview(self.host.map)
        elif k == "nvme2gpu":
            self.staging = [HostBuffer(nbytes, spec["numa"]) for _ in range(2)]
            self.views = [memoryview(b.map) for b in self.staging]
            self.dev = torch.empty(nbytes, dtype=torch.uint8, device=f"cuda:{spec['gpu']}")
            self.reader = DirectReader(files, spec.get("file_idx", 0))
            self.device = spec["gpu"]
        else:
            raise ValueError(k)
        if k in ("h2d", "d2h", "p2p", "nvme2gpu"):
            self.stream = torch.cuda.Stream(device=self.device)
            self.one(self.nbytes)  # warm the path outside any timed region

    def one(self, nbytes):
        """One synchronous operation of nbytes; returns bytes moved."""
        import torch
        if self.kind in ("h2d", "d2h", "p2p"):
            with torch.cuda.stream(self.stream):
                self.dst[:nbytes].copy_(self.src[:nbytes], non_blocking=True)
            self.stream.synchronize()
            return nbytes
        if self.kind == "nvme":
            return self.reader.read_into(self.mview, nbytes)
        return self._pipelined(nbytes)

    def _pipelined(self, nbytes, block=64 << 20):
        """Double-buffered: disk read of block k+1 overlaps H2D of block k."""
        import torch
        block = min(block, self.staging[0].nbytes)
        nblocks = (nbytes + block - 1) // block
        filled, free = queue.Queue(), queue.Queue()
        events = [torch.cuda.Event() for _ in range(2)]
        for i in range(2):
            free.put(i)

        def reader():
            for b in range(nblocks):
                i = free.get()
                n = min(block, nbytes - b * block)
                self.reader.read_into(self.views[i], n)
                filled.put((i, b, n))

        th = threading.Thread(target=reader)
        th.start()
        for _ in range(nblocks):
            i, b, n = filled.get()
            with torch.cuda.stream(self.stream):
                self.dev[b * block:b * block + n].copy_(self.staging[i].tensor[:n], non_blocking=True)
                events[i].record(self.stream)
            events[i].synchronize()
            free.put(i)
        th.join()
        return nbytes

    def close(self):
        import torch
        for v in [getattr(self, "mview", None)] + list(getattr(self, "views", [])):
            if v is not None:
                v.release()
        for name in ("src", "dst", "dev", "mview", "views"):
            if hasattr(self, name):
                delattr(self, name)
        for b in [self.host] + self.staging:
            if b is not None:
                b.close()
        if self.reader:
            self.reader.close()
        torch.cuda.empty_cache()


def devices_of(spec):
    k = spec["kind"]
    if k == "p2p":
        return sorted({spec["src"], spec["dst"]})
    return [spec["gpu"]] if "gpu" in spec else []


def _localize(spec, devs):
    """Global GPU ids -> indices within this worker's HIP_VISIBLE_DEVICES."""
    out = dict(spec)
    for f in ("gpu", "src", "dst"):
        if f in out and devs is not None:
            out[f] = devs.index(out[f])
    return out


def _worker_main(conn, files_dir, devs):
    import torch
    if devs is None or devs:
        torch.zeros(1, device="cuda:0")
    files = list_files(files_dir)
    task = None
    conn.send("ready")
    while True:
        cmd, arg = conn.recv()
        try:
            if cmd == "setup":
                if task:
                    task.close()
                task = Task(_localize(arg["spec"], devs), arg["nbytes"], files)
                conn.send({"ok": True})
            elif cmd == "run":
                start_at, window = arg
                while time.time() < start_at:
                    pass
                t0 = time.time()
                deadline, moved, ops = t0 + window, 0, 0
                while time.time() < deadline:
                    moved += task.one(task.nbytes)
                    ops += 1
                t1 = time.time()
                conn.send({"ok": True, "t0": t0, "t1": t1, "bytes": moved, "ops": ops})
            elif cmd == "fixed":  # move exactly total bytes in ops of <= task.nbytes, optionally paced
                start_at, total, rate = arg
                while time.time() < start_at:
                    pass
                t0 = time.time()
                left = total
                while left > 0:
                    if rate:  # token bucket: never get ahead of rate (GB/s)
                        ahead = t0 + (total - left) / 1e9 / rate - time.time()
                        if ahead > 0:
                            time.sleep(ahead)
                    left -= task.one(min(task.nbytes, left))
                t1 = time.time()
                conn.send({"ok": True, "t0": t0, "t1": t1, "bytes": total})
            elif cmd == "pull":  # dynamic execution: ask the parent for chunks until it answers 0
                start_at = arg
                while time.time() < start_at:
                    pass
                t0, moved = time.time(), 0
                while True:
                    conn.send({"need": True})
                    n = conn.recv()
                    if not n:
                        break
                    left = n
                    while left > 0:
                        left -= task.one(min(task.nbytes, left))
                    moved += n
                t1 = time.time()
                conn.send({"ok": True, "t0": t0, "t1": t1, "bytes": moved})
            elif cmd == "sizes":
                sizes, reps = arg
                out = {}
                for s in sizes:
                    task.one(s)
                    ts = []
                    for _ in range(reps):
                        t = time.perf_counter()
                        task.one(s)
                        ts.append(time.perf_counter() - t)
                    out[s] = ts
                conn.send({"ok": True, "times": out})
            elif cmd == "teardown":
                if task:
                    task.close()
                    task = None
                conn.send({"ok": True})
            elif cmd == "exit":
                if task:
                    task.close()
                conn.send({"ok": True})
                return
        except Exception as exc:  # report and keep serving
            conn.send({"ok": False, "error": repr(exc)})


class Pool:
    """n worker processes. With `visible`, worker i only sees GPUs visible[i] (global ids) — the
    deployment regime of a short-lived per-card agent; specs are always given in global ids."""

    def __init__(self, n, files_dir=DEFAULT_FILES_DIR, visible=None):
        ctx = mp.get_context("spawn")
        self.conns, self.procs = [], []
        saved = {k: os.environ.get(k) for k in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")}
        try:
            for i in range(n):
                devs = None if visible is None else list(visible[i])
                if devs is not None:
                    for k in saved:
                        os.environ[k] = ",".join(map(str, devs))
                a, b = ctx.Pipe()
                p = ctx.Process(target=_worker_main, args=(b, files_dir, devs), daemon=True)
                p.start()
                self.conns.append(a)
                self.procs.append(p)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        for c in self.conns:
            assert c.recv() == "ready"

    def _all(self, idxs, cmd, args):
        for i, a in zip(idxs, args):
            self.conns[i].send((cmd, a))
        out = [self.conns[i].recv() for i in idxs]
        for r in out:
            if not r.get("ok"):
                raise RuntimeError(r)
        return out

    @classmethod
    def for_specs(cls, specs, files_dir=DEFAULT_FILES_DIR):
        """One fresh worker per spec, each seeing only the GPUs its flow touches."""
        return cls(len(specs), files_dir, visible=[devices_of(s) for s in specs])

    def concurrent(self, specs, nbytes, window, repeats, warmup=0.4, idxs=None):
        """Run all specs simultaneously; returns per-repeat per-flow records.
        A discarded warm-up window absorbs the first-window transient seen after setup.
        `idxs` picks which workers run the specs (default: the first len(specs))."""
        idxs = list(idxs) if idxs is not None else list(range(len(specs)))
        self._all(idxs, "setup", [{"spec": s, "nbytes": nbytes[s["kind"]] if isinstance(nbytes, dict) else nbytes}
                                  for s in specs])
        if warmup:
            self._all(idxs, "run", [(time.time() + 0.3, warmup)] * len(specs))
        runs = []
        for _ in range(repeats):
            start = time.time() + 0.3
            recs = self._all(idxs, "run", [(start, window)] * len(specs))
            runs.append([dict(spec=s, bytes=r["bytes"], ops=r["ops"], t0=r["t0"], t1=r["t1"],
                              GBps=r["bytes"] / (r["t1"] - r["t0"]) / 1e9) for s, r in zip(specs, recs)])
        self._all(idxs, "teardown", [None] * len(specs))
        return runs

    def fixed(self, specs, totals, op_bytes, repeats, rates=None):
        """Each stream moves a fixed byte count; all start together. Returns per-repeat records.
        rates: optional per-stream pacing in GB/s (None entries = unpaced)."""
        rates = rates or [None] * len(specs)
        idxs = list(range(len(specs)))
        self._all(idxs, "setup", [{"spec": s, "nbytes": op_bytes[s["kind"]]} for s in specs])
        self._all(idxs, "run", [(time.time() + 0.3, 0.4)] * len(specs))  # warm-up, discarded
        runs = []
        for _ in range(repeats):
            start = time.time() + 0.3
            recs = self._all(idxs, "fixed", [(start, b, r) for b, r in zip(totals, rates)])
            runs.append([dict(spec=s, bytes=b, t0=r["t0"], t1=r["t1"], start=start)
                         for s, b, r in zip(specs, totals, recs)])
        self._all(idxs, "teardown", [None] * len(specs))
        return runs

    def dynamic(self, specs, serves, class_bytes, chunk, op_bytes, repeats):
        """Work-stealing execution. serves[i]: classes stream i may carry, in preference order;
        each idle stream pulls a chunk (chunk[i] bytes) from its first class with bytes left."""
        from multiprocessing.connection import wait
        idxs = list(range(len(specs)))
        self._all(idxs, "setup", [{"spec": s, "nbytes": op_bytes[s["kind"]]} for s in specs])
        self._all(idxs, "run", [(time.time() + 0.3, 0.4)] * len(specs))  # warm-up, discarded
        runs = []
        for _ in range(repeats):
            left = list(class_bytes)
            took = [dict() for _ in specs]
            start = time.time() + 0.3
            for i in idxs:
                self.conns[i].send(("pull", start))
            active, recs = {self.conns[i]: i for i in idxs}, {}
            while active:
                for c in wait(list(active)):
                    i, msg = active[c], c.recv()
                    if msg.get("need"):
                        n = 0
                        for k in serves[i]:
                            if left[k] > 0:
                                n = min(chunk[i], left[k])
                                left[k] -= n
                                took[i][k] = took[i].get(k, 0) + n
                                break
                        c.send(n)
                    else:
                        if not msg.get("ok"):
                            raise RuntimeError(msg)
                        recs[i] = msg
                        del active[c]
            runs.append([dict(spec=s, bytes=recs[i]["bytes"], took=took[i], t0=recs[i]["t0"], t1=recs[i]["t1"],
                              start=start) for i, s in enumerate(specs)])
        self._all(idxs, "teardown", [None] * len(specs))
        return runs

    def sizes(self, spec, max_bytes, sizes, reps, w=0):
        self._all([w], "setup", [{"spec": spec, "nbytes": max_bytes}])
        out = self._all([w], "sizes", [(sizes, reps)])[0]["times"]
        self._all([w], "teardown", [None])
        return out

    def close(self):
        for c in self.conns:
            c.send(("exit", None))
        for c in self.conns:
            c.recv()
        for p in self.procs:
            p.join(10)
