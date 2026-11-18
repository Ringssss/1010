"""Bursts on any medium at any time, trace-driven requests, three keep-alive systems, one executor.

Catalog, homes, card groups and the request sequence are those of maas2_run.py (853 GiB of real
weights; model sequence of clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv). What
changes is the environment and the accounting:

  * Other tenants do not come in phases. A seeded schedule -- a pure function of (seed, number of
    requests), written to <out>/burst_schedule.json and identical for the three systems -- starts
    and ends DRAM / HBM / NVMe read / NVMe write / RDMA / PCIe bursts at request indices, several
    at a time; about a third of them fire 0.2 s into a wave of restores, so they hit restores in
    flight. The first --calm requests see none.
  * Every system first serves one pass over every (model, home) pair, untimed, so the baselines
    are measured in steady state and not from an empty cache.
  * A model stays resident in HBM on its cards until those cards are needed (LRU), for all three
    systems: that is SGLang Weight Cache's IPC keep-alive, Janus's active state and our arena
    attach. A request for a resident model is a hit and is logged with the calibrated IPC attach
    cost (--ipc-hit-ms-per-card; 1.9 ms median / 6.2 ms first per card buffer in
    pool_r01/burst_r01/ipc_attach.log), flagged calibrated, never measured per request.
  * The time spent making room on the cards (demotion, D2H back-fill) is recorded per request as
    evict_s next to the restore latency; total_s is their sum.

Systems (mechanism comparisons inside the FluidPool executor, not the original systems):
  fluidpool  one pool over the four nodes: DRAM of every node + idle HBM (node27/node29 cards 4-7)
             + NVMe copies striped over the pool disks; restores are planned as a wave on the live
             graph; a model leaving its cards is demoted, not dropped; reacts chunk by chunk.
  janus      Janus-style warm / D2D core (lib_cite/Janus, commit ec1beca): every node pre-warms
             whole models homed there into its pinned memory; wake-up tries D2D from ONE peer that
             holds an active copy, then H2D from the local copy, then the checkpoint. Topology-
             aware static plan by default (--janus-plan). OUR ADAPTATIONS, absent upstream: the
             per-node byte budget with whole-model LRU eviction, and yielding pinned memory to a
             tenant (react_whole) -- upstream has neither and would not yield.
  sglang     SGLang Weight-Cache style: nothing is kept but the HBM-resident model (no DRAM, no
             SSD, no peer, never published). A miss is a cold load from the checkpoint; when its
             cards are taken (another model or a tenant) the whole model is gone.

--dry replaces the pool, the node daemons and the tenants by a small in-file model so that the
control flow, the schedule alignment and summarize_maas3.py can be exercised on a CPU.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
import os
import random
import threading
import time

GiB = 1 << 30
NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
SERVE = {"node27": [0, 1, 2, 3], "node29": [0, 1, 2, 3], "node28": list(range(8)), "node30": list(range(8))}
IDLE = {"node27": [4, 5, 6, 7], "node29": [4, 5, 6, 7]}        # cards nobody serves on: HBM to park in
CARDS = {"9g-8b": 1, "qwen36-35b": 2, "qwen3-0.6b": 1, "clover-35b": 2, "glm53-n28": 8, "glm53-n30": 8}
DURABLE = {"9g-8b": "node28", "qwen36-35b": "node28", "qwen3-0.6b": "node28", "clover-35b": "node30",
           "glm53-n28": "node28", "glm53-n30": "node30"}
HOMES = {"9g-8b": ["node27", "node29", "node28", "node30"],
         "qwen36-35b": ["node27", "node29", "node27", "node29", "node28", "node30"],
         "qwen3-0.6b": ["node27", "node29"],
         "clover-35b": ["node29", "node27", "node30"]}
GLM = [("glm53-n28", "node28"), ("glm53-n30", "node30")]
RANK = ["9g-8b", "qwen36-35b", "qwen3-0.6b", "clover-35b", "glm53", "qwen36-35b", "clover-35b", "glm53", "9g-8b"]
HEAT_ORDER = ["9g-8b", "qwen36-35b", "qwen3-0.6b", "clover-35b", "glm53-n28", "glm53-n30"]   # hottest first
SIZE_GIB = {"9g-8b": 15.25, "qwen36-35b": 66.97, "qwen3-0.6b": 1.4, "clover-35b": 64.56,
            "glm53-n28": 359.65, "glm53-n30": 344.09}          # weight bytes of the manifests (dry mode)
TRACE = "/home/zhujianian/clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv"
TENANT = "/home/zhujianian/zhujianian/fluidpool_trace/pod_memory_util_anon.csv"
KIND_W = collections.OrderedDict([("dram", .25), ("ib", .20), ("ssd_rd", .15), ("ssd_wr", .10),
                                  ("hbm_idle", .10), ("hbm_serve", .10), ("h2d", .10)])
NUMA_GIB = 64                                                   # one NUMA node of these machines


# Stress catalog: byte-distinct variants (make_variants.py) of the 35B and 8B checkpoints, standing in
# for full fine-tunes -- no chunk is shared with the base model, so content addressing gains nothing.
# (name, base, durable node, homes, size GiB); ranks 10-16 of the trace's model popularity.
VARIANTS = [
    ("qwen36-35b-v1", "qwen36-35b", "node28", ["node27", "node29", "node28"], 66.97),
    ("9g-8b-v1", "9g-8b", "node28", ["node27", "node28", "node29"], 15.25),
    ("clover-35b-v1", "clover-35b", "node30", ["node29", "node30"], 64.56),
    ("qwen36-35b-v2", "qwen36-35b", "node29", ["node29", "node27", "node30"], 66.97),
    ("9g-8b-v2", "9g-8b", "node29", ["node30", "node29", "node27"], 15.25),
    ("clover-35b-v2", "clover-35b", "node28", ["node27", "node28"], 64.56),   # node29's host cannot read node30's disk
    ("qwen36-35b-v3", "qwen36-35b", "node30", ["node27", "node30", "node29"], 66.97),
]


def apply_catalog(name):
    """Extend the module-level catalog in place (everything below reads these globals)."""
    if name == "base":
        return
    assert name == "big", name
    for v, base, durable, homes, gib in VARIANTS:
        CARDS[v] = CARDS[base]
        DURABLE[v] = durable
        HOMES[v] = list(homes)
        SIZE_GIB[v] = gib
        RANK.append(v)
    glm = [m for m in HEAT_ORDER if m.startswith("glm53")]
    del HEAT_ORDER[len(HEAT_ORDER) - len(glm):]
    HEAT_ORDER.extend([v for v, *_ in VARIANTS] + glm)   # GLM stays coldest: its deficit goes last


def home_pairs():
    """Every (model, node) a request can name."""
    out = [(m, n) for m in HOMES for n in dict.fromkeys(HOMES[m])]
    return out + GLM


def homed_on(node):
    return [m for m in HEAT_ORDER if (m, node) in home_pairs()]


# ---------------------------------------------------------------- requests
def requests():
    """[(label, [(model, node)])] in trace order (same mapping as maas2_run.py)."""
    rows = list(csv.DictReader(open(TRACE)))
    cnt = collections.Counter(r["checkpoint_model_version_id"] for r in rows)
    name = dict(zip([m for m, _ in cnt.most_common(len(RANK))], RANK))
    rows.sort(key=lambda r: r["gmt_create"])
    k, out = collections.Counter(), []
    for r in rows:
        m = name.get(r["checkpoint_model_version_id"])
        if m is None:
            continue
        if m == "glm53":
            out.append(("glm53", list(GLM)))
        else:
            node = HOMES[m][k[m] % len(HOMES[m])]
            k[m] += 1
            out.append((f"{m}@{node}", [(m, node)]))
    return out


def warmup_requests():
    """One pass over every (model, home) pair."""
    out = [(f"{m}@{n}", [(m, n)]) for m in HOMES for n in dict.fromkeys(HOMES[m])]
    return out + [("glm53", list(GLM))]


def tenant_series(points=512):
    """The most variable container of the tenant memory trace, normalised to [0, 1]."""
    by = collections.defaultdict(list)
    for r in csv.DictReader(open(TENANT)):
        by[r["container_ip"]].append((float(r["timestamp_anon"]), float(r["value"])))
    best, best_sd = None, -1
    for k, v in sorted(by.items()):
        if len(v) >= 500:
            xs = [x for _, x in v]
            mu = sum(xs) / len(xs)
            sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
            if sd > best_sd:
                best, best_sd = k, sd
    xs = [x for _, x in sorted(by[best])]
    lo, hi = min(xs), max(xs)
    return [round((xs[int(i * len(xs) / points)] - lo) / max(hi - lo, 1e-9), 4) for i in range(points)], best


# ---------------------------------------------------------------- burst schedule
def _poisson(rng, lam):
    lim, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= lim:
            return k
        k += 1


def make_schedule(seed, total, calm, load, amp, series, kinds, ssd_nodes, hbm_gib, dram_min):
    """Burst events as a pure function of the arguments. An event holds [start_req, end_req) in
    request indices of the measured part; durations are 2-8 requests (mean 5), arrivals are
    Poisson with rate load / 5 per request, so about `load` bursts are active at any time. Two
    events never overlap on the same resource (a node's DRAM, a card, a node's NVMe direction, a
    receiving HCA port), which keeps what the tenant daemon is asked for unambiguous."""
    rng = random.Random(seed)
    nodes = sorted(NODES)
    w = [(k, KIND_W[k]) for k in KIND_W if k in kinds]
    if not w or load <= 0:
        return []
    lam = load / 5.0
    events, active = [], []                              # active: (end_req, keys)

    def draw():
        kind = rng.choices([k for k, _ in w], weights=[x for _, x in w])[0]
        if kind == "dram":
            node = rng.choice(nodes)
            numas = sorted(rng.sample(range(8), rng.randint(3, 8)))
            level = series[rng.randrange(len(series))]
            params = {"numas": numas, "gib_each": max(dram_min, int(round(level * amp))), "level": level}
            keys = {("dram", node)}
        elif kind == "hbm_idle":
            node = rng.choice(sorted(IDLE))
            cards = sorted(rng.sample(IDLE[node], rng.randint(1, 2)))
            params, keys = {"cards": cards, "gib": hbm_gib}, {("card", node, c) for c in cards}
        elif kind == "hbm_serve":
            node = rng.choice(nodes)
            cards = sorted(rng.sample(SERVE[node], rng.randint(1, 2)))
            params = {"cards": cards, "gib": hbm_gib}
            keys = {("card", node, c) for c in cards} | {("hbm_serve", node)}
        elif kind in ("ssd_rd", "ssd_wr"):
            node = rng.choice(ssd_nodes)
            params, keys = {"streams": 4}, {(kind, node)}
        elif kind == "ib":
            node = rng.choice(nodes)
            src = rng.choice([n for n in nodes if n != node])
            ports = sorted(rng.sample(range(4), rng.randint(1, 2)))
            params, keys = {"src": src, "ports": ports}, {("ib", node, p) for p in ports}
        else:                                            # h2d
            node = rng.choice(nodes)
            card = rng.choice(SERVE[node])
            params, keys = {"card": card}, {("card", node, card)}
        dur, during = rng.randint(2, 8), rng.random() < 1 / 3
        # taking serving cards away is a manager decision made between restores, never mid-flight
        return kind, node, params, keys, dur, during and kind != "hbm_serve"

    for k in range(calm, total):
        active = [(e, ks) for e, ks in active if e > k]
        for _ in range(_poisson(rng, lam)):
            for _try in range(8):
                kind, node, params, keys, dur, during = draw()
                if not any(keys & ks for _, ks in active):
                    end = min(total, k + dur)
                    events.append({"id": f"b{len(events):03d}", "kind": kind, "node": node, "params": params,
                                   "start_req": k, "end_req": end, "during": during})
                    active.append((end, keys))
                    break
    return events


def schedule_stats(events, total, calm):
    conc = [sum(1 for e in events if e["start_req"] <= k < e["end_req"]) for k in range(calm, total)]
    return {"events": len(events), "by_kind": dict(collections.Counter(e["kind"] for e in events)),
            "during": sum(1 for e in events if e["during"]),
            "mean_concurrency": round(sum(conc) / max(len(conc), 1), 2), "max_concurrency": max(conc, default=0),
            "requests_without_burst": sum(1 for c in conc if c == 0)}


# ---------------------------------------------------------------- adapters
class Adapters:
    """Every Pool / node-daemon / tenant-daemon call this driver makes, in one place.

    The pool-side API is being extended while this file is written; if a call is missing or does
    not take an optional argument, the adapter logs `adapter_fallback` once and uses the nearest
    older behaviour, so a mismatch is visible in the run log instead of silently changing the
    experiment."""

    def __init__(self, pool, log, tenant_client):
        self.pool, self.log, self._tenant = pool, log, tenant_client
        self._fell = set()

    def fallback(self, api, why):
        if api not in self._fell:
            self._fell.add(api)
            self.log(event="adapter_fallback", api=api, why=str(why)[:200])

    def _opt(self, fn, api, optional, *a, **kw):
        """Call fn; if it rejects one of the `optional` keyword arguments, drop those and retry."""
        try:
            return fn(*a, **kw)
        except TypeError as e:
            if "unexpected keyword" not in str(e) or not any(k in kw for k in optional):
                raise
            self.fallback(api, e)
            return fn(*a, **{k: v for k, v in kw.items() if k not in optional})

    # -- placement
    def set_hbm_park(self, park):
        self.pool.hbm_park = {n: set(cs) for n, cs in park.items()}

    def hbm_park_add(self, node, card, present):
        park = getattr(self.pool, "hbm_park", None)
        if park is not None:
            (park.setdefault(node, set()).add if present else park.setdefault(node, set()).discard)(card)

    def park(self, name, **kw):
        # Pool.park(name, copies, tiers, prefer, pin_nodes, avoid_nodes, budget, all_or_nothing,
        #           from_job, local_frac=None)
        return self._opt(self.pool.park, "park(local_frac)", ("local_frac",), name, **kw)

    def park_within(self, name, node, **kw):
        """Whole model into ONE node, counting only the replicas that node already holds.
        Pool.place counts replicas on every node, so a model that is warm on node A would never
        get a copy on node B with copies=1 (this silently kept the maas2 Janus/SGLang baselines
        from ever being warm on a second node). Preferred: Pool.park(..., within=(node,)). Without
        that argument the placement is scoped here by hiding the other nodes' replicas while
        Pool.place runs (under the pool lock)."""
        pool = self.pool
        try:
            return pool.park(name, within=(node,), **kw)
        except TypeError as e:
            if "within" not in str(e):
                raise
            self.fallback("park(within)", e)
        orig = pool.place

        def place(model, *a, **k):
            full, view = pool.rep, collections.defaultdict(dict)
            for c in model.chunks:
                view[c.id] = {v: loc for v, loc in full[c.id].items() if v.startswith(node + "/")}
            pool.rep = view
            try:
                return orig(model, *a, **k)
            finally:
                pool.rep = full
        pool.place = place
        try:
            return pool.park(name, **kw)
        finally:
            del pool.place

    def stripe(self, name, nodes=None, budget=None):
        if not hasattr(self.pool, "stripe"):
            return self.fallback("stripe", "missing")
        return self.pool.stripe(name, nodes=nodes, budget=budget)

    def rebalance(self, order):
        if not hasattr(self.pool, "rebalance"):
            return self.fallback("rebalance", "missing")
        return self.pool.rebalance(order)

    def touch(self, name):
        if hasattr(self.pool, "touch"):
            return self.pool.touch(name)
        self.fallback("touch", "missing")
        self.pool.models[name].last_used = time.time()

    # -- activation / deactivation
    def restore(self, name, node, cards, **kw):
        return self.pool.restore(name, node, cards, **kw)

    def restore_wave(self, items):
        """items: [(name, node, cards, kw)] -> [restore result] in the same order."""
        if hasattr(self.pool, "restore_wave"):
            return self.pool.restore_wave(items)
        self.fallback("restore_wave", "missing: independent restore() calls in threads")
        out, errs = [None] * len(items), []

        def one(k, name, node, cards, kw):
            try:
                out[k] = self.pool.restore(name, node, cards, **kw)
            except Exception as e:                       # noqa: BLE001
                errs.append(f"{name}@{node}: {e!r}")
        th = [threading.Thread(target=one, args=(k, *it)) for k, it in enumerate(items)]
        [x.start() for x in th]
        [x.join() for x in th]
        if errs:
            raise RuntimeError("; ".join(errs))
        return out

    def retire(self, name, node, job, cards, budget=None):
        """Demote-then-release. -> {demoted_GiB, s, left_slow_GiB}"""
        if hasattr(self.pool, "retire"):
            return self.pool.retire(name, node, job, cards, budget=budget)
        self.fallback("retire", "missing: unpublish + job_release, nothing demoted")
        self.pool.unpublish(node, job)
        self.noded(node, "job_release", job=job)
        return {"demoted_GiB": 0.0, "s": 0.0, "left_slow_GiB": None}

    def make_room(self, node, cards, nbytes):
        if hasattr(self.pool, "make_room"):
            return self.pool.make_room(node, cards, nbytes)
        self.fallback("make_room", "missing")

    def yield_card(self, node, card):
        if hasattr(self.pool, "yield_card"):
            return self.pool.yield_card(node, card)
        self.fallback("yield_card", "missing")

    def publish(self, name, node, job):
        return self.pool.publish(name, node, job)

    def unpublish(self, node, job):
        return self.pool.unpublish(node, job)

    def drop_model(self, name, tiers, nodes):
        return self.pool.drop_model(name, tiers=tiers, nodes=nodes)

    def from_job(self, name, node, job, cards):
        """The `from_job` argument of Pool.park: fill from the HBM the model is leaving."""
        p = self.pool
        return (node, job, p.where(p.models[name], cards))

    # -- state
    def coverage(self, name, tiers, nodes=None):
        return self.pool.coverage(name, tiers=tiers, nodes=nodes)

    def coverage_all(self, tiers):
        if hasattr(self.pool, "coverage_all"):
            return self.pool.coverage_all(tiers)
        self.fallback("coverage_all", "missing: byte-weighted mean of coverage()")
        ms = self.pool.models
        return sum(self.pool.coverage(m, tiers=tiers) * ms[m].nbytes for m in ms) / max(sum(x.nbytes for x in ms.values()), 1)

    def nbytes(self, name):
        return self.pool.models[name].nbytes

    def models_by_age(self):
        return sorted(self.pool.models.values(), key=lambda m: m.last_used)

    def pressure(self):
        return self.pool.pressure()

    def holds(self, name, vertex):
        """Does the model keep bytes on this vertex (sampled, as maas2 did)?"""
        p = self.pool
        if hasattr(p, "holds"):
            return p.holds(name, vertex)
        return any(vertex in p.rep[c.id] for c in p.models[name].chunks[::7])

    def dram_vertices(self):
        if hasattr(self.pool, "dram_vertices"):
            return self.pool.dram_vertices()
        return [vid for vid, v in self.pool.topology.vertices.items() if v.kind == "dram"]

    def events(self):
        return list(self.pool.events)

    def poll_ms(self):
        return sorted(getattr(self.pool, "poll_ms", []) or [])

    # -- daemons
    def noded(self, node, op, **kw):
        return self.pool.rpc[node].call(op, **kw)

    def arena_GiB(self, node):
        return {int(c): g for c, g in (self.noded(node, "hello").get("arena_GiB") or {}).items()}

    def ensure_arena(self, node, card, gib):
        """Make the card's arena exist (gib > 0) or not exist (gib == 0)."""
        have = self.arena_GiB(node).get(int(card), 0)
        if (gib > 0) != (have > 0):
            self.noded(node, "arena", card=card, gib=gib)
            return True
        return False

    def tenant(self, node, op, **kw):
        return self._tenant(node).call(op, **kw)


