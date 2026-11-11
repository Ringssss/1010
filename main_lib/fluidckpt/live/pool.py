"""FluidPool coordinator: one live graph, one chunk catalog, three verbs.

  park(model)      scatter the model's chunks into whatever free DRAM / SSD / HBM the live graph
                   shows right now, on any node (fragmented placement: a chunk goes where there is
                   room, neighbours need not follow it);
  restore(model)   plan sources on the current snapshot (flow.plan_fetch), hand the plan to the
                   destination's node daemon, which pulls from all of them at once;
  react()          called on every graph update: when another tenant squeezes a vertex we hold
                   chunks on, give the space back immediately (revoke -> drop), then re-protect the
                   affected models elsewhere in the background.

Everything that decides is here and reads the graph; everything that moves bytes is in noded.py.
"""
from __future__ import annotations

import collections
import json
import os
import threading
import time
import urllib.request

from .agent import Aggregator
from .chunks import load_manifest
from .flow import plan_fetch
from .graph import GiB, LiveGraph, Policy, build_topology
from .noded import Client
from .types import Snapshot

MiB = 1 << 20
TIER_CLASS = {"dram": "ldram", "ssd": "lssd", "hbm": "lhbm"}
# Rate priors for the executor's stealing rule (GB/s per path class), all measured on this
# cluster (pool_r01/pin_probe, pool_r01/rdma_bench); the executor replaces them with what it sees.
PRIOR = {"ldram": 47.0, "ldram_pageable": 13.5, "lhbm": 100.0, "dram": 60.0, "lssd": 5.0,
         "rstage": 5.0, "file": 5.0}


class Model:
    def __init__(self, name, manifest, model_dir, durable_node):
        self.name, self.manifest, self.dir, self.durable_node = name, manifest, model_dir, durable_node
        # Only weight files go to HBM; config/tokenizer files are read by the engine as usual.
        self.chunks = [c for c in manifest.chunks if manifest.files[c.file]["role"] == "weights"]
        self.nbytes = sum(c.nbytes for c in self.chunks)
        self.last_used = 0.0


