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

_HIP = None


def _hip_pull():
    """libamdhip64 for target-side pulls over xGMI (hipMemcpyAsync on the target card's stream)."""
    global _HIP
    if _HIP is None:
        h = ctypes.CDLL("libamdhip64.so")
        h.hipMemcpyAsync.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p]
        h.hipMemcpyAsync.restype = ctypes.c_int
        _HIP = h
    return _HIP

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


_hip = [None]


def _hip_lib():
    """libamdhip64, for hipHostRegister / hipHostUnregister on memory we map ourselves."""
    if _hip[0] is None:
        try:
            h = ctypes.CDLL("libamdhip64.so")
            h.hipHostRegister.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
            h.hipHostRegister.restype = ctypes.c_int
            h.hipHostUnregister.argtypes = [ctypes.c_void_p]
            h.hipHostUnregister.restype = ctypes.c_int
            _hip[0] = h
        except OSError:
            _hip[0] = False
    return _hip[0]


def numa_of_regions(regions):
    """{numa node: bytes} actually backing the given [(addr, nbytes)] regions of this process,
    from /proc/self/numa_maps (move_pages is not permitted in the container). Adjacent anonymous
    mappings merge into one VMA, so the count is taken over every VMA that contains a region."""
    import bisect
    import re
    vmas = []
    for line in open("/proc/self/maps"):
        a, b = line.split()[0].split("-")
        vmas.append((int(a, 16), int(b, 16)))
    vmas.sort()
    starts = [v[0] for v in vmas]
    want = set()
    for addr, _ in regions:
        i = bisect.bisect_right(starts, addr) - 1
        if i >= 0 and vmas[i][0] <= addr < vmas[i][1]:
            want.add(vmas[i][0])
    out = collections.Counter()
    for line in open("/proc/self/numa_maps"):
        f = line.split()
        if int(f[0], 16) in want:
            for x in f[1:]:
                m = re.match(r"N(\d+)=(\d+)$", x)
                if m:
                    out[int(m.group(1))] += int(m.group(2)) * PAGE
    return dict(out)