# ---------------------------------------------------------------- systems
class System:
    name = "?"
    publishes = True                    # an active instance is announced as a source for peers
    uses_idle_hbm = False

    def __init__(self, A, a, log):
        self.A, self.a, self.log = A, a, log
        self.cap = {vid: int(a.cap * GiB) for vid in A.dram_vertices()}
        self.events = []                # this system's own reactions: {"event", "t", "freed_GiB": {vertex: GiB}}
        self.inflight = set()           # (model, node) being activated right now
        self.last_use = {}              # (model, node) -> time of the last request served there

    def setup(self):
        pass

    def tick(self):
        pass

    def background(self):
        pass

    def touch(self, m, node):
        self.last_use[(m, node)] = time.time()

    def deactivate(self, m, node, cards, job):
        pass

    def release(self, m, node, cards, job):
        """The model leaves its cards (they are needed by another model or by a tenant)."""
        if self.publishes:
            self.A.unpublish(node, job)
        self.deactivate(m, node, cards, job)
        self.A.noded(node, "job_release", job=job)

    def yield_card(self, node, card):
        pass

    def warm(self, m, node):
        """Could (m, node) be activated now without a cold read, given it is not resident?"""
        return False

    def _go(self, m, node, cards, kind, **kw):
        r = self.A.restore(m, node, cards, release=False, **kw)
        r["kind"] = kind
        return r

    def activate(self, m, node, cards):
        raise NotImplementedError

    def activate_wave(self, plan):
        """plan: [(label, model, node, cards)] -> ({(label, model): result}, [errors])."""
        res, errs = {}, []

        def go(label, m, n, cards):
            try:
                res[(label, m)] = self.activate(m, n, cards)
            except Exception as e:                       # noqa: BLE001
                errs.append(f"{m}@{n}: {e!r}")
        self.inflight = {(m, n) for _, m, n, _ in plan}
        th = [threading.Thread(target=go, args=x) for x in plan]
        [x.start() for x in th]
        [x.join() for x in th]
        self.inflight = set()
        return res, errs

    # -- whole-model helpers shared by the baselines
    def local_warm(self, m, node):
        return self.A.coverage(m, ("dram",), {node}) > 0.9999

    def keep_local(self, m, node, cards=None, job=None):
        """Whole model into this node's pinned memory (from the HBM it is leaving, or from disk)."""
        others = tuple(n for n in NODES if n != node)
        kw = dict(copies=1, tiers=("dram",), pin_nodes=(node,), budget=self.cap, avoid_nodes=others,
                  all_or_nothing=True)
        if job is not None:
            kw["from_job"] = self.A.from_job(m, node, job, cards)
        return self.A.park_within(m, node, **kw)

    def react_whole(self):
        """Whole-model systems under pressure: a squeezed NUMA vertex costs every model with bytes
        on it (on that node), oldest first, until the deficit is covered. A model whose wake-up is
        in flight on that node is spared."""
        A = self.A
        sq = dict(A.pressure())
        lost, freed = [], collections.Counter()
        for m in A.models_by_age():
            if not sq:
                break
            for node in sorted({v.split("/")[0] for v in sq}):
                if (m.name, node) in self.inflight:
                    continue
                if not any(v.startswith(node + "/") and A.holds(m.name, v) for v in sq):
                    continue
                out = A.drop_model(m.name, ("dram",), {node})
                lost.append(f"{m.name}@{node}")
                for v, o in out.items():
                    freed[v] += o["freed"]
                    if v in sq:
                        sq[v] -= o["freed"]
                        if sq[v] <= 0:
                            del sq[v]
        if lost:
            ev = {"event": "evict_whole", "t": time.time(), "system": self.name, "models": lost,
                  "freed_GiB": {v: round(b / GiB, 2) for v, b in freed.items()}}
            self.events.append(ev)
            self.log(**ev)


