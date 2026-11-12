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
from .types import Path, Snapshot

MiB = 1 << 20
TIER_CLASS = {"dram": "ldram", "ssd": "lssd", "hbm": "lhbm"}
# Rate priors for the executor's stealing rule (GB/s per path class), all measured on this
# cluster (pool_r01/pin_probe, pool_r01/rdma_bench); the executor replaces them with what it sees.
PRIOR = {"ldram": 47.0, "ldram_pageable": 13.5, "lhbm": 100.0, "dram": 30.0, "rhbm": 30.0, "lssd": 5.0,
         "rstage": 5.0, "file": 5.0}
# Capacities of the direct data plane, measured on node28 (pool_r02/affinity/*.log), GB/s.
PORT_GBPS = 47.0        # one HCA port writing device memory, one stream (line rate is 50)
CARD_GBPS = 47.0        # ingress of one card (H2D or direct RDMA, they share it)
HOST_DMA_GBPS = 88.0    # all host->device copies of a node together (H2D alone: 85-95 on 2-8 cards)
NVME_GBPS = 6.5         # one NVMe pool, O_DIRECT, 4-8 readers


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
        self._own_net = {}                               # node -> when our last transfer into it ended
        self.plan_paths = 6                              # candidate paths per (source, card)
        self.events = []
        self._stop = threading.Event()
        self._thr = None
        self.info = {n: self.rpc[n].call("hello") for n in nodes}
        for n in nodes:                                  # every pair connected before any restore
            self.rpc[n].call("warm", peers=[ip for m, ip in nodes.items() if m != n])
        # Direct data plane: capacity of every (HCA port, card) pair of every node, GB/s. Seeded
        # from the topology (the port's own NUMA node and its sibling carry line rate, the rest of
        # the socket half of it, the other socket about a quarter) and corrected by every restore.
        V = self.topology.vertices
        self.direct_cap = {}
        for n in self.nodes:
            nic = self.info[n].get("nic_numa") or [0, 1, 4, 5]
            self.direct_cap[n] = {}
            for p, pn in enumerate(nic):
                self.direct_cap[n][p] = {}
                for c in self.info[n]["cards"]:
                    cn = V[f"{n}/hbm/{c}"].numa
                    if cn is None or pn is None:
                        g = PORT_GBPS / 2
                    elif cn == pn or (cn // 4 == pn // 4 and abs(cn - pn) == 2):
                        g = PORT_GBPS
                    elif cn // 4 == pn // 4:
                        g = PORT_GBPS / 2
                    else:
                        g = PORT_GBPS / 3.5
                    self.direct_cap[n][p][c] = g

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

    def warm_arenas(self, seconds=60.0):
        """Warm every node's HBM arenas with direct RDMA traffic from a peer, each port feeding
        its two near cards (the pairs the capacity table rates at line speed)."""
        out = {}

        def one(n):
            names = list(self.nodes)                       # a ring: every node feeds exactly one other
            peer = names[(names.index(n) + 1) % len(names)]
            region = self.rpc[peer].call("stage_region")
            cap = self.direct_cap[n]
            pairs = []
            for c in self.info[n]["cards"]:
                p = max(cap, key=lambda p: cap[p][c])
                pairs.append([p, c])
            out[n] = Client(self.nodes[n]).call("warm_hbm", peer_ip=self.nodes[peer], region=region,
                                                seconds=seconds, pairs=pairs)
        th = [threading.Thread(target=one, args=(n,)) for n in self.nodes if self.info[n].get("arena_GiB")]
        [x.start() for x in th]
        [x.join() for x in th]
        for n in out:
            self._own_net[n] = time.time()
        return out

    def warm_paths(self, node, cards):
        """Enumerate (and cache) the paths into the given cards before the first restore needs
        them; enumeration is per topology, not per request."""
        t, fn = time.time(), self._paths_fn()
        for card in cards:
            for vid in self.topology.vertices:
                if vid != f"{node}/hbm/{card}":
                    fn(vid, f"{node}/hbm/{card}")
        return round(time.time() - t, 3)

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
             budget=None, threads=8, all_or_nothing=False, from_job=None):
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
                    if from_job and from_job[0] == node and c.id in from_job[2]:
                        card, off = from_job[2][c.id]        # still in this node's HBM: D2H, no disk
                        src = {"k": "job", "job": from_job[1], "card": card, "off": off}
                    elif here and kind != "dram":
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
    def where(self, model, cards):
        """{chunk id: (card, offset)} of a model restored onto `cards` (same rule as restore)."""
        base, _ = self.layout(model, cards)
        return {c.id: (base[c.file][0], base[c.file][1] + c.offset) for c in model.chunks}

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
            elif kind in ("dram", "hbm") and isinstance(loc, dict):
                # a peer's DRAM slab or device memory: both are RDMA regions the HCA reads directly
                cand = {"k": "dram" if kind == "dram" else "rhbm", "node": n, "ip": self.nodes[n],
                        "addr": loc["addr"], "rkeys": loc["rkeys"]}
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
                for p in self.graph.paths(src, dst, k=self.plan_paths):
                    # The data plane moves bytes between host memories; GPU-direct IB is not built.
                    if any(e.kind == "ib" and V[e.src].kind == "hbm" for e in p.edges):
                        continue
                    keep.append(p)
                if not keep and V[src].kind == "hbm" and V[src].node != V[dst].node:
                    # The graph prunes HBM -> DRAM -> (IB) -> DRAM -> HBM as dominated by the
                    # GPU-direct edge it assumes; without that edge it is the only real route.
                    pair = self.graph._pair
                    a = f"{V[src].node}/dram/{V[src].numa}"
                    for b in (v.id for v in V.values() if v.node == V[dst].node and v.kind == "dram"):
                        e1, e2, e3 = pair.get((src, a)), pair.get((a, b)), pair.get((b, dst))
                        if e1 and e2 and e3:
                            keep.append(Path((e1[0], e2[0], e3[0])))
                            if len(keep) >= 2:
                                break
                cache[key] = keep
            return cache[key]
        return fn

    def flow_plan(self, chunks, node, cards, ports, snap, mode="lp", use_matrix=True):
        """Assign every chunk to an ingress path of the destination node and return the predicted
        time. The destination is a small flow network:

            HCA port p --(<= port residual)--> card c      peers' DRAM/HBM, straight into HBM
            host DMA   --(<= host budget)----> card c      local DRAM (H2D), and every slow source
            NVMe       --(<= disk residual)--> host DMA    local/remote SSD copies, durable files
            card c takes <= its PCIe ingress in total

        mode "lp": min T  s.t. all bytes delivered and every capacity * T respected (a max
        concurrent flow); capacities are the live residuals of the snapshot, so a port another
        tenant is using, or a card whose PCIe is busy, gets less. The fractional flow is rounded
        to chunks; each port receives its share as contiguous per-card runs.
        mode "rr": no flow reasoning -- local copy if there is one, else ports round-robin per
        chunk. Used as the ablation."""
        t0 = time.time()
        NETK, LOCK = ("dram", "rhbm"), ("ldram", "lhbm")

        def slow_cls(cd):
            return f"rstage:{cd['node']}" if cd["k"] == "rstage" else cd["k"]
        meta = []
        for ch in chunks:
            ks = [cd["k"] for cd in ch["cands"]]
            loc = next((k for k in ks if k in LOCK), None)
            net = any(k in NETK for k in ks)
            slow = [slow_cls(cd) for cd in ch["cands"] if cd["k"] not in NETK + LOCK]
            meta.append((loc, net, slow))
        res = lambda rid, nominal: (snap.residual_GBps(rid) / nominal) if rid in self.topology.resources else 1.0
        share = {p: res(f"{node}/ib_rx/shca_{p}", 50.0) for p in ports}        # of the port's time
        if time.time() - self._own_net.get(node, 0) < 1.5:
            # The counters (100 ms samples, EWMA) still carry our own last transfer into this
            # node; reading that as another tenant would steer us away from our best ports.
            share = {p: 1.0 for p in ports}
        e = {p: {c: (self.direct_cap[node][p][c] if use_matrix else PORT_GBPS) for c in cards} for p in ports}
        cap_port = {p: PORT_GBPS * share[p] for p in ports}
        cap_card = {c: CARD_GBPS * res(f"{node}/pcie_in/{c}", 47.5) for c in cards}
        disks = {(node if cl in ("file", "lssd") else cl.split(":")[1]) for _, _, sl in meta for cl in sl}
        cap_slow = sum(NVME_GBPS * res(f"{d}/nvme_rd/{self.ssd_disk.get(d)}", 7.0) for d in disks) or NVME_GBPS
        GB = 1e9
        B, L, N, U, S = ({c: 0.0 for c in cards} for _ in range(5))
        for ch, (loc, net, slow) in zip(chunks, meta):
            c, g = ch["card"], ch["n"] / GB
            B[c] += g
            L[c] += g if loc else 0
            N[c] += g if net else 0
            U[c] += g if (loc or net) else 0
            S[c] += g if slow else 0
        info = {"mode": mode, "use_matrix": use_matrix, "cap_port": {p: round(v, 1) for p, v in cap_port.items()},
                "cap_card": {c: round(v, 1) for c, v in cap_card.items()}, "cap_slow": round(cap_slow, 1)}
        quota = {c: {} for c in cards}                  # card -> {class: GB}
        pred = None
        if mode == "lp":
            import numpy as np
            from scipy.optimize import linprog
            P, C = list(ports), list(cards)
            nx = len(P) * len(C)
            iy, iz, iT = nx, nx + len(C), nx + 2 * len(C)
            nv = iT + 1
            X = lambda a, j: a * len(C) + j
            Aub, bub, Aeq, beq = [], [], [], []
            row = lambda: np.zeros(nv)
            for j, c in enumerate(C):
                r = row(); r[[X(a, j) for a in range(len(P))]] = 1; r[iy + j] = 1; r[iz + j] = 1
                Aeq.append(r); beq.append(B[c])                           # every byte of the card
                r2 = r.copy(); r2[iT] = -cap_card[c]; Aub.append(r2); bub.append(0)   # card ingress
                r = row(); r[iy + j] = 1; Aub.append(r); bub.append(L[c])
                r = row(); r[iz + j] = 1; Aub.append(r); bub.append(S[c])       # only what has a slow copy
                r = row(); r[[X(a, j) for a in range(len(P))]] = 1; Aub.append(r); bub.append(N[c])
                r = row(); r[[X(a, j) for a in range(len(P))]] = 1; r[iy + j] = 1; Aub.append(r); bub.append(U[c])
            for a, p in enumerate(P):
                # the port is one pipe: bytes to card c cost 1/e[p][c] seconds of it each
                r = row()
                for j, c in enumerate(C):
                    r[X(a, j)] = 1.0 / e[p][c]
                r[iT] = -share[p]; Aub.append(r); bub.append(0)
            r = row(); r[iy:iT] = 1; r[iT] = -HOST_DMA_GBPS; Aub.append(r); bub.append(0)      # host DMA budget
            r = row(); r[iz:iT] = 1; r[iT] = -cap_slow; Aub.append(r); bub.append(0)           # disks
            cost = row(); cost[iT] = 1.0; cost[iz:iT] = 1e-4               # same T: prefer the fast copy
            for a, p in enumerate(P):                                      # ... and the fast pairs
                for j, c in enumerate(C):
                    cost[X(a, j)] = 1e-6 / e[p][c]
            sol = linprog(cost, A_ub=np.array(Aub), b_ub=bub, A_eq=np.array(Aeq), b_eq=beq, bounds=(0, None), method="highs")
            if sol.success:
                pred = float(sol.x[iT])
                for j, c in enumerate(C):
                    for a, p in enumerate(P):
                        if sol.x[X(a, j)] > 1e-6:
                            quota[c][f"net:{p}"] = float(sol.x[X(a, j)])
                    quota[c]["local"] = float(sol.x[iy + j])
                    quota[c]["slow"] = float(sol.x[iz + j])
                info["flow_GB"] = {f"c{c}": {k: round(v, 1) for k, v in q.items() if v > 0.05} for c, q in quota.items()}
            else:
                mode = "rr"
                info["lp_failed"] = sol.message
        rr = 0
        by_card = collections.defaultdict(list)
        for i, ch in enumerate(chunks):
            by_card[ch["card"]].append(i)
        for c, idxs in by_card.items():
            q = quota[c]
            idxs.sort(key=lambda i: chunks[i]["off"])
            # least flexible chunks first, so the flexible ones absorb what is left of each quota
            for i in sorted(idxs, key=lambda i: (meta[i][0] is not None) + meta[i][1]):
                loc, net, slow = meta[i]
                g = chunks[i]["n"] / GB
                if mode != "lp":
                    if loc:
                        cls = loc
                    elif net:
                        cls = f"net:{ports[rr % len(ports)]}"; rr += 1
                    else:
                        cls = slow[rr % len(slow)]; rr += 1
                else:
                    opts = []
                    if loc:
                        opts.append(("local", loc))
                    if net:
                        opts += [(k, k) for k in q if k.startswith("net:")]
                    opts = [(q.get(k, 0.0), k, cls) for k, cls in opts]
                    best = max(opts, default=(0.0, None, None))
                    if best[1] is None or (best[0] < g * 0.5 and slow and q.get("slow", 0.0) >= g * 0.5):
                        k, cls = "slow", (slow[rr % len(slow)] if slow else None); rr += 1
                    else:
                        k, cls = best[1], best[2]
                    if cls is None:                      # only fast copies, quotas exhausted by rounding
                        k, cls = best[1], best[2]
                    q[k] = q.get(k, 0.0) - g
                chunks[i]["plan_cls"] = cls
        if pred is None:                                 # bottleneck estimate of the rr assignment
            load = collections.Counter()
            for ch in chunks:
                cls = ch["plan_cls"]
                load[cls if cls.startswith("net:") else ("local" if cls in LOCK else "slow")] += ch["n"] / GB
            pred = max([load[f"net:{p}"] / (cap_port[p] * 0.5) for p in ports] +
                       [(load["local"] + load["slow"]) / HOST_DMA_GBPS, load["slow"] / cap_slow] +
                       [B[c] / cap_card[c] for c in cards])
        info["plan_ms"] = round((time.time() - t0) * 1e3, 2)
        return pred, info

    def restore(self, name, node, cards, allow=("dram", "ssd", "hbm", "file"), strategy="lp",
                steal=True, live=True, job=None, verify=False, release=True, single_source=None,
                avoid_shared_root=True, direct=True, learn=False, use_arena=True):
        """Bring the model's weights into HBM on `node`'s `cards`. Returns timings and path stats.

        live=False plans on nominal capacities (what a static system would assume).
        single_source: restrict remote candidates to one node (D2D-fork style baselines)."""
        model = self.models[name]
        t0 = time.time()
        edge = None
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
        V = self.topology.vertices
        nic_numa = self.info[node].get("nic_numa") or []
        all_ports = list(range(len(nic_numa))) or [0, 1, 2, 3]
        if direct:
            # Direct landing: every port is usable (a port writing device memory does not go
            # through the host, so the shared-root penalty of the relay does not apply).
            ports = all_ports
            for ch in chunks:
                ch.pop("_verts", None)
            predicted, plan_info = self.flow_plan(chunks, node, list(cards), ports, snap,
                                                  mode="lp" if strategy in ("lp", "lp_blind") else "rr",
                                                  use_matrix=strategy == "lp")
            prior = {f"net:{p}": plan_info["cap_port"][p] for p in ports}
            if strategy == "lp":
                edge = {f"{p}:{c}": self.direct_cap[node][p][c] for p in ports for c in cards}
        else:
            plan = plan_fetch(snap, demands, replicas, self._paths_fn(),
                              strategy=strategy if strategy in ("lp", "greedy", "fastest") else "greedy")
            predicted, plan_info = plan.predicted_s, {"mode": "relay:" + strategy}
            src_of = {t.chunk_id: t.path.src for t in plan.transfers}
            for ch, (key, _, _) in zip(chunks, demands):
                verts = ch.pop("_verts")
                ch["plan"] = verts.index(src_of[key]) if key in src_of and src_of[key] in verts else 0
            # Relay: receive only on ports that do not share a NUMA node (PCIe root) with a target card.
            card_numa = {V[f"{node}/hbm/{c}"].numa for c in cards}
            ports = [p for p, nu in enumerate(nic_numa) if nu not in card_numa] if avoid_shared_root else []
            if len(ports) < 2:
                ports = all_ports
            prior = {}
        for ch in chunks:
            for cd in ch["cands"]:
                k = cd["k"]
                cls = f"dram:{cd['node']}" if k in ("dram", "rhbm") else (f"rstage:{cd['node']}" if k == "rstage" else k)
                prior.setdefault(cls, PRIOR[k])
        plan_s = time.time() - t0
        job = job or f"{name}-{int(time.time() * 1000)}"
        self.active[job] = node
        try:
            r = Client(self.nodes[node]).call("restore", job=job, cards={str(c): n for c, n in used.items() if n},
                                              chunks=chunks, steal=steal, prior=prior, ports=ports, direct=direct, edge=edge,
                                              use_arena=use_arena)
        finally:
            self.active.pop(job, None)
        total = time.time() - t0
        self._own_net[node] = time.time()
        for k, (gbps, nb) in (r.get("pair_GBps") or {}).items():          # learn the pair capacities
            if nb >= 2 * GiB and learn:
                pp, cc = (int(x) for x in k.split(":"))
                old = self.direct_cap[node][pp][cc]
                self.direct_cap[node][pp][cc] = min(PORT_GBPS + 2, 0.5 * old + 0.5 * max(gbps, 3.0))
        out = {"model": name, "node": node, "cards": list(cards), "GiB": round(model.nbytes / GiB, 2),
               "total_s": round(total, 4), "plan_s": round(plan_s, 4), "exec_s": r["total_s"],
               "alloc_s": r["alloc_s"], "GBps": round(model.nbytes / total / 1e9, 2),
               "predicted_s": round(predicted, 4), "strategy": strategy, "steal": steal, "live": live,
               "direct": direct, "plan": plan_info,
               "failed_fetches": r["failed_fetches"], "fail_why": r.get("fail_why"), "paths": r["paths"], "rates": r["rates_GBps"], "prof": r["prof"], "ports": ports, "pair_GBps": {k: v[0] for k, v in (r.get("pair_GBps") or {}).items()}, "job": job}
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

    def publish(self, name, node, job):
        """An active instance is a replica too: its HBM is registered, peers can read it."""
        loc = Client(self.nodes[node]).call("job_locators", job=job)
        with self.lock:
            for cid, l in loc.items():
                self.rep[cid][f"{node}/hbm/{l['card']}"] = {"addr": l["addr"], "rkeys": l["rkeys"], "job": job}
        return len(loc)

    def unpublish(self, node, job):
        with self.lock:
            for cid, reps in self.rep.items():
                for v in [v for v, l in reps.items() if isinstance(l, dict) and l.get("job") == job]:
                    del reps[v]

    def trim(self, need_bytes, tier="dram"):
        """Free `need_bytes` of budget by dropping redundant replicas: chunks that have two or
        more copies in `tier` lose one. Coverage of every model is untouched; only redundancy
        is traded for room (used when a model has chunks with no fast copy at all)."""
        per, freed = collections.defaultdict(list), 0
        with self.lock:
            for cid, reps in self.rep.items():
                if freed >= need_bytes:
                    break
                vs = [v for v in reps if self._vparts(v)[1] == tier]
                if len(vs) >= 2:
                    v = max(vs, key=lambda v: self.held[v])       # thin out the fullest vertex
                    per[v].append(cid)
                    freed += self.size[cid]
        self._drop(per)
        return freed

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