class Slab:
    """A contiguous host region carved into chunk extents (first fit, coalescing free list)."""

    def __init__(self, nbytes, numa, pin, torch=None):
        self.nbytes, self.numa, self.pin = nbytes, numa, bool(pin)
        self.registered, self.closed, self.regs, self.plock = False, False, [], threading.Lock()
        # The slab is mapped, bound and touched by us and handed to the GPU runtime with
        # hipHostRegister (gpu_register). torch.empty(pin_memory=True) must not be used here: its
        # allocation ignores the thread's NUMA policy (asked for node 5 / 2, got node 4 both
        # times) and its caching allocator never returns freed blocks to the OS -- 163 GiB of
        # "freed" slabs sat on NUMA 0/1/3 of every node, the coordinator's per-vertex accounting
        # was fiction and the pool evicted 180 GiB of its own chunks with no tenant present
        # (maas2_r01). Registered mmap memory: right node, H2D 45-48 GB/s, D2H 46.6 GB/s, freed
        # on close. hipHostRegister does not scale (1.3 GiB/s per process however many threads),
        # so it is done later by the daemon's pinner thread: a slab is usable at once -- peers
        # RDMA-READ it, D2H / H2D run at pageable speed -- and reaches PCIe rate when pinned.
        prefer_numa(numa, strict=numa is not None)
        try:
            self.mm = mmap.mmap(-1, nbytes, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
            self.addr = ctypes.addressof(ctypes.c_char.from_buffer(self.mm))
            ctypes.memset(self.addr, 0, nbytes)         # fault every page in under the bind policy
            self.view = memoryview(self.mm)
            self.t = None if torch is None else torch.frombuffer(self.mm, dtype=torch.uint8)
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

    def gpu_register(self):
        with self.plock:
            if self.closed or self.registered or not self.pin:
                return False
            h = _hip_lib()
            if h and h.hipHostRegister(self.addr, self.nbytes, 0) == 0:
                self.registered = True
            return self.registered

    def close(self):
        """Unregister and give the pages back to the OS now. The mapping itself goes away with
        the last Python view of it, so a stale reader sees zeros instead of a segfault."""
        for r in self.regs:
            r.close()
        self.regs = []
        with self.plock:
            self.closed = True
            if self.registered:
                _hip_lib().hipHostUnregister(self.addr)
                self.registered = False
        self.t = self.view = None
        try:
            self.mm.madvise(mmap.MADV_DONTNEED)
        except (ValueError, OSError):
            pass
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

    def __init__(self, chunks, steal, prior, key=None):
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
        self.edge = {}                                 # (class, card) -> GB/s, when paths differ by card
        self.busy = collections.Counter()              # card -> direct streams writing it right now
        self.claim = {}                                # chunk -> class moving it right now
        self.inflight = collections.Counter()          # class -> chunks whose source is still read
        order = sorted(range(len(chunks)), key=key or (lambda i: len(chunks[i]["classes"])))
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
                    edge = self.edge
                    for v, vq in self.planned.items():
                        if v == cls or not vq:
                            continue
                        t = self._est(v)
                        if t > best_t:
                            # take from the tail: the head is what the victim reaches next
                            for k in range(len(vq) - 1, max(-1, len(vq) - 33), -1):
                                i = vq[k]
                                # a port's speed depends on the card it writes: judge the pair
                                card = self.chunks[i].get("card")
                                me = edge.get((cls, card), 0) * 1e9 or mine
                                # a card takes ~47 GB/s in all: a second port on a card that is
                                # already being written gets 3-6 GB/s and helps nobody
                                # -- unless the port feeding it is itself starved (its source
                                # or its receive side is taken by a tenant): then the card has
                                # ingress to spare and another port's bytes are a net gain.
                                if cls.startswith("net:") and self.busy[card] > 0 and \
                                        self.rate.get(v, 47.0) >= 0.5 * (edge.get((v, card), 0) or 47.0):
                                    continue
                                if self.state[i] == 0 and cls in self.chunks[i]["classes"] \
                                        and self.chunks[i]["n"] / me < t:
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
        if cls.startswith("net:"):
            self.busy[self.chunks[i].get("card")] += 1
        self.rem[self.chunks[i]["plan"]] -= self.chunks[i]["n"]
        if stolen:
            self.stats[cls]["stolen"] += 1
        return i

    def observe(self, cls, key, gbps):
        """Rate of a class measured somewhere else than its fetch (local sources are 'fetched'
        instantly; what limits them is the H2D that follows, seen by the lander)."""
        with self.cv:
            m = self.meas.setdefault(cls, {})
            m[key] = gbps if key not in m else 0.5 * m[key] + 0.5 * gbps
            self.rate[cls] = sum(m.values())

    def retire(self, idxs, classes):
        """Chunks `idxs` can no longer be served by `classes` (their last source there is gone).
        Unclaimed ones that were planned on them move to their best remaining class."""
        dead, moved = set(classes), 0
        with self.cv:
            for i in idxs:
                c = self.chunks[i]
                c["classes"] = [x for x in c["classes"] if x not in dead]
                if self.state[i] == 0 and c["plan"] in dead:
                    try:
                        self.planned[c["plan"]].remove(i)
                        self.rem[c["plan"]] -= c["n"]
                    except ValueError:
                        pass
                    if not c["classes"]:
                        self.error = f"chunk {c['id'][:12]} lost its last source"
                        break
                    self._plan(i, min(c["classes"], key=self._est))
                    moved += 1
            self.cv.notify_all()
        return moved

    def fetched(self, cls, worker, i, dt, rate=True):
        n = self.chunks[i]["n"]
        with self.cv:
            if rate:
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
                if cls.startswith("net:"):
                    self.busy[self.chunks[i].get("card")] -= 1
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

    def fail(self, cls, i, why, also=()):
        """A path lost a chunk (source evicted it, link error): hand it to another class."""
        with self.cv:
            c = self.chunks[i]
            gone = {cls, *also}
            c["classes"] = [x for x in c["classes"] if x not in gone]
            self.failed += 1
            if len(self.why) < 4:
                self.why.append(f"{cls}: {why}"[:200])
            if self.claim.pop(i, None) is not None:
                self.inflight[cls] -= 1
                if cls.startswith("net:"):
                    self.busy[c.get("card")] -= 1
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
                 slab_bytes=256 * MiB, spare_slabs=0, log=None, arena_bytes=0):
        import torch
        self.torch, self.node, self.ip, self.ssd_dir = torch, node, ip, ssd_dir
        self.cards = list(cards)
        self.chunk_bytes, self.slab_bytes, self.spare_slabs = chunk_bytes, slab_bytes, spare_slabs
        self.lock = threading.RLock()
        self.grow_locks = collections.defaultdict(threading.Lock)   # (numa, pin) -> one grower at a time
        self.log = log or (lambda **k: print(json.dumps(k), flush=True))
        self.slabs, self.spare = [], []
        self.pin_q = queue.Queue()                      # slabs waiting for hipHostRegister
        threading.Thread(target=self._pinner, daemon=True).start()
        self.dram, self.ssd, self.hbm = {}, {}, {}      # cid -> (slab, off, n) | n | (card, tensor)
        self.jobs, self.exposed, self.active = {}, {}, {}
        self.traffic = collections.Counter()            # bytes we moved, by kind (for the graph)
        self.conns, self.rpcs, self.conn_lock = {}, {}, threading.Lock()
        # One direct stream per HCA port at a time, across ALL restores running on this node:
        # concurrent jobs take turns chunk by chunk instead of multiplexing a port (which
        # collapses it: 70 GB/s all-to-all vs 187 GB/s one stream per port).
        self.port_lock = [threading.Lock() for _ in range(rdma.PORTS)]
        t = time.time()
        th = [threading.Thread(target=lambda c=c: torch.zeros(1, device=f"cuda:{c}")) for c in self.cards]
        [x.start() for x in th]                         # contexts cost ~14 s each on first touch
        [x.join() for x in th]
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
        # HBM arenas: one long-lived, RDMA-registered device buffer per serving card. Restores
        # land in ranges of it instead of freshly allocated memory, because direct RDMA into a
        # device buffer warms up per registration (70 -> 165+ GB/s over tens of seconds of
        # traffic, kept while idle, lost when the buffer is freed; pool_r02/affinity/
        # gpudirect_range_node28.log). The daemon is the card's page pool; engines attach by IPC.
        self.arena = {}                                 # card -> {"t", "regs", "free": [(off, len)], "nbytes"}
        self.arena_lock = threading.Lock()
        for c in self.cards:
            if arena_bytes:
                self._arena_make(c, arena_bytes)
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

    # HBM arenas ---------------------------------------------------------------------------
    def _arena_make(self, card, nbytes):
        t = self.torch.empty(int(nbytes), dtype=self.torch.uint8, device=f"cuda:{card}")
        regs = {p: rdma.Region(p, t.data_ptr(), t.numel(), True) for p in range(rdma.PORTS)}
        with self.arena_lock:
            self.arena[card] = {"t": t, "regs": regs, "free": [(0, t.numel())], "nbytes": t.numel(), "warm_GBps": None}

    def _arena_alloc(self, card, n, top=False):
        """First fit, 2 MiB aligned. Returns the offset or None (no arena / no room). Restored
        models grow from the bottom of the arena and parked chunks (top=True) from its top, so a
        card can keep parked chunks while a model that leaves room for them is loaded."""
        need = (int(n) + (2 * MiB - 1)) & ~(2 * MiB - 1)
        with self.arena_lock:
            a = self.arena.get(card)
            if a is None:
                return None
            order = range(len(a["free"]) - 1, -1, -1) if top else range(len(a["free"]))
            for i in order:
                off, ln = a["free"][i]
                if ln >= need:
                    if ln == need:
                        a["free"].pop(i)
                    elif top:
                        a["free"][i] = (off, ln - need)
                        return off + ln - need
                    else:
                        a["free"][i] = (off + need, ln - need)
                    return off
        return None

    def _arena_free(self, card, off, n):
        need = (int(n) + (2 * MiB - 1)) & ~(2 * MiB - 1)
        with self.arena_lock:
            a = self.arena.get(card)
            if a is None:
                return
            a["free"].append((off, need))
            a["free"].sort()
            merged = [a["free"][0]]
            for o, ln in a["free"][1:]:
                po, pl = merged[-1]
                if po + pl == o:
                    merged[-1] = (po, pl + ln)
                else:
                    merged.append((o, ln))
            a["free"] = merged

    def op_arena(self, card, gib):
        """Create (gib > 0) or give back (gib == 0) the arena of a card -- e.g. another tenant
        needs the card. Refused while a restored model still lives in it."""
        card = int(card)
        with self.arena_lock:
            a = self.arena.get(card)
            if a is not None:
                if a["free"] != [(0, a["nbytes"])]:
                    raise RuntimeError(f"arena of card {card} is in use")
                del self.arena[card]
        if a is not None:
            for r in a["regs"].values():
                r.close()
            a["t"] = None
            self.torch.cuda.empty_cache()
        if gib > 0:
            self._arena_make(card, int(gib * GiB))
        return {"card": card, "GiB": gib}

    def op_hbm_room(self, card, nbytes):
        """Which parked chunks of `card` must go so that a contiguous arena range of `nbytes`
        exists (a restore lands in one range per card). Pure planning: nothing is dropped here,
        the coordinator drops the ids (after revoking readers) and then restores.
        Returns {"ok", "ids", "bytes"}; ok=False if ranges held by restored models are in the way."""
        card = int(card)
        need = (int(nbytes) + (2 * MiB - 1)) & ~(2 * MiB - 1)
        with self.lock, self.arena_lock:                     # same order as op_drop
            a = self.arena.get(card)
            if a is None:
                return {"ok": True, "ids": [], "bytes": 0}       # no arena: fresh allocation
            segs = [(off, ln, None) for off, ln in a["free"]]
            for cid, h in self.hbm.items():
                if h["card"] == card and h["off"] is not None:
                    segs.append((h["off"], (h["n"] + (2 * MiB - 1)) & ~(2 * MiB - 1), cid))
            segs.sort()
        best = None
        for i in range(len(segs)):
            got, cost, ids, end = 0, 0, [], segs[i][0]
            for off, ln, cid in segs[i:]:
                if off != end:                                # a restored model sits in between
                    break
                got, end = got + ln, off + ln
                if cid is not None:
                    cost += ln
                    ids.append(cid)
                if got >= need:
                    if best is None or cost < best[0]:
                        best = (cost, ids)
                    break
            if best and best[0] == 0:
                break
        if best is None:
            return {"ok": False, "ids": [], "bytes": 0}
        return {"ok": True, "ids": best[1], "bytes": best[0]}

    def op_stage_region(self):
        """A registered host region a peer may read from (used as the source of arena warm-up)."""
        return [{"addr": pool["addr"], "rkeys": [r.rkey for r in pool["regs"]], "nbytes": pool["t"].numel()}
                for pool in self.pools]

    def op_warm_hbm(self, peer_ip, region, seconds=60.0, pairs=None, target_GBps=40.0):
        """Run direct RDMA traffic from a peer's host region into the free arenas until every
        (port, card) stream reaches `target_GBps` or `seconds` pass. pairs: [[port, card]] --
        one stream per port at a time; a port walks its cards in turn."""
        by_port = collections.defaultdict(list)
        for p, c in (pairs or []):
            if int(c) in self.arena:
                by_port[int(p)].append(int(c))
        out, lock = {}, threading.Lock()
        src = region[0]
        span = min(src["nbytes"], 1 * GiB) // (64 * MiB) * (64 * MiB)

        def one(p, cards):
            t_end = time.time() + seconds
            rates = {c: 0.0 for c in cards}
            hist = {c: [] for c in cards}
            while time.time() < t_end and min(rates.values()) < target_GBps:
                for c in cards:
                    a = self.arena[c]
                    with self.arena_lock:
                        fr = max(a["free"], key=lambda e: e[1], default=None)
                    if not fr or fr[1] < 64 * MiB:
                        rates[c] = target_GBps
                        continue
                    off, ln = fr
                    t0, moved = time.time(), 0
                    with self.port_lock[p]:
                        while time.time() - t0 < 1.0:
                            m = 64 * MiB
                            self._rread(peer_ip, p, src["addr"] + (moved % span), src["rkeys"][p],
                                        a["t"].data_ptr() + off + (moved % (ln // m * m)), a["regs"][p].lkey, m)
                            moved += m
                    rates[c] = moved / (time.time() - t0) / 1e9
                    hist[c].append(round(rates[c], 1))
            with lock:
                for c in cards:
                    out[f"{p}:{c}"] = hist[c]
                    self.arena[c]["warm_GBps"] = rates[c]
        th = [threading.Thread(target=one, args=(p, cs)) for p, cs in by_port.items()]
        t0 = time.time()
        [x.start() for x in th]
        [x.join() for x in th]
        return {"s": round(time.time() - t0, 1), "GBps_per_second": out}

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

    def _pinner(self):
        """Registers pinned slabs with the GPU runtime, one at a time, in the background."""
        while True:
            s = self.pin_q.get()
            try:
                s.gpu_register()
            except Exception:                           # noqa: BLE001
                traceback.print_exc()

    def _new_slab(self, nbytes, numa, pin):
        s = Slab(nbytes, numa, pin, self.torch)
        if pin:
            self.pin_q.put(s)
        return s

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
        with self.grow_locks[(numa, bool(pin))]:        # one grower per NUMA node; re-check first
            with self.lock:
                for s in self.slabs:
                    if s.pin == bool(pin) and (numa is None or s.numa == numa):
                        off = s.alloc(n)
                        if off is not None:
                            return s, off
            s = self._new_slab(max(self.slab_bytes, n), numa, pin)
            with self.lock:
                self.slabs.append(s)
                return s, s.alloc(n)

    def _reserve(self, need, numa, pin):
        """Make room for `need` more bytes on (numa, pin) before a put starts filling, so that
        parallel fillers share slabs instead of each opening its own."""
        with self.grow_locks[(numa, bool(pin))]:
            with self.lock:
                have = sum(s.nbytes - s.used for s in self.slabs
                           if s.pin == bool(pin) and (numa is None or s.numa == numa))
            deficit = int(need * 1.02) + self.chunk_bytes - have
            if deficit <= 0:
                return 0
            count = -(-deficit // self.slab_bytes)
            made, lock = [], threading.Lock()

            def one():
                s = self._new_slab(self.slab_bytes, numa, pin)
                with lock:
                    made.append(s)
            th = [threading.Thread(target=one) for _ in range(count)]
            for i in range(0, count, 16):               # touching + ibv_reg_mr scale with threads
                [x.start() for x in th[i:i + 16]]
                [x.join() for x in th[i:i + 16]]
            with self.lock:
                self.slabs += made
            return count

    def _locator(self, cid):
        s, off, n = self.dram[cid]
        return {"addr": s.addr + off, "rkeys": [r.rkey for r in s.regs], "pin": s.pin, "numa": s.numa,
                "slab": id(s)}                              # memory returns to the OS slab by slab

    # ---- storage ops ---------------------------------------------------------------------
    def op_hello(self):
        return {"node": self.node, "ip": self.ip, "cards": self.cards, "chunk_bytes": self.chunk_bytes,
                "slab_bytes": self.slab_bytes,
                "arena_GiB": {str(c): round(a["nbytes"] / GiB, 1) for c, a in self.arena.items()},
                "slot_bytes": self.slot_bytes, "pid": os.getpid(), "nic_numa": self.nic_numa}

    def op_stat(self, numa=False):
        """numa=True also reports where the slab pages really are ({numa node: bytes}, sampled
        with move_pages) next to where they were meant to be, and the process' anonymous RSS."""
        extra = {}
        if numa:
            with self.lock:
                slabs = list(self.slabs) + list(self.spare)
            intended = collections.Counter()
            for s in slabs:
                intended[str(s.numa)] += s.nbytes
            actual = {str(k): v for k, v in numa_of_regions([(s.addr, s.nbytes) for s in slabs]).items()}
            rss = 0
            for line in open("/proc/self/status"):
                if line.startswith("RssAnon:"):
                    rss = int(line.split()[1]) * 1024
            extra = {"slab_numa_actual": actual, "slab_numa_intended": dict(intended), "rss_anon": rss,
                     "slabs_registered": sum(1 for s in slabs if s.registered), "slabs": len(slabs)}
        with self.lock:
            extra["pin_pending"] = sum(1 for s in self.slabs if s.pin and not s.registered)
        with self.arena_lock:
            arena = {str(c): {"nbytes": a["nbytes"], "free": sum(ln for _, ln in a["free"]),
                              "largest": max((ln for _, ln in a["free"]), default=0)} for c, a in self.arena.items()}
        with self.lock:
            by_numa, slab_numa = collections.Counter(), collections.Counter()
            for s in self.slabs:
                by_numa[str(s.numa)] += s.used
                slab_numa[str(s.numa)] += s.nbytes
            extra["slab_by_numa"] = dict(slab_numa)
            by_card = collections.Counter()
            for cid, h in self.hbm.items():
                by_card[str(h["card"])] += h["n"]
            return {**extra, "arena": arena,
                    "node": self.node, "dram_chunks": len(self.dram), "dram_bytes": dict(by_numa),
                    "slab_bytes": sum(s.nbytes for s in self.slabs), "spare_bytes": sum(s.nbytes for s in self.spare),
                    "ssd_chunks": len(self.ssd), "ssd_bytes": sum(self.ssd.values()),
                    "hbm_chunks": len(self.hbm), "hbm_bytes": dict(by_card),
                    "jobs": {j: sum(b.numel() for b in v["bufs"].values()) for j, v in self.jobs.items()},
                    "job_cards": sorted({int(c) for v in self.jobs.values() for c in v["bufs"]} |
                                        {int(c) for a in self.active.values() for c in a.get("cards", ())}),
                    "traffic": dict(self.traffic), "t": time.time()}

    def op_put(self, items, tier, numa=None, pin=False, card=None, threads=8, ports=None):
        """Store chunks. items: [{id, n, src}] with src one of
             {"k": "file", "path", "off"}                      read from a file (O_DIRECT)
             {"k": "rdma", "ip", "addr", "rkeys"}              pull from a peer's DRAM / HBM region
             {"k": "rfile", "ip", "path", "off"}               pull a peer's file range (it stages it)
             {"k": "rssd", "ip"}                               pull a peer's NVMe copy (it stages it)
             {"k": "lssd"}                                     this node's NVMe copy
             {"k": "local"}                                    copy from this node's DRAM tier
             {"k": "job", "job", "card", "off"}                a restored model still in this node's HBM
        tier "hbm" stores into ranges of `card`'s arena (registered and warm); a peer's memory
        is RDMA-READ straight into them over `ports` (the card's near ports), one stream per port.
        Returns {id: locator} for DRAM and HBM, else the stored ids."""
        use_ports = [int(x) for x in ports] if ports else list(range(rdma.PORTS))
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

        def fill(view, addr, it, w, dst_t=None):
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
            elif src["k"] == "job":                         # weights still in this node's HBM: D2H
                g = self.jobs[src["job"]]["bufs"][int(src["card"])][src["off"]:src["off"] + it["n"]]
                host = self.torch.frombuffer(view, dtype=self.torch.uint8) if dst_t is None else dst_t
                host.copy_(g)
                self.traffic["d2h"] += it["n"]
            elif src["k"] == "lssd":                        # this node's NVMe copy
                slot = self._sget(w % rdma.PORTS)
                try:
                    fd = os.open(self._ssd_path(it["id"]), os.O_RDONLY | os.O_DIRECT)
                    try:
                        delta = self._direct_read(fd, slot, 0, it["n"])
                    finally:
                        os.close(fd)
                    ctypes.memmove(addr, self._saddr(slot) + delta, it["n"])
                finally:
                    self._sput(slot)
                self.traffic["ssd_rd"] += it["n"]
            elif src["k"] in ("rfile", "rssd"):             # a peer's durable file / NVMe copy, via its staging
                port = w % rdma.PORTS
                reg, _ = self._region_of(addr, port)
                cl = self._rpc(src["ip"], 200 + w)
                if src["k"] == "rssd":
                    e = cl.call("expose", cid=it["id"], tier="ssd")
                else:
                    e = cl.call("expose", cid=it["id"], tier="file", path=src["path"], off=src["off"], n=it["n"])
                try:
                    self._rread(src["ip"], port, e["addr"], e["rkeys"][port], addr, reg.lkey, it["n"])
                finally:
                    cl.call("unexpose", token=e["token"])
                self.traffic["rdma_rx"] += it["n"]
            else:                                           # peer DRAM / HBM -> here over RDMA
                port = w % rdma.PORTS
                reg, base = self._region_of(addr, port)
                with self.port_lock[port]:                  # one stream per port, as in restores
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
                            fill(s.view[off:off + n], s.addr + off, it, w, s.t[off:off + n] if s.t is not None else None)
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
                            self._put_hbm(it, int(card), w, use_ports, fill)
                        with lock:
                            out[cid] = self._hbm_locator(cid)    # peers RDMA-READ device memory directly
                    else:
                        raise ValueError(tier)
                except Exception as e:                      # noqa: BLE001
                    with lock:
                        errs.append(f"{cid[:12]}: {e!r}")
                    traceback.print_exc()

        th = [self._thread(work, w) for w in range(min(threads, max(1, len(items))))]
        [x.start() for x in th]
        [x.join() for x in th]
        for fd in fds.values():
            os.close(fd)
        if errs:
            raise RuntimeError("; ".join(errs[:3]))
        nbytes = sum(it["n"] for it in items)
        return {"stored": out, "s": round(time.time() - t0, 4), "bytes": nbytes, "reserve_s": round(reserve_s, 4)}

    def _hbm_locator(self, cid):
        h = self.hbm[cid]
        if h["off"] is not None:
            a = self.arena[h["card"]]
            return {"addr": a["t"].data_ptr() + h["off"], "rkeys": [a["regs"][p].rkey for p in range(rdma.PORTS)],
                    "card": h["card"], "off": h["off"]}
        return {"addr": h["t"].data_ptr(), "rkeys": [r.rkey for r in h["regs"]], "card": h["card"], "off": None}

    def _put_hbm(self, it, card, w, ports, fill):
        """One chunk into device memory of `card`: a range at the top of its arena when it has
        one (no allocation, no registration, warm for direct RDMA), else a fresh tensor."""
        torch, cid, n, src = self.torch, it["id"], it["n"], it["src"]
        off = self._arena_alloc(card, n, top=True)
        regs = None
        if off is not None:
            a = self.arena[card]
            g, gaddr = a["t"][off:off + n], a["t"].data_ptr() + off
            lkey = lambda p: a["regs"][p].lkey                                    # noqa: E731
        elif card in self.arena:
            raise RuntimeError(f"no arena room on card {card}")
        else:
            g = torch.empty(n, dtype=torch.uint8, device=f"cuda:{card}")
            gaddr = g.data_ptr()
            regs = [rdma.Region(p, gaddr, n, True) for p in range(rdma.PORTS)]
            lkey = lambda p: regs[p].lkey                                         # noqa: E731
        try:
            if src["k"] == "rdma":                           # peer DRAM / HBM -> this card, no host
                port = ports[w % len(ports)]
                with self.port_lock[port]:
                    self._rread(src["ip"], port, src["addr"], src["rkeys"][port], gaddr, lkey(port), n)
                self.traffic["rdma_rx"] += n
            elif src["k"] == "job":                          # D2D inside the node
                g.copy_(self.jobs[src["job"]]["bufs"][int(src["card"])][src["off"]:src["off"] + n])
            elif src["k"] == "local":
                s, o, _ = self.dram[cid]
                g.copy_(s.t[o:o + n])
            else:                                            # file / lssd / rfile / rssd through staging
                slot = self._sget(w % rdma.PORTS)
                try:
                    fill(self._sview(slot)[:n], self._saddr(slot), it, w)
                    g.copy_(self._sten(slot)[:n])
                finally:
                    self._sput(slot)
            torch.cuda.synchronize(f"cuda:{card}")
        except Exception:
            if off is not None:
                self._arena_free(card, off, n)
            for r in regs or []:
                r.close()
            raise
        with self.lock:
            self.hbm[cid] = {"card": card, "t": g, "regs": regs, "off": off, "n": n}

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
                    h = self.hbm.pop(cid)
                    for r in h["regs"] or []:
                        r.close()
                    if h["off"] is not None:
                        self._arena_free(h["card"], h["off"], h["n"])
                    h["t"] = None
                    freed += h["n"]
            if tier == "dram":
                for s in [s for s in self.slabs if s.used == 0]:
                    self.slabs.remove(s)
                    if len(self.spare) < self.spare_slabs:
                        s.free = [(0, s.nbytes)]
                        self.spare.append(s)
                    else:
                        dead.append(s)
        t = time.time()
        if len(dead) > 2:                                   # returns the memory to the OS
            dq = collections.deque(dead)

            def closer():
                while True:
                    try:
                        sl = dq.popleft()
                    except IndexError:
                        return
                    sl.close()
            th = [threading.Thread(target=closer) for _ in range(min(8, len(dead)))]
            [x.start() for x in th]
            [x.join() for x in th]
        else:
            for s in dead:
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
                g = self.hbm[cid]["t"]
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
                   rdma_window=8, ports=None, direct=True, local_inflight=24, edge=None, use_arena=True,
                   srcpref=None, relay=None, relay_slots=8):
        """Land `chunks` into fresh HBM buffers. cards: {card: nbytes}. Each chunk:
             {id, n, card, off, cands: [cand], plan_cls: class name (or plan: index into cands)}
        cand: {"k": "ldram"} | {"k": "lssd"} | {"k": "lhbm"} | {"k": "file", "path", "off"} |
              {"k": "dram" | "rhbm", "node", "ip", "addr", "rkeys"}   a peer's DRAM / HBM region |
              {"k": "rstage", "node", "ip", "tier"}                  a peer stages it (SSD, file).

        direct=True: the destination buffers are registered with the HCA and a peer's memory is
        RDMA-READ straight into device memory -- no host staging, no H2D. One worker per HCA
        port ("net:<p>"), one stream at a time, walking its planned chunks card by card: a port and
        a card each carry ~48 GB/s and a port that multiplexes several cards at once collapses
        (measured 187 GB/s matched vs 70 GB/s all-to-all on 4+ cards). Local DRAM goes through
        host H2D at the same time; the two ingress paths add up (235 GB/s into 8 cards).
        direct=False: the earlier host relay (peer -> pinned staging -> H2D), kept for comparison;
        it is capped near 35 GB/s per node because both halves share the host's DMA budget.

        relay {port: card} (direct only): borrowed ingress. Port p lands chunks in a slot ring
        carved from the arena of `card` (a near card of p that is idle and not a target here) and
        the target card pulls each chunk over xGMI with hipMemcpyAsync on its own stream, so the
        relay card runs no kernel. A card then takes its own PCIe link plus xGMI fan-in (measured
        184 GB/s into one card vs 48 direct, pool_r03/relay_r01). Class "relay:<p>"."""
        ports = list(ports) if ports else list(range(rdma.PORTS))
        # srcpref {port: {node: score}}: the coordinator's view of how much of port p's sending
        # side each source still has (the four ports are four subnets: port p reads port p).
        # A chunk may carry "src", the source its plan assumed. Absent = least loaded source.
        srcpref = {int(k): v for k, v in (srcpref or {}).items()}
        torch = self.torch
        t0 = time.time()
        bufs, regs, in_arena, own_regs = {}, {}, {}, {}
        for c, n in cards.items():
            c, n = int(c), int(n)
            off = self._arena_alloc(c, n) if use_arena else None
            if off is not None:                         # a range of the long-lived, warmed arena
                a = self.arena[c]
                bufs[c] = a["t"][off:off + n]
                regs[c] = a["regs"]
                in_arena[c] = (off, n)
            else:
                bufs[c] = torch.empty(n, dtype=torch.uint8, device=f"cuda:{c}")
                if direct:
                    regs[c] = own_regs[c] = {p: rdma.Region(p, bufs[c].data_ptr(), n, True) for p in ports}
        base = {c: b.data_ptr() for c, b in bufs.items()}
        alloc_s = time.time() - t0
        NET = ("dram", "rhbm")
        net_classes = [f"net:{p}" for p in ports]
        rings = {}
        if direct and relay:
            big = max((c["n"] for c in chunks), default=0)
            slot_n = (big + (2 * MiB - 1)) & ~(2 * MiB - 1)
            for p, rc in sorted({int(k): int(v) for k, v in relay.items()}.items()):
                if p not in ports or rc in bufs or slot_n == 0:
                    continue
                roff = self._arena_alloc(rc, slot_n * relay_slots)
                if roff is None:
                    continue
                a = self.arena[rc]
                free = queue.Queue()
                for k in range(relay_slots):
                    free.put(k)
                rings[p] = {"card": rc, "off": roff, "base": a["t"].data_ptr() + roff, "slot": slot_n,
                            "lkey": a["regs"][p].lkey, "free": free, "n": slot_n * relay_slots}
        relay_classes = [f"relay:{p}" for p in sorted(rings)]
        netlike = net_classes + relay_classes

        def cls_of(c):
            k = c["k"]
            if k in NET:
                return f"dram:{c['node']}"
            return f"{k}:{c['node']}" if k == "rstage" else k

        for i, c in enumerate(chunks):
            c["by"], c["net"] = {}, []
            for cand in c["cands"]:
                if direct and cand["k"] in NET:
                    c["net"].append(cand)
                else:
                    c["by"].setdefault(cls_of(cand), cand)
            # A port with a relay ring feeds only through it: relay_pick borrows a port only when
            # none of its near cards is a target, so a direct write from it would land on a far
            # card at 12-24 GB/s while holding the port the ring needs (smoke2: relay lanes got
            # 1.5 GiB each, the far direct lane stole the rest).
            c["classes"] = list(c["by"]) + ([x for x in net_classes if int(x.split(":")[1]) not in rings] +
                                            relay_classes if c["net"] else [])
            pc = c.get("plan_cls")
            if pc is not None and pc.startswith("relay:") and pc not in c["classes"]:
                pc = "net:" + pc.split(":")[1]           # no ring on that relay card: go direct
            elif pc is not None and pc.startswith("net:") and int(pc.split(":")[1]) in rings:
                pc = "relay:" + pc.split(":")[1]
            if pc is None:
                cand = c["cands"][c.get("plan", 0)]
                pc = net_classes[i % len(ports)] if (direct and cand["k"] in NET) else cls_of(cand)
            c["plan"] = pc if pc in c["classes"] else c["classes"][0]
        # a port walks one card after another; other classes keep 'fewest alternatives first'
        # A port walks its chunks one card after another. Which card matters: the capacity of
        # a (port, card) pair is 47 GB/s for the two cards near the port and half or less for the
        # others (measured, pool_r02/affinity/gpudirect_repro_node28.log), and a card takes ~47
        # GB/s in total. `edge` {"p:c": GB/s} is the coordinator's current estimate; each port
        # serves its best cards first. Without it the ports just start on different cards.
        edge = {(int(k.split(":")[0]), int(k.split(":")[1])): v for k, v in (edge or {}).items()}
        card_ix = {c: k for k, c in enumerate(sorted(bufs))}
        stride = max(1, len(bufs) // len(ports))

        def order(i):
            c = chunks[i]
            if c["plan"].startswith("relay:"):
                return (0, 0.0, c["card"], c["off"])
            if c["plan"].startswith("net:"):
                p = int(c["plan"].split(":")[1])
                if edge:
                    return (0, -edge.get((p, c["card"]), 0.0), c["card"], c["off"])
                return (0, (card_ix[c["card"]] - ports.index(p) * stride) % len(bufs), c["off"])
            return (1, len(c["classes"]), 0)
        sched = Sched(chunks, steal, prior or {}, key=order)
        sched.edge = {(f"net:{p}", c): v for (p, c), v in edge.items()}
        pair = collections.defaultdict(lambda: [0, 0.0])        # (port, card) -> [bytes, seconds]
        spair = collections.defaultdict(lambda: [0, 0.0])       # (source node, port) -> [bytes, seconds]
        landq = {c: queue.Queue() for c in bufs}
        first = [None]
        prof = collections.Counter()                    # where the wall time of this restore went
        node_load, dead_nodes, rr_lock = collections.Counter(), set(), threading.Lock()
        reading = {}                                    # port -> source being read right now
        local_sem = threading.Semaphore(local_inflight * len(bufs))

        def drop_node(node, timeout=10.0, ranges=None):
            """`node` is about to free its DRAM/HBM copies: stop reading them, wait for reads in flight."""
            t = time.time()
            gone = []

            def hit(cd):
                if cd["node"] != node:
                    return False
                return ranges is None or any(lo <= cd["addr"] < hi for lo, hi in ranges)
            with rr_lock:
                if ranges is None:
                    dead_nodes.add(node)
                for i, c in enumerate(chunks):
                    if any(hit(cd) for cd in c["net"]):
                        c["net"] = [cd for cd in c["net"] if not hit(cd)]
                        if not c["net"]:
                            gone.append(i)
            moved = sched.retire(gone, netlike)

            def busy():
                if ranges is None:
                    return node_load[node] > 0
                with rr_lock:
                    return any(hit(cd) for cd in reading.values())
            while busy() and time.time() - t < timeout and sched.left and not sched.error:
                time.sleep(0.002)
            return {"moved": moved, "wait_s": round(time.time() - t, 5)}
        self.active[job] = {"sched": sched, "drop_node": drop_node, "direct": direct,
                            "cards": sorted(bufs) + sorted(r["card"] for r in rings.values())}

        def lander(card):
            dev, q, buf = f"cuda:{card}", landq[card], bufs[card]
            # Chunks parked on another card of this node come over xGMI on their own stream (a
            # target-side pull), so they overlap the H2D copies of local DRAM instead of queueing
            # behind them on the PCIe link.
            xs = torch.cuda.Stream(device=dev)
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
                for i, src, slot, cls in batch:
                    c = chunks[i]
                    if cls == "lhbm" and src.device.index != card:
                        with torch.cuda.device(card):
                            rc = _hip_pull().hipMemcpyAsync(ctypes.c_void_p(buf.data_ptr() + c["off"]),
                                                       ctypes.c_void_p(src.data_ptr()), c["n"], 3,
                                                       ctypes.c_void_p(xs.cuda_stream))
                        if rc == 0:
                            continue
                    buf[c["off"]:c["off"] + c["n"]].copy_(src, non_blocking=True)
                torch.cuda.synchronize(dev)                 # every stream of the card, xs included
                dt = time.time() - tl
                prof["land_s"] += dt
                prof["land_batches"] += 1
                if first[0] is None:
                    first[0] = time.time() - t0
                local = collections.Counter()
                for i, src, slot, cls in batch:
                    if slot is not None:
                        self._sput(slot)
                    else:
                        local[cls] += chunks[i]["n"]
                        local_sem.release()
                for cls, nb in local.items():
                    sched.observe(cls, card, nb / max(dt, 1e-6) / 1e9)
                sched.landed([b[0] for b in batch])

        def net_worker(p):
            """Direct path: peer memory -> this card's HBM over port p, one chunk after another."""
            cls = f"net:{p}"
            while True:
                i = sched.next(cls)
                if i is None:
                    return
                c = chunks[i]
                n, t, ok, why = c["n"], time.time(), False, "no remote source"
                while True:
                    with rr_lock:
                        live = [cd for cd in c["net"] if cd["node"] not in dead_nodes]
                        if not live:
                            break
                        cand = None
                        if c.get("src") and c["plan"] == cls:
                            cand = next((cd for cd in live if cd["node"] == c["src"]), None)
                        if cand is None:
                            pref = srcpref.get(p, {})
                            cand = max(live, key=lambda cd: (round(pref.get(cd["node"], 1.0), 1),
                                                             -node_load[cd["node"]]))
                        node_load[cand["node"]] += 1
                        reading[p] = cand
                    try:
                        with self.port_lock[p]:
                            tr = time.time()
                            self._rread(cand["ip"], p, cand["addr"], cand["rkeys"][p], base[c["card"]] + c["off"],
                                        regs[c["card"]][p].lkey, n, rdma_window)
                            wire = time.time() - tr
                        ok = True
                    except Exception as e:              # noqa: BLE001 -- that source lost the chunk
                        why = repr(e)
                        with rr_lock:
                            c["net"] = [cd for cd in c["net"] if cd is not cand]
                    finally:
                        with rr_lock:
                            node_load[cand["node"]] -= 1
                            reading.pop(p, None)
                    if ok:
                        break
                if not ok:
                    sched.fail(cls, i, why, also=netlike)
                    continue
                dt = time.time() - t
                self.traffic["rdma_rx"] += n
                pr = pair[(p, c["card"])]
                pr[0] += n
                pr[1] += wire                           # time on the wire, not waiting for the port
                sp = spair[(cand["node"], p)]
                sp[0] += n
                sp[1] += wire
                prof["fetch_s:net"] += dt
                if first[0] is None:
                    first[0] = time.time() - t0
                sched.fetched(cls, 0, i, dt)
                sched.landed([i])

        def relay_worker(p):
            """Borrowed ingress: peer memory -> slot on the relay card over port p -> target over xGMI."""
            cls, ring = f"relay:{p}", rings[p]
            streams = {}
            while True:
                i = sched.next(cls)
                if i is None:
                    return
                c = chunks[i]
                n, t, ok, why = c["n"], time.time(), False, "no remote source"
                slot = ring["free"].get()
                saddr = ring["base"] + slot * ring["slot"]
                wire = 0.0
                try:
                    while True:
                        with rr_lock:
                            live = [cd for cd in c["net"] if cd["node"] not in dead_nodes]
                            if not live:
                                break
                            cand = None
                            if c.get("src") and c["plan"] == cls:
                                cand = next((cd for cd in live if cd["node"] == c["src"]), None)
                            if cand is None:
                                pref = srcpref.get(p, {})
                                cand = max(live, key=lambda cd: (round(pref.get(cd["node"], 1.0), 1),
                                                                 -node_load[cd["node"]]))
                            node_load[cand["node"]] += 1
                            reading[(p, "relay", slot)] = cand
                        try:
                            with self.port_lock[p]:
                                tr = time.time()
                                self._rread(cand["ip"], p, cand["addr"], cand["rkeys"][p], saddr, ring["lkey"], n,
                                            rdma_window)
                                wire = time.time() - tr
                            ok = True
                        except Exception as e:          # noqa: BLE001 -- that source lost the chunk
                            why = repr(e)
                            with rr_lock:
                                c["net"] = [cd for cd in c["net"] if cd is not cand]
                        finally:
                            with rr_lock:
                                node_load[cand["node"]] -= 1
                                reading.pop((p, "relay", slot), None)
                        if ok:
                            break
                    if ok:
                        card = c["card"]
                        st = streams.get(card)
                        if st is None:
                            st = streams[card] = torch.cuda.Stream(device=f"cuda:{card}")
                        tx = time.time()
                        with torch.cuda.device(card):
                            rc = _hip_pull().hipMemcpyAsync(ctypes.c_void_p(base[card] + c["off"]), ctypes.c_void_p(saddr),
                                                       n, 3, ctypes.c_void_p(st.cuda_stream))
                            if rc == 0:
                                st.synchronize()
                        prof["xgmi_s"] += time.time() - tx
                        if rc != 0:
                            ok, why = False, f"hipMemcpyAsync {rc}"
                except Exception as e:                  # noqa: BLE001 -- never leave a claimed chunk behind
                    ok, why = False, repr(e)
                finally:
                    ring["free"].put(slot)
                if not ok:
                    sched.fail(cls, i, why, also=netlike)
                    continue
                dt = time.time() - t
                self.traffic["rdma_rx"] += n
                self.traffic["xgmi_relay"] += n
                pr = pair[(p, ring["card"])]
                pr[0] += n
                pr[1] += wire
                sp = spair[(cand["node"], p)]
                sp[0] += n
                sp[1] += wire
                prof["fetch_s:relay"] += dt
                if first[0] is None:
                    first[0] = time.time() - t0
                sched.fetched(cls, 0, i, dt)
                sched.landed([i])

        def worker(cls, w, port=None):
            is_local = cls in ("ldram", "lhbm")
            while True:
                if is_local:                            # claim only what the lander can take soon
                    while not local_sem.acquire(timeout=0.05):
                        if sched.left == 0 or sched.error:
                            return
                i = sched.next(cls)
                if i is None:
                    if is_local:
                        local_sem.release()
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
                        if cand.get("job"):                 # another instance of the model on this node
                            src = self.jobs[cand["job"]]["bufs"][int(cand["card"])][cand["off"]:cand["off"] + n]
                        else:
                            src = self.hbm[c["id"]]["t"]
                    else:
                        p = port if port is not None else ports[w % len(ports)]
                        slot = self._sget(p)
                        prof["slot_wait_s"] += time.time() - t
                        t = time.time()
                        delta = 0
                        if k in NET:
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
                    if is_local:
                        local_sem.release()
                    sched.fail(cls, i, repr(e))
                    continue
                dt = time.time() - t
                prof["fetch_s:" + cls] += dt
                sched.fetched(cls, w, i, dt, rate=not is_local)
                landq[c["card"]].put((i, src, slot, cls))

        threads = [threading.Thread(target=lander, args=(c,), daemon=True) for c in bufs]
        classes = {cl for c in chunks for cl in c["classes"]}
        w = 0
        for cl in sorted(classes):
            kind = cl.split(":")[0]
            if kind == "net":
                threads.append(threading.Thread(target=net_worker, args=(int(cl.split(":")[1]),), daemon=True))
                sched.nworkers[cl] += 1
            elif kind == "relay":                       # two in flight: one reads while one forwards
                for _ in range(2):
                    threads.append(threading.Thread(target=relay_worker, args=(int(cl.split(":")[1]),), daemon=True))
                    sched.nworkers[cl] += 1
            elif kind == "dram":
                for p in ports:
                    threads.append(self._thread(worker, cl, w, p, daemon=True))
                    sched.nworkers[cl] += 1
                    w += 1
            else:
                reps = {"lssd": ssd_readers, "file": file_readers, "rstage": 4}.get(kind, 2)
                for _ in range(reps):
                    threads.append(self._thread(worker, cl, w, daemon=True))
                    sched.nworkers[cl] += 1
                    w += 1
        nland = len(bufs)
        [x.start() for x in threads]
        [x.join() for x in threads[nland:]]
        for q in landq.values():
            q.put(None)
        [x.join() for x in threads[:nland]]
        for c in bufs:                                  # RDMA wrote device memory behind the runtime
            torch.cuda.synchronize(f"cuda:{c}")
        for r in rings.values():                        # the relay rings go back to their arenas
            self._arena_free(r["card"], r["off"], r["n"])
        total = time.time() - t0
        self.active.pop(job, None)
        if sched.error:
            for rr in own_regs.values():
                for r in rr.values():
                    r.close()
            for c, (off, n) in in_arena.items():
                self._arena_free(c, off, n)
            del bufs
            torch.cuda.empty_cache()
            raise RuntimeError(sched.error)
        self.jobs[job] = {"bufs": bufs, "regs": regs, "own_regs": own_regs, "arena": in_arena,
                          "chunks": [(c["id"], c["card"], c["off"], c["n"]) for c in chunks]}
        nbytes = sum(c["n"] for c in chunks)
        return {"job": job, "bytes": nbytes, "total_s": round(total, 4), "alloc_s": round(alloc_s, 4),
                "first_land_s": None if first[0] is None else round(first[0], 4),
                "GBps": round(nbytes / max(total, 1e-9) / 1e9, 2), "failed_fetches": sched.failed, "fail_why": sched.why,
                "arena_cards": sorted(in_arena), "relay": {str(p): r["card"] for p, r in rings.items()},
                "paths": {k: v for k, v in sched.stats.items()},
                "prof": {k: round(v, 4) for k, v in prof.items()}, "workers": len(threads) - nland,
                "pair_GBps": {f"{p}:{c}": [round(b / max(sec, 1e-9) / 1e9, 1), b] for (p, c), (b, sec) in pair.items()},
                "src_GBps": {f"{s}:{p}": round(b / max(sec, 1e-9) / 1e9, 1) for (s, p), (b, sec) in spair.items()},
                "rates_GBps": {k: round(v, 2) for k, v in sched.rate.items()}}

    def op_revoke(self, node, tiers=("dram",), ranges=None):
        """Called before `node` drops chunks: every running restore stops reading from it.
        ranges [[lo, hi]]: only sources whose address lies in one of them (one restored model
        or one card of `node`), so the node's other copies stay usable."""
        out = {}
        for job, a in list(self.active.items()):
            sched = a["sched"]
            if node == self.node:
                out[job] = sched.revoke([{"dram": "ldram", "ssd": "lssd", "hbm": "lhbm"}[t] for t in tiers])
                continue
            res = {}
            if a["direct"] and any(t in ("dram", "hbm") for t in tiers):
                res = a["drop_node"](node, ranges=ranges)
            legacy = ([f"dram:{node}"] if not a["direct"] else []) + ([f"rstage:{node}"] if "ssd" in tiers else [])
            if legacy:
                res = {**sched.revoke(legacy), **res}
            out[job] = res
        return out

    def op_job_locators(self, job):
        """Where each chunk of a restored model sits in this node's HBM, as RDMA regions a peer
        can read directly (HBM -> HBM, no host on either side). {chunk id: {addr, rkeys, card}}."""
        j = self.jobs[job]
        if not j.get("regs"):
            return {}
        out = {}
        for cid, card, off, n in j["chunks"]:
            rr = j["regs"][card]
            out[cid] = {"addr": j["bufs"][card].data_ptr() + off, "card": card, "off": off, "n": n,
                        "rkeys": [rr[p].rkey if p in rr else 0 for p in range(rdma.PORTS)]}
        return out

    def op_job_export(self, job):
        """HIP IPC handles of a job's HBM buffers, for a zero-copy attach by another process."""
        import base64
        out = {}
        for card, buf in self.jobs[job]["bufs"].items():
            h = buf.untyped_storage()._share_cuda_()
            # the buffer may be a range of the card's arena: the consumer maps the storage and
            # starts at `offset`
            out[str(card)] = {"handle": [base64.b64encode(x).decode() if isinstance(x, bytes) else x for x in h],
                              "offset": buf.storage_offset(), "nbytes": buf.numel()}
        return out

    def _rpc(self, ip, w):
        """An RPC client to a peer that belongs to the calling thread. An expose/unexpose pair
        holds a staging slot on the peer between its two calls; with clients shared by worker
        index, concurrent puts/restores queued one thread's unexpose behind another thread's
        expose that was waiting for a slot -- a deadlock that exhausted node28's 124 slots
        (pool_r03/formal_r400_p1_hung). `w` is kept for call-site readability only."""
        key = (ip, threading.get_ident())
        with self.conn_lock:
            c = self.rpcs.get(key)
            if c is None:
                c = self.rpcs[key] = Client(ip)
            return c

    def _rpc_done(self):
        """Close the calling thread's peer clients (worker threads come and go with every call)."""
        me = threading.get_ident()
        with self.conn_lock:
            mine = [k for k in self.rpcs if k[1] == me]
            cls = [self.rpcs.pop(k) for k in mine]
        for c in cls:
            if c.sock is not None:
                try:
                    c.sock.close()
                except OSError:
                    pass
                c.sock = None

    def _thread(self, fn, *args, **kw):
        """threading.Thread whose target releases its peer clients when it returns."""
        def run(*a):
            try:
                fn(*a)
            finally:
                self._rpc_done()
        return threading.Thread(target=run, args=args, **kw)

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
            for rr in j.get("own_regs", {}).values():
                for r in rr.values():
                    r.close()
            j["bufs"].clear()
            for c, (off, ln) in j.get("arena", {}).items():
                self._arena_free(c, off, ln)
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
    p.add_argument("--arena-gib", type=float, default=0, help="HBM arena per card (0 = restores allocate fresh)")
    a = p.parse_args(argv)
    try:                                                # keep-alive must never outlive a tenant
        open("/proc/self/oom_score_adj", "w").write("900")
    except OSError:
        pass
    cards = [int(x) for x in a.cards.split(",") if x != ""]
    d = NodeD(a.node, a.ip, a.ssd or None, cards, int(a.stage_gib * GiB), a.chunk_mib * MiB,
              a.slab_mib * MiB, a.spare_slabs, arena_bytes=int(a.arena_gib * GiB))
    d.serve()


if __name__ == "__main__":
    main()