class Fluid(System):
    name = "fluidpool"
    uses_idle_hbm = True

    def setup(self):
        A, a = self.A, self.a
        A.set_hbm_park(IDLE)
        self.budget = dict(self.cap)
        for n, cards in IDLE.items():
            for c in cards:
                self.budget[f"{n}/hbm/{c}"] = int(a.arena * GiB)
        allpin = tuple(NODES)
        lf = None if a.local_frac == "auto" else float(a.local_frac)
        for m in HEAT_ORDER:                             # hottest first: the deficit, if any, goes to the coldest
            home = DURABLE[m] if m.startswith("glm53") else None     # one home -> rate-proportional local share
            A.park(m, copies=1, tiers=("dram", "hbm"), prefer=home, pin_nodes=allpin, budget=self.budget,
                   local_frac=lf)
        for m in HEAT_ORDER:                             # the slow tier, striped over the pool disks
            nodes = (DURABLE[m],) if self.stripe_durable_only else tuple(a.stripe_nodes.split(","))
            A.stripe(m, nodes=nodes, budget=None)
        A.rebalance(HEAT_ORDER)

    restore_kw = {}                      # ablations: e.g. {"strategy": "rr", "live": False}
    demote = True
    stripe_durable_only = False

    def activate_wave(self, plan):
        A = self.A
        kw = dict(allow=("dram", "ssd", "hbm", "file"), release=False, **self.restore_kw)
        for _, m, n, cards in plan:                      # parked chunks never sit on serving cards; cheap no-op
            if set(cards) & set(IDLE.get(n, ())):
                A.make_room(n, cards, A.nbytes(m))
        res, errs = {}, []
        try:
            out = A.restore_wave([(m, n, cards, dict(kw)) for _, m, n, cards in plan])
            for (label, m, n, cards), r in zip(plan, out):
                if isinstance(r, BaseException):         # restore_wave returns a failed item as its exception
                    self.log(event="wave_item_failed", what=f"{m}@{n}", error=repr(r)[:300])
                    t0 = time.time()
                    r = self._go(m, n, cards, "fluid", allow=kw["allow"])
                    r["fallback_from"] = ["restore_wave"]
                    r["total_s"] = round(time.time() - t0, 4)
                r["kind"] = "fluid"
                res[(label, m)] = r
        except Exception as e:                           # noqa: BLE001 -- one retry, part by part
            self.log(event="wave_failed", error=repr(e)[:300])
            t0 = time.time()
            for label, m, n, cards in plan:
                try:
                    r = self._go(m, n, cards, "fluid", allow=kw["allow"])
                    r["total_s"] = round(time.time() - t0, 4)
                    r["fallback_from"] = ["restore_wave"]
                    res[(label, m)] = r
                except Exception as e2:                  # noqa: BLE001
                    errs.append(f"{m}@{n}: {e2!r}")
        return res, errs

    def release(self, m, node, cards, job):
        if not self.demote:                              # ablation: the instance is simply dropped
            if self.publishes:
                self.A.unpublish(node, job)
            self.A.noded(node, "job_release", job=job)
            return
        r = self.A.retire(m, node, job, cards, budget=self.budget) or {}
        if r.get("demoted_GiB"):
            self.log(event="demote", t=time.time(), model=m, node=node, **{k: r[k] for k in ("demoted_GiB", "s", "left_slow_GiB") if k in r})

    def yield_card(self, node, card):
        self.A.yield_card(node, card)

    def background(self):
        self.A.rebalance(HEAT_ORDER)

    def warm(self, m, node):
        return self.A.coverage(m, ("dram", "hbm")) >= 0.999


