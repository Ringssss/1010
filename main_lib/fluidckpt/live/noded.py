"""Node daemon: holds chunks in this node's DRAM / SSD / HBM and lands restores into its GPUs.

One per node. It is the only process that touches the node's media, so every tier is one address
space away from every other:

  DRAM  slabs of host memory, registered for RDMA on all four HCA ports. A chunk in a slab is
        served to any peer with zero work on this side (the peer RDMA-READs it). Slabs on a node
        that also restores locally are GPU-pinned so local H2D runs at PCIe rate.
  SSD   one file per chunk under the node's NVMe pool directory.
  HBM   chunks parked in idle cards as device tensors.

A restore is executed by the *destination's* daemon and is pull-based: one worker per path
(peer node x HCA port, local slab, local NVMe reader, ...) repeatedly takes the next chunk it can
serve. Workers take their planned chunks first and then steal from the path with the longest
remaining time, so every path stays busy until the model is in HBM, and a path that slows down or
loses its chunks mid-restore (another tenant took the link or the memory) sheds work to the
others within one chunk. The coordinator's plan is the prior; this loop is the reaction.

RPC: length-prefixed JSON over TCP on the node's ib0 address.
"""
from __future__ import annotations

import collections
import ctypes
import hashlib
import json
import mmap
import os
import queue
import socket
import struct
import threading
import time
import traceback

from . import rdma

MiB, GiB, PAGE = 1 << 20, 1 << 30, 4096
RPC_PORT, RDMA_PORT = 18760, 18761


# ---------------------------------------------------------------- rpc
def _recvn(sock, n):
    buf = bytearray(n)
    view, got = memoryview(buf), 0
    while got < n:
        m = sock.recv_into(view[got:])
        if not m:
            raise ConnectionError("peer closed")
        got += m
    return buf


def send_msg(sock, obj):
    data = json.dumps(obj).encode()
    sock.sendall(struct.pack("<I", len(data)) + data)


def recv_msg(sock):
    return json.loads(_recvn(sock, struct.unpack("<I", _recvn(sock, 4))[0]))


class Client:
    """Blocking RPC client; one in-flight call at a time (use one per thread for concurrency)."""

    def __init__(self, ip, port=RPC_PORT, timeout=3600):
        self.addr, self.timeout, self.sock, self.lock = (ip, port), timeout, None, threading.Lock()

    def call(self, op, **kw):
        with self.lock:
            if self.sock is None:
                self.sock = socket.create_connection(self.addr, timeout=self.timeout)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                send_msg(self.sock, {"op": op, **kw})
                out = recv_msg(self.sock)
            except Exception:
                self.sock.close()
                self.sock = None
                raise
        if "error" in out:
            raise RuntimeError(f"{self.addr[0]} {op}: {out['error']}")
        return out["ok"]


# ---------------------------------------------------------------- host memory
_libc = ctypes.CDLL("libc.so.6", use_errno=True)


def prefer_numa(node, strict=False):
    """Thread memory policy: prefer `node` (None resets); strict binds, so the kernel reclaims
    on that node instead of spilling to another. Best effort: needs CAP_SYS_NICE."""
    try:
        if node is None:
            _libc.syscall(238, 0, None, 0)                       # set_mempolicy(MPOL_DEFAULT)
        else:
            mask = (ctypes.c_ulong * 2)(1 << int(node), 0)
            _libc.syscall(238, 2 if strict else 1, mask, 128)    # MPOL_BIND | MPOL_PREFERRED
    except Exception:
        pass


class Slab:
    """A contiguous host region carved into chunk extents (first fit, coalescing free list)."""

    def __init__(self, nbytes, numa, pin, torch=None):
        self.nbytes, self.numa, self.pin = nbytes, numa, bool(pin)
        prefer_numa(numa)
        try:
            if pin:
                self.t = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True)
                self.addr = self.t.data_ptr()
                self.view = memoryview(self.t.numpy())
            else:
                self.mm = mmap.mmap(-1, nbytes)
                self.addr = ctypes.addressof(ctypes.c_char.from_buffer(self.mm))
                self.view = memoryview(self.mm)
                self.t = None if torch is None else torch.frombuffer(self.mm, dtype=torch.uint8)
            # Registration faults the pages in under this thread's NUMA policy.
            self.regs = [rdma.Region(p, self.addr, nbytes, True) for p in range(rdma.PORTS)]
        finally:
            prefer_numa(None)
        self.free = [(0, nbytes)]
        self.used = 0

    def alloc(self, n):
        need = (n + 63) & ~63
        for i, (off, ln) in enumerate(self.free):
            if ln >= need:
                if ln == need:
                    self.free.pop(i)
                else:
                    self.free[i] = (off + need, ln - need)
                self.used += need
                return off
        return None

    def release(self, off, n):
        need = (n + 63) & ~63
        self.used -= need
        self.free.append((off, need))
        self.free.sort()
        merged = [self.free[0]]
        for o, ln in self.free[1:]:
            po, pl = merged[-1]
            if po + pl == o:
                merged[-1] = (po, pl + ln)
            else:
                merged.append((o, ln))
        self.free = merged

    def close(self):
        for r in self.regs:
            r.close()
        self.regs, self.view, self.t = [], None, None
        if getattr(self, "mm", None) is not None:
            # Return the pages now; a late reference to the mapping must not keep the memory.
            _libc.madvise(ctypes.c_void_p(self.addr), ctypes.c_size_t(self.nbytes), 4)   # MADV_DONTNEED
            try:
                self.mm.close()
            except BufferError:
                pass


