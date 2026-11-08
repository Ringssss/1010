"""Keeper mesh: one worker per GPU on every node, keeping post-processed TP shards alive and moving
them between HBM, pinned DRAM and SSD on any of the nodes.

Each worker:
  * owns one local GPU (used as the IB staging point and as the restore target),
  * holds zero or more shards in pinned DRAM, keyed (snap, tp_rank), each packed contiguously
    with an index name -> (offset, nbytes),
  * is a rank in one long-lived torch.distributed world spanning all keeper workers of all nodes,
    with a fixed set of 2-rank "pair" groups warmed at start (cross-node send/recv is broken on
    this RCCL, so every move is a broadcast on a pair group; see docs/findings_multinode.md).

Control is HTTP per worker (port = base + local gpu). A flow is a list of ops executed by every
worker in list order, each worker doing only the ops it is part of; common order across workers
is what keeps the pair-group broadcasts deadlock-free.

Op kinds (all byte ranges refer to the packed layout of shard (snap, tp_rank)):
  ib    {src, dst, group, snap, tp, off, n}  src worker's pinned -> staging -> broadcast -> dst
  ssd   {dst, path, snap, tp, off, n}        dst worker reads the range from a local file
  h2d   {dst, snap, tp, off, n}              dst worker's own pinned copy
Destination of every op is either the dst worker's pinned copy of (snap, tp) ("into": "dram") or
the live SGLang skeleton that exported rank tp on the dst GPU ("into": "hbm").

Population is outside the flow language, because it is per-worker setup rather than a move:
  synth   fake shard, deterministic bytes -- for timing flows without a real checkpoint
  ingest  safetensors on local storage -> TP-partitioned packed pinned DRAM for one rank
  load    a previously written rank{tp}.bin packed shard from SSD -> pinned DRAM
  capture the live SGLang skeleton's allocations -> pinned DRAM (post-processed, no ingest)
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import pickle
import threading
import time
import traceback
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from fluidckpt.memory import HostAllocation
from fluidckpt.planner.loading import validate_ops
from fluidckpt.serving.replicas import ReplicaCatalog, identity
from fluidckpt.executor.regions import execute_regions
from fluidckpt.model import ingest as model_ingest

MiB = 1 << 20
CHUNK = 256 * MiB


class Shard:
    def __init__(self, snap, tp, index, buf, meta, owner=None):
        self.snap, self.tp, self.index, self.buf, self.meta = snap, tp, index, buf, meta
        self.owner = owner

    def close(self):
        self.buf = None
        if self.owner is not None:
            self.owner.close()

    @property
    def nbytes(self):
        return self.buf.numel()


def _pinned(n):
    return torch.empty(n, dtype=torch.uint8, pin_memory=True)


class HbmTarget:
    """Live SGLang storages of one TP rank, addressable by the packed layout of a shard."""

    def __init__(self, pkl_path, index, gpu, expected_meta=None, trace=None):
        span = trace.span if trace else lambda *a, **kw: nullcontext()
        with span('pickle_read', path=str(pkl_path)):
            if trace is None:
                with open(pkl_path, 'rb') as stream:
                    d = pickle.load(stream)
            else:
                with span('file_open'):
                    stream = open(pkl_path, 'rb')
                try:
                    d = pickle.load(trace.reader(stream))
                finally:
                    with span('file_close'):
                        stream.close()
        self.meta = d["meta"]
        self.pkl_path = pkl_path
        with span('metadata_compare'):
            if expected_meta is not None:
                for key in ("model_identity", "tp_size", "tp_rank"):
                    if self.meta.get(key) != expected_meta.get(key):
                        raise ValueError(f"snapshot identity/layout mismatch: {key}")
                from fluidckpt.serving.layout import validate_tensor_layout
                validate_tensor_layout(expected_meta.get('tensors'), self.meta.get('tensors'))
        self.segs = []          # (offset, nbytes, uint8 cuda view), sorted by offset
        with span('index_validation'):
            self.missing = [e["name"] for e in d["entries"]
                            if e["name"] not in index or index[e["name"]][1] != e["nbytes"]]
            absent = set(index) - {e["name"] for e in d["entries"]}
        if self.missing or absent:
            raise ValueError(f"snapshot/skeleton mismatch: incompatible={self.missing[:8]}, "
                             f"absent={sorted(absent)[:8]}")
        with span('set_device'):
            torch.cuda.set_device(gpu)
        for i, e in enumerate(d["entries"]):
            ent = index[e["name"]]
            if trace is None:
                st = torch.UntypedStorage._new_shared_cuda(*e["handle"])
                t = torch.empty(0, dtype=torch.uint8, device=f"cuda:{gpu}").set_(st)
            else:
                with span('ipc_import', entry=i, name=e['name'], nbytes=e['nbytes']):
                    st = torch.UntypedStorage._new_shared_cuda(*e["handle"])
                with span('tensor_bind', entry=i, name=e['name'], nbytes=e['nbytes']):
                    t = torch.empty(0, dtype=torch.uint8, device=f"cuda:{gpu}").set_(st)
            self.segs.append((ent[0], ent[1], t))
        with span('sort_segments'):
            self.segs.sort(key=lambda s: s[0])
            self.starts = [s[0] for s in self.segs]

    def scatter(self, off, src):
        """Copy a contiguous range [off, off+len(src)) of the packed layout into the storages."""
        import bisect
        end = off + src.numel()
        i = max(0, bisect.bisect_right(self.starts, off) - 1)
        moved = 0
        while i < len(self.segs) and self.segs[i][0] < end:
            o, n, t = self.segs[i]
            a, b = max(o, off), min(o + n, end)
            if a < b:
                t[a - o:b - o].copy_(src[a - off:b - off], non_blocking=True)
                moved += b - a
            i += 1
        return moved

    def gather(self, off, destination):
        """Copy one logical interval to an independently owned host allocation."""
        import bisect
        end = off + destination.numel()
        i = max(0, bisect.bisect_right(self.starts, off)-1)
        copied = 0
        while i < len(self.segs) and self.segs[i][0] < end:
            start, n, tensor = self.segs[i]
            lo, hi = max(start, off), min(start+n, end)
            if lo < hi:
                destination[lo-off:hi-off].copy_(tensor[lo-start:hi-start], non_blocking=True)
                copied += hi-lo
            i += 1
        return copied

    def done(self, info):
        p = os.path.join(os.path.dirname(self.pkl_path), f"rank{self.meta['tp_rank']}.done")
        with open(p + ".tmp", "w") as f:
            json.dump(info, f)
        os.replace(p + ".tmp", p)


class Worker:
    def __init__(self, a):
        self.a = a
        self.gpu = a.gpu
        self.node = a.node
        # Partition factor for ingest. Every keeper in a mesh must agree, or rank r's shard
        # holds ranges of a differently-split tensor than its peers.
        self.tp_size = getattr(a, "tp_size", 2)
        self.shards = {}        # (snap, tp) -> Shard
        self.live_targets = {}  # optional verification leases; each IPC export is imported once
        self.replicas = ReplicaCatalog(extra_budget=getattr(a, "replica_budget_bytes", 8 << 30))
        self.events = []
        self.lock = threading.Lock()
        self.dist_ready = False
        self.groups = []
        self.rank = None
        torch.cuda.set_device(self.gpu)
        self.dev = torch.device("cuda", self.gpu)

    def log(self, **kw):
        kw.update(t=time.time(), node=self.node, gpu=self.gpu)
        self.events.append(kw)
        print(json.dumps(kw), flush=True)

    def allocate_host(self, nbytes, numa=None):
        torch.cuda.set_device(self.gpu)
        return HostAllocation(nbytes, getattr(self.a, "memory_backend", "torch_pinned_default"),
                              numa if numa is not None else getattr(self.a, "numa", None))

    def register_primary(self, snap, tp):
        sh = self.shards[(snap, tp)]
        model_identity = identity(sh.meta, sh.index)
        rid = f"{snap}/{tp}/primary"
        existing = next((r for r in self.replicas.inventory() if r["replica_id"] == rid), None)
        if existing:
            if existing["identity"] != model_identity:
                raise ValueError("primary identity changed")
            return existing
        return self.replicas.publish(rid, model_identity, tp, 0, sh.buf,
                                     sh.owner.numa if sh.owner else None, self.rank,
                                     build={"primary": True, "snap": snap})

    def replica_inventory(self, snap):
        for sn, tp in self.shards:
            if sn == snap:
                self.register_primary(snap, tp)
        ids = {identity(sh.meta, sh.index) for (sn, _), sh in self.shards.items() if sn == snap}
        return {"replicas": [r for r in self.replicas.inventory() if r["identity"] in ids],
                "extra_allocation_bytes": self.replicas.owned_bytes(),
                "extra_budget_bytes": self.replicas.extra_budget}

    def create_replica(self, body):
        torch.cuda.set_device(self.gpu)
        torch.set_num_threads(1)
        primary = self.register_primary(body["snap"], body["tp"])
        return self.replicas.clone(primary["replica_id"], body["replica_id"],
                                   body["off"], body["n"], body["numa"])

    def run_region_flow(self, body):
        started = time.monotonic()
        validate_ops(body["ops"], {int(tp): max(o+n for o, n in idx.values())
                                  for tp, idx in body["index"].items()})
        ops = [op for op in body["ops"] if op["dst_w"] == self.rank]
        if not ops:
            raise ValueError("worker has no regional operations")
        tps = sorted({op["tp"] for op in ops})
        targets = {}
        with self.replicas.acquire(ops):
            for tp in tps:
                sh = self.shards[(body["snap"], tp)]
                expected = identity(sh.meta, sh.index)
                if any(op["identity"] != expected for op in ops if op["tp"] == tp):
                    raise ValueError("plan identity differs from target snapshot")
                key = (body["tag"], tp)
                if key not in self.live_targets:
                    p = self.wait_skeleton(body["tag"], tp)
                    self.live_targets[key] = HbmTarget(p, sh.index, self.gpu, sh.meta)
                targets[tp] = self.live_targets[key]
            prepared = time.monotonic()
            result = execute_regions(self.replicas, ops, targets, self.gpu,
                                     parallel=body.get("parallel", True), chunk_bytes=body.get("chunk_bytes", 64 << 20),
                                     max_inflight=body.get("max_inflight", 2), paced=body.get("paced", False),
                                     trace_enabled=body.get("trace_enabled", False))
        result.update(prep_s=prepared-started, flow_total_s=time.monotonic()-started,
                      ev="region_flow", into="hbm", dst_tps=tps)
        for tp, target in targets.items():
            target.done({"tp": tp, "missing": 0, "regional": True, "flow_s": result["flow_total_s"]})
        self.log(ev="region_flow", total_s=result["flow_total_s"], bytes=result["bytes"],
                 paths=len(result["paths"]), overlap_ms=result["gpu_path_overlap_ms"])
        return result

    # ------------------------------------------------------------ mesh
    def init_mesh(self):
        """world spec: MESH='node27:0-7,node28:2-7,node29:0-7'; global rank = position in spec."""
        members = []
        for part in self.a.mesh.split(","):
            n, r = part.split(":")
            lo, hi = map(int, r.split("-"))
            members += [(n, g) for g in range(lo, hi + 1)]
        self.members = members
        self.rank = members.index((self.node, self.gpu))
        torch.distributed.init_process_group(
            "nccl", init_method=f"tcp://{self.a.master}", rank=self.rank, world_size=len(members),
            timeout=datetime.timedelta(seconds=1800))  # no device_id: keep pair comms lazy
        # every cross-node pair gets a group; all ranks create all groups in the same order
        self.pair_of = {}
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if members[i][0] == members[j][0]:
                    continue
                g = torch.distributed.new_group([i, j])
                self.pair_of[(i, j)] = self.pair_of[(j, i)] = g
        self.staging = [torch.empty(CHUNK, dtype=torch.uint8, device=self.dev) for _ in range(2)]
        self.dist_ready = True
        self.log(ev="mesh_up", rank=self.rank, world=len(members), pairs=len(self.pair_of) // 2)

    def warm(self, pairs):
        """Create RCCL communicators for the given pairs (list of [i, j]) in the given order."""
        t0 = time.time()
        x = torch.zeros(1, device=self.dev)
        for i, j in pairs:
            if self.rank in (i, j):
                torch.distributed.broadcast(x, src=i, group=self.pair_of[(i, j)])
        torch.cuda.synchronize()
        return {"warm_s": time.time() - t0}

    def wid(self, node, gpu):
        return self.members.index((node, gpu))

    # ------------------------------------------------------------ capture / restore (local)
    def _my_pkls(self, tag, phase):
        out = []
        host = os.uname().nodename
        for p in sorted(glob.glob(os.path.join(self.a.handoff, tag, "rank*.pkl"))):
            with open(p, 'rb') as stream:
                m = pickle.load(stream)["meta"]
            # A shared TP16 handoff includes the same local GPU ordinal on both hosts.
            # HIP IPC handles are only meaningful on the host that exported them.
            if (m["gpu_id"] == self.gpu and m["phase"] == phase
                    and m.get('host', host) == host):
                out.append((p, m))
        return out

    def capture(self, tag, snap, timeout=7200, retain_live=False):
        res = []
        t_wait = time.time()
        while not self._my_pkls(tag, "capture") and time.time() - t_wait < timeout:
            time.sleep(0.2)     # armed before SGLang finishes loading; fire as soon as it exports
        if not self._my_pkls(tag, "capture"):
            raise TimeoutError(f"no capture export for {tag} on GPU {self.gpu}")
        for p, meta in self._my_pkls(tag, "capture"):
            if (snap, meta["tp_rank"]) in self.shards:
                raise ValueError("snapshot already exists; release it explicitly before replacement")
            d = pickle.load(open(p, "rb"))
            t0 = time.time()
            index, total = {}, 0
            for e in d["entries"]:
                index[e["name"]] = (total, e["nbytes"])
                total += e["nbytes"]
            target = HbmTarget(p, index, self.gpu)
            if retain_live:
                self.live_targets[(tag, meta["tp_rank"])] = target
            t1 = time.time()
            owner = None
            buf = None
            try:
                owner = self.allocate_host(total)
                buf = owner.tensor
                t2 = time.time()
                s = torch.cuda.Stream(self.dev)
                with torch.cuda.stream(s):
                    for off, nbytes, t in target.segs:
                        buf[off:off + nbytes].copy_(t, non_blocking=True)
                s.synchronize()
            except BaseException:
                torch.cuda.synchronize()
                buf = None
                if owner is not None:
                    owner.close()
                self.live_targets.pop((tag, meta["tp_rank"]), None)
                raise
            t3 = time.time()
            del target, t
            self.shards[(snap, meta["tp_rank"])] = Shard(snap, meta["tp_rank"], index, buf, meta, owner)
            r = dict(ev="capture", snap=snap, tp=meta["tp_rank"], GiB=total / 2**30, n=len(index),
                     map_s=t1 - t0, pin_s=t2 - t1, d2h_s=t3 - t2, d2h_GBps=total / (t3 - t2) / 1e9,
                     memory=owner.info())
            c = os.path.join(os.path.dirname(p), f"rank{meta['tp_rank']}.captured")
            with open(c + ".tmp", "w") as f:
                json.dump(r, f)
            os.replace(c + ".tmp", c)
            self.log(**r)
            res.append(r)
        return res

    def save(self, snap, dirpath):
        os.makedirs(dirpath, exist_ok=True)
        res = []
        for (s, tp), sh in list(self.shards.items()):
            if s != snap:
                continue
            t0 = time.time()
            with open(os.path.join(dirpath, f"rank{tp}.bin"), "wb", buffering=0) as f:
                mv = memoryview(sh.buf.numpy())
                for o in range(0, sh.nbytes, CHUNK):
                    f.write(mv[o:o + CHUNK])
            json.dump({"index": sh.index, "meta": sh.meta}, open(os.path.join(dirpath, f"rank{tp}.json"), "w"))
            r = dict(ev="save", snap=snap, tp=tp, GiB=sh.nbytes / 2**30, s=time.time() - t0)
            self.log(**r)
            res.append(r)
        return res

    def index_of(self, snap, tp, dirpath=None):
        sh = self.shards.get((snap, tp))
        if sh is not None:
            return sh.index
        j = json.load(open(os.path.join(dirpath, f"rank{tp}.json")))
        return {k: tuple(v) for k, v in j["index"].items()}

    def load(self, snap, tp, dirpath):
        """SSD -> pinned DRAM for one shard."""
        t0 = time.time()
        j = json.load(open(os.path.join(dirpath, f"rank{tp}.json")))
        idx = {k: tuple(v) for k, v in j["index"].items()}
        n = max(o + b for o, b in idx.values())
        if (snap, tp) in self.shards:
            raise ValueError("snapshot already exists")
        owner = self.allocate_host(n)
        buf = owner.tensor
        t1 = time.time()
        try:
            with open(os.path.join(dirpath, f"rank{tp}.bin"), "rb", buffering=0) as f:
                with memoryview(buf.numpy()) as mv:
                    for o in range(0, n, CHUNK):
                        count = min(CHUNK, n - o)
                        if f.readinto(mv[o:o + count]) != count:
                            raise IOError("short snapshot read")
        except BaseException:
            buf = None
            owner.close()
            raise
        self.shards[(snap, tp)] = Shard(snap, tp, idx, buf, j["meta"], owner)
        r = dict(ev="load", snap=snap, tp=tp, GiB=n / 2**30, pin_s=t1 - t0, read_s=time.time() - t1,
                 GBps=n / (time.time() - t1) / 1e9)
        self.log(**r)
        return r

    def alloc(self, snap, tp, index, meta):
        if (snap, tp) in self.shards:
            raise ValueError("snapshot already exists")
        n = max(o + b for o, b in index.values())
        owner = self.allocate_host(n)
        self.shards[(snap, tp)] = Shard(snap, tp, {k: tuple(v) for k, v in index.items()}, owner.tensor, meta, owner)
        return {"GiB": n / 2**30}

    def synth(self, snap, tp, nbytes, seed):
        """Fake shard for testing flows: deterministic bytes, index of 1 MiB..64 MiB storages."""
        g = torch.Generator().manual_seed(seed)
        if (snap, tp) in self.shards:
            raise ValueError("snapshot already exists")
        owner = self.allocate_host(nbytes)
        buf = owner.tensor
        buf.copy_(torch.randint(0, 256, (nbytes,), dtype=torch.uint8, generator=g))
        index, off, i = {}, 0, 0
        while off < nbytes:
            n = min(nbytes - off, (1 + (i * 7919) % 64) * MiB)
            index[f"t{i}"] = (off, n)
            off += n
            i += 1
        self.shards[(snap, tp)] = Shard(snap, tp, index, buf, {"tp_rank": tp, "synthetic": True}, owner)
        return {"GiB": nbytes / 2**30, "n": len(index), "sum": self.checksum(snap, tp)["sum"]}

    def ingest(self, snap, tp, model_dir, index_path=None):
        """Safetensors -> TP-partitioned, packed pinned DRAM for one shard.

        The expensive work the keepalive exists to amortise. Partition and pack order come from
        fluidckpt.model.ingest, which is also what a cold reload from storage uses, so a warm
        flow out of this snapshot and a cold path must agree byte for byte.

        Mirrors load()'s two-phase shape: reserve and pin first (`pin_s`), then fill. The fill
        reads only the byte ranges this rank owns, so a TP2 rank of a 67 GiB model touches
        ~33 GiB of storage rather than all of it.
        """
        t0 = time.time()
        if (snap, tp) in self.shards:
            raise ValueError("snapshot already exists")
        _by_file, total, index = model_ingest.plan(model_dir, tp, self.tp_size, index_path)
        owner = self.allocate_host(total)
        buf = owner.tensor
        t1 = time.time()

        last = [0.0]

        def progress(fname, written, nbytes_total):
            # The fill runs inside this HTTP handler, so a per-file line doubles as a liveness
            # signal for an hour-long read; rate is over the current file, not the whole job.
            now = time.time()
            self.log(ev="ingest_progress", snap=snap, tp=tp, file=fname,
                     GiB=round(written / 2**30, 2), of_GiB=round(nbytes_total / 2**30, 2),
                     GBps=round(written / max(now - t1, 1e-9) / 1e9, 2))
            last[0] = now

        try:
            # numpy view over the pinned allocation: ingest.fill assigns byte slices into it.
            with memoryview(buf.numpy()) as mv:
                written, filled = model_ingest.fill(model_dir, tp, self.tp_size, mv, index_path, progress)
            if written != total:
                raise IOError(f"packed {written} of {total} bytes")
        except BaseException:
            buf = None
            owner.close()
            raise

        meta = {"tp_rank": tp, "tp_size": self.tp_size, "model_dir": model_dir,
                "source": "safetensors"}
        self.shards[(snap, tp)] = Shard(snap, tp, {k: tuple(v) for k, v in filled.items()},
                                        buf, meta, owner)
        elapsed = time.time() - t1
        r = dict(ev="ingest", snap=snap, tp=tp, GiB=total / 2**30, tensors=len(filled),
                 pin_s=t1 - t0, fill_s=elapsed, GBps=total / elapsed / 1e9)
        self.log(**r)
        return r

    def checksum(self, snap, tp):
        sh = self.shards[(snap, tp)]
        x = sh.buf[: sh.nbytes // 8 * 8].view(torch.int64)
        return {"sum": int(x.to(self.dev).sum().item()) if sh.nbytes else 0, "GiB": sh.nbytes / 2**30}

    def drop(self, snap):
        torch.cuda.set_device(self.gpu)
        for k in [k for k in self.shards if k[0] == snap]:
            if any(t.meta["tp_rank"] == k[1] for t in self.live_targets.values()):
                raise RuntimeError("release live verification leases before dropping a snapshot")
            sh = self.shards[k]
            self.replicas.retire_identity(identity(sh.meta, sh.index))
            self.shards.pop(k).close()
        torch.cuda.empty_cache()
        return {"dropped": snap}

    def verify(self, tag, snap, tp, phase="restore"):
        """Compare every live storage byte with the DRAM snapshot, outside restore timing."""
        torch.cuda.set_device(self.gpu)
        sh = self.shards[(snap, tp)]
        start = time.monotonic()
        target = self.live_targets.get((tag, tp))
        if target is None or target.meta["phase"] != phase:
            raise ValueError("verification requires capture/flow with retain_live=true")
        mismatch = torch.zeros((), dtype=torch.int64, device=self.dev)
        checked = 0
        for off, nbytes, live in target.segs:
            for pos in range(0, nbytes, 16 * MiB):
                size = min(16 * MiB, nbytes - pos)
                reference = sh.buf[off + pos:off + pos + size].to(self.dev)
                mismatch += torch.count_nonzero(live[pos:pos + size] != reference)
                checked += size
        errors = int(mismatch.item())
        result = {"checked_bytes": checked, "expected_bytes": sh.nbytes,
                  "mismatched_bytes": errors, "storages": len(target.segs),
                  "passed": errors == 0 and checked == sh.nbytes,
                  "elapsed_s": time.monotonic() - start}
        del target, live, reference, mismatch
        torch.cuda.empty_cache()
        self.log(ev="verify", tp=tp, **result)
        return result

    def release_live(self, tag):
        """Release verification mappings before the exporting process exits."""
        keys = [key for key in self.live_targets if key[0] == tag]
        torch.cuda.set_device(self.gpu)
        torch.cuda.synchronize()
        for key in keys:
            del self.live_targets[key]
        torch.cuda.empty_cache()
        return {"released": len(keys)}

    # ------------------------------------------------------------ flows
    def wait_skeleton(self, tag, tp, timeout=3600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            for p, m in self._my_pkls(tag, "restore"):
                if m["tp_rank"] == tp:
                    return p
            time.sleep(0.01)
        raise TimeoutError(f"no skeleton for tp {tp}")

    def run_flow(self, flow):
        """flow = {"ops": [...], "into": "hbm"|"dram", "tag": T, "snap": S, "index": {tp: index},
                   "meta": {tp: meta}}. Returns per-kind timing for this worker."""
        into = flow["into"]
        # Guard before anything else: on an uninitialised mesh self.rank is None, the op filter below
        # matches nothing, and the caller gets a 0.008 s "success" that moved zero bytes. Fail loudly.
        if self.rank is None:
            raise RuntimeError("run_flow before init_mesh: rank is unset, no op can be matched")
        validate_ops(flow["ops"], {int(tp): max(o + n for o, n in index.values())
                                   for tp, index in flow["index"].items()})
        executor = flow.get("executor", "staged")
        if executor not in ("staged", "direct"):
            raise ValueError("unknown executor")
        chunk = flow.get("chunk_bytes", CHUNK)
        if type(chunk) is not int or chunk <= 0 or chunk > CHUNK:
            raise ValueError("chunk size must be positive and fit staging capacity")
        ops = [o for o in flow["ops"] if self.rank in (o.get("src_w"), o.get("dst_w"))]
        my_dst = sorted({o["tp"] for o in ops if o["dst_w"] == self.rank})
        targets = {}
        t0 = time.time()
        for tp in my_dst:
            idx = {k: tuple(v) for k, v in flow["index"][str(tp)].items()}
            if into == "hbm":
                p = self.wait_skeleton(flow["tag"], tp)
                key = (flow["tag"], tp)
                targets[tp] = self.live_targets.get(key) or HbmTarget(p, idx, self.gpu, flow["meta"][str(tp)])
                if flow.get("retain_live", False):
                    self.live_targets[key] = targets[tp]
            else:
                if (flow["snap"], tp) not in self.shards:
                    self.alloc(flow["snap"], tp, idx, flow["meta"][str(tp)])
                targets[tp] = self.shards[(flow["snap"], tp)]
        t_ready = time.time()

        comm = torch.cuda.Stream(self.dev)
        copy = torch.cuda.Stream(self.dev)
        stats = {"ib_bytes": 0, "ssd_bytes": 0, "h2d_bytes": 0}
        local_timing = {}

        def land(tp, off, gpu_src, stream):
            """gpu_src holds bytes [off, off+len) of shard tp; put them at the destination."""
            with torch.cuda.stream(stream):
                if into == "hbm":
                    moved = targets[tp].scatter(off, gpu_src)
                    if moved != gpu_src.numel():
                        raise RuntimeError(f"incomplete target write: {moved}/{gpu_src.numel()}")
                else:
                    targets[tp].buf[off:off + gpu_src.numel()].copy_(gpu_src, non_blocking=True)

        # local ops (ssd / h2d) run on a side thread so they overlap with IB ops
        local_ops = [o for o in ops if o["kind"] in ("ssd", "h2d")]
        ib_ops = [o for o in ops if o["kind"] == "ib"]

        def run_local():
            start_local = time.monotonic()
            s = torch.cuda.Stream(self.dev)
            stage = [torch.empty(chunk, dtype=torch.uint8, device=self.dev) for _ in range(2)] if executor == "staged" else None
            host = [_pinned(chunk) for _ in range(2)] if any(o["kind"] == "ssd" for o in local_ops) else None
            buffers_ready = time.monotonic()
            evs = [None, None]
            k = 0
            for o in local_ops:
                if o["kind"] == "ssd":
                    f = open(o["path"], "rb", buffering=0)
                for c in range(o["off"], o["off"] + o["n"], chunk):
                    m = min(chunk, o["off"] + o["n"] - c)
                    if evs[k] is not None:
                        evs[k].synchronize()
                    if o["kind"] == "ssd":
                        f.seek(c)
                        got = f.readinto(memoryview(host[k].numpy())[:m])
                        if got != m:
                            raise IOError(f"short SSD read: {got}/{m}")
                        src = host[k][:m]
                        stats["ssd_bytes"] += m
                    else:
                        src = self.shards[(o["snap"], o["tp"])].buf[c:c + m]
                        stats["h2d_bytes"] += m
                    if into == "hbm":
                        if executor == "direct":
                            land(o["tp"], c, src, s)
                        else:
                            with torch.cuda.stream(s):
                                stage[k][:m].copy_(src, non_blocking=True)
                            land(o["tp"], c, stage[k][:m], s)
                    else:
                        with torch.cuda.stream(s):
                            targets[o["tp"]].buf[c:c + m].copy_(src)
                    evs[k] = torch.cuda.Event()
                    evs[k].record(s)
                    k ^= 1
                if o["kind"] == "ssd":
                    f.close()
            s.synchronize()
            local_timing.update(buffer_prepare_s=buffers_ready - start_local,
                                copy_and_sync_s=time.monotonic() - buffers_ready)

        local_errors = []

        def checked_local():
            try:
                torch.cuda.set_device(self.gpu)
                run_local()
            except BaseException as exc:
                local_errors.append(exc)

        th = threading.Thread(target=checked_local)
        th.start()
        t_ib0 = time.time()
        # IB ops: double-buffered. Sender: H2D chunk into staging[k] on `copy`, broadcast on `comm`.
        # Receiver: broadcast into staging[k] on `comm`, land on `copy`.
        ev_free = [None, None]
        k = 0
        for o in ib_ops:
            g = self.pair_of[(o["src_w"], o["dst_w"])]
            sender = self.rank == o["src_w"]
            for c in range(o["off"], o["off"] + o["n"], CHUNK):
                m = min(CHUNK, o["off"] + o["n"] - c)
                buf = self.staging[k][:m]
                if ev_free[k] is not None:
                    (copy if sender else comm).wait_event(ev_free[k])
                if sender:
                    with torch.cuda.stream(copy):
                        buf.copy_(self.shards[(o["snap"], o["tp"])].buf[c:c + m], non_blocking=True)
                    e = torch.cuda.Event(); e.record(copy)
                    comm.wait_event(e)
                    with torch.cuda.stream(comm):
                        torch.distributed.broadcast(buf, src=o["src_w"], group=g)
                    ev_free[k] = torch.cuda.Event(); ev_free[k].record(comm)
                else:
                    with torch.cuda.stream(comm):
                        torch.distributed.broadcast(buf, src=o["src_w"], group=g)
                    e = torch.cuda.Event(); e.record(comm)
                    copy.wait_event(e)
                    land(o["tp"], c, buf, copy)
                    ev_free[k] = torch.cuda.Event(); ev_free[k].record(copy)
                    stats["ib_bytes"] += m
                k ^= 1
        comm.synchronize(); copy.synchronize()
        t_ib1 = time.time()
        th.join()
        if local_errors:
            failure = local_errors.pop()
            # A thread traceback retains this frame and every mapped storage through closures.
            failure.__traceback__ = None
            raise RuntimeError("local restore failed") from failure
        t_end = time.time()
        out = dict(ev="flow", into=into, dst_tps=my_dst, prep_s=t_ready - t0, ib_s=t_ib1 - t_ib0,
                   total_s=t_end - t0, executor=executor, chunk_bytes=chunk, local=local_timing, **stats)
        if into == "hbm":
            for tp, tg in targets.items():
                tg.done(dict(out, tp=tp, missing=len(tg.missing), skeleton_to_done_s=t_end - tg.meta["t_export"]))
        self.log(**out)
        return out

    def status(self):
        return {"node": self.node, "gpu": self.gpu, "rank": self.rank, "dist": self.dist_ready,
                "shards": {f"{s}/{tp}": round(sh.nbytes / 2**30, 2) for (s, tp), sh in self.shards.items()},
                "memory": {f"{s}/{tp}": sh.owner.info() if sh.owner else None
                           for (s, tp), sh in self.shards.items()},
                "pid": os.getpid(),
                "hbm_alloc_GiB": torch.cuda.memory_allocated(self.dev) / 2**30}


def serve(a):
    w = Worker(a)

    class H(BaseHTTPRequestHandler):
        def _reply(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/index"):
                q = dict(x.split("=") for x in self.path.split("?")[1].split("&"))
                sh = w.shards[(q["snap"], int(q["tp"]))]
                return self._reply({"index": sh.index, "meta": sh.meta})
            self._reply(w.status() if self.path.startswith("/status") else {"events": w.events[-100:]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            op = self.path.strip("/")
            try:
                fn = {"mesh": lambda: (w.init_mesh(), w.status())[1],
                      "warm": lambda: w.warm(body["pairs"]),
                      "capture": lambda: w.capture(body["tag"], body["snap"],
                          body.get("timeout", 7200), body.get("retain_live", False)),
                      "save": lambda: w.save(body["snap"], body["dir"]),
                      "load": lambda: w.load(body["snap"], body["tp"], body["dir"]),
                      "flow": lambda: w.run_flow(body),
                      "region_flow": lambda: w.run_region_flow(body),
                      "replica_inventory": lambda: w.replica_inventory(body["snap"]),
                      "create_replica": lambda: w.create_replica(body),
                      "retire_replica": lambda: self._retire(body),
                      "synth": lambda: w.synth(body["snap"], body["tp"], body["nbytes"], body.get("seed", 0)),
                      "ingest": lambda: w.ingest(body["snap"], body["tp"], body["dir"],
                                                 body.get("index_path")),
                      "checksum": lambda: w.checksum(body["snap"], body["tp"]),
                      "verify": lambda: w.verify(body["tag"], body["snap"], body["tp"],
                                                   body.get("phase", "restore")),
                      "release_live": lambda: w.release_live(body["tag"]),
                      "drop": lambda: w.drop(body["snap"])}[op]
                with w.lock:
                    self._reply(fn())
            except Exception as e:
                self._reply({"error": repr(e), "tb": traceback.format_exc()}, 500)

        def log_message(self, *a):
            pass

        def _retire(self, body):
            torch.cuda.set_device(w.gpu)
            if body["replica_id"].endswith("/primary"):
                raise ValueError("release primary snapshots through drop")
            return w.replicas.retire(body["replica_id"])

    print(f"keeper worker {a.node}:{a.gpu} on :{a.port_base + a.gpu}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", a.port_base + a.gpu), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--gpu", type=int, required=True)
    ap.add_argument("--port-base", type=int, default=39500)
    ap.add_argument("--handoff", default="/dev/shm/fluidckpt")
    ap.add_argument("--mesh", default="node27:0-7,node28:2-7,node29:0-7")
    ap.add_argument("--master", default="12.11.3.27:39600")
    ap.add_argument("--memory-backend", choices=["torch_pinned_default", "numa_registered"], default="torch_pinned_default")
    ap.add_argument("--numa", type=int)
    ap.add_argument("--replica-budget-bytes", type=int, default=8 << 30)
    ap.add_argument("--tp-size", type=int, default=2,
                    help="tensor-parallel width; must match across the whole keeper mesh")
    serve(ap.parse_args())