class Janus(System):
    name = "janus"

    def __init__(self, *x):
        super().__init__(*x)
        plan = self.a.janus_plan
        self.plan_kw = {"static": dict(strategy="lp", live=False), "rr": dict(strategy="rr", live=False),
                        "live": dict(strategy="lp", live=True)}[plan]

    def setup(self):
        """fork_master_and_sleep: every node holds the models homed on it in pinned memory from
        the start (popularity order, until the node's budget is full)."""
        for node in NODES:
            for m in homed_on(node):
                r = self.keep_local(m, node)
                self.log(event="prewarm", system=self.name, model=m, node=node, kept=r is not None,
                         GiB=None if r is None else r.get("GiB"))

    def deactivate(self, m, node, cards, job):
        A = self.A
        if self.local_warm(m, node):
            return
        while True:                                      # LIGHT_SLEEP: D2H back-fill, whole model or nothing
            if self.keep_local(m, node, cards, job) is not None:
                return
            victims = [x for x in A.models_by_age() if x.name != m and A.coverage(x.name, ("dram",), {node}) > 0]
            if not victims:
                return                                   # larger than the node's budget: DEEP_SLEEP
            A.drop_model(victims[0].name, ("dram",), {node})

    def tick(self):
        self.react_whole()

    def d2d_source(self, m, node):
        peers = [p for p in NODES if p != node and self.A.coverage(m, ("hbm",), {p}) > 0.9999]
        return max(peers, key=lambda p: self.last_use.get((m, p), 0.0), default=None)

    def activate(self, m, node, cards):
        t0, tried, chain = time.time(), [], []
        peer = self.d2d_source(m, node)
        if peer:                                         # one source, the most recently used instance
            chain.append(("d2d_fork", dict(allow=("hbm",), single_source=peer)))
        if self.local_warm(m, node):
            chain.append(("h2d_wakeup", dict(allow=("dram",), single_source=node)))
        if self.a.janus_order == "h2d_first":
            chain.sort(key=lambda x: x[0] != "h2d_wakeup")
        chain.append(("cold", dict(allow=("file",))))
        for kind, kw in chain:
            try:
                r = self._go(m, node, cards, kind, **kw, **self.plan_kw)
            except Exception as e:                       # noqa: BLE001 -- source gone: next path
                tried.append(f"{kind}: {e!r}"[:160])
                continue
            if tried:                                    # the failed attempts are part of the wake-up time
                r["total_s"] = round(time.time() - t0, 4)
                r["fallback_from"] = tried
            return r
        raise RuntimeError("; ".join(tried))

    def warm(self, m, node):
        return self.local_warm(m, node) or self.d2d_source(m, node) is not None


class SGLang(System):
    name = "sglang"
    publishes = False

    def activate(self, m, node, cards):
        return self._go(m, node, cards, "cold", allow=("file",), strategy="rr", live=False)


class FluidRelay(Fluid):
    """FluidPool plus borrowed ingress: a restore also lands through idle near cards of the
    destination node and xGMI (Pool.relay_pick / flow_plan relay paths, noded relay lanes)."""
    name = "fluidpool_relay"

    def setup(self):
        if hasattr(self.A.pool, "relay_policy"):
            self.A.pool.relay_policy = self.a.relay_policy
        super().setup()


class FluidAblation(Fluid):
    """FluidPool with one component removed (name says which); everything else unchanged."""

    def setup(self):
        if hasattr(self.A.pool, "evict_order"):
            self.A.pool.evict_order = self.evict_order
        super().setup()

    evict_order = "value"


def _ablation(name, **attrs):
    return type(name, (FluidAblation,), {"name": name, **attrs})


ABLATIONS = {
    "fluidpool_noplan": _ablation("fluidpool_noplan", restore_kw={"strategy": "rr", "live": False}),   # no flow LP, no live graph
    "fluidpool_static": _ablation("fluidpool_static", restore_kw={"strategy": "lp", "live": False}),   # LP on nominal capacities
    "fluidpool_nodemote": _ablation("fluidpool_nodemote", demote=False),                               # drop on release
    "fluidpool_nostripe": _ablation("fluidpool_nostripe", stripe_durable_only=True),                    # slow tier on one disk
    "fluidpool_age": _ablation("fluidpool_age", evict_order="age"),                                     # LRU, no replica value
}
class FluidV2(FluidAblation):
    """FluidPool v2 (2026-11-16): everything of fluidpool plus
      relay      borrowed ingress through idle near cards and xGMI, revoked the moment a tenant
                 takes a borrowed card (noded op_relay_release, called by every arena release);
      hbm_first  a model leaving its cards lands its only-copy chunks in pre-registered HBM arenas
                 first (same node over xGMI, then peers' idle arenas) -- no slab pinning on the
                 critical path -- and Pool.settle() gives them a DRAM copy in the background;
      prestage   node-level prediction: an EWMA of requests per (model, node) ranks the models of
                 each node with idle cards, and Pool.prestage() keeps an extra replica of the top
                 ones in that node's idle HBM, so their restores pull over xGMI;
      volatile   copies in lent (idle-card) HBM never make a DRAM copy look redundant to eviction.
    Each flag can be switched off for the ablations below."""
    name = "fluidpool_v2"
    relay, hbm_first, prestage = True, True, True
    ring_reserve = 1 * GiB                               # arena bytes per idle card kept for a relay ring

    def setup(self):
        P = self.A.pool
        if hasattr(P, "relay_policy"):
            P.relay_policy = self.a.relay_policy if self.relay else "off"
            P.demote_hbm_first = self.hbm_first
            P.hbm_volatile = self.hbm_first or self.prestage
        self.demand = collections.Counter()
        super().setup()
        for n, cards in IDLE.items():                    # leave room for a relay ring on every idle card
            for c in cards:
                self.budget[f"{n}/hbm/{c}"] = int(self.a.arena * GiB) - self.ring_reserve
        if self.prestage and hasattr(P, "prestage"):     # same warm start as the baselines' pre-warm
            for n in IDLE:
                for m in homed_on(n):
                    if not m.startswith("glm53"):
                        for _ in range(64):
                            if not P.prestage(m, n, budget=self.budget, max_bytes=16 * GiB):
                                break

    def touch(self, m, node):
        super().touch(m, node)
        for k in list(self.demand):
            self.demand[k] *= 0.97
        self.demand[(m, node)] += 1.0

    def background(self):
        super().background()
        P = self.A.pool
        if self.hbm_first and hasattr(P, "settle"):
            P.settle(budget=self.budget, max_bytes=8 * GiB, order=HEAT_ORDER)
        if self.prestage and hasattr(P, "prestage"):
            for n in IDLE:
                ranked = sorted({m for m in homed_on(n) if not m.startswith("glm53")},
                                key=lambda m: (-self.demand.get((m, n), 0.0), HEAT_ORDER.index(m)))
                for m in ranked:
                    if P.staged(m, n) < 0.999:
                        P.prestage(m, n, budget=self.budget, max_bytes=8 * GiB)
                        break


def _v2(name, **attrs):
    return type(name, (FluidV2,), {"name": name, **attrs})


V2 = {
    "fluidpool_v2": FluidV2,
    "fluidpool_v2_norelay": _v2("fluidpool_v2_norelay", relay=False),
    "fluidpool_v2_noprestage": _v2("fluidpool_v2_noprestage", prestage=False),
    "fluidpool_v2_nohbmfirst": _v2("fluidpool_v2_nohbmfirst", hbm_first=False),
    "fluidpool_v2_noplan": _v2("fluidpool_v2_noplan", restore_kw={"strategy": "rr", "live": False}, relay=False),
    "fluidpool_v2_nodemote": _v2("fluidpool_v2_nodemote", demote=False),
    "fluidpool_v2_age": _v2("fluidpool_v2_age", evict_order="age"),
}
SYSTEMS = {"fluidpool": Fluid, "fluidpool_relay": FluidRelay, **ABLATIONS, **V2, "janus": Janus, "sglang": SGLang}