def read_into(fd, view, off):
    """pread until `view` is full."""
    got, n = 0, len(view)
    while got < n:
        m = os.preadv(fd, [view[got:]], off + got)
        if m <= 0:
            raise IOError(f"short read at {off + got}")
        got += m


# ---------------------------------------------------------------- restore scheduling
class Sched:
    """Chunk -> path-class assignment that adapts while the restore runs (see module docstring)."""

    def __init__(self, chunks, steal, prior):
        self.chunks, self.steal = chunks, steal
        self.cv = threading.Condition()
        self.state = [0] * len(chunks)                 # 0 unclaimed, 1 claimed, 2 done
        self.left = len(chunks)
        self.planned = collections.defaultdict(collections.deque)
        self.rem = collections.defaultdict(int)        # unclaimed planned bytes per class
        self.rate = dict(prior)                        # class -> GB/s (prior, then measured)
        self.meas = {}                                 # class -> {worker: EWMA GB/s}
        self.stats = collections.defaultdict(lambda: {"bytes": 0, "chunks": 0, "stolen": 0})
        self.failed, self.error, self.why = 0, None, []
        self.nworkers = collections.Counter()          # class -> workers (set by the executor)
        self.claim = {}                                # chunk -> class moving it right now
        self.inflight = collections.Counter()          # class -> chunks whose source is still read
        order = sorted(range(len(chunks)), key=lambda i: len(chunks[i]["classes"]))
        for i in order:
            self._plan(i, chunks[i]["plan"])

    def _plan(self, i, cls):
        self.chunks[i]["plan"] = cls
        self.planned[cls].append(i)
        self.rem[cls] += self.chunks[i]["n"]

    def _est(self, cls):
        return self.rem[cls] / max(self.rate.get(cls, 1.0), 0.05) / 1e9

    def next(self, cls):
        """Blocks until this class has a chunk to move; None when the restore is over."""
        with self.cv:
            while True:
                if self.left == 0 or self.error:
                    return None
                q = self.planned[cls]
                while q:
                    i = q.popleft()
                    if self.state[i] == 0:
                        return self._claim(i, cls, False)
                if self.steal:
                    best, best_t = None, 0.0
                    # one worker moves one chunk at the class rate divided by its workers
                    mine = max(self.rate.get(cls, 1.0), 0.05) * 1e9 / max(1, self.nworkers[cls])
                    for v, vq in self.planned.items():
                        if v == cls or not vq:
                            continue
                        t = self._est(v)
                        if t > best_t:
                            # take from the tail: the head is what the victim reaches next
                            for k in range(len(vq) - 1, max(-1, len(vq) - 33), -1):
                                i = vq[k]
                                if self.state[i] == 0 and cls in self.chunks[i]["classes"] \
                                        and self.chunks[i]["n"] / mine < t:
                                    best, best_t = (v, k, i), t
                                    break
                    if best:
                        v, k, i = best
                        del self.planned[v][k]
                        return self._claim(i, cls, True)
                self.cv.wait(0.05)

    def _claim(self, i, cls, stolen):
        self.state[i] = 1
        self.claim[i] = cls
        self.inflight[cls] += 1
        self.rem[self.chunks[i]["plan"]] -= self.chunks[i]["n"]
        if stolen:
            self.stats[cls]["stolen"] += 1
        return i

    def fetched(self, cls, worker, i, dt):
        n = self.chunks[i]["n"]
        with self.cv:
            m = self.meas.setdefault(cls, {})
            r = n / max(dt, 1e-6) / 1e9
            m[worker] = r if worker not in m else 0.5 * m[worker] + 0.5 * r
            self.rate[cls] = sum(m.values())
            s = self.stats[cls]
            s["bytes"] += n
            s["chunks"] += 1
            if not cls.startswith("l"):                # remote bytes now sit in our staging slot
                self.inflight[cls] -= 1
                self.claim.pop(i, None)
                self.cv.notify_all()

    def landed(self, idxs):
        with self.cv:
            for i in idxs:
                if self.state[i] != 2:
                    self.state[i] = 2
                    self.left -= 1
                cls = self.claim.pop(i, None)          # local sources are read until the H2D ends
                if cls is not None:
                    self.inflight[cls] -= 1
            self.cv.notify_all()

    def fail(self, cls, i, why):
        """A path lost a chunk (source evicted it, link error): hand it to another class."""
        with self.cv:
            c = self.chunks[i]
            c["classes"] = [x for x in c["classes"] if x != cls]
            self.failed += 1
            if len(self.why) < 4:
                self.why.append(f"{cls}: {why}"[:200])
            if self.claim.pop(i, None) is not None:
                self.inflight[cls] -= 1
            if not c["classes"]:
                self.error = f"chunk {c['id'][:12]} has no source left ({why})"
            else:
                self.state[i] = 0
                best = min(c["classes"], key=self._est)
                self._plan(i, best)
            self.cv.notify_all()

    def revoke(self, classes, timeout=10.0):
        """Stop using `classes` as sources (their node is about to drop the chunks). Unclaimed
        chunks move to their best remaining class; returns once nothing is still being read from
        the revoked classes, so the caller may free the memory. Seconds waited is returned."""
        t0 = time.time()
        dead = set(classes)
        with self.cv:
            moved = 0
            for i, c in enumerate(self.chunks):
                if not dead.intersection(c["classes"]):
                    continue
                c["classes"] = [x for x in c["classes"] if x not in dead]
                if self.state[i] == 0 and c["plan"] in dead:
                    if not c["classes"]:
                        self.error = f"chunk {c['id'][:12]} lost its last source (revoked)"
                        break
                    self._plan(i, min(c["classes"], key=self._est))
                    moved += 1
            for d in dead:
                self.rem[d] = 0
                self.planned[d].clear()
            self.cv.notify_all()
            while any(self.inflight[d] > 0 for d in dead) and time.time() - t0 < timeout \
                    and self.left and not self.error:
                self.cv.wait(0.01)
        return {"moved": moved, "wait_s": round(time.time() - t0, 5)}

    def abort(self, why):
        with self.cv:
            self.error = self.error or why
            self.cv.notify_all()


