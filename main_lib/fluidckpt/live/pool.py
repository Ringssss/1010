"""FluidPool coordinator: one live graph, one chunk catalog, three verbs.

  park(model)      scatter the model's chunks into whatever free DRAM / SSD / HBM the live graph
                   shows right now, on any node (fragmented placement: a chunk goes where there is
                   room, neighbours need not follow it);
  restore(model)   plan sources on the current snapshot (flow.plan_fetch), hand the plan to the
                   destination's node daemon, which pulls from all of them at once;
  react()          called on every graph update: when another tenant squeezes a vertex we hold
                   chunks on, give the space back immediately (revoke -> drop), least valuable
                   chunks first; rebalance() re-protects the affected models elsewhere.
  retire(model)    a model leaves its cards: chunks whose only fast copy was that instance flow
                   out of HBM into whatever fast room the pool has (demote, not drop).
  stripe(model)    NVMe copies spread over every pool disk, so the slow tail reads all of them.

HBM and DRAM form one pool: a published instance is a replica, and a DRAM copy of a chunk that is
also live in HBM is redundancy that is given up first when room is needed.

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
XGMI_IN_GBPS = 165.0    # xGMI fan-in into one card from all peers (pool_r03/relay_r01: 164-165)
XGMI_PAIR_GBPS = 170.0  # one card -> one card over xGMI, target-side pull (relay_r01: 168-173)
RELAY_RING = 8 * 64 * MiB   # arena room a relay card must have for its slot ring


class Model:
    def __init__(self, name, manifest, model_dir, durable_node):
        self.name, self.manifest, self.dir, self.durable_node = name, manifest, model_dir, durable_node
        # Only weight files go to HBM; config/tokenizer files are read by the engine as usual.
        self.chunks = [c for c in manifest.chunks if manifest.files[c.file]["role"] == "weights"]
        self.nbytes = sum(c.nbytes for c in self.chunks)
        self.last_used = 0.0
        self.heat, self.heat_at = 0.0, 0                 # EWMA of requests (Pool.touch), and its clock


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
        self.place_margin = 4 * GiB
        self.hbm_park = {n: set() for n in nodes}        # node -> cards that may hold parked chunks
        self.hbm_yielded = {}                            # (node, card) -> when a tenant made us give it up
        self.hbm_min_chunk = 1 * MiB                     # arena ranges are 2 MiB aligned
        self.ledger = {}                                 # job -> {"use": {resource: GB/s}, "end": t | None}
        # Borrowed ingress (relay through idle near cards over xGMI): "off" | "idle" (only cards
        # with nothing of ours or anybody else's on them) | "loose" (our own resident, idle
        # instances allowed). Owner first: a card a tenant uses is never borrowed.
        self.relay_policy = "off"
        # Ablation knob: "value" (chunks with another fast replica first, then the coldest model)
        # or "age" (whole models' chunks, least recently used model first -- no replica reasoning).
        self.evict_order = "value"
        self.inflight_cards = {}                         # job -> (node, {cards targeted or borrowed})
        self.plan_lock = threading.Lock()                # plans are made one at a time against the ledger
        self._tl = threading.local()
        self.heat_decay, self._clock = 0.97, 0           # per request seen by touch()
        self.contend_min, self.contend_factor = 0.10, 0.15   # see _share()
        self._stat_rpc = {n: Client(ip) for n, ip in nodes.items()}   # used under self.lock only
        self.plan_paths = 6                              # candidate paths per (source, card)
        self.events = []
        self._stop = threading.Event()
        self._thr = None
        self.info = {n: self.rpc[n].call("hello") for n in nodes}
        self.slab_bytes = max([i.get("slab_bytes", 256 * MiB) for i in self.info.values()])
        # Arena size to re-create on a card we take back; arenas come and go during a run (tenants,
        # the driver), so what exists right now is always asked from the daemon (stat), never cached.
        self.arena_default_gib = max([g for i in self.info.values() for g in (i.get("arena_GiB") or {}).values()] or [0])
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
        with self.lock:                                  # the counters still show this traffic for a moment
            for n in out:
                use = {f"{n}/ib_rx/shca_{p}": PORT_GBPS for p in self.direct_cap[n]}
                self.ledger[f"warm-{n}-{time.time()}"] = {"use": use, "end": time.time()}
            self._own_refresh()
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

    def _cl(self, node):
        """This thread's own RPC client to `node` (a Client carries one call at a time)."""
        tl = self._tl
        if not hasattr(tl, "cl"):
            tl.cl = {}
        if node not in tl.cl:
            tl.cl[node] = Client(self.nodes[node])
        return tl.cl[node]

    def _event(self, event, **k):
        ev = {"event": event, "t": round(time.time(), 4), **k}
        self.events.append(ev)
        self.log(**ev)
        return ev

    # ---- value of a replica ----------------------------------------------------------------
    def touch(self, name, w=1.0):
        """One request for `name` (hit or restore): its heat is an EWMA over all requests."""
        with self.lock:
            self._clock += 1
            m = self.models[name]
            m.heat = self._heat(m) + w
            m.heat_at = self._clock
            m.last_used = time.time()

    def _heat(self, m):
        return m.heat * (self.heat_decay ** (self._clock - m.heat_at))

    def _cheat(self, cid):
        """Heat of a chunk: that of its hottest owner (ties by recency)."""
        return max(((self._heat(self.models[o]), self.models[o].last_used) for o in self.owners[cid]),
                   default=(0.0, 0.0))

    def _fast(self, cid, but=None):
        """Does the chunk have a fast replica (DRAM, parked HBM, a published instance) other than `but`?"""
        for v in self.rep[cid]:
            if v != but and self._vparts(v)[1] in ("dram", "hbm"):
                return True
        return False

    def coverage_all(self, tiers=("dram", "hbm")):
        """Bytes of the catalog with at least one replica in `tiers` / bytes of the catalog."""
        with self.lock:
            ok = sum(n for cid, n in self.size.items() if any(self._vparts(v)[1] in tiers for v in self.rep[cid]))
            return ok / max(1, sum(self.size.values()))

    # ---- park: fragmented placement ------------------------------------------------------
    def _rooms(self, tiers, avoid_nodes=(), snap=None, budget=None):
        """{vertex: bytes that can be stored there now}. DRAM: what the live graph shows free,
        clear of the eviction threshold, plus the holes inside slabs we already hold; HBM: free
        arena bytes of the cards in hbm_park; SSD: the node's pool disk. budget caps listed vertices."""
        snap = snap or self.snapshot()
        V = self.topology.vertices
        stat = {}
        if "dram" in tiers or "hbm" in tiers:
            for n in self.nodes:
                if n not in avoid_nodes:
                    try:
                        stat[n] = self._stat_rpc[n].call("stat")
                    except Exception:                    # noqa: BLE001
                        stat[n] = {}
        room = {}
        for vid, v in V.items():
            if v.kind not in tiers or v.node in avoid_nodes or v.node not in self.nodes:
                continue
            if v.kind == "hbm":
                c = int(v.index)
                if c not in self.hbm_park.get(v.node, ()) or (v.node, c) in self.hbm_yielded:
                    continue
                a = (stat.get(v.node, {}).get("arena") or {}).get(str(c))
                r = 0 if a is None else a["free"] - self.reserved[vid]
            elif v.kind == "ssd":
                if v.index != self.ssd_disk.get(v.node):
                    continue
                r = self.free(vid, snap)
            else:
                st = stat.get(v.node, {})
                holes = (st.get("slab_by_numa") or {}).get(str(v.index), 0) - (st.get("dram_bytes") or {}).get(str(v.index), 0)
                r = max(0, self.free(vid, snap) - self.place_margin) + max(0, holes - self.reserved[vid])
            if budget is not None and vid in budget:
                r = min(r, budget[vid] - self.held[vid] - self.reserved[vid])
            room[vid] = max(0, r)
        return room

    def place(self, model, copies=2, tiers=("dram",), avoid_nodes=(), prefer=None, pin_nodes=(),
              snap=None, budget=None, chunks=None, local_frac=None, spread=False, within=None):
        """Choose vertices for each chunk that still lacks `copies` replicas in `tiers`.

        Water-filling over the live room of every vertex of those tiers: each chunk goes to the
        vertex with the most room left that does not already hold it and whose node holds none
        of its other replicas. chunks: restrict to these (default: the whole model).
        prefer: the model's home node(s), where its restores will land. With local_frac the home
        gets that share of the bytes and peers the rest -- a restore then fills the host's H2D
        path and the HCA ports at the same time and both finish together; without it the home
        gets every byte it has room for (local_frac=1.0). local_frac=None with a home means
        "auto": local_share(). spread: balance this call's bytes over the vertices (striping).
        within: a node set -- only replicas and vertices on those nodes count, so copies=1 means
        "one copy inside this set" whatever other nodes hold (per-node islands of the whole-model
        baselines). budget: {vertex: max bytes} caps on top of the graph (DRAM and HBM vertices).
        Returns ({vertex: [(chunk, rank)]}, short): rank 0 is a chunk's first new replica, later
        ranks are filled from an earlier one."""
        V = self.topology.vertices
        if within is not None:
            within = {within} if isinstance(within, str) else set(within)
            avoid_nodes = tuple(set(avoid_nodes) | {n for n in self.nodes if n not in within})
        room = self._rooms(tiers, avoid_nodes, snap, budget)
        homes = set() if prefer is None else ({prefer} if isinstance(prefer, str) else set(prefer))
        if homes and local_frac is None:
            local_frac = self.local_share()
        out = collections.defaultdict(list)
        put = collections.Counter()
        short = 0
        uniq = list({c.id: c for c in (chunks if chunks is not None else model.chunks)}.values())
        total = sum(c.nbytes for c in uniq) or 1
        local = 0
        if local_frac is not None and homes:
            local = sum(c.nbytes for c in uniq if any(self._vparts(v)[0] in homes and self._vparts(v)[1] in tiers
                                                      for v in self.rep[c.id]))
        for c in sorted(uniq, key=lambda c: -c.nbytes):
            have_nodes = {self._vparts(v)[0] for v in self.rep[c.id] if self._vparts(v)[1] in tiers}
            if within is not None:
                have_nodes &= within
            for rank in range(max(0, copies - len(have_nodes))):
                want_local = None if (local_frac is None or not homes) else local < local_frac * total
                best, best_key = None, None
                for vid, r in room.items():
                    v = V[vid]
                    if r < c.nbytes or v.node in have_nodes:
                        continue
                    if v.kind == "hbm" and c.nbytes < self.hbm_min_chunk:
                        continue
                    at_home = v.node in homes
                    key = (at_home if want_local is None else at_home == want_local, v.kind != "hbm",
                           -put[vid] if spread else 0, r)
                    if best is None or key > best_key:
                        best, best_key = vid, key
                if best is None:
                    short += 1
                    break
                room[best] -= c.nbytes
                put[best] += c.nbytes
                if V[best].node in homes:
                    local += c.nbytes
                have_nodes.add(V[best].node)
                out[best].append((c, rank))
        return out, short

    def wait_pinned(self, timeout=900.0, poll=1.0):
        """Block until every node has registered its pinned slabs with the GPU runtime (local
        H2D then runs at PCIe rate instead of pageable speed). Returns {seconds, pending}."""
        t0 = time.time()
        while True:
            pend = {n: self._cl(n).call("stat").get("pin_pending", 0) for n in self.nodes}
            if not any(pend.values()) or time.time() - t0 > timeout:
                return {"s": round(time.time() - t0, 1), "pending": pend}
            time.sleep(poll)

    def local_share(self, nports=4):
        """Share of a model's fast bytes that belongs on its home's own DRAM: the host H2D path
        against the HCA ports, so both ingress paths of a restore are busy for the same time."""
        return HOST_DMA_GBPS / (HOST_DMA_GBPS + nports * PORT_GBPS)

    def _put_src(self, c, model, node, kind, from_job, rr):
        """Fastest source for storing chunk c on (node, kind), given what the catalog holds."""
        path = os.path.join(model.dir, c.file)
        reps = self.rep[c.id]
        if from_job and c.id in from_job[2]:
            if from_job[0] == node:                      # still in this node's HBM: D2H / D2D, no disk
                card, off = from_job[2][c.id]
                return {"k": "job", "job": from_job[1], "card": card, "off": off}
            loc = (from_job[3] if len(from_job) > 3 else {}).get(c.id)
            if loc:                                      # a peer's instance: read its device memory
                return {"k": "rdma", "ip": self.nodes[from_job[0]], "addr": loc["addr"], "rkeys": loc["rkeys"]}
        if kind != "dram" and any(v.startswith(node + "/dram/") for v in reps):
            return {"k": "local"}                        # this node's DRAM copy feeds its SSD/HBM
        for v, l in reps.items():                        # a peer's DRAM slab or device memory
            n, k, _ = self._vparts(v)
            if n != node and k in ("dram", "hbm") and isinstance(l, dict) and "addr" in l:
                return {"k": "rdma", "ip": self.nodes[n], "addr": l["addr"], "rkeys": l["rkeys"]}
        if any(v.startswith(node + "/ssd/") for v in reps) and kind != "ssd":
            return {"k": "lssd"}
        if node == model.durable_node:
            return {"k": "file", "path": path, "off": c.offset}
        holders = [self._vparts(v)[0] for v in reps if self._vparts(v)[1] == "ssd" and self._vparts(v)[0] != node]
        srcs = [("rfile", model.durable_node)] + [("rssd", h) for h in holders if h != model.durable_node]
        k, n = srcs[rr % len(srcs)]                      # spread the reads over the disks that hold it
        if k == "rssd":
            return {"k": "rssd", "ip": self.nodes[n]}
        return {"k": "rfile", "ip": self.nodes[n], "path": path, "off": c.offset}

    def park(self, name, copies=2, tiers=("dram",), prefer=None, pin_nodes=None, avoid_nodes=(),
             budget=None, threads=8, all_or_nothing=False, from_job=None, chunks=None, local_frac=None,
             spread=False, quiet=False, within=None):
        """Place and store. A chunk's first replica is filled from the fastest copy that exists
        (an instance in HBM, a DRAM / parked-HBM replica, an NVMe copy, the durable file);
        further replicas are pulled from an existing replica, so the durable disk is read once.
        pin_nodes: nodes whose DRAM slabs are registered with the GPU runtime (None = all).
        from_job: (node, job, {cid: (card, off)}[, {cid: locator}]) -- an instance to copy from.
        prefer / local_frac / within / spread: see place(). all_or_nothing: return None (and
        store nothing) unless every chunk could be placed. The result's placed_GiB / short /
        short_GiB say what was stored and what found no room."""
        model = self.models[name]
        t0 = time.time()
        if pin_nodes is None:
            pin_nodes = tuple(self.nodes)
        with self.lock:
            plan, short = self.place(model, copies, tiers, avoid_nodes, prefer, pin_nodes, budget=budget,
                                     chunks=chunks, local_frac=local_frac, spread=spread, within=within)
            if all_or_nothing and short:
                return None                              # whole-model systems cannot keep a part
            for vid, cs in plan.items():
                self.reserved[vid] += sum(c.nbytes for c, _ in cs)
        errs, prof = [], []

        def run(vid, cs):
            node, kind, idx = self._vparts(vid)
            cl = Client(self.nodes[node])
            try:
                with self.lock:
                    items = [{"id": c.id, "n": c.nbytes, "src": self._put_src(c, model, node, kind, from_job, k)}
                             for k, c in enumerate(cs)]
                if kind == "dram":
                    kw = {"numa": int(idx), "pin": node in pin_nodes}
                elif kind == "hbm":
                    cap = self.direct_cap[node]
                    kw = {"card": int(idx), "ports": sorted(cap, key=lambda p: -cap[p][int(idx)])[:2]}
                else:
                    kw = {}
                r = cl.call("put", items=items, tier=kind, threads=threads, **kw)
                prof.append((r.get("reserve_s", 0.0), r.get("s", 0.0)))
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
        stored = 0 if errs else nbytes
        out = {"model": name, "s": round(dt, 3), "GiB": round(nbytes / GiB, 2), "placed_GiB": round(stored / GiB, 2),
               "GBps": round(nbytes / max(dt, 1e-9) / 1e9, 2), "short": short, "tiers": list(tiers),
               "reserve_s_max": round(max([a for a, _ in prof] or [0.0]), 3),
               "put_s_max": round(max([b for _, b in prof] or [0.0]), 3),
               "vertices": {v: round(sum(c.nbytes for c, _ in cs) / GiB, 2) for v, cs in sorted(plan.items())},
               "errors": errs}
        if not quiet or nbytes or errs:
            self.log(event="park", **out)
        return out

    def stripe(self, name, nodes=None, budget=None, threads=8):
        """An NVMe copy of every chunk on a pool disk other than the durable one, balanced over
        the disks, so the model's cold bytes can be read from several NVMe at once (a restore's
        slow tail then runs at the sum of the disks instead of one disk's 6 GB/s)."""
        model = self.models[name]
        avoid = {model.durable_node} | ({n for n in self.nodes if n not in nodes} if nodes else set())
        with self.lock:
            need = [c for c in model.chunks
                    if not any(self._vparts(v)[1] == "ssd" and self._vparts(v)[0] != model.durable_node
                               for v in self.rep[c.id])]
        if not need:
            return {"model": name, "GiB": 0.0, "s": 0.0, "short": 0, "vertices": {}, "errors": []}
        return self.park(name, copies=1, tiers=("ssd",), avoid_nodes=tuple(avoid), budget=budget, threads=threads,
                         chunks=need, spread=True)

    def unpark(self, name, tiers=("dram", "ssd", "hbm")):
        """Drop every replica of the model (chunks shared with another parked model stay)."""
        model = self.models[name]
        per = collections.defaultdict(list)
        with self.lock:
            for c in model.chunks:
                if len(self.owners[c.id]) > 1 and any(o != name and self.is_parked(o) for o in self.owners[c.id]):
                    continue
                for v, l in list(self.rep[c.id].items()):
                    if isinstance(l, dict) and l.get("job"):
                        continue                         # a serving instance is not ours to drop
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
            if not ids:
                continue
            node, kind, _ = self._vparts(vid)
            cl = clients[node] if clients else self._cl(node)
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
                        "release_s": r["release_s"], "slabs": r.get("slabs_released", 0)}
        return out

    # ---- HBM as a parking tier -----------------------------------------------------------
    def _parked_on(self, node, card):
        vid = f"{node}/hbm/{card}"
        return [cid for cid, reps in self.rep.items()
                if isinstance(reps.get(vid), dict) and not reps[vid].get("job")]

    def relay_pick(self, node, cards, policy=None, snap=None, ring=RELAY_RING):
        """{port: relay card} for a restore into `cards` on `node`. For every port whose two near
        cards are not targets, borrow the better near card that (FuseLink's owner-first rule)
        no tenant holds (live HBM use minus our arena <= 4 GiB, not yielded), no running or
        planned restore targets or borrows, has `ring` contiguous arena bytes free, and whose
        PCIe ingress the live graph shows idle. policy "idle" also skips cards where one of our
        restored models lives. Measured cost of borrowing a card that runs an HBM-bound job:
        -26% for that job (relay_r01), hence the rule."""
        policy = self.relay_policy if policy is None else policy
        if policy in (None, "off") or node not in self.direct_cap:
            return {}
        try:
            with self.lock:
                st = self._stat_rpc[node].call("stat")
        except Exception:                                # noqa: BLE001
            return {}
        arena = st.get("arena") or {}
        busy = {int(c) for c in cards}
        with self.lock:
            inflight = list(self.inflight_cards.values())
        for n, cs in inflight:
            if n == node:
                busy |= set(cs)
        if policy == "idle":
            busy |= {int(c) for c in st.get("job_cards") or ()}
        used = (self.fast.get(node) or {}).get("hbm_used") or {}
        ok = set()
        for c in sorted({c for row in self.direct_cap[node].values() for c in row}):
            a = arena.get(str(c))
            if c in busy or (node, c) in self.hbm_yielded or a is None or a.get("largest", 0) < ring:
                continue
            if str(c) in used and used[str(c)] - a["nbytes"] > 4 * GiB:
                continue                                 # a tenant holds memory on the card
            if snap is not None and f"{node}/pcie_in/{c}" in self.topology.resources and \
                    self._share(snap, f"{node}/pcie_in/{c}") < 0.8:
                continue
            ok.add(c)
        out, taken = {}, set()
        cap = self.direct_cap[node]
        for p in sorted(cap):
            near = sorted(cap[p], key=lambda c: -cap[p][c])[:2]
            if any(c in cards for c in near):
                continue                                 # the port feeds a target card directly
            pick = next((c for c in near if c in ok and c not in taken), None)
            if pick is not None and cap[p][pick] >= 0.8 * PORT_GBPS:
                out[p] = pick
                taken.add(pick)
        return out

    def make_room(self, node, cards, nbytes=None):
        """Before a restore lands on `cards` ({card: bytes}, or a list with one `nbytes`): parked
        chunks that sit where the model's contiguous arena range must go are dropped."""
        need = dict(cards) if isinstance(cards, dict) else {c: nbytes for c in cards}
        per, t0 = {}, time.time()
        for c, n in need.items():
            vid = f"{node}/hbm/{int(c)}"
            if not n or self.held[vid] <= 0:
                continue
            r = self._cl(node).call("hbm_room", card=int(c), nbytes=int(n))
            if not r["ok"]:
                raise RuntimeError(f"{node} card {c}: no contiguous arena range of {n} bytes")
            if r["ids"]:
                per[vid] = r["ids"]
        out = self._drop(per)
        freed = sum(o["freed"] for o in out.values())
        if freed:
            self._event("hbm_make_room", node=node, freed_GiB=round(freed / GiB, 2), s=round(time.time() - t0, 4))
        return {"dropped_GiB": round(freed / GiB, 2), "s": round(time.time() - t0, 4)}

    def yield_card(self, node, card, release_arena=False):
        """Give a card back (to a tenant, or to serving): parked chunks leave it and nothing new
        is parked there until the caller adds it to hbm_park again. release_arena also frees the
        card's arena, which only works when no restored model lives in it."""
        card, t0 = int(card), time.time()
        self.hbm_park.setdefault(node, set()).discard(card)
        with self.lock:
            ids = self._parked_on(node, card)
        out = self._drop({f"{node}/hbm/{card}": ids})
        freed = sum(o["freed"] for o in out.values())
        if release_arena:
            self._cl(node).call("arena", card=card, gib=0)
        return {"freed_GiB": round(freed / GiB, 2), "chunks": len(ids), "s": round(time.time() - t0, 4)}

    # ---- our own traffic: the reservation ledger ---------------------------------------------
    def _own_refresh(self):
        """Tell the graph what we are driving ourselves, so its counters are not read as another
        tenant. Entries stay for a short tail after their transfer ended because the counters
        (100 ms samples, EWMA) lag. Call with self.lock held."""
        now = time.time()
        for j in [j for j, e in self.ledger.items() if e["end"] is not None and now - e["end"] > 0.6]:
            del self.ledger[j]
        R, own = self.topology.resources, collections.Counter()
        for e in self.ledger.values():
            for rid, g in e["use"].items():
                own[rid] += g
        # We cannot tell our bytes from others' on a resource we load heavily: call it ours.
        self.graph.own_traffic({rid: min(R[rid].nominal_GBps, 1.5 * g + 2.0) for rid, g in own.items() if rid in R})

    def _resv(self, rid, but=None):
        """GB/s of `rid` promised to our restores that are still running (not `but`)."""
        return sum(e["use"].get(rid, 0.0) for j, e in self.ledger.items() if e["end"] is None and j != but)

    def _share(self, snap, rid, job=None):
        """Fraction of resource `rid` a new restore can count on: the live residual minus what
        our other running restores reserved. An HCA port another tenant is driving is not shared
        in proportion, and not per direction either: while a tenant pulled 17-19 GB/s from
        node29 into node28 on ports 0 and 1, we read 8-15 GB/s from node29's side of those ports
        and only 4.8-5.9 GB/s from node28's side -- the *receiving* node, whose tx counter was
        idle -- against 36-39 GB/s from an untouched node (pool_r03/smoke/i.log; maas2_r01 ib
        phase: 0.1-0.3 GB/s into a port a tenant was filling). So a port another tenant drives in
        either direction is worth `contend_factor` of its residual in both, and the plan routes
        around it when it can."""
        R = self.topology.resources
        nom = R[rid].nominal_GBps
        res = snap.residual_GBps(rid)
        share = (res - self._resv(rid, job)) / nom
        if "/ib_tx/" in rid or "/ib_rx/" in rid:
            other = rid.replace("/ib_tx/", "/ib_rx/") if "/ib_tx/" in rid else rid.replace("/ib_rx/", "/ib_tx/")
            used = (nom - res) + ((R[other].nominal_GBps - snap.residual_GBps(other)) if other in R else 0.0)
            if used / nom > self.contend_min:
                share = min(share, max(0.0, 1.0 - used / nom)) * self.contend_factor
        return min(1.0, max(0.02, share))

    def _ledger_end(self, job):
        with self.lock:
            e = self.ledger.get(job)
            if e is not None:
                e["end"] = time.time()
            self._own_refresh()

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
                if kind == "hbm" and isinstance(loc, dict) and loc.get("job"):
                    if loc.get("off") is None:
                        continue
                    cand = {"k": "lhbm", "job": loc["job"], "card": loc["card"], "off": loc["off"]}
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

    def flow_plan(self, chunks, node, cards, ports, snap, mode="lp", use_matrix=True, live=True, job=None,
                  relay=None):
        """Assign every chunk to an ingress path of the destination node and return the predicted
        time. The restore is a small flow network:

            source s, port p --(<= tx residual of s on p)--> HCA port p of the destination
            HCA port p --(<= rx residual, pair capacity)--> card c     peers' DRAM/HBM, straight into HBM
            host DMA   --(<= host budget)----> card c      local DRAM (H2D), and every slow source
            NVMe       --(<= disk residuals)-> host DMA    local/remote SSD copies, durable files
            card c takes <= its PCIe ingress in total
            relay[p] = r: port p --> slot on idle near card r --(xGMI)--> card c   (borrowed ingress:
                uses the port, r's PCIe ingress and xGMI, not c's PCIe; c's xGMI fan-in <= 165)
            parked HBM on this node (lhbm) --(xGMI)--> card c        (no host DMA, no PCIe of c)

        The four HCA ports are four subnets: destination port p reads only from port p of a
        source, so a port is worth what its *sources* can still send on it. Chunks are grouped by
        (card, set of sources holding them, has a local copy, has a slow copy) and the LP moves
        bytes of each group over (source, port) pairs.

        mode "lp": min T  s.t. all bytes delivered and every capacity * T respected (a max
        concurrent flow). live=True: capacities are the residuals of the snapshot minus what our
        own running restores have reserved (the ledger), on both sides of every link, and each
        chunk gets the source its flow assumed. live=False: nominal capacities, destination side
        only, no source choice -- what a static planner knows.
        mode "rr": no flow reasoning -- local copy if there is one, else ports round-robin per
        chunk. Used as the ablation."""
        t0 = time.time()
        relay = {int(p): int(r) for p, r in (relay or {}).items() if int(p) in ports}
        NETK, LOCK = ("dram", "rhbm"), ("ldram", "lhbm")
        R = self.topology.resources

        def slow_cls(cd):
            return f"rstage:{cd['node']}" if cd["k"] == "rstage" else cd["k"]

        def eff(rid):
            """Usable share of a resource: live residual minus our other restores, as a fraction."""
            if not live or rid not in R:
                return 1.0
            return self._share(snap, rid, job)
        meta = []
        for ch in chunks:
            loc = next((cd["k"] for cd in ch["cands"] if cd["k"] in LOCK), None)
            srcs = tuple(sorted({cd["node"] for cd in ch["cands"] if cd["k"] in NETK})) if live else \
                (("*",) if any(cd["k"] in NETK for cd in ch["cands"]) else ())
            slow = [slow_cls(cd) for cd in ch["cands"] if cd["k"] not in NETK + LOCK]
            kinds = {cd["k"] for cd in ch["cands"]}
            meta.append((loc, srcs, slow, "ldram" in kinds, "lhbm" in kinds))
        all_src = sorted({s for _, srcs, _, _, _ in meta for s in srcs})
        share = {p: eff(f"{node}/ib_rx/shca_{p}") for p in ports}              # of the port's time
        tx = {s: {p: (eff(f"{s}/ib_tx/shca_{p}") if s != "*" else 1.0) for p in ports} for s in all_src}
        e = {p: {c: (self.direct_cap[node][p][c] if use_matrix else PORT_GBPS) for c in cards} for p in ports}
        cap_port = {p: PORT_GBPS * min(share[p], max([tx[s][p] for s in all_src] or [1.0])) for p in ports}
        cap_card = {c: CARD_GBPS * eff(f"{node}/pcie_in/{c}") for c in cards}
        disks = sorted({(node if cl in ("file", "lssd") else cl.split(":")[1]) for _, _, sl, _, _ in meta for cl in sl})
        disk_cap = {d: NVME_GBPS * eff(f"{d}/nvme_rd/{self.ssd_disk.get(d)}") for d in disks}
        cap_slow = sum(disk_cap.values()) or NVME_GBPS
        GB = 1e9
        groups, gidx = {}, []
        for ch, (loc, srcs, slow, has_ld, has_lh) in zip(chunks, meta):
            key = (ch["card"], srcs, has_ld, has_lh, bool(slow))
            groups[key] = groups.get(key, 0.0) + ch["n"] / GB
            gidx.append(key)
        B = {c: 0.0 for c in cards}
        for k, g in groups.items():
            B[k[0]] += g
        info = {"mode": mode, "use_matrix": use_matrix, "live": live,
                "cap_port": {p: round(v, 1) for p, v in cap_port.items()},
                "cap_card": {c: round(v, 1) for c, v in cap_card.items()}, "cap_slow": round(cap_slow, 1)}
        if relay:
            info["relay"] = {str(p): r for p, r in relay.items()}
        e_r = {p: (self.direct_cap[node][p][r] if use_matrix else PORT_GBPS) for p, r in relay.items()}
        cap_rin = {r: CARD_GBPS * eff(f"{node}/pcie_in/{r}") for r in relay.values()}
        busy_tx = {s: {p: round(v, 2) for p, v in d.items() if v < 0.9} for s, d in tx.items()}
        if any(busy_tx.values()):
            info["src_tx_share"] = {s: d for s, d in busy_tx.items() if d}
        quota = {k: {} for k in groups}                 # group -> {("net", s, p) | "local" | "slow": GB}
        pred, use = None, collections.Counter()
        if mode == "lp":
            import numpy as np
            from scipy.optimize import linprog
            P, C, G = list(ports), list(cards), list(groups)
            var = {}
            for k in G:
                c, srcs, has_ld, has_lh, has_slow = k
                for s in srcs:
                    for p in P:
                        var[(k, s, p)] = len(var)
                        if p in relay and relay[p] != c:
                            var[(k, s, p, "R")] = len(var)
                if has_ld:
                    var[(k, "local")] = len(var)
                if has_lh:
                    var[(k, "lhbm")] = len(var)
                if has_slow:
                    var[(k, "slow")] = len(var)
            iT = len(var)
            nv = iT + 1
            Aub, bub, Aeq, beq = [], [], [], []
            row = lambda: np.zeros(nv)                                      # noqa: E731
            cost = row()
            cost[iT] = 1.0
            card_rows = {c: row() for c in C}
            port_rows = {p: row() for p in P}
            tx_rows = {(s, p): row() for s in all_src for p in P}
            dma, slow_row = row(), row()
            rin_rows = {r: row() for r in set(relay.values())}           # relay card's PCIe ingress
            xout_rows = {r: row() for r in set(relay.values())}          # relay card -> xGMI
            xin_rows = {c: row() for c in C}                             # xGMI fan-in of the target
            for k in G:
                c = k[0]
                r = row()
                for key, i in var.items():
                    if key[0] != k:
                        continue
                    r[i] = 1
                    if len(key) == 4:                                       # borrowed ingress
                        _, s, p, _ = key
                        rc = relay[p]
                        port_rows[p][i] = 1.0 / e_r[p]
                        tx_rows[(s, p)][i] = 1.0 / PORT_GBPS
                        rin_rows[rc][i] = 1
                        xout_rows[rc][i] = 1
                        xin_rows[c][i] = 1
                        cost[i] = 3e-6 / e_r[p]                             # direct first at equal T
                    elif key[1] == "lhbm":                                  # parked HBM, over xGMI
                        xin_rows[c][i] = 1
                        cost[i] = 1e-7
                    elif key[1] == "local":
                        card_rows[c][i] = 1
                        dma[i] = 1
                    elif key[1] == "slow":
                        card_rows[c][i] = 1
                        dma[i] = 1
                        slow_row[i] = 1
                        cost[i] = 1e-4                                      # same T: prefer the fast copy
                    else:
                        _, s, p = key
                        card_rows[c][i] = 1
                        port_rows[p][i] = 1.0 / e[p][c]     # the port is one pipe: 1/e seconds per GB
                        tx_rows[(s, p)][i] = 1.0 / PORT_GBPS
                        cost[i] = 1e-6 / e[p][c]                            # ... and the fast pairs
                Aeq.append(r)
                beq.append(groups[k])                                       # every byte of the group
            for c in C:
                card_rows[c][iT] = -cap_card[c]
                Aub.append(card_rows[c]); bub.append(0)                     # card ingress
            for p in P:
                port_rows[p][iT] = -share[p]
                Aub.append(port_rows[p]); bub.append(0)
            for (s, p), r in tx_rows.items():
                if tx[s][p] < 0.999:                                        # a source's sending side
                    r[iT] = -tx[s][p]
                    Aub.append(r); bub.append(0)
            dma[iT] = -HOST_DMA_GBPS
            Aub.append(dma); bub.append(0)                                  # host DMA budget
            for rc, rr_ in rin_rows.items():
                rr_[iT] = -cap_rin[rc]
                Aub.append(rr_); bub.append(0)
                xout_rows[rc][iT] = -XGMI_PAIR_GBPS
                Aub.append(xout_rows[rc]); bub.append(0)
            for c, rr_ in xin_rows.items():
                if rr_[:iT].any():
                    rr_[iT] = -XGMI_IN_GBPS
                    Aub.append(rr_); bub.append(0)
            slow_row[iT] = -cap_slow
            Aub.append(slow_row); bub.append(0)                             # disks
            sol = linprog(cost, A_ub=np.array(Aub), b_ub=bub, A_eq=np.array(Aeq) if Aeq else None,
                          b_eq=beq if Aeq else None, bounds=(0, None), method="highs")
            if sol.success:
                pred = float(sol.x[iT])
                flow = {c: collections.Counter() for c in C}
                for key, i in var.items():
                    v = float(sol.x[i])
                    if v <= 1e-6:
                        continue
                    k = key[0]
                    if len(key) == 4:
                        _, s, p, _ = key
                        quota[k][("relay", s, p)] = v
                        flow[k[0]][f"relay:{p}"] += v
                        use[f"{node}/ib_rx/shca_{p}"] += v
                        if s != "*":
                            use[f"{s}/ib_tx/shca_{p}"] += v
                    elif key[1] in ("local", "slow", "lhbm"):
                        quota[k][key[1]] = v
                        flow[k[0]][key[1]] += v
                    else:
                        _, s, p = key
                        quota[k][("net", s, p)] = v
                        flow[k[0]][f"net:{p}"] += v
                        use[f"{node}/ib_rx/shca_{p}"] += v
                        if s != "*":
                            use[f"{s}/ib_tx/shca_{p}"] += v
                info["flow_GB"] = {f"c{c}": {k: round(v, 1) for k, v in q.items() if v > 0.05} for c, q in flow.items()}
                if live and len(all_src) > 1:
                    by_src = collections.Counter()
                    for k, q in quota.items():
                        for key, v in q.items():
                            if isinstance(key, tuple):
                                by_src[key[1]] += v
                    info["src_GB"] = {s: round(v, 1) for s, v in by_src.items()}
            else:
                mode = "rr"
                info["lp_failed"] = sol.message
        rr = 0
        by_group = collections.defaultdict(list)
        for i, k in enumerate(gidx):
            by_group[k].append(i)
        for k, idxs in by_group.items():
            q = quota[k]
            idxs.sort(key=lambda i: chunks[i]["off"])
            for i in idxs:
                loc, srcs, slow, has_ld, has_lh = meta[i]
                g = chunks[i]["n"] / GB
                chunks[i].pop("src", None)
                if mode != "lp":
                    if loc:
                        cls = loc
                    elif srcs:
                        cls = f"net:{ports[rr % len(ports)]}"; rr += 1
                    else:
                        cls = slow[rr % len(slow)]; rr += 1
                else:
                    fast = [(v, key) for key, v in q.items() if key != "slow"]
                    best = max(fast, key=lambda x: x[0], default=(0.0, None))
                    if best[1] is None or (best[0] < g * 0.5 and slow and q.get("slow", 0.0) >= g * 0.5):
                        key = "slow" if slow else best[1]
                    else:
                        key = best[1]
                    if key is None:                      # only fast copies, nothing planned (rounding)
                        key = ("lhbm" if has_lh else "local") if loc else ("net", srcs[0], ports[rr % len(ports)])
                        rr += 1
                    q[key] = q.get(key, 0.0) - g
                    if key == "slow":
                        cls = slow[rr % len(slow)]; rr += 1
                    elif key == "local":
                        cls = "ldram" if has_ld else loc
                    elif key == "lhbm":
                        cls = "lhbm"
                    else:
                        cls = f"{'relay' if key[0] == 'relay' else 'net'}:{key[2]}"
                        if key[1] != "*":
                            chunks[i]["src"] = key[1]
                chunks[i]["plan_cls"] = cls
        if pred is None:                                 # bottleneck estimate of the rr assignment
            load = collections.Counter()
            for ch in chunks:
                cls = ch["plan_cls"]
                load[cls if cls.startswith("net:") else ("local" if cls in LOCK else "slow")] += ch["n"] / GB
            pred = max([load[f"net:{p}"] / (cap_port[p] * 0.5) for p in ports] +
                       [(load["local"] + load["slow"]) / HOST_DMA_GBPS, load["slow"] / cap_slow] +
                       [B[c] / cap_card[c] for c in cards])
            for p in ports:
                use[f"{node}/ib_rx/shca_{p}"] += load[f"net:{p}"]
        slow_gb = collections.Counter()
        for ch in chunks:
            cls = ch["plan_cls"]
            if not cls.startswith(("net:", "relay:")) and cls not in LOCK:
                slow_gb[node if cls in ("file", "lssd") else cls.split(":")[1]] += ch["n"] / GB
        for d, g in slow_gb.items():
            use[f"{d}/nvme_rd/{self.ssd_disk.get(d)}"] += g
        T = max(pred, 1e-3)
        info["use"] = {rid: round(g / T, 2) for rid, g in use.items() if g / T > 0.05}
        info["plan_ms"] = round((time.time() - t0) * 1e3, 2)
        return pred, info

    def _plan_restore(self, name, node, cards, allow=("dram", "ssd", "hbm", "file"), strategy="lp",
                      live=True, job=None, single_source=None, avoid_shared_root=True, direct=True,
                      use_arena=True, relay=None):
        """Everything of a restore up to the RPC: sources, layout, flow plan, ledger entry.
        relay: None = self.relay_policy, or "off" | "idle" | "loose" (see relay_pick)."""
        model = self.models[name]
        t0 = time.time()
        edge, srcpref = None, None
        job = job or f"{name}-{int(time.time() * 1000)}-{node[-2:]}"
        base, used = self.layout(model, cards)
        if use_arena:
            self.make_room(node, {c: n for c, n in used.items() if n})
        demands, replicas, chunks = [], {}, []
        with self.lock:
            snap = self.snapshot() if live else Snapshot(self.topology, {}, {}, -1, time.time())
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
            if live:
                snap = self.snapshot()
            relay_map = self.relay_pick(node, list(cards), relay, snap if live else None) \
                if strategy in ("lp", "lp_blind") else {}
            predicted, plan_info = self.flow_plan(chunks, node, list(cards), ports, snap,
                                                  mode="lp" if strategy in ("lp", "lp_blind") else "rr",
                                                  use_matrix=strategy == "lp", live=live, job=job,
                                                  relay=relay_map)
            used_relay = {p: r for p, r in relay_map.items()
                          if any(ch.get("plan_cls") == f"relay:{p}" for ch in chunks)}
            if live:
                R = self.topology.resources
                srcs = {cd["node"] for ch in chunks for cd in ch["cands"] if cd["k"] in ("dram", "rhbm")}
                with self.lock:
                    srcpref = {str(p): {s: round(self._share(snap, rid, job), 2)
                                        for s in srcs for rid in [f"{s}/ib_tx/shca_{p}"] if rid in R}
                               for p in ports}
                    self.ledger[job] = {"use": plan_info.get("use", {}), "end": None}
                    self._own_refresh()
            prior = {f"net:{p}": plan_info["cap_port"][p] for p in ports}
            prior.update({f"relay:{p}": plan_info["cap_port"][p] for p in used_relay})
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
            used_relay = {}
        with self.lock:
            self.inflight_cards[job] = (node, set(int(c) for c in cards) | set(used_relay.values()))
        for ch in chunks:
            for cd in ch["cands"]:
                k = cd["k"]
                cls = f"dram:{cd['node']}" if k in ("dram", "rhbm") else (f"rstage:{cd['node']}" if k == "rstage" else k)
                prior.setdefault(cls, PRIOR[k])
        return {"name": name, "node": node, "cards": list(cards), "used": used, "chunks": chunks, "ports": ports,
                "prior": prior, "edge": edge, "srcpref": srcpref, "predicted": predicted, "plan_info": plan_info,
                "job": job, "t0": t0, "plan_s": time.time() - t0, "strategy": strategy, "live": live,
                "direct": direct, "use_arena": use_arena, "relay": used_relay}

    def _exec_restore(self, ctx, steal=True, verify=False, release=True, learn=False):
        name, node, job, cards = ctx["name"], ctx["node"], ctx["job"], ctx["cards"]
        model, t0 = self.models[name], ctx["t0"]
        self.active[job] = node
        try:
            r = Client(self.nodes[node]).call("restore", job=job, cards={str(c): n for c, n in ctx["used"].items() if n},
                                              chunks=ctx["chunks"], steal=steal, prior=ctx["prior"], ports=ctx["ports"],
                                              direct=ctx["direct"], edge=ctx["edge"], use_arena=ctx["use_arena"],
                                              srcpref=ctx["srcpref"],
                                              **({"relay": {str(p): r for p, r in ctx["relay"].items()}}
                                                 if ctx.get("relay") else {}))
        finally:
            self.active.pop(job, None)
            with self.lock:
                self.inflight_cards.pop(job, None)
            self._ledger_end(job)
        total = time.time() - t0
        for k, (gbps, nb) in (r.get("pair_GBps") or {}).items():          # learn the pair capacities
            if nb >= 2 * GiB and learn:
                pp, cc = (int(x) for x in k.split(":"))
                old = self.direct_cap[node][pp][cc]
                self.direct_cap[node][pp][cc] = min(PORT_GBPS + 2, 0.5 * old + 0.5 * max(gbps, 3.0))
        out = {"model": name, "node": node, "cards": list(cards), "GiB": round(model.nbytes / GiB, 2),
               "total_s": round(total, 4), "plan_s": round(ctx["plan_s"], 4), "exec_s": r["total_s"],
               "alloc_s": r["alloc_s"], "GBps": round(model.nbytes / total / 1e9, 2),
               "predicted_s": round(ctx["predicted"], 4), "strategy": ctx["strategy"], "steal": steal,
               "live": ctx["live"], "direct": ctx["direct"], "plan": ctx["plan_info"],
               "failed_fetches": r["failed_fetches"], "fail_why": r.get("fail_why"), "paths": r["paths"],
               "rates": r["rates_GBps"], "prof": r["prof"], "ports": ctx["ports"],
               "pair_GBps": {k: v[0] for k, v in (r.get("pair_GBps") or {}).items()},
               "src_GBps": r.get("src_GBps"), "job": job, "relay": r.get("relay") or {}}
        if verify:
            out["verify"] = self.rpc[node].call("job_verify", job=job)
        if release:
            self.rpc[node].call("job_release", job=job)
        model.last_used = time.time()
        self.log(event="restore", **out)
        return out

    def restore(self, name, node, cards, allow=("dram", "ssd", "hbm", "file"), strategy="lp",
                steal=True, live=True, job=None, verify=False, release=True, single_source=None,
                avoid_shared_root=True, direct=True, learn=False, use_arena=True, relay=None):
        """Bring the model's weights into HBM on `node`'s `cards`. Returns timings and path stats.

        live=True: plan on the live residuals of both ends of every link, minus what our other
        running restores reserved, and tell the executor which source each port should prefer.
        live=False: plan on nominal capacities with none of that (what a static system assumes).
        single_source: restrict remote candidates to one node (D2D-fork style baselines)."""
        with self.plan_lock:                             # one plan at a time against the ledger
            ctx = self._plan_restore(name, node, cards, allow, strategy, live, job, single_source,
                                     avoid_shared_root, direct, use_arena, relay)
        return self._exec_restore(ctx, steal, verify, release, learn)

    def restore_wave(self, items, **common):
        """Several restores issued together: [(name, node, cards[, kw])]. They are planned one
        after another (largest first), each against the ports, sources and disks the earlier
        ones reserved, and then run in parallel. Returns results in the order given; a failed
        restore is returned as its exception."""
        plan_kw = ("allow", "strategy", "live", "job", "single_source", "avoid_shared_root", "direct", "use_arena",
                   "relay")
        todo = []
        for k, it in enumerate(items):
            kw = {**common, **(it[3] if len(it) > 3 else {})}
            todo.append((k, it[0], it[1], it[2], kw))
        ctxs, res = {}, [None] * len(todo)
        with self.plan_lock:
            for k, name, node, cards, kw in sorted(todo, key=lambda x: -self.models[x[1]].nbytes):
                try:
                    ctxs[k] = self._plan_restore(name, node, cards, **{a: kw[a] for a in plan_kw if a in kw})
                except Exception as e:                  # noqa: BLE001
                    res[k] = e

        def go(k, kw):
            try:
                res[k] = self._exec_restore(ctxs[k], **{a: kw[a] for a in ("steal", "verify", "release", "learn") if a in kw})
            except Exception as e:                      # noqa: BLE001
                res[k] = e
        th = [threading.Thread(target=go, args=(k, kw)) for k, _, _, _, kw in todo if k in ctxs]
        [x.start() for x in th]
        [x.join() for x in th]
        return res

    # ---- react: give space back when a tenant needs it -------------------------------------
    def pressure(self):
        """{vertex: bytes we must give back}. A vertex is squeezed when what is left for
        everybody (free + the reclaimable share) has fallen under the headroom the policy
        promises other tenants -- and we still hold bytes there. For HBM this covers the cards
        we park on (hbm_park): a tenant that fills the card beside our arena gets the card."""
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
                if used is None or vid not in V:
                    continue
                parking = int(g) in self.hbm_park.get(node, ())
                if self.held[vid] <= 0 and not parking:
                    continue
                deficit = p.hbm_headroom - (V[vid].capacity_bytes - used)
                if deficit > 0:
                    out[vid] = int(deficit)
        return out

    def _victims(self, vid, need, slabs=False, below=None, skip_models=()):
        """Chunk ids to drop from `vid` to free `need` bytes, least valuable first: chunks that
        keep another fast replica (DRAM elsewhere, parked HBM, a published instance) go before
        any chunk that would lose its only fast copy, and among those the coldest model's go
        first -- a hot model never loses its only fast copy while a colder one still has fast
        copies on the vertex. slabs=True (DRAM under tenant pressure): whole slabs, because host
        memory only returns to the OS slab by slab; a slab is worth its most valuable chunk.
        below: only chunks strictly colder than this heat (displacement). Call with self.lock."""
        items = []
        for cid, reps in self.rep.items():
            loc = reps.get(vid)
            if loc is None or (isinstance(loc, dict) and loc.get("job")):
                continue
            if skip_models and self.owners[cid] & set(skip_models):
                continue
            sole = not self._fast(cid, but=vid)
            heat = self._cheat(cid)
            if below is not None and sole and heat[0] >= below:
                continue
            if self.evict_order == "age":                   # replica state ignored, oldest model first
                key = (0, max(self.models[o].last_used for o in self.owners[cid]), 0.0)
            else:
                key = (1, heat[0], heat[1]) if sole else (0, 0.0, 0.0)
            items.append((key, cid, loc.get("slab") if isinstance(loc, dict) else None, sole))
        ids, lost, freed = [], collections.Counter(), 0
        if slabs:
            groups = collections.defaultdict(list)
            sole_of = {cid: so for _, cid, _, so in items}
            for key, cid, slab, _ in items:
                groups[slab if slab is not None else cid].append((key, cid))
            order = sorted(groups.values(), key=lambda g: (max(k for k, _ in g), -len(g)))
            for g in order:
                if freed >= need:
                    break
                for key, cid in g:
                    ids.append(cid)
                    if sole_of[cid]:
                        lost[max(self.owners[cid], key=lambda o: self._heat(self.models[o]))] += 1
                freed += max(self.slab_bytes, sum(self.size[cid] for _, cid in g))
        else:
            for key, cid, _, sole in sorted(items, key=lambda x: x[0]):
                if freed >= need:
                    break
                ids.append(cid)
                freed += self.size[cid]
                if sole:
                    lost[max(self.owners[cid], key=lambda o: self._heat(self.models[o]))] += 1
        return ids, dict(lost), freed

    def react(self, clients=None, slack=1 * GiB):
        t0 = time.time()
        cool = getattr(self, "_cool", {})
        squeezed = {v: d for v, d in self.pressure().items() if t0 - cool.get(v, 0) > 0.5}
        if not squeezed:
            return None
        per, lost, cards = {}, collections.Counter(), []
        with self.lock:
            for vid, deficit in squeezed.items():
                node, kind, idx = self._vparts(vid)
                if kind == "hbm":
                    # The arena is one allocation: the tenant gains nothing until all of it goes.
                    self.hbm_park[node].discard(int(idx))
                    ids, ls, _ = self._victims(vid, 1 << 62)
                    cards.append((node, int(idx)))
                else:
                    ids, ls, _ = self._victims(vid, deficit + slack, slabs=True)
                for m, k in ls.items():
                    lost[m] += k
                per[vid] = ids
        out = self._drop(per, clients)
        arena = {}
        for node, card in cards:                         # hand the card over if nothing of ours is left
            try:
                (clients[node] if clients else self._cl(node)).call("arena", card=card, gib=0)
                arena[f"{node}/hbm/{card}"] = "released"
                self.hbm_yielded[(node, card)] = time.time()   # rebalance() takes it back when free
            except Exception as e:                      # noqa: BLE001 -- a restored model lives there
                arena[f"{node}/hbm/{card}"] = f"kept: {e}"[:80]
        for v in squeezed:
            cool[v] = time.time()
        self._cool = cool
        ev = {"event": "evict", "t": t0, "react_s": round(time.time() - t0, 4),
              "tiers": sorted({self._vparts(v)[1] for v in squeezed}),
              "squeezed_GiB": {v: round(d / GiB, 2) for v, d in squeezed.items()},
              "freed_GiB": {v: round(o["freed"] / GiB, 2) for v, o in out.items()},
              "lost_fast_copy": dict(lost), "arena": arena,
              "detail": {v: {k: o[k] for k in ("s", "revoke_s", "release_s", "slabs")} for v, o in out.items()}}
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
                self.rep[cid][f"{node}/hbm/{l['card']}"] = {"addr": l["addr"], "rkeys": l["rkeys"], "job": job,
                                                            "card": l["card"], "off": l.get("off")}
        return len(loc)

    def unpublish(self, node, job):
        with self.lock:
            for cid, reps in self.rep.items():
                for v in [v for v, l in reps.items() if isinstance(l, dict) and l.get("job") == job]:
                    del reps[v]

    def trim(self, need_bytes, tier="dram"):
        """Free `need_bytes` of `tier` by dropping redundant replicas: a chunk that has two or
        more fast replicas (DRAM, parked HBM, or a published instance in HBM) loses one in
        `tier`. Coverage of every model is untouched; only redundancy is traded for room. This
        is what makes the HBM + DRAM pool exclusive, lazily: the DRAM copy of weights that are
        live on a card is kept until somebody else's chunks need the room."""
        per, freed = collections.defaultdict(list), 0
        with self.lock:
            for cid, reps in self.rep.items():
                if freed >= need_bytes:
                    break
                fast = [v for v in reps if self._vparts(v)[1] in ("dram", "hbm")]
                vs = [v for v in fast if self._vparts(v)[1] == tier
                      and not (isinstance(reps[v], dict) and reps[v].get("job"))]
                if len(fast) >= 2 and vs:
                    v = max(vs, key=lambda v: self.held[v])       # thin out the fullest vertex
                    per[v].append(cid)
                    freed += self.size[cid]
        self._drop(per)
        return freed

    def displace(self, need_bytes, below, skip_models=(), tier="dram"):
        """Free `need_bytes` of `tier` by dropping the only fast copies of models colder than
        heat `below` (coldest first). Their chunks fall back to NVMe; rebalance() brings them
        back when there is room. Returns (bytes freed, {model: chunks})."""
        per, lost, freed = collections.defaultdict(list), collections.Counter(), 0
        with self.lock:
            cand = []
            for cid, reps in self.rep.items():
                if self.owners[cid] & set(skip_models):
                    continue
                heat = self._cheat(cid)
                if heat[0] >= below:
                    continue
                for v, l in reps.items():
                    if self._vparts(v)[1] == tier and not (isinstance(l, dict) and l.get("job")):
                        cand.append((heat, cid, v))
                        break
            for heat, cid, v in sorted(cand):
                if freed >= need_bytes:
                    break
                per[v].append(cid)
                freed += self.size[cid]
                lost[max(self.owners[cid], key=lambda o: self._heat(self.models[o]))] += 1
        self._drop(per)
        return freed, dict(lost)

    def _store_fast(self, model, chunks, tiers=("dram", "hbm"), budget=None, from_job=None, displace=True,
                    pin_nodes=None, prefer=None, local_frac=None):
        """Give `chunks` a fast replica wherever there is room; make room when there is none:
        first redundancy (trim), then colder models' copies (displace). Returns stats."""
        name = model.name
        out = {"stored": 0, "trimmed": 0, "displaced": 0, "displaced_models": {}, "errors": []}

        def missing():
            with self.lock:
                return [c for c in chunks if not self._fast(c.id)]

        def put(cs):
            r = self.park(name, copies=1, tiers=tiers, budget=budget, from_job=from_job, chunks=cs,
                          pin_nodes=pin_nodes, prefer=prefer, local_frac=local_frac, quiet=True)
            out["stored"] += int(r["GiB"] * GiB)
            out["errors"] += r["errors"]
        todo = missing()
        if todo:
            put(todo)
            todo = missing()
        if todo and not out["errors"]:
            need = sum(c.nbytes for c in todo)
            out["trimmed"] = self.trim(need)
            if out["trimmed"]:
                put(todo)
                todo = missing()
        if todo and displace and not out["errors"]:
            need = sum(c.nbytes for c in todo)
            with self.lock:
                below = self._heat(model)
            out["displaced"], out["displaced_models"] = self.displace(need, below, skip_models=(name,))
            if out["displaced"]:
                put(todo)
                todo = missing()
        out["left"] = sum(c.nbytes for c in todo)
        return out

    def retire(self, name, node, job, cards, budget=None, tiers=("dram", "hbm"), demote=True, displace=True,
               pin_nodes=None, release=True):
        """The model leaves `cards` on `node` (job = its restore). Instead of dropping the
        instance, every chunk whose only fast replica it was flows out of HBM: by D2H into this
        node's DRAM, by RDMA READ of the instance's device memory into other nodes' DRAM or
        idle HBM -- whatever fast room the pool has, making room by giving up redundancy and
        then colder models' copies. What does not fit keeps only its NVMe / file copy.
        Then readers of the instance are revoked and its memory released (release=False keeps
        the job, e.g. when the caller hands the buffers on)."""
        model, t0 = self.models[name], time.time()
        try:
            loc = Client(self.nodes[node]).call("job_locators", job=job)
        except Exception:                               # noqa: BLE001
            loc = {}
        self.unpublish(node, job)
        where = self.where(model, cards)
        with self.lock:
            uniq = list({c.id: c for c in model.chunks}.values())
            need = [c for c in uniq if c.id in where and not self._fast(c.id)]
        nbytes = sum(c.nbytes for c in need)
        st = {"stored": 0, "left": nbytes, "trimmed": 0, "displaced": 0, "displaced_models": {}, "errors": []}
        if demote and need:
            st = self._store_fast(model, need, tiers, budget, from_job=(node, job, where, loc), displace=displace,
                                  pin_nodes=pin_nodes)
        t_dem = time.time() - t0
        if release:
            # Nobody may still be reading device memory that is about to be reused.
            ranges = collections.defaultdict(lambda: [1 << 62, 0])
            for l in loc.values():
                r = ranges[l["card"]]
                r[0], r[1] = min(r[0], l["addr"]), max(r[1], l["addr"] + l.get("n", 0) + 1)
            for j, dst in list(self.active.items()):
                try:
                    if dst == node:
                        Client(self.nodes[dst]).call("revoke", node=node, tiers=["hbm"])
                    else:
                        Client(self.nodes[dst]).call("revoke", node=node, tiers=["hbm"], ranges=list(ranges.values()))
                except Exception:                       # noqa: BLE001
                    pass
            self._cl(node).call("job_release", job=job)
        out = {"model": name, "node": node, "need_GiB": round(nbytes / GiB, 2),
               "demoted_GiB": round((nbytes - st["left"]) / GiB, 2), "left_slow_GiB": round(st["left"] / GiB, 2),
               "trimmed_GiB": round(st["trimmed"] / GiB, 2), "displaced_GiB": round(st["displaced"] / GiB, 2),
               "displaced_models": st["displaced_models"], "errors": st["errors"][:3],
               "demote_s": round(t_dem, 4), "s": round(time.time() - t0, 4)}
        self._event("demote", **out)
        return out

    def rebalance(self, order=None, budget=None, tiers=("dram", "hbm"), max_bytes=64 * GiB, displace=True,
                  pin_nodes=None, homes=None):
        """Background repair, hottest model first (order overrides): chunks without a fast
        replica get one from the best source that exists (a published instance, an NVMe copy on
        any disk, the durable file), with the same room-making rules as retire. Also takes back
        HBM cards a tenant made us give up, once they are free again. At most `max_bytes` per
        call, so a foreground restore never waits long behind it.
        homes: {model: node or nodes} -> rate-proportional placement (local_share) for it."""
        t0, done, per_model = time.time(), 0, {}
        back = []
        for (node, card), t in list(self.hbm_yielded.items()):
            fast, V = self.fast.get(node), self.topology.vertices
            used = None if not fast else fast["hbm_used"].get(str(card), fast["hbm_used"].get(card))
            gib = self.arena_default_gib
            if used is None or t0 - t < 2.0:
                continue
            try:
                have = str(card) in (self._cl(node).call("stat").get("arena") or {})
                if not have and gib and \
                        V[f"{node}/hbm/{card}"].capacity_bytes - used >= gib * GiB + 2 * self.policy.hbm_headroom:
                    self._cl(node).call("arena", card=card, gib=gib)
                    have = True
                if have:                                 # ours again (re-created here or by the driver)
                    del self.hbm_yielded[(node, card)]
                    self.hbm_park[node].add(card)
                    back.append(f"{node}/hbm/{card}")
            except Exception:                           # noqa: BLE001
                pass
        with self.lock:
            names = list(order) if order else sorted(self.models, key=lambda n: -self._heat(self.models[n]))
        for name in names:
            if done >= max_bytes:
                break
            model = self.models[name]
            with self.lock:
                need, acc = [], 0
                for c in {c.id: c for c in model.chunks}.values():
                    if not self._fast(c.id):
                        need.append(c)
                        acc += c.nbytes
                        if done + acc >= max_bytes:
                            break
            if not need:
                continue
            home = (homes or {}).get(name)
            st = self._store_fast(model, need, tiers, budget, displace=displace, pin_nodes=pin_nodes,
                                  prefer=home, local_frac=self.local_share() if home else None)
            got = sum(c.nbytes for c in need) - st["left"]
            done += got
            if got or st["errors"]:
                per_model[name] = {"GiB": round(got / GiB, 2), "left_GiB": round(st["left"] / GiB, 2),
                                   "trimmed_GiB": round(st["trimmed"] / GiB, 2),
                                   "displaced_GiB": round(st["displaced"] / GiB, 2), "errors": st["errors"][:2]}
        out = {"repaired_GiB": round(done / GiB, 2), "s": round(time.time() - t0, 3), "models": per_model,
               "hbm_back": back}
        if done or back or any(m.get("errors") for m in per_model.values()):
            self._event("repair", **out)
        return out

    def drop_model(self, name, tiers=("dram",), nodes=None):
        """Drop the model's replicas in `tiers` (on `nodes`); chunks other models share stay."""
        model = self.models[name]
        per = collections.defaultdict(list)
        with self.lock:
            for c in model.chunks:
                for v, l in list(self.rep[c.id].items()):
                    if isinstance(l, dict) and l.get("job"):
                        continue                         # a serving instance is released, not dropped
                    node, kind, _ = self._vparts(v)
                    if kind in tiers and (nodes is None or node in nodes) and c.id not in per[v]:
                        per[v].append(c.id)
        return self._drop(per)