# ---------------------------------------------------------------- bursts at run time
class Bursts:
    """Starts and ends the scheduled events on the tenant daemons and records what the tenant got
    and how the system under test reacted."""

    def __init__(self, A, a, log, sysm, driver):
        self.A, self.a, self.log, self.sysm, self.d = A, a, log, sysm, driver
        self.active = {}                                 # id -> {"ev", "t_start", "tenant", ...}
        self.done = []
        self.lock = threading.Lock()

    def _wait(self, threads, tick):
        while any(x.is_alive() for x in threads):        # the tenant waits for room; systems get to yield
            if tick:
                tick()
            time.sleep(self.a.tick_s)
        [x.join() for x in threads]

    def start(self, ev, tick=None):
        try:
            return self._start(ev, tick)
        except Exception as e:                           # noqa: BLE001 -- loud, but the run goes on
            self.log(event="burst_failed", id=ev["id"], kind=ev["kind"], node=ev["node"], where="start", error=repr(e)[:300])

    def stop(self, ev):
        try:
            return self._stop(ev)
        except Exception as e:                           # noqa: BLE001
            self.log(event="burst_failed", id=ev["id"], kind=ev["kind"], node=ev["node"], where="stop", error=repr(e)[:300])
            if ev["kind"] == "hbm_serve":                # never leave serving cards blocked
                self.d.blocked[ev["node"]] -= set(ev["params"]["cards"])

    def _start(self, ev, tick=None):
        A, a, p, node, kind = self.A, self.a, ev["params"], ev["node"], ev["kind"]
        rec = {"ev": ev, "t_start": time.time(), "tenant": {}}
        with self.lock:
            self.active[ev["id"]] = rec
        self.log(event="burst_start", id=ev["id"], kind=kind, node=node, params=p, during=ev["during"],
                 start_req=ev["start_req"], end_req=ev["end_req"])
        t0 = rec["t_start"]
        if kind == "dram":
            got = {}

            def one(nu):
                got[nu] = A.tenant(node, "dram", numa=nu, gib=p["gib_each"], reserve_gib=a.dram_reserve,
                                   patience_s=a.patience)
            th = [threading.Thread(target=one, args=(nu,)) for nu in p["numas"]]
            [x.start() for x in th]
            self._wait(th, tick)
            rec["tenant"] = {"asked_GiB": p["gib_each"] * len(p["numas"]),
                             "obtained_GiB": sum(r["held_GiB"] for r in got.values()),
                             "obtain_s": round(time.time() - t0, 3)}
        elif kind in ("hbm_idle", "hbm_serve"):
            if kind == "hbm_serve":                      # the manager takes serving cards away
                rec["evict_s"] = round(self.d.evict_cards(node, p["cards"]), 3)
                self.d.blocked[node] |= set(p["cards"])
            driver_yield = kind == "hbm_serve" or (self.sysm.uses_idle_hbm and a.hbm_idle_yield == "driver")
            if driver_yield:
                for c in p["cards"]:
                    self.sysm.yield_card(node, c)
                    A.hbm_park_add(node, c, False)
                    A.ensure_arena(node, c, 0)
            got = {}

            def one(c):
                got[c] = A.tenant(node, "hbm", card=c, gib=p["gib"], patience_s=a.patience)
            th = [threading.Thread(target=one, args=(c,)) for c in p["cards"]]
            [x.start() for x in th]
            self._wait(th, tick)
            rec["tenant"] = {"asked_GiB": p["gib"] * len(p["cards"]),
                             "obtained_GiB": sum(r["held_GiB"] for r in got.values()),
                             "obtain_s": round(time.time() - t0, 3),
                             "waited_s": max((r.get("waited_s") or 0.0) for r in got.values())}
        elif kind in ("ssd_rd", "ssd_wr"):
            A.tenant(node, "ssd", mode="read" if kind == "ssd_rd" else "write", seconds=7200,
                     streams=p["streams"], name=ev["id"])
        elif kind == "ib":
            A.tenant(node, "ib", peer=NODES[p["src"]], ports=p["ports"], seconds=7200, name=ev["id"])
        elif kind == "h2d":
            A.tenant(node, "h2d", card=p["card"], seconds=7200, name=ev["id"])
        rec["t_ready"] = time.time()
        return rec

    def _stop(self, ev):
        A, a, p, node, kind = self.A, self.a, ev["params"], ev["node"], ev["kind"]
        with self.lock:
            rec = self.active.pop(ev["id"], None)
        if rec is None:
            return
        if kind == "dram":
            for nu in p["numas"]:
                A.tenant(node, "dram", numa=nu, gib=0)
        elif kind in ("hbm_idle", "hbm_serve"):
            for c in p["cards"]:
                A.tenant(node, "hbm", card=c, gib=0)
            for c in p["cards"]:
                if kind == "hbm_serve":
                    A.ensure_arena(node, c, a.arena)
                elif self.sysm.uses_idle_hbm:            # the idle card is ours to park in again
                    A.ensure_arena(node, c, a.arena)
                    A.hbm_park_add(node, c, True)
            if kind == "hbm_serve":
                self.d.blocked[node] -= set(p["cards"])
        else:
            rec["traffic"] = A.tenant(node, "stop", name=ev["id"])
        rec["t_end"] = time.time()
        rec["reaction"] = self.reaction(ev, rec["t_start"], rec["t_end"])
        out = {k: v for k, v in rec.items() if k != "ev"}
        self.done.append({**ev, **out})
        self.log(event="burst_end", **{**ev, **out})

    def reaction(self, ev, t0, t1):
        """What the system under test did on the burst's node while the burst was on."""
        node, p = ev["node"], ev["params"]
        mine = {"dram": [f"{node}/dram/{k}" for k in p.get("numas", [])],
                "hbm_idle": [f"{node}/hbm/{c}" for c in p.get("cards", [])],
                "hbm_serve": [f"{node}/hbm/{c}" for c in p.get("cards", [])]}.get(ev["kind"])
        first, n, freed, kinds = None, 0, 0.0, collections.Counter()
        for e in self.A.events() + self.sysm.events:
            t = e.get("t")
            if t is None or not t0 <= t <= t1:
                continue
            fg = e.get("freed_GiB") if isinstance(e.get("freed_GiB"), dict) else {}
            on = [v for v in fg if (v in mine if mine is not None else v.startswith(node + "/"))]
            if fg and not on:
                continue
            if not fg and e.get("node") not in (None, node):
                continue
            n += 1
            kinds[e.get("event", "?")] += 1
            freed += sum(fg[v] for v in on)
            if fg and (first is None or t - t0 < first):
                first = t - t0
        return {"events": n, "kinds": dict(kinds), "yielded_GiB": round(freed, 2),
                "first_yield_s": None if first is None else round(first, 3)}

    def stop_all(self):
        for rec in list(self.active.values()):
            self.stop(rec["ev"])