# ---------------------------------------------------------------- the daemon
class NodeD:
    def __init__(self, node, ip, ssd_dir, cards, stage_bytes=6 * GiB, chunk_bytes=64 * MiB,
                 slab_bytes=256 * MiB, spare_slabs=0, log=None):
        import torch
        self.torch, self.node, self.ip, self.ssd_dir = torch, node, ip, ssd_dir
        self.cards = list(cards)
        self.chunk_bytes, self.slab_bytes, self.spare_slabs = chunk_bytes, slab_bytes, spare_slabs
        self.lock, self.grow_lock = threading.RLock(), threading.Lock()
        self.log = log or (lambda **k: print(json.dumps(k), flush=True))
        self.slabs, self.spare = [], []
        self.dram, self.ssd, self.hbm = {}, {}, {}      # cid -> (slab, off, n) | n | (card, tensor)
        self.jobs, self.exposed, self.active = {}, {}, {}
        self.traffic = collections.Counter()            # bytes we moved, by kind (for the graph)
        self.conns, self.rpcs, self.conn_lock = {}, {}, threading.Lock()
        t = time.time()
        for c in self.cards:                            # contexts cost ~14 s each on first touch
            torch.zeros(1, device=f"cuda:{c}")
        self.log(event="contexts", cards=self.cards, s=round(time.time() - t, 2))
        # Staging: GPU-pinned and RDMA-registered once, reused by every restore and expose.
        # One pool per HCA port, allocated on that port's NUMA node, so the NIC writes into local
        # memory. Slots are numbered globally; pool k owns [k * per, (k + 1) * per).
        self.slot_bytes = ((chunk_bytes + 2 * PAGE + PAGE - 1) // PAGE) * PAGE
        self.nic_numa = []
        for name in rdma.devices()[:rdma.PORTS]:
            try:
                self.nic_numa.append(int(open(f"/sys/class/infiniband/{name}/device/numa_node").read()))
            except (OSError, ValueError):
                self.nic_numa.append(None)
        self.per = max(8, stage_bytes // self.slot_bytes // rdma.PORTS)
        t = time.time()
        self.pools = []
        for k in range(rdma.PORTS):
            prefer_numa(self.nic_numa[k])
            try:
                ten = torch.empty(self.per * self.slot_bytes, dtype=torch.uint8, pin_memory=True)
            finally:
                prefer_numa(None)
            assert ten.data_ptr() % PAGE == 0, "pinned staging is not page aligned"
            free = queue.Queue()
            for i in range(self.per):
                free.put(k * self.per + i)
            self.pools.append({"t": ten, "addr": ten.data_ptr(), "view": memoryview(ten.numpy()), "free": free,
                               "regs": [rdma.Region(p, ten.data_ptr(), ten.numel(), True)
                                        for p in range(rdma.PORTS)]})
        self._rr = 0
        nslot = self.per * rdma.PORTS
        for p in range(rdma.PORTS):
            rdma.serve(rdma.port_ip(ip, p), RDMA_PORT)
        if ssd_dir:
            os.makedirs(ssd_dir, exist_ok=True)
            for sub in os.listdir(ssd_dir):             # chunks that survived a restart
                d = os.path.join(ssd_dir, sub)
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        if len(f) == 64:
                            self.ssd[f] = os.path.getsize(os.path.join(d, f))
        self.log(event="ready", node=node, slots=nslot, stage_s=round(time.time() - t, 2),
                 ssd_chunks=len(self.ssd))

    # ---- helpers -------------------------------------------------------------------------
    def _ssd_path(self, cid):
        return os.path.join(self.ssd_dir, cid[:2], cid)

    # staging slots ------------------------------------------------------------------------
    def _sget(self, prefer=None):
        """Take a staging slot, from pool `prefer` when it has one (blocks on it otherwise)."""
        n = len(self.pools)
        if prefer is None:
            self._rr = (self._rr + 1) % n
            prefer = self._rr
        for k in [prefer] + [k for k in range(n) if k != prefer]:
            try:
                return self.pools[k]["free"].get_nowait()
            except queue.Empty:
                pass
        return self.pools[prefer]["free"].get()

    def _sput(self, slot):
        self.pools[slot // self.per]["free"].put(slot)

    def _spool(self, slot):
        return self.pools[slot // self.per], (slot % self.per) * self.slot_bytes

    def _saddr(self, slot):
        pool, base = self._spool(slot)
        return pool["addr"] + base

    def _sview(self, slot):
        pool, base = self._spool(slot)
        return pool["view"][base:base + self.slot_bytes]

    def _sten(self, slot):
        pool, base = self._spool(slot)
        return pool["t"][base:base + self.slot_bytes]

    def _slkey(self, slot, port):
        return self.pools[slot // self.per]["regs"][port].lkey

    def _srkeys(self, slot):
        return [r.rkey for r in self.pools[slot // self.per]["regs"]]

    def _rread(self, peer_ip, port, raddr, rkey, laddr, lkey, n, window=8):
        """RDMA READ over an idle connection to (peer, port); connections are pooled and reused,
        and a connection that failed is closed instead of returned (its QP is in error)."""
        key = (peer_ip, port)
        with self.conn_lock:
            idle = self.conns.setdefault(key, [])
            c = idle.pop() if idle else None
        if c is None:
            c = rdma.Conn(rdma.port_ip(self.ip, port), rdma.port_ip(peer_ip, port), RDMA_PORT)
        try:
            c.read(raddr, rkey, laddr, lkey, n, 4 * MiB, window)
        except Exception:
            try:
                c.close()
            except Exception:                           # noqa: BLE001
                pass
            raise
        with self.conn_lock:
            self.conns[key].append(c)

    def op_warm(self, peers, per_port=2):
        """Open connections to peers ahead of time (a connect costs tens of ms)."""
        t = time.time()
        made = 0
        for ip in peers:
            for port in range(rdma.PORTS):
                key = (ip, port)
                with self.conn_lock:
                    have = len(self.conns.setdefault(key, []))
                for _ in range(max(0, per_port - have)):
                    c = rdma.Conn(rdma.port_ip(self.ip, port), rdma.port_ip(ip, port), RDMA_PORT)
                    with self.conn_lock:
                        self.conns[key].append(c)
                    made += 1
        return {"made": made, "s": round(time.time() - t, 3)}

    def _slab_alloc(self, n, numa, pin):
        with self.lock:
            for s in self.slabs:
                if s.pin == bool(pin) and (numa is None or s.numa == numa):
                    off = s.alloc(n)
                    if off is not None:
                        return s, off
            for k, s in enumerate(self.spare):
                if s.pin == bool(pin) and s.nbytes >= n:
                    self.spare.pop(k)
                    s.numa = numa
                    self.slabs.append(s)
                    return s, s.alloc(n)
        with self.grow_lock:                            # one grower at a time; re-check first
            with self.lock:
                for s in self.slabs:
                    if s.pin == bool(pin) and (numa is None or s.numa == numa):
                        off = s.alloc(n)
                        if off is not None:
                            return s, off
            s = Slab(max(self.slab_bytes, n), numa, pin, self.torch)
            with self.lock:
                self.slabs.append(s)
                return s, s.alloc(n)

    def _reserve(self, need, numa, pin):
        """Make room for `need` more bytes on (numa, pin) before a put starts filling, so that
        parallel fillers share slabs instead of each opening its own."""
        with self.grow_lock:
            with self.lock:
                have = sum(s.nbytes - s.used for s in self.slabs
                           if s.pin == bool(pin) and (numa is None or s.numa == numa))
            deficit = int(need * 1.02) + self.chunk_bytes - have
            if deficit <= 0:
                return 0
            count = -(-deficit // self.slab_bytes)
            made, lock = [], threading.Lock()

            def one():
                s = Slab(self.slab_bytes, numa, pin, self.torch)
                with lock:
                    made.append(s)
            th = [threading.Thread(target=one) for _ in range(count)]
            for i in range(0, count, 8):                # pinning is ~3 GB/s per thread
                [x.start() for x in th[i:i + 8]]
                [x.join() for x in th[i:i + 8]]
            with self.lock:
                self.slabs += made
            return count

    def _locator(self, cid):
        s, off, n = self.dram[cid]
        return {"addr": s.addr + off, "rkeys": [r.rkey for r in s.regs], "pin": s.pin, "numa": s.numa}

    # ---- storage ops ---------------------------------------------------------------------
    def op_hello(self):
        return {"node": self.node, "ip": self.ip, "cards": self.cards, "chunk_bytes": self.chunk_bytes,
                "slot_bytes": self.slot_bytes, "pid": os.getpid(), "nic_numa": self.nic_numa}

    def op_stat(self):
        with self.lock:
            by_numa = collections.Counter()
            for s in self.slabs:
                by_numa[str(s.numa)] += s.used
            by_card = collections.Counter()
            for cid, (card, t) in self.hbm.items():
                by_card[str(card)] += t.numel()
            return {"node": self.node, "dram_chunks": len(self.dram), "dram_bytes": dict(by_numa),
                    "slab_bytes": sum(s.nbytes for s in self.slabs), "spare_bytes": sum(s.nbytes for s in self.spare),
                    "ssd_chunks": len(self.ssd), "ssd_bytes": sum(self.ssd.values()),
                    "hbm_chunks": len(self.hbm), "hbm_bytes": dict(by_card),
                    "jobs": {j: sum(b.numel() for b in v["bufs"].values()) for j, v in self.jobs.items()},
                    "traffic": dict(self.traffic), "t": time.time()}

    def op_put(self, items, tier, numa=None, pin=False, card=None, threads=8):
        """Store chunks. items: [{id, n, src}] with src one of
             {"k": "file", "path", "off"}                      read from a file (O_DIRECT)
             {"k": "rdma", "ip", "addr", "rkeys"}              pull from a peer's DRAM
             {"k": "rfile", "ip", "path", "off"}               pull a peer's file range (it stages it)
             {"k": "local"}                                    copy from this node's DRAM tier
        Returns {id: locator} for DRAM, else the stored ids."""
        t0 = time.time()
        if tier == "dram":
            with self.lock:
                need = sum(it["n"] for it in {it["id"]: it for it in items}.values() if it["id"] not in self.dram)
            self._reserve(need, numa, pin)
        reserve_s = time.time() - t0
        todo = collections.deque(enumerate(items))
        out, errs, lock = {}, [], threading.Lock()
        fds = {}

        def fd_of(path):
            with lock:
                if path not in fds:
                    fds[path] = os.open(path, os.O_RDONLY | os.O_DIRECT)
                return fds[path]

        def fill(view, addr, it, w):
            src = it["src"]
            if src["k"] == "file":
                # O_DIRECT through an aligned staging slot: a buffered read would turn free
                # memory into page cache and look like a tenant squeezing this very node.
                slot = self._sget(w % rdma.PORTS)
                try:
                    delta = self._direct_read(fd_of(src["path"]), slot, src["off"], it["n"])
                    ctypes.memmove(addr, self._saddr(slot) + delta, it["n"])
                finally:
                    self._sput(slot)
                self.traffic["file_rd"] += it["n"]
            elif src["k"] == "local":
                s, off, n = self.dram[it["id"]]
                view[:] = s.view[off:off + n]
            elif src["k"] == "rfile":                       # a peer's durable file, via its staging
                port = w % rdma.PORTS
                reg, _ = self._region_of(addr, port)
                cl = self._rpc(src["ip"], 200 + w)
                e = cl.call("expose", cid=it["id"], tier="file", path=src["path"], off=src["off"], n=it["n"])
                try:
                    self._rread(src["ip"], port, e["addr"], e["rkeys"][port], addr, reg.lkey, it["n"])
                finally:
                    cl.call("unexpose", token=e["token"])
                self.traffic["rdma_rx"] += it["n"]
            else:                                           # peer DRAM -> here over RDMA
                port = w % rdma.PORTS
                reg, base = self._region_of(addr, port)
                self._rread(src["ip"], port, src["addr"], src["rkeys"][port], addr, reg.lkey, it["n"])
                self.traffic["rdma_rx"] += it["n"]

        def work(w):
            while True:
                with lock:
                    if not todo or errs:
                        return
                    _, it = todo.popleft()
                cid, n = it["id"], it["n"]
                try:
                    if tier == "dram":
                        with self.lock:
                            have = cid in self.dram
                        if not have:
                            s, off = self._slab_alloc(n, numa, pin)
                            fill(s.view[off:off + n], s.addr + off, it, w)
                            with self.lock:
                                self.dram[cid] = (s, off, n)
                        with lock:
                            out[cid] = self._locator(cid)
                    elif tier == "ssd":
                        if cid not in self.ssd:
                            slot = self._sget(w % rdma.PORTS)
                            try:
                                view = self._sview(slot)[:n]
                                fill(view, self._saddr(slot), it, w)
                                path = self._ssd_path(cid)
                                os.makedirs(os.path.dirname(path), exist_ok=True)
                                fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                                try:
                                    done = 0
                                    while done < n:
                                        done += os.write(fd, view[done:])
                                    os.fsync(fd)
                                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                                finally:
                                    os.close(fd)
                                os.replace(path + ".tmp", path)
                                self.traffic["ssd_wr"] += n
                            finally:
                                self._sput(slot)
                            with self.lock:
                                self.ssd[cid] = n
                        with lock:
                            out[cid] = True
                    elif tier == "hbm":
                        with self.lock:
                            have = cid in self.hbm
                        if not have:
                            slot = self._sget(w % rdma.PORTS)
                            try:
                                fill(self._sview(slot)[:n], self._saddr(slot), it, w)
                                g = self.torch.empty(n, dtype=self.torch.uint8, device=f"cuda:{card}")
                                g.copy_(self._sten(slot)[:n])
                            finally:
                                self._sput(slot)
                            with self.lock:
                                self.hbm[cid] = (card, g)
                        with lock:
                            out[cid] = True
                    else:
                        raise ValueError(tier)
                except Exception as e:                      # noqa: BLE001
                    with lock:
                        errs.append(f"{cid[:12]}: {e!r}")
                    traceback.print_exc()

        th = [threading.Thread(target=work, args=(w,)) for w in range(min(threads, max(1, len(items))))]
        [x.start() for x in th]
        [x.join() for x in th]
        for fd in fds.values():
            os.close(fd)
        if errs:
            raise RuntimeError("; ".join(errs[:3]))
        nbytes = sum(it["n"] for it in items)
        return {"stored": out, "s": round(time.time() - t0, 4), "bytes": nbytes, "reserve_s": round(reserve_s, 4)}

    def _region_of(self, addr, port):
        """The RDMA region (on `port`) that contains a local address: a slab or the staging pool."""
        for pool in self.pools:
            if pool["addr"] <= addr < pool["addr"] + pool["t"].numel():
                return pool["regs"][port], pool["addr"]
        with self.lock:
            for s in self.slabs:
                if s.addr <= addr < s.addr + s.nbytes:
                    return s.regs[port], s.addr
        raise KeyError("address not in a registered region")

    def op_reset(self, ssd=True):
        """Forget everything this daemon holds (between experiments)."""
        for j in list(self.jobs):
            self.op_job_release(j)
        with self.lock:
            d, h, sd = list(self.dram), list(self.hbm), list(self.ssd)
        out = {"dram": self.op_drop(d, "dram")["freed"], "hbm": self.op_drop(h, "hbm")["freed"]}
        if ssd:
            out["ssd"] = self.op_drop(sd, "ssd")["freed"]
        return out

    def op_locate(self, ids):
        with self.lock:
            return {cid: self._locator(cid) for cid in ids if cid in self.dram}

    def op_drop(self, ids, tier):
        freed = 0
        dead = []
        with self.lock:
            for cid in ids:
                if tier == "dram" and cid in self.dram:
                    s, off, n = self.dram.pop(cid)
                    s.release(off, n)
                    freed += n
                elif tier == "ssd" and cid in self.ssd:
                    freed += self.ssd.pop(cid)
                    try:
                        os.unlink(self._ssd_path(cid))
                    except OSError:
                        pass
                elif tier == "hbm" and cid in self.hbm:
                    freed += self.hbm.pop(cid)[1].numel()
            if tier == "dram":
                for s in [s for s in self.slabs if s.used == 0]:
                    self.slabs.remove(s)
                    if len(self.spare) < self.spare_slabs:
                        s.free = [(0, s.nbytes)]
                        self.spare.append(s)
                    else:
                        dead.append(s)
        t = time.time()
        for s in dead:                                      # returns the memory to the OS
            s.close()
        if tier == "hbm":
            self.torch.cuda.empty_cache()
        return {"freed": freed, "slabs_released": len(dead), "release_s": round(time.time() - t, 4)}

    def op_expose(self, cid, tier, path=None, off=0, n=None):
        """Stage an SSD chunk, an HBM chunk or a byte range of a durable file in registered
        memory so a peer can RDMA-READ it."""
        slot = self._sget()
        delta = 0
        try:
            if tier in ("ssd", "file"):
                if tier == "ssd":
                    n, path, off = self.ssd[cid], self._ssd_path(cid), 0
                fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
                try:
                    delta = self._direct_read(fd, slot, off, n)
                finally:
                    os.close(fd)
                self.traffic["ssd_rd" if tier == "ssd" else "file_rd"] += n
            else:
                card, g = self.hbm[cid]
                n = g.numel()
                self._sten(slot)[:n].copy_(g)
        except Exception:
            self._sput(slot)
            raise
        tok = f"{slot}-{time.time():.6f}"
        self.exposed[tok] = slot
        return {"token": tok, "addr": self._saddr(slot) + delta, "rkeys": self._srkeys(slot), "n": n}

    def op_unexpose(self, token):
        slot = self.exposed.pop(token, None)
        if slot is not None:
            self._sput(slot)
        return True

    def _direct_read(self, fd, slot, off, n):
        """O_DIRECT read of file range [off, off+n) into staging slot `slot`.
        Returns the offset inside the slot where the range starts."""
        view = self._sview(slot)
        a = off & ~(PAGE - 1)
        ln = ((off + n + PAGE - 1) & ~(PAGE - 1)) - a
        got = 0
        while got < off - a + n:
            m = os.preadv(fd, [view[got:ln]], a + got)
            if m <= 0:
                break
            got += m
        if got < off - a + n:
            raise IOError(f"direct read short: {got} < {off - a + n}")
        return off - a

    # ---- restore -------------------------------------------------------------------------
    def op_restore(self, job, cards, chunks, steal=True, prior=None, ssd_readers=4, file_readers=8,
                   rdma_window=8, ports=None):
        """Land `chunks` into fresh HBM buffers. cards: {card: nbytes}. Each chunk:
             {id, n, card, off, cands: [cand], plan: index into cands}
        cand: {"k": "ldram"} | {"k": "lssd"} | {"k": "lhbm"} | {"k": "dram", "node", "ip", "addr",
               "rkeys"} | {"k": "rstage", "node", "ip", "tier"} | {"k": "file", "path", "off"}.
        ports: HCA ports this restore may receive on. A port that shares a PCIe root (NUMA node)
        with a destination card throttles that card's H2D (measured: 47 -> 9..30 GB/s), so the
        coordinator leaves those out."""
        ports = list(ports) if ports else list(range(rdma.PORTS))
        torch = self.torch
        t0 = time.time()
        bufs = {int(c): torch.empty(int(n), dtype=torch.uint8, device=f"cuda:{int(c)}") for c, n in cards.items()}
        alloc_s = time.time() - t0

        def cls_of(c):
            k = c["k"]
            return f"{k}:{c['node']}" if k in ("dram", "rstage") else k

        for c in chunks:
            c["by"] = {}
            for cand in c["cands"]:
                c["by"].setdefault(cls_of(cand), cand)
            c["classes"] = list(c["by"])
            c["plan"] = cls_of(c["cands"][c.get("plan", 0)])
        sched = Sched(chunks, steal, prior or {})
        self.active[job] = sched
        landq = {c: queue.Queue() for c in bufs}
        first = [None]
        prof = collections.Counter()                    # where the wall time of this restore went

        def lander(card):
            dev, q, buf = f"cuda:{card}", landq[card], bufs[card]
            while True:
                item = q.get()
                if item is None:
                    return
                batch = [item]
                while len(batch) < 16:
                    try:
                        nxt = q.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        q.put(None)
                        break
                    batch.append(nxt)
                tl = time.time()
                for i, src, slot in batch:
                    c = chunks[i]
                    buf[c["off"]:c["off"] + c["n"]].copy_(src, non_blocking=True)
                torch.cuda.synchronize(dev)
                prof["land_s"] += time.time() - tl
                prof["land_batches"] += 1
                if first[0] is None:
                    first[0] = time.time() - t0
                for i, src, slot in batch:
                    if slot is not None:
                        self._sput(slot)
                sched.landed([b[0] for b in batch])

        def worker(cls, w, port=None):
            while True:
                i = sched.next(cls)
                if i is None:
                    return
                c = chunks[i]
                cand, n = c["by"][cls], c["n"]
                slot = None
                t = time.time()
                try:
                    k = cand["k"]
                    if k == "ldram":
                        s, off, _ = self.dram[c["id"]]
                        src = s.t[off:off + n]
                    elif k == "lhbm":
                        src = self.hbm[c["id"]][1]
                    else:
                        p = port if port is not None else ports[w % len(ports)]
                        slot = self._sget(p)
                        prof["slot_wait_s"] += time.time() - t
                        t = time.time()
                        delta = 0
                        if k == "dram":
                            self._rread(cand["ip"], port, cand["addr"], cand["rkeys"][port],
                                        self._saddr(slot), self._slkey(slot, port), n, rdma_window)
                            self.traffic["rdma_rx"] += n
                        elif k == "rstage":
                            cl = self._rpc(cand["ip"], w)
                            e = cl.call("expose", cid=c["id"], tier=cand["tier"], path=cand.get("path"),
                                        off=cand.get("off", 0), n=n)
                            try:
                                self._rread(cand["ip"], p, e["addr"], e["rkeys"][p], self._saddr(slot),
                                            self._slkey(slot, p), n, rdma_window)
                            finally:
                                cl.call("unexpose", token=e["token"])
                            self.traffic["rdma_rx"] += n
                        elif k == "lssd":
                            fd = os.open(self._ssd_path(c["id"]), os.O_RDONLY | os.O_DIRECT)
                            try:
                                delta = self._direct_read(fd, slot, 0, n)
                            finally:
                                os.close(fd)
                            self.traffic["ssd_rd"] += n
                        elif k == "file":
                            fd = os.open(cand["path"], os.O_RDONLY | os.O_DIRECT)
                            try:
                                delta = self._direct_read(fd, slot, cand["off"], n)
                            finally:
                                os.close(fd)
                            self.traffic["file_rd"] += n
                        else:
                            raise ValueError(k)
                        src = self._sten(slot)[delta:delta + n]
                except Exception as e:                      # noqa: BLE001 -- shed to another path
                    if slot is not None:
                        self._sput(slot)
                    sched.fail(cls, i, repr(e))
                    continue
                dt = time.time() - t
                prof["fetch_s:" + cls] += dt
                sched.fetched(cls, w, i, dt)
                landq[c["card"]].put((i, src, slot))

        threads = [threading.Thread(target=lander, args=(c,), daemon=True) for c in bufs]
        classes = {cl for c in chunks for cl in c["classes"]}
        w = 0
        for cl in sorted(classes):
            kind = cl.split(":")[0]
            if kind == "dram":
                for p in ports:
                    threads.append(threading.Thread(target=worker, args=(cl, w, p), daemon=True))
                    sched.nworkers[cl] += 1
                    w += 1
            else:
                reps = {"lssd": ssd_readers, "file": file_readers, "rstage": 4}.get(kind, 2)
                for _ in range(reps):
                    threads.append(threading.Thread(target=worker, args=(cl, w), daemon=True))
                    sched.nworkers[cl] += 1
                    w += 1
        nland = len(bufs)
        [x.start() for x in threads]
        [x.join() for x in threads[nland:]]
        for q in landq.values():
            q.put(None)
        [x.join() for x in threads[:nland]]
        total = time.time() - t0
        self.active.pop(job, None)
        if sched.error:
            del bufs
            torch.cuda.empty_cache()
            raise RuntimeError(sched.error)
        self.jobs[job] = {"bufs": bufs, "chunks": [(c["id"], c["card"], c["off"], c["n"]) for c in chunks]}
        nbytes = sum(c["n"] for c in chunks)
        return {"job": job, "bytes": nbytes, "total_s": round(total, 4), "alloc_s": round(alloc_s, 4),
                "first_land_s": None if first[0] is None else round(first[0], 4),
                "GBps": round(nbytes / max(total, 1e-9) / 1e9, 2), "failed_fetches": sched.failed, "fail_why": sched.why,
                "paths": {k: v for k, v in sched.stats.items()},
                "prof": {k: round(v, 4) for k, v in prof.items()}, "workers": len(threads) - nland,
                "rates_GBps": {k: round(v, 2) for k, v in sched.rate.items()}}

    def op_revoke(self, node, tiers=("dram",)):
        """Called before `node` drops chunks: every running restore stops reading from it."""
        out = {}
        for job, sched in list(self.active.items()):
            classes = []
            for t in tiers:
                if node == self.node:
                    classes.append({"dram": "ldram", "ssd": "lssd", "hbm": "lhbm"}[t])
                else:
                    classes.append(f"dram:{node}" if t == "dram" else f"rstage:{node}")
            out[job] = sched.revoke(classes)
        return out

    def op_job_export(self, job):
        """HIP IPC handles of a job's HBM buffers, for a zero-copy attach by another process."""
        import base64
        out = {}
        for card, buf in self.jobs[job]["bufs"].items():
            h = buf.untyped_storage()._share_cuda_()
            out[str(card)] = [base64.b64encode(x).decode() if isinstance(x, bytes) else x for x in h]
        return out

    def _rpc(self, ip, w):
        key = (ip, w)
        with self.conn_lock:
            c = self.rpcs.get(key)
            if c is None:
                c = self.rpcs[key] = Client(ip)
            return c

    def op_job_verify(self, job, threads=16):
        """sha256 every landed chunk straight out of HBM and compare with its content id."""
        j = self.jobs[job]
        todo, bad, lock = collections.deque(j["chunks"]), [], threading.Lock()
        t0 = time.time()

        def work():
            while True:
                with lock:
                    if not todo:
                        return
                    cid, card, off, n = todo.popleft()
                data = j["bufs"][card][off:off + n].cpu().numpy()
                if hashlib.sha256(data).hexdigest() != cid:
                    with lock:
                        bad.append(cid)
        th = [threading.Thread(target=work) for _ in range(threads)]
        [x.start() for x in th]
        [x.join() for x in th]
        return {"chunks": len(j["chunks"]), "bad": len(bad), "s": round(time.time() - t0, 2)}

    def op_job_release(self, job):
        j = self.jobs.pop(job, None)
        n = 0
        if j:
            n = sum(b.numel() for b in j["bufs"].values())
            j["bufs"].clear()
            self.torch.cuda.empty_cache()
        return {"freed": n}

    # ---- serving -------------------------------------------------------------------------
    def serve(self, port=RPC_PORT):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.ip, port))
        srv.listen(128)
        self.log(event="serving", node=self.node, rpc=f"{self.ip}:{port}")
        while True:
            conn, _ = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            while True:
                try:
                    msg = recv_msg(conn)
                except (ConnectionError, OSError):
                    return
                op = msg.pop("op")
                try:
                    if op == "shutdown":
                        send_msg(conn, {"ok": True})
                        os._exit(0)
                    out = {"ok": getattr(self, "op_" + op)(**msg)}
                except Exception as e:                      # noqa: BLE001
                    traceback.print_exc()
                    out = {"error": f"{type(e).__name__}: {e}"}
                try:
                    send_msg(conn, out)
                except OSError:
                    return


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--node", required=True)
    p.add_argument("--ip", required=True)
    p.add_argument("--ssd", default="/ssdpool/chunks")
    p.add_argument("--cards", default="")
    p.add_argument("--stage-gib", type=float, default=8)
    p.add_argument("--chunk-mib", type=int, default=64)
    p.add_argument("--slab-mib", type=int, default=256)
    p.add_argument("--spare-slabs", type=int, default=0)
    a = p.parse_args(argv)
    try:                                                # keep-alive must never outlive a tenant
        open("/proc/self/oom_score_adj", "w").write("900")
    except OSError:
        pass
    cards = [int(x) for x in a.cards.split(",") if x != ""]
    d = NodeD(a.node, a.ip, a.ssd or None, cards, int(a.stage_gib * GiB), a.chunk_mib * MiB,
              a.slab_mib * MiB, a.spare_slabs)
    d.serve()


if __name__ == "__main__":
    main()