class Pool:
    def __init__(self, nodes, agent_port=18740, policy=None, log=None, ssd_disk=None):
        """nodes: {name: ib0 ip}. ssd_disk: {node: block device name of the pool NVMe}."""
        self.nodes = dict(nodes)
        self.log = log or (lambda **k: print(json.dumps(k), flush=True))
        self.rpc = {n: Client(ip) for n, ip in nodes.items()}
        self.policy = policy or Policy()
        inv = {}
        for n, ip in nodes.items():
            with urllib.request.urlopen(f"http://{ip}:{agent_port}/inventory", timeout=5) as r:
                inv[n] = json.loads(r.read())
        self.topology = build_topology(list(inv.values()), self.policy)
        self.graph = LiveGraph(self.topology, self.policy)
        self.agg = Aggregator(self.graph, {n: f"http://{ip}:{agent_port}" for n, ip in nodes.items()})
        self.ssd_disk = ssd_disk or {n: next((v.index for v in self.topology.vertices.values()
                                              if v.node == n and v.kind == "ssd"), None) for n in nodes}
        self.fast = {}                                   # node -> latest raw fast sample
        orig = self.graph.ingest

        def ingest(node, fast=None, pcie=None):
            if fast:
                self.fast[node] = fast
            orig(node, fast=fast, pcie=pcie)
        self.graph.ingest = ingest
        self.lock = threading.RLock()
        self.models = {}
        self.rep = collections.defaultdict(dict)         # cid -> {vertex: locator | True}
        self.size = {}                                   # cid -> nbytes
        self.owners = collections.defaultdict(set)       # cid -> {model}
        self.held = collections.Counter()                # vertex -> bytes we hold there
        self.reserved = collections.Counter()            # vertex -> bytes of puts in flight
        self.active = {}                                 # job -> destination node (running restores)
        self.place_margin = 2 * GiB
        self.events = []
        self._stop = threading.Event()
        self._thr = None
        self.info = {n: self.rpc[n].call("hello") for n in nodes}
        for n in nodes:                                  # every pair connected before any restore
            self.rpc[n].call("warm", peers=[ip for m, ip in nodes.items() if m != n])

    # ---- graph loop ----------------------------------------------------------------------
    def start(self, period=0.1, react=True):
        """Two threads: one keeps the graph current at ~10 Hz and does nothing else, so a slow
        eviction can never make the whole picture stale; the other reacts to what it shows."""
        tick = threading.Event()

        def poll_loop():
            while not self._stop.wait(period):
                try:
                    t = time.perf_counter()
                    self.agg.poll()
                    self.poll_ms.append((time.perf_counter() - t) * 1e3)
                    tick.set()
                except Exception as e:                  # noqa: BLE001
                    self.log(event="graph_loop_error", error=repr(e))

        def react_loop():
            cl = {n: Client(ip) for n, ip in self.nodes.items()}      # own sockets for this thread
            while not self._stop.is_set():
                if not tick.wait(0.5):
                    continue
                tick.clear()
                try:
                    self.react(cl)
                except Exception as e:                  # noqa: BLE001
                    self.log(event="react_error", error=repr(e))
        self.poll_ms = collections.deque(maxlen=20000)
        self.agg.poll()
        time.sleep(0.3)
        self.agg.poll()
        self._thr = [threading.Thread(target=poll_loop, daemon=True)]
        if react:
            self._thr.append(threading.Thread(target=react_loop, daemon=True))
        [t.start() for t in self._thr]

    def stop(self):
        self._stop.set()
        for t in self._thr or []:
            t.join(2)

    def snapshot(self):
        return self.graph.snapshot(now=time.time())

    def free(self, vertex, snap=None):
        s = (snap or self.snapshot()).vertices.get(vertex)
        return 0 if s is None or s.stale else max(0, s.free_bytes - self.reserved[vertex])

    # ---- catalog -------------------------------------------------------------------------
    def register(self, name, manifest_path, durable_node):
        m = load_manifest(manifest_path)
        model = Model(name, m, m.build["model_dir"], durable_node)
        with self.lock:
            self.models[name] = model
            for c in model.chunks:
                self.size[c.id] = c.nbytes
                self.owners[c.id].add(name)
        return model

    def _vparts(self, vertex):
        node, kind, idx = vertex.split("/")
        return node, kind, idx

    # ---- park: fragmented placement ------------------------------------------------------
    def place(self, model, copies=2, tiers=("dram",), avoid_nodes=(), prefer=None, pin_nodes=(),
              snap=None, budget=None):
        """Choose vertices for each chunk that still lacks `copies` replicas in `tiers`.

        Water-filling over the live free bytes of every vertex of those tiers: each chunk goes to
        the vertex with the most room left that does not already hold it and whose node holds
        none of its other replicas. `prefer` (a node) wins ties so one copy lands next to the GPUs
        that will most likely take the model back. budget: {vertex: max bytes} caps on top of the
        graph (used to keep experiments polite). Returns ({vertex: [(chunk, rank)]}, short):
        rank 0 is a chunk's first new replica (filled from the durable copy), later ranks are
        filled from an earlier one over RDMA."""
        snap = snap or self.snapshot()
        V = self.topology.vertices
        room = {}
        for vid, v in V.items():
            if v.kind in tiers and v.node not in avoid_nodes and v.node in self.nodes:
                if v.kind == "hbm" and int(v.index) not in self.info[v.node]["cards"]:
                    continue
                if v.kind == "ssd" and v.index != self.ssd_disk.get(v.node):
                    continue
                r = self.free(vid, snap)
                if v.kind == "dram":                     # stay clear of the eviction threshold
                    r = max(0, r - self.place_margin)
                if budget is not None:
                    r = min(r, max(0, budget.get(vid, 0) - self.held[vid] - self.reserved[vid]))
                room[vid] = r
        out = collections.defaultdict(list)
        short = 0
        uniq = list({c.id: c for c in model.chunks}.values())
        for c in sorted(uniq, key=lambda c: -c.nbytes):
            have_nodes = {self._vparts(v)[0] for v in self.rep[c.id] if self._vparts(v)[1] in tiers}
            for rank in range(max(0, copies - len(have_nodes))):
                best, best_key = None, None
                for vid, r in room.items():
                    node = V[vid].node
                    if r < c.nbytes or node in have_nodes:
                        continue
                    key = (node == prefer, r)
                    if best is None or key > best_key:
                        best, best_key = vid, key
                if best is None:
                    short += 1
                    break
                room[best] -= c.nbytes
                have_nodes.add(V[best].node)
                out[best].append((c, rank))
        return out, short

    def park(self, name, copies=2, tiers=("dram",), prefer=None, pin_nodes=(), avoid_nodes=(),
             budget=None, threads=8, all_or_nothing=False):
        """Place and store. A chunk's first replica is filled from the durable file (read locally
        on the durable node, or staged there and pulled over RDMA); further replicas are pulled
        from an existing DRAM replica, so the durable disk is read once."""
        model = self.models[name]
        t0 = time.time()
        with self.lock:
            plan, short = self.place(model, copies, tiers, avoid_nodes, prefer, pin_nodes, budget=budget)
            if all_or_nothing and short:
                return None                              # whole-model systems cannot keep a part
            for vid, cs in plan.items():
                self.reserved[vid] += sum(c.nbytes for c, _ in cs)
        errs = []

        def run(vid, cs):
            node, kind, idx = self._vparts(vid)
            cl = Client(self.nodes[node])
            try:
                items = []
                for c in cs:
                    with self.lock:
                        loc = next(((v, l) for v, l in self.rep[c.id].items()
                                    if "/dram/" in v and not v.startswith(node + "/")), None)
                        here = any(v.startswith(node + "/dram/") for v in self.rep[c.id])
                    path = os.path.join(model.dir, c.file)
                    if here and kind != "dram":
                        src = {"k": "local"}                     # this node's DRAM copy feeds its SSD/HBM
                    elif loc:
                        src = {"k": "rdma", "ip": self.nodes[self._vparts(loc[0])[0]], "addr": loc[1]["addr"],
                               "rkeys": loc[1]["rkeys"]}
                    elif node == model.durable_node:
                        src = {"k": "file", "path": path, "off": c.offset}
                    else:
                        src = {"k": "rfile", "ip": self.nodes[model.durable_node], "path": path, "off": c.offset}
                    items.append({"id": c.id, "n": c.nbytes, "src": src})
                kw = {"numa": int(idx), "pin": node in pin_nodes} if kind == "dram" else \
                     ({"card": int(idx)} if kind == "hbm" else {})
                r = cl.call("put", items=items, tier=kind, threads=threads, **kw)
                with self.lock:
                    for c in cs:
                        self.rep[c.id][vid] = r["stored"][c.id]
                        self.held[vid] += c.nbytes
            except Exception as e:                      # noqa: BLE001
                errs.append(f"{vid}: {e}")
            finally:
                with self.lock:
                    self.reserved[vid] -= sum(c.nbytes for c in cs)

        ranks = sorted({r for cs in plan.values() for _, r in cs})
        for rank in ranks:
            th = [threading.Thread(target=run, args=(v, [c for c, r in cs if r == rank]))
                  for v, cs in plan.items() if any(r == rank for _, r in cs)]
            [x.start() for x in th]
            [x.join() for x in th]
        nbytes = sum(c.nbytes for cs in plan.values() for c, _ in cs)
        dt = time.time() - t0
        out = {"model": name, "s": round(dt, 3), "GiB": round(nbytes / GiB, 2),
               "GBps": round(nbytes / max(dt, 1e-9) / 1e9, 2), "short": short,
               "vertices": {v: round(sum(c.nbytes for c, _ in cs) / GiB, 2) for v, cs in sorted(plan.items())},
               "errors": errs}
        self.log(event="park", **out)
        return out

    def unpark(self, name, tiers=("dram", "ssd", "hbm")):
        """Drop every replica of the model (chunks shared with another parked model stay)."""
        model = self.models[name]
        per = collections.defaultdict(list)
        with self.lock:
            for c in model.chunks:
                if len(self.owners[c.id]) > 1 and any(o != name and self.is_parked(o) for o in self.owners[c.id]):
                    continue
                for v in list(self.rep[c.id]):
                    if self._vparts(v)[1] in tiers and c.id not in per[v]:
                        per[v].append(c.id)
        self._drop(per)

    def is_parked(self, name):
        m = self.models[name]
        return any(self.rep[c.id] for c in m.chunks[:4])

    def _drop(self, per, clients=None):
        """per: {vertex: [cid]}. Revoke from running restores first, then free."""
        out = {}
        for vid, ids in per.items():
            node, kind, _ = self._vparts(vid)
            cl = (clients or self.rpc)[node]
            t = time.time()
            for job, dst in list(self.active.items()):
                try:
                    Client(self.nodes[dst]).call("revoke", node=node, tiers=[kind])
                except Exception:                       # noqa: BLE001
                    pass
            t_rev = time.time() - t
            r = cl.call("drop", ids=ids, tier=kind)
            with self.lock:
                for cid in ids:
                    if self.rep[cid].pop(vid, None) is not None:
                        self.held[vid] -= self.size[cid]
            out[vid] = {"freed": r["freed"], "s": round(time.time() - t, 4), "revoke_s": round(t_rev, 4),
                        "release_s": r["release_s"]}
        return out

    # ---- restore -------------------------------------------------------------------------
    def layout(self, model, cards):
        """Weight files -> cards (largest first onto the emptiest card); file bases page aligned."""
        m = model.manifest
        files = sorted({c.file for c in model.chunks}, key=lambda f: -m.files[f]["size"])
        used = {c: 0 for c in cards}
        base = {}
        for f in files:
            card = min(used, key=used.get)
            base[f] = (card, used[card])
            used[card] = (used[card] + m.files[f]["size"] + 4095) & ~4095
        return base, used

    def _cands(self, c, model, node, allow, snap):
        """Executable sources of one chunk for a restore into `node`."""
        cands, verts = [], []
        for vid, loc in self.rep[c.id].items():
            n, kind, idx = self._vparts(vid)
            if kind not in allow:
                continue
            if n == node:
                cand = {"k": TIER_CLASS[kind]}
            elif kind == "dram":
                cand = {"k": "dram", "node": n, "ip": self.nodes[n], "addr": loc["addr"], "rkeys": loc["rkeys"]}
            else:
                cand = {"k": "rstage", "node": n, "ip": self.nodes[n], "tier": kind}
            cands.append(cand)
            verts.append(vid)
        if "file" in allow:
            path = os.path.join(model.dir, c.file)
            dn = model.durable_node
            if dn == node:
                cands.append({"k": "file", "path": path, "off": c.offset})
            else:
                cands.append({"k": "rstage", "node": dn, "ip": self.nodes[dn], "tier": "file",
                              "path": path, "off": c.offset})
            verts.append(f"{dn}/ssd/{self.ssd_disk[dn]}")
        return cands, verts

    def _paths_fn(self, kinds_ok=("h2d", "d2h", "p2p", "nvme_rd", "ib")):
        V = self.topology.vertices
        cache = {}

        def fn(src, dst):
            key = (src, dst)
            if key not in cache:
                keep = []
                for p in self.graph.paths(src, dst):
                    # The data plane moves bytes between host memories; GPU-direct IB is not built.
                    if any(e.kind == "ib" and V[e.src].kind == "hbm" for e in p.edges):
                        continue
                    keep.append(p)
                cache[key] = keep
            return cache[key]
        return fn

    def restore(self, name, node, cards, allow=("dram", "ssd", "hbm", "file"), strategy="greedy",
                steal=True, live=True, job=None, verify=False, release=True, single_source=None,
                avoid_shared_root=True):
        """Bring the model's weights into HBM on `node`'s `cards`. Returns timings and path stats.

        live=False plans on nominal capacities (what a static system would assume).
        single_source: restrict remote candidates to one node (D2D-fork style baselines)."""
        model = self.models[name]
        t0 = time.time()
        snap = self.snapshot() if live else Snapshot(self.topology, {}, {}, -1, time.time())
        base, used = self.layout(model, cards)
        demands, replicas, chunks = [], {}, []
        with self.lock:
            for c in model.chunks:
                card, fb = base[c.file]
                cands, verts = self._cands(c, model, node, allow, snap)
                if single_source is not None:
                    keep = [k for k, cd in enumerate(cands) if cd.get("node", node) == single_source]
                    cands, verts = [cands[k] for k in keep], [verts[k] for k in keep]
                if not cands:
                    raise RuntimeError(f"{name}: chunk {c.id[:12]} has no source under allow={allow}")
                key = str(len(demands))                   # the same content can appear twice in a model
                demands.append((key, c.nbytes, f"{node}/hbm/{card}"))
                replicas[key] = list(dict.fromkeys(verts))
                chunks.append({"id": c.id, "n": c.nbytes, "card": card, "off": fb + c.offset,
                               "cands": cands, "_verts": verts})
        plan = plan_fetch(snap, demands, replicas, self._paths_fn(), strategy=strategy)
        src_of = {t.chunk_id: t.path.src for t in plan.transfers}
        for ch, (key, _, _) in zip(chunks, demands):
            verts = ch.pop("_verts")
            ch["plan"] = verts.index(src_of[key]) if key in src_of and src_of[key] in verts else 0
        # Receive only on HCA ports that do not share a NUMA node (PCIe root) with a target card.
        V = self.topology.vertices
        card_numa = {V[f"{node}/hbm/{c}"].numa for c in cards}
        nic_numa = self.info[node].get("nic_numa") or []
        ports = [p for p, nu in enumerate(nic_numa) if nu not in card_numa] if avoid_shared_root else []
        if len(ports) < 2:
            ports = list(range(len(nic_numa))) or None
        prior = {}
        for ch in chunks:
            for cd in ch["cands"]:
                k = cd["k"]
                cls = f"{k}:{cd['node']}" if k in ("dram", "rstage") else k
                prior.setdefault(cls, PRIOR[k])
        plan_s = time.time() - t0
        job = job or f"{name}-{int(time.time() * 1000)}"
        self.active[job] = node
        try:
            r = Client(self.nodes[node]).call("restore", job=job, cards={str(c): n for c, n in used.items() if n},
                                              chunks=chunks, steal=steal, prior=prior, ports=ports)
        finally:
            self.active.pop(job, None)
        total = time.time() - t0
        out = {"model": name, "node": node, "cards": list(cards), "GiB": round(model.nbytes / GiB, 2),
               "total_s": round(total, 4), "plan_s": round(plan_s, 4), "exec_s": r["total_s"],
               "alloc_s": r["alloc_s"], "GBps": round(model.nbytes / total / 1e9, 2),
               "predicted_s": round(plan.predicted_s, 4), "strategy": strategy, "steal": steal, "live": live,
               "failed_fetches": r["failed_fetches"], "fail_why": r.get("fail_why"), "paths": r["paths"], "rates": r["rates_GBps"], "prof": r["prof"], "ports": ports, "job": job}
        if verify:
            out["verify"] = self.rpc[node].call("job_verify", job=job)
        if release:
            self.rpc[node].call("job_release", job=job)
        model.last_used = time.time()
        self.log(event="restore", **out)
        return out

    # ---- react: give space back when a tenant needs it -------------------------------------
    def pressure(self):
        """{vertex: bytes we must give back}. A vertex is squeezed when what is left for
        everybody (free + the reclaimable share) has fallen under the headroom the policy
        promises other tenants -- and we still hold bytes there."""
        p, V, out = self.policy, self.topology.vertices, {}
        for node, fast in self.fast.items():
            for k, m in fast["dram"].items():
                vid = f"{node}/dram/{k}"
                if self.held[vid] <= 0 or m.get("free") is None:
                    continue
                avail = m["free"] + p.dram_reclaim_frac * (m.get("reclaimable") or 0)
                deficit = p.dram_headroom_frac * V[vid].capacity_bytes - avail
                if deficit > 0:
                    out[vid] = int(deficit)
            for g, used in fast["hbm_used"].items():
                vid = f"{node}/hbm/{g}"
                if self.held[vid] <= 0 or used is None:
                    continue
                deficit = p.hbm_headroom - (V[vid].capacity_bytes - used)
                if deficit > 0:
                    out[vid] = int(deficit)
        return out

    def react(self, clients=None, slack=1 * GiB):
        t0 = time.time()
        cool = getattr(self, "_cool", {})
        squeezed = {v: d for v, d in self.pressure().items() if t0 - cool.get(v, 0) > 0.5}
        if not squeezed:
            return None
        per, lost = {}, collections.Counter()
        with self.lock:
            # Oldest models go first; within a model, chunks that keep another fast replica.
            by_age = sorted(self.models.values(), key=lambda m: m.last_used)
            for vid, deficit in squeezed.items():
                need, ids = deficit + slack, []
                for m in by_age:
                    for c in m.chunks:
                        if need <= 0:
                            break
                        if vid in self.rep[c.id] and c.id not in ids:
                            ids.append(c.id)
                            need -= c.nbytes
                            lost[m.name] += 1
                per[vid] = ids
        out = self._drop(per, clients)
        for v in squeezed:
            cool[v] = time.time()
        self._cool = cool
        ev = {"event": "evict", "t": t0, "react_s": round(time.time() - t0, 4),
              "squeezed_GiB": {v: round(d / GiB, 2) for v, d in squeezed.items()},
              "freed_GiB": {v: round(o["freed"] / GiB, 2) for v, o in out.items()}, "models": dict(lost),
              "detail": {v: {k: o[k] for k in ("s", "revoke_s", "release_s")} for v, o in out.items()}}
        self.events.append(ev)
        self.log(**ev)
        return ev

    def coverage(self, name, tiers=("dram", "hbm"), nodes=None):
        """Fraction of the model's bytes with at least one replica in `tiers` (on `nodes`)."""
        m = self.models[name]
        ok = sum(c.nbytes for c in m.chunks
                 if any(self._vparts(v)[1] in tiers and (nodes is None or self._vparts(v)[0] in nodes)
                        for v in self.rep[c.id]))
        return ok / max(m.nbytes, 1)

    def drop_model(self, name, tiers=("dram",), nodes=None):
        """Drop the model's replicas in `tiers` (on `nodes`); chunks other models share stay."""
        model = self.models[name]
        per = collections.defaultdict(list)
        with self.lock:
            for c in model.chunks:
                for v in list(self.rep[c.id]):
                    node, kind, _ = self._vparts(v)
                    if kind in tiers and (nodes is None or node in nodes) and c.id not in per[v]:
                        per[v].append(c.id)
        return self._drop(per)