# ---------------------------------------------------------------- driver
class Driver:
    def __init__(self, A, a, log, sysm, schedule):
        self.A, self.a, self.log, self.sysm = A, a, log, sysm
        self.active = {n: collections.OrderedDict() for n in NODES}     # node -> {model: (cards, job)}, LRU first
        self.blocked = {n: set() for n in NODES}
        self.bursts = Bursts(A, a, log, sysm, self)
        self.start_at, self.end_at = collections.defaultdict(list), collections.defaultdict(list)
        for ev in schedule:
            self.start_at[ev["start_req"]].append(ev)
            self.end_at[ev["end_req"]].append(ev)
        self.recs, self.states, self.verified = [], [], set()
        self.skipped = 0

    # -- residency
    def free_cards(self, node):
        used = {c for cards, _ in self.active[node].values() for c in cards}
        return [c for c in SERVE[node] if c not in used and c not in self.blocked[node]]

    def release(self, node, m):
        cards, job = self.active[node].pop(m)
        t = time.time()
        self.sysm.release(m, node, cards, job)
        return time.time() - t

    def evict_cards(self, node, cards):
        """A tenant takes serving cards: every model resident on them leaves through the system's
        own path. Returns the seconds that took."""
        t = time.time()
        for m in [m for m, (cs, _) in self.active[node].items() if set(cs) & set(cards)]:
            self.release(node, m)
        return time.time() - t

    def resident(self, apps):
        return all(m in self.active[n] and self.active[n][m][1] is not None for m, n in apps)

    def warm_servable(self):
        """Share of (model, home) pairs that could be activated now without a cold read."""
        ok_n = ok_b = tot_b = 0
        pairs = home_pairs()
        detail = {}
        for m, n in pairs:
            b = self.A.nbytes(m)
            ok = m in self.active[n] or self.sysm.warm(m, n)
            detail[f"{m}@{n}"] = bool(ok)
            ok_n += ok
            ok_b += b * ok
            tot_b += b
        return {"pairs": round(ok_n / len(pairs), 4), "bytes": round(ok_b / tot_b, 4),
                "cold": sorted(k for k, v in detail.items() if not v)}

    def burst_tags(self, nodes):
        act = [r["ev"] for r in self.bursts.active.values()]
        on = [e for e in act if e["node"] in nodes or e["params"].get("src") in nodes]
        return {"bursts": sorted(e["id"] for e in act), "burst_kinds": sorted({e["kind"] for e in act}),
                "burst_kinds_on_node": sorted({e["kind"] for e in on})}

    # -- one pass over a request list
    def serve(self, reqs, total, phase_of, measured):
        A, a, sysm, log = self.A, self.a, self.sysm, self.log
        i = idx = 0
        synced = -1
        pend_during = {k: [e for e in v if e["during"]] for k, v in self.start_at.items()} if measured else {}
        carry = []                                       # 'during' events waiting for a restore to hit
        while idx < total and i < len(reqs):
            wave, busy, during = [], set(), []
            while i < len(reqs) and len(wave) < a.wave and idx < total:
                label, apps = reqs[i]
                if measured and synced < idx:            # burst boundaries of this request index
                    pre = [e for e in self.start_at.get(idx, []) if not e["during"]]
                    ends = self.end_at.get(idx, [])
                    if (pre or ends) and wave:
                        break                            # a wave never spans a boundary
                    for e in ends:
                        self.bursts.stop(e)
                    for e in pre:
                        self.bursts.start(e, tick=sysm.tick)
                    if pre:
                        time.sleep(a.settle)
                    synced = idx
                nodes = {n for _, n in apps}
                carry += pend_during.pop(idx, [])
                if self.resident(apps):                  # hit: attach to what is already in HBM
                    # A 'during' event waits for the next wave of restores; one whose window ends
                    # with this request starts now, as an ordinary burst, so that none is lost.
                    last = [e for e in carry if e["end_req"] <= idx + 1]
                    if last and wave:
                        break
                    for m, n in apps:
                        self.active[n].move_to_end(m)
                        A.touch(m)
                        sysm.touch(m, n)
                    ncards = sum(CARDS[m] for m, _ in apps)
                    lat = round(a.ipc_hit_ms_per_card * ncards / 1e3, 4)
                    self.record({"what": label, "kind": "ipc_hit", "latency_s": lat, "evict_s": 0.0, "total_s": lat,
                                 "GiB": round(sum(A.nbytes(m) for m, _ in apps) / GiB, 1), "calibrated": True,
                                 "wave": 0, "nodes": sorted(nodes)}, idx, phase_of(idx), nodes)
                    for e in last:
                        carry.remove(e)
                        self.bursts.start(e, tick=sysm.tick)
                    idx += 1
                    i += 1
                    if measured:                         # background work runs between requests, hits included
                        sysm.tick()
                        sysm.background()
                    continue
                if nodes & busy:
                    break
                if any(CARDS[m] > len(SERVE[n]) - len(self.blocked[n]) for m, n in apps):
                    self.skipped += 1                    # cannot be served while a tenant holds the cards
                    i += 1
                    continue
                wave.append((idx, label, apps))
                during += carry
                carry = []
                busy |= nodes
                idx += 1
                i += 1
            if wave:
                self.run_wave(wave, during, phase_of)
        return idx

    def record(self, rec, idx, phase, nodes):
        rec = {"event": "request", "system": self.a.system, "i": idx, "phase": phase, **rec,
               **(self.burst_tags(nodes) if phase != "warmup" else {})}
        self.log(**rec)
        self.recs.append(rec)

    def run_wave(self, wave, during, phase_of):
        A, a, sysm, log = self.A, self.a, self.sysm, self.log
        phase = phase_of(wave[0][0])
        if phase != "warmup":
            st = {"event": "state", "i": wave[0][0], "phase": phase, "t": time.time(), **self.warm_servable(),
                  "fast_coverage": round(A.coverage_all(("dram", "hbm")), 4)}
            self.states.append(st)
            log(**st)
        tags = {label: self.burst_tags({n for _, n in apps}) for _, label, apps in wave}
        # ---- make room on the cards (timed apart: evict_s), then restore the whole wave at once
        plan, evict_s = [], collections.Counter()
        for _, label, apps in wave:
            for m, n in apps:
                A.touch(m)
                sysm.touch(m, n)
                if m in self.active[n]:
                    continue
                t = time.time()
                while len(self.free_cards(n)) < CARDS[m]:
                    self.release(n, next(iter(self.active[n])))
                evict_s[label] += time.time() - t
                cards = self.free_cards(n)[:CARDS[m]]
                self.active[n][m] = (cards, None)        # reserve
                plan.append((label, m, n, cards))
        sysm.tick()
        fired = []

        def fire():
            for e in during:
                fired.append(self.bursts.start(e, tick=sysm.tick))
        timer = threading.Timer(a.during_delay, fire)
        tw = time.time()
        if during:
            timer.start()
        res, errs = sysm.activate_wave(plan)
        wall = time.time() - tw
        if during:
            timer.join()                                 # the burst settles before the next request
        if errs:
            log(event="restore_failed", errors=errs)
            raise SystemExit("; ".join(errs))
        for label, m, n, cards in plan:
            r = res[(label, m)]
            self.active[n][m] = (cards, r["job"])
            if sysm.publishes:
                A.publish(m, n, r["job"])
        for idx, label, apps in wave:
            parts = [(m, n, res[(label, m)]) for m, n in apps if (label, m) in res]
            lat = max(r["total_s"] for _, _, r in parts)
            gib = sum(r["GiB"] for _, _, r in parts)
            vbad = None
            key = label.split("@")[0]
            if phase != "warmup" and (key not in self.verified or (idx % a.verify_every == 0 and gib < 80)):
                self.verified.add(key)
                vbad = sum(A.noded(n, "job_verify", job=r["job"])["bad"] for _, n, r in parts)
            paths = collections.Counter()
            for _, _, r in parts:
                for kk, v in r["paths"].items():
                    paths["net" if kk.startswith("net:") else kk.split(":")[0]] += v["bytes"] / GiB
            ev = round(evict_s[label], 3)
            rec = {"what": label, "GiB": round(gib, 1), "kind": "+".join(sorted({r["kind"] for _, _, r in parts})),
                   "latency_s": round(lat, 3), "evict_s": ev, "total_s": round(lat + ev, 3),
                   "wave": len(wave), "wave_wall_s": round(wall, 3), "GBps": round(gib * GiB / 1e9 / max(lat, 1e-9), 1),
                   "plan_s": round(max(r.get("plan_s", 0.0) for _, _, r in parts), 4),
                   "failed_fetches": sum(r.get("failed_fetches", 0) for _, _, r in parts),
                   "paths_GiB": {kk: round(v, 1) for kk, v in paths.items()}, "verify_bad": vbad,
                   "nodes": sorted({n for _, n in apps}),
                   "during_bursts": sorted(e["id"] for e in during),
                   "fallback": [x for _, _, r in parts for x in r.get("fallback_from", [])] or None}
            rec = {"event": "request", "system": a.system, "i": idx, "phase": phase_of(idx), **rec,
                   **(tags[label] if phase != "warmup" else {})}
            log(**rec)
            self.recs.append(rec)
        sysm.background()


# ---------------------------------------------------------------- dry mode
class FakePool:
    """A byte-level stand-in for Pool + the node daemons: enough state for the driver's decisions
    (what is fast where, what is published, what a tenant holds) and synthetic restore times
    from the capacities of pool.py. Nothing here is a measurement."""

    class _Rpc:
        def __init__(self, pool, node):
            self.pool, self.node = pool, node

        def call(self, op, **kw):
            p = self.pool
            if op == "hello":
                return {"arena_GiB": {str(c): g for c, g in p.arena[self.node].items() if g}}
            if op == "arena":
                p.arena[self.node][int(kw["card"])] = kw["gib"]
                return {"card": kw["card"], "GiB": kw["gib"]}
            if op == "stat":
                return {"slab_bytes": int(sum(b for m in p.dram.values() for v, b in m.items() if v.startswith(self.node + "/")))}
            if op == "job_verify":
                return {"bad": 0}
            return {}

    def __init__(self, react):
        self.models = {m: argparse.Namespace(name=m, nbytes=int(g * GiB), last_used=0.0, heat=0.0) for m, g in SIZE_GIB.items()}
        self.dram = {m: collections.Counter() for m in SIZE_GIB}        # model -> {vertex: bytes}
        self.hbmp = {m: collections.Counter() for m in SIZE_GIB}        # parked in idle HBM
        self.ssd = {m: set() for m in SIZE_GIB}
        self.pub = {}                                                   # job -> (model, node)
        self.tenant = collections.Counter()                             # vertex -> bytes a tenant holds
        self.hbm_park, self.events, self.poll_ms = {}, [], [0.4, 0.5, 0.9]
        self.arena = {n: {c: 48 for c in range(8)} for n in NODES}
        self.rpc = {n: FakePool._Rpc(self, n) for n in NODES}
        self.react_on, self.calls, self._job = react, collections.Counter(), 0
        self.lock = threading.RLock()

    def dram_vertices(self):
        return [f"{n}/dram/{k}" for n in NODES for k in range(8)]

    def _held(self, vid):
        return sum(m[vid] for m in self.dram.values()) + sum(m[vid] for m in self.hbmp.values())

    def _room(self, vid, budget):
        if "/hbm/" in vid:
            n, _, c = vid.split("/")
            cap = self.arena[n].get(int(c), 0) * GiB if int(c) in self.hbm_park.get(n, ()) else 0
            phys = cap - self.tenant[vid]
        else:
            phys = (NUMA_GIB * (1 - 0.15) - 4) * GiB - self.tenant[vid]
        r = phys - self._held(vid)
        if budget is not None:
            r = min(r, budget.get(vid, 0) - self._held(vid))
        return max(0, int(r))

    def _fast(self, name, nodes=None, tiers=("dram", "hbm")):
        b = 0
        ok = lambda v: nodes is None or v.split("/")[0] in nodes
        if "dram" in tiers:
            b += sum(x for v, x in self.dram[name].items() if ok(v))
        if "hbm" in tiers:
            b += sum(x for v, x in self.hbmp[name].items() if ok(v))
            if any(m == name and (nodes is None or n in nodes) for m, n in self.pub.values()):
                b += self.models[name].nbytes
        return min(b, self.models[name].nbytes)

    def coverage(self, name, tiers=("dram", "hbm"), nodes=None):
        return self._fast(name, nodes, tiers) / self.models[name].nbytes

    def coverage_all(self, tiers):
        return sum(self._fast(m, None, tiers) for m in self.models) / sum(x.nbytes for x in self.models.values())

    def park(self, name, copies=1, tiers=("dram",), prefer=None, pin_nodes=(), avoid_nodes=(), budget=None,
             threads=8, all_or_nothing=False, from_job=None, local_frac=None, within=None):
        with self.lock:
            self.calls["park"] += 1
            scope = set(within) if within else None
            need = self.models[name].nbytes - self._fast(name, scope, tiers)
            verts = [v for v in self.dram_vertices() if "dram" in tiers] + \
                    [f"{n}/hbm/{c}" for n, cs in self.hbm_park.items() for c in sorted(cs) if "hbm" in tiers]
            verts = [v for v in verts if v.split("/")[0] not in avoid_nodes and (scope is None or v.split("/")[0] in scope)]
            room = {v: self._room(v, budget) for v in verts}
            if all_or_nothing and sum(room.values()) < need:
                return None
            placed = 0
            for v in sorted(verts, key=lambda v: (v.split("/")[0] != prefer, -room[v])):
                take = min(room[v], need - placed)
                if take > 0:
                    (self.hbmp if "/hbm/" in v else self.dram)[name][v] += take
                    placed += take
            return {"model": name, "GiB": round(placed / GiB, 2), "s": round(placed / 5e9, 2),
                    "short": int(need - placed > 0), "errors": []}

    def stripe(self, name, nodes=None, budget=None):
        self.ssd[name] |= set(nodes or NODES)

    def rebalance(self, order):
        self.calls["rebalance"] += 1
        for m in order:
            if self.coverage(m) < 0.999:
                self.park(m, tiers=("dram", "hbm"), budget=getattr(self, "budget", None))
                self.events.append({"event": "repair", "t": time.time(), "model": m})

    def touch(self, name):
        self.models[name].heat += 1
        self.models[name].last_used = time.time()

    def restore(self, name, node, cards, allow=("dram", "ssd", "hbm", "file"), strategy="lp", live=True,
                single_source=None, release=True, **kw):
        with self.lock:
            self.calls["restore"] += 1
            B = self.models[name].nbytes
            src_ok = lambda n: single_source is None or n == single_source
            loc = net = 0
            if "dram" in allow:
                loc += sum(b for v, b in self.dram[name].items() if v.startswith(node + "/") and src_ok(node))
                net += sum(b for v, b in self.dram[name].items() if not v.startswith(node + "/") and src_ok(v.split("/")[0]))
            if "hbm" in allow:
                net += sum(b for v, b in self.hbmp[name].items() if src_ok(v.split("/")[0]))
                if any(m == name and n != node and src_ok(n) for m, n in self.pub.values()):
                    net += B
            loc = min(B, loc)
            net = min(B - loc, net)
            slow = B - loc - net
            if slow > 0 and not {"ssd", "file"} & set(allow):
                raise RuntimeError(f"{name}: no source under allow={allow}")
            disks = 1 + (len(self.ssd[name] - {DURABLE[name]}) if "ssd" in allow else 0)
            t = max(loc / 88e9, net / (47e9 * min(4, len(cards))), B / (47e9 * len(cards)), slow / (6.5e9 * disks)) + 0.03
            self._job += 1
            paths = {k: {"bytes": b} for k, b in (("ldram", loc), ("net:0", net), ("file", slow)) if b}
            self.models[name].last_used = time.time()
            return {"model": name, "node": node, "cards": list(cards), "GiB": round(B / GiB, 2), "total_s": round(t, 4),
                    "plan_s": 0.01, "paths": paths, "failed_fetches": 0, "job": f"{name}-{self._job}"}

    def restore_wave(self, items):
        self.calls["restore_wave"] += 1
        return [self.restore(m, n, c, **kw) for m, n, c, kw in items]

    def publish(self, name, node, job):
        self.pub[job] = (name, node)

    def unpublish(self, node, job):
        self.pub.pop(job, None)

    def retire(self, name, node, job, cards, budget=None):
        self.calls["retire"] += 1
        self.unpublish(node, job)
        need = self.models[name].nbytes - self._fast(name)
        r = self.park(name, tiers=("dram", "hbm"), budget=budget) if need > 0 else {"GiB": 0.0}
        left = self.models[name].nbytes - self._fast(name)
        if r["GiB"]:
            self.events.append({"event": "demote", "t": time.time(), "model": name, "node": node, "GiB": r["GiB"]})
        return {"demoted_GiB": r["GiB"], "s": round(r["GiB"] * GiB / 46e9, 3), "left_slow_GiB": round(left / GiB, 2)}

    def make_room(self, node, cards, nbytes):
        pass

    def where(self, model, cards):
        return {}

    def yield_card(self, node, card):
        vid, freed = f"{node}/hbm/{card}", 0
        for m in self.hbmp:
            freed += self.hbmp[m].pop(vid, 0)
        if freed:
            self.events.append({"event": "evict", "t": time.time(), "freed_GiB": {vid: round(freed / GiB, 2)}})

    def drop_model(self, name, tiers=("dram",), nodes=None):
        out = {}
        for v in [v for v in self.dram[name] if nodes is None or v.split("/")[0] in nodes]:
            out[v] = {"freed": self.dram[name].pop(v)}
        return out

    def holds(self, name, vertex):
        return self.dram[name][vertex] > 0

    def pressure(self):
        out = {}
        for (v, t) in list(self.tenant.items()):
            if "/dram/" in v and self._held(v) > 0:
                d = self._held(v) + t - NUMA_GIB * (1 - 0.15) * GiB
                if d > 0:
                    out[v] = int(d)
        return out

    def react(self):
        """Chunk-level yielding of the pool under test: coldest model first, only what is asked."""
        if not self.react_on:
            return
        with self.lock:
            freed = collections.Counter()
            for v, d in self.pressure().items():
                for m in sorted(self.models.values(), key=lambda m: m.heat):
                    take = min(self.dram[m.name][v], d - freed[v])
                    if take > 0:
                        self.dram[m.name][v] -= take
                        freed[v] += take
            for v in [v for v, t in self.tenant.items() if "/hbm/" in v and t > 0]:
                for m in self.hbmp:
                    freed[v] += self.hbmp[m].pop(v, 0)
            freed = {v: round(b / GiB, 2) for v, b in freed.items() if b}
            if freed:
                self.events.append({"event": "evict", "t": time.time(), "freed_GiB": freed})


class FakeTenant:
    def __init__(self, pool, node):
        self.pool, self.node = pool, node

    def call(self, op, **kw):
        p, node, t0 = self.pool, self.node, time.time()
        if op == "dram":
            v, want = f"{node}/dram/{kw['numa']}", kw["gib"] * GiB
            for _ in range(12 if kw.get("patience_s") else 1):
                room = (NUMA_GIB - kw.get("reserve_gib", 16)) * GiB - p._held(v)
                p.tenant[v] = max(0, min(want, room))
                if p.tenant[v] >= want:
                    break
                p.tenant[v] = want                      # what the tenant is pushing for
                p.react()
                room = (NUMA_GIB - kw.get("reserve_gib", 16)) * GiB - p._held(v)
                p.tenant[v] = max(0, min(want, room))
                if p.tenant[v] >= want:
                    break
                time.sleep(0.01)                        # whole-model systems yield from the driver's tick
            return {"held_GiB": int(p.tenant[v] // GiB), "wanted_GiB": kw["gib"], "s": round(time.time() - t0, 3)}
        if op == "hbm":
            v, want = f"{node}/hbm/{kw['card']}", kw["gib"] * GiB
            if want == 0:
                p.tenant.pop(v, None)
                return {"held_GiB": 0}
            p.tenant[v] = want
            p.react()
            blocked = p.arena[node].get(int(kw["card"]), 0) > 0 and any(p.hbmp[m][v] for m in p.hbmp)
            held = 16 if blocked else kw["gib"]
            if p.arena[node].get(int(kw["card"]), 0) > 0 and not blocked and p.react_on:
                p.arena[node][int(kw["card"])] = 0      # the pool gave the empty arena back
            return {"held_GiB": held, "wanted_GiB": kw["gib"], "waited_s": 0.0, "s": round(time.time() - t0, 3)}
        if op == "stop":
            return {"job": kw["name"], "found": True, "bytes": 0, "GBps": 0.0}
        return {"job": kw.get("name"), "t_start": t0}


# ---------------------------------------------------------------- main
def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def summarize(recs):
    out = {}
    groups = {"all": recs, "misses": [r for r in recs if r["kind"] != "ipc_hit"]}
    for ph in ("calm", "burst"):
        groups[f"{ph}:all"] = [r for r in recs if r["phase"] == ph]
        groups[f"{ph}:misses"] = [r for r in recs if r["phase"] == ph and r["kind"] != "ipc_hit"]
    for k, rs in groups.items():
        lat = [r["latency_s"] for r in rs]
        if lat:
            out[k] = {"n": len(rs), "mean_s": round(sum(lat) / len(lat), 3), "p50_s": pct(lat, .5), "p95_s": pct(lat, .95),
                      "p99_s": pct(lat, .99), "max_s": max(lat), "kinds": dict(collections.Counter(r["kind"] for r in rs))}
    return out


def run(a):
    apply_catalog(a.catalog)
    os.makedirs(a.out, exist_ok=True)
    # ---- the schedule: one file per out dir, the same for every system that runs into it
    series, container = tenant_series()
    kinds = [k for k in a.kinds.split(",") if k]
    sched = make_schedule(a.seed, a.requests, a.calm, a.burst_load, a.amp, series, kinds,
                          a.ssd_nodes.split(","), a.hbm_gib, a.dram_min)
    blob = json.dumps(sched, sort_keys=True)
    sha = hashlib.sha256(blob.encode()).hexdigest()
    sp = os.path.join(a.out, "burst_schedule.json")
    meta = {"seed": a.seed, "requests": a.requests, "calm": a.calm, "burst_load": a.burst_load, "amp": a.amp,
            "kinds": kinds, "tenant_container": container, "sha256": sha, **schedule_stats(sched, a.requests, a.calm)}
    if os.path.exists(sp):
        old = json.load(open(sp))
        if old["meta"]["sha256"] != sha:
            raise SystemExit(f"{sp} holds a different schedule ({old['meta']['sha256'][:12]} vs {sha[:12]}): "
                             "the systems of one comparison must share it; use another --out")
    else:
        json.dump({"meta": meta, "events": sched}, open(sp, "w"), indent=1)
    out_dir = os.path.join(a.out, a.system)
    os.makedirs(out_dir, exist_ok=True)
    logf = open(os.path.join(out_dir, "run.log"), "a")
    llock = threading.Lock()
    LOUD = ("request", "summary", "burst_start", "burst_end", "evict_whole", "setup_done", "adapter_fallback",
            "restore_failed", "wave_failed", "burst_failed", "prewarm", "run_start", "graph_stats")

    def log(**k):
        k.setdefault("t", round(time.time(), 3))
        line = json.dumps(k)
        with llock:
            logf.write(line + "\n")
            logf.flush()
            if k.get("event") in LOUD:
                print(line[:300], flush=True)

    log(event="run_start", system=a.system, args=vars(a))
    log(event="schedule", **meta)

    # ---- pool
    if a.dry:
        pool = FakePool(react=a.system.startswith("fluidpool"))
        tenant_client = lambda node: FakeTenant(pool, node)
        a.tick_s, a.settle, a.during_delay = 0.01, 0.0, 0.005
    else:
        from fluidckpt.live.graph import Policy
        from fluidckpt.live.noded import Client
        from fluidckpt.live.pool import Pool
        # The four nodes are ours for this run: clean page cache counts as room (0.9 of it).
        pool = Pool(NODES, policy=Policy(dram_headroom_frac=a.headroom, dram_reclaim_frac=0.9), log=log)
        tenant_client = lambda node: Client(NODES[node], 18770)
        for n in NODES:
            pool.rpc[n].call("reset")
        pool.start(react=(a.system.startswith("fluidpool")))
        for m in CARDS:
            pool.register(m, os.path.join(a.manifests, m + ".json"), DURABLE[m])
    A = Adapters(pool, log, tenant_client)
    for n in NODES:
        A.tenant(n, "clear")
    sysm = SYSTEMS[a.system](A, a, log)
    if a.dry and a.system.startswith("fluidpool"):
        pool.budget = None
    # ---- arenas: every serving card has one; idle cards only for a system that parks in them
    for n in NODES:
        for c in SERVE[n]:
            A.ensure_arena(n, c, a.arena)
        for c in IDLE.get(n, []):
            A.ensure_arena(n, c, a.arena if sysm.uses_idle_hbm else 0)
    if a.warm_arenas > 0 and hasattr(pool, "warm_arenas"):
        log(event="arenas_warmed", out=pool.warm_arenas(seconds=a.warm_arenas))
    t0 = time.time()
    sysm.setup()
    if not a.dry and hasattr(pool, "wait_pinned"):
        # Slabs are pinned by a background thread; an unpinned slab feeds H2D at pageable speed.
        tp = time.time()
        pool.wait_pinned()
        log(event="pinned", s=round(time.time() - tp, 1))
    if a.dry and a.system.startswith("fluidpool"):
        pool.budget = sysm.budget
    log(event="setup_done", system=a.system, s=round(time.time() - t0, 1),
        slab_GiB={n: round(A.noded(n, "stat")["slab_bytes"] / GiB, 1) for n in NODES},
        dram_coverage={m: round(A.coverage(m, ("dram",)), 3) for m in CARDS},
        fast_coverage={m: round(A.coverage(m, ("dram", "hbm")), 3) for m in CARDS},
        fast_coverage_all=round(A.coverage_all(("dram", "hbm")), 4))
    if not a.dry and hasattr(pool, "warm_paths"):
        for n in NODES:
            log(event="paths_warmed", node=n, s=pool.warm_paths(n, SERVE[n]))

    D = Driver(A, a, log, sysm, sched)
    try:
        if not a.no_warmup:
            wu = warmup_requests()
            D.serve(wu, len(wu), lambda i: "warmup", measured=False)
            log(event="warmup_done", resident={n: list(D.active[n]) for n in NODES}, **D.warm_servable())
        n_warm = len(D.recs)
        served = D.serve(requests(), a.requests, lambda i: "calm" if i < a.calm else "burst", measured=True)
    finally:
        D.bursts.stop_all()
        for n in NODES:
            try:
                A.tenant(n, "clear")
            except Exception:                            # noqa: BLE001
                pass
    recs = D.recs[n_warm:]
    poll = A.poll_ms()
    kinds_ev = collections.Counter(e.get("event", "?") for e in A.events() + sysm.events)
    gs = {"poll_ms_p50": pct(poll, .5), "poll_ms_p99": pct(poll, .99), "polls": len(poll), "events": dict(kinds_ev),
          "slab_GiB": {n: round(A.noded(n, "stat")["slab_bytes"] / GiB, 1) for n in NODES},
          "fast_coverage_all": round(A.coverage_all(("dram", "hbm")), 4)}
    log(event="graph_stats", **gs)
    for n in NODES:                                      # leave the nodes as we found them
        for m in list(D.active[n]):
            cards, job = D.active[n].pop(m)
            if job:
                if sysm.publishes:
                    A.unpublish(n, job)
                A.noded(n, "job_release", job=job)
        A.noded(n, "reset")
        for c in range(8):
            A.ensure_arena(n, c, a.arena)
    burst_recs = [r for r in recs if r["phase"] == "burst"]
    st_b = [s for s in D.states if s["phase"] == "burst"]
    summary = {"system": a.system, "served": served, "skipped": D.skipped, "hits": sum(r["kind"] == "ipc_hit" for r in recs),
               "misses": sum(r["kind"] != "ipc_hit" for r in recs), "bursts": len(D.bursts.done),
               "latency": summarize(recs), "graph": gs,
               "warm_servable_burst": {"pairs_mean": round(sum(s["pairs"] for s in st_b) / max(len(st_b), 1), 4),
                                       "pairs_min": min((s["pairs"] for s in st_b), default=None),
                                       "bytes_mean": round(sum(s["bytes"] for s in st_b) / max(len(st_b), 1), 4),
                                       "bytes_min": min((s["bytes"] for s in st_b), default=None)},
               "cold_burst": sum("cold" in r["kind"] for r in burst_recs),
               "calls": dict(getattr(pool, "calls", {})) if a.dry else None}
    log(event="summary", **summary)
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    if not a.dry:
        pool.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("system", choices=list(SYSTEMS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifests", default="")
    ap.add_argument("--requests", type=int, default=90, help="measured requests, hits and misses, in trace order")
    ap.add_argument("--calm", type=int, default=15, help="leading requests without any burst")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--burst-load", type=float, default=2.0, help="mean number of bursts active at a time")
    ap.add_argument("--kinds", default=",".join(KIND_W), help="burst kinds to draw from")
    ap.add_argument("--cap", type=float, default=30, help="GiB of DRAM per NUMA vertex (x8 per node), same for all systems")
    ap.add_argument("--headroom", type=float, default=0.15, help="share of each NUMA node left to others")
    ap.add_argument("--amp", type=float, default=40, help="GiB per NUMA node a DRAM tenant asks for at trace level 1.0")
    ap.add_argument("--dram-min", type=int, default=8, help="smallest DRAM ask per NUMA node, GiB")
    ap.add_argument("--dram-reserve", type=float, default=4, help="GiB a DRAM tenant always leaves on a NUMA node")
    ap.add_argument("--patience", type=float, default=8.0, help="seconds a tenant waits for memory before giving up")
    ap.add_argument("--hbm-gib", type=int, default=56, help="GiB an HBM tenant asks for per card")
    ap.add_argument("--hbm-idle-yield", choices=["graph", "driver"], default="graph",
                    help="idle-card HBM bursts: the pool notices by itself, or the driver revokes the card")
    ap.add_argument("--ssd-nodes", default="node28,node29,node30", help="nodes whose NVMe tenants may load (node27's is the shared home disk)")
    ap.add_argument("--stripe-nodes", default="node28,node29,node30", help="disks fluidpool stripes its slow tier over")
    ap.add_argument("--local-frac", default="auto", help="fluidpool: share of a single-home model kept on its home's DRAM")
    ap.add_argument("--janus-plan", choices=["static", "rr", "live"], default="static")
    ap.add_argument("--catalog", choices=["base", "big"], default="base",
                    help="big: + 7 byte-distinct 35B/8B variants (VARIANTS), ~1.2 TiB in all")
    ap.add_argument("--relay-policy", choices=["idle", "loose"], default="idle",
                    help="fluidpool_relay: which near cards may be borrowed (Pool.relay_pick)")
    ap.add_argument("--janus-order", choices=["d2d_first", "h2d_first"], default="d2d_first")
    ap.add_argument("--ipc-hit-ms-per-card", type=float, default=6.0, help="calibrated IPC attach cost of a hit")
    ap.add_argument("--wave", type=int, default=3)
    ap.add_argument("--arena", type=float, default=48, help="GiB of HBM arena per card the daemons were started with")
    ap.add_argument("--warm-arenas", type=float, default=0, help="seconds of arena warm-up traffic before setup (0: skip)")
    ap.add_argument("--verify-every", type=int, default=9)
    ap.add_argument("--during-delay", type=float, default=0.2, help="seconds into a wave at which 'during' bursts fire")
    ap.add_argument("--settle", type=float, default=0.5)
    ap.add_argument("--tick-s", type=float, default=0.3)
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--dry", action="store_true", help="no hardware: in-file pool/daemon/tenant model")
    run(ap.parse_args())
