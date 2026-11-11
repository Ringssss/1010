"""Fluid simulator of keep-alive and restore on the real four-node graph with dynamic tenants.

World: topology from recorded probe inventories (graph_r02), nominal capacities from
live/graph.py. Other tenants are seeded on/off processes:
  * bandwidth bursts on every resource family (fraction of nominal while "on");
  * HBM tenants per GPU (40-60 GiB while on); DRAM pressure per NUMA node.
Observability matches the probes: PCIe seen with ~1 s lag, IB/NVMe/free bytes with ~0.1 s lag;
NUMA memory bandwidth and GPU P2P switch load are never observed (no cheap counter), so the live
policy plans them at nominal, exactly like the real system would.

Models alternate idle -> restore request -> serving -> park. A restore is a set of path flows; all
flows of all concurrent restores share resources by max-min fairness (model/fluid.py) under the
*true* residual capacities, advanced in dt steps. Planning time (wall clock of the planner call)
is charged to the restore.

Policies (each runs in its own world with the same seed):
  P0  SGLang-like: on park, back the weights up into pinned DRAM of the serving node (local NUMA
      first, spilling to the node's other NUMA nodes) if the whole shard fits; resume only on the
      same GPUs over one fixed route; otherwise cold load from the durable copy.
  P1  static: global placement LP and fetch LP with nominal capacities; no reaction afterwards.
  P2  live: placement and fetch LP on the observed snapshot (several destination scenarios),
      periodic replanning of unstarted chunks when the snapshot changed, work stealing.
  P2f ablation: P2's information and replanning, but per-chunk fastest single path.
  P1s ablation: P1 (nominal capacities, no reaction) plus P2's multi-destination placement, to
      separate the value of live information from the value of planning for several landings.
  P2n variant: placement like P1s (nominal: a park decision lives for minutes, a burst for
      seconds), fetch like P2 (live snapshot, replanning, work stealing).
Parking hands the vacated GPUs to another workload (model B) for Exp(120 s) with 40-60 GiB, so
neither policy can keep weights on the GPUs it just released; P0 resumes in place only after B
leaves. Evictions (a tenant needs space we hold) drop replicas; the durable copy always remains.
"""
import bisect
import math
import random
import time
from collections import defaultdict

from fluidckpt.live.flow import plan_fetch
from fluidckpt.live.graph import GiB, LiveGraph
from fluidckpt.live.placement import KeepAlivePool, park
from fluidckpt.live.types import ResourceState, Snapshot
from fluidckpt.model.fluid import maxmin_rates

GB = 1e9
LEVELS = {
    #            bw duty, bw intensity, hbm duty, dram duty
    "none":     (0.0, (0.0, 0.0), 0.0, 0.0),
    "moderate": (0.3, (0.3, 0.7), 0.3, 0.3),
    "heavy":    (0.6, (0.5, 0.95), 0.5, 0.5),
}
OBSERVED = {"pcie_in": 1.0, "pcie_out": 1.0, "ib_tx": 0.1, "ib_rx": 0.1, "ib_node_tx": 0.1,
            "ib_node_rx": 0.1, "nvme_rd": 0.1, "nvme_wr": 0.1}          # family -> sample lag (s)


def family(rid):
    return rid.split("/")[1]


def onoff(rng, horizon, duty, mean_on, value):
    """Events [(t0, t1, v)] of an on/off process with the given duty cycle."""
    if duty <= 0:
        return []
    mean_off = mean_on * (1 - duty) / duty
    t, out = rng.expovariate(1 / mean_off), []
    while t < horizon:
        d = rng.expovariate(1 / mean_on)
        out.append((t, t + d, value()))
        t += d + rng.expovariate(1 / mean_off)
    return out


class Series:
    def __init__(self, events):
        self.ev = sorted(events)
        self.starts = [e[0] for e in self.ev]

    def at(self, t):
        i = bisect.bisect_right(self.starts, t) - 1
        return self.ev[i][2] if i >= 0 and self.ev[i][1] > t else 0.0


class World:
    def __init__(self, topo, level, seed, horizon, base_hbm_used):
        duty, (lo, hi), hduty, dduty = LEVELS[level]
        rng = random.Random(seed)
        self.topo = topo
        self.bg = {}
        for rid, r in sorted(topo.resources.items()):
            nominal = r.nominal_GBps
            self.bg[rid] = Series(onoff(rng, horizon, duty, 3.0, lambda: nominal * rng.uniform(lo, hi)))
        self.hbm = {}
        for vid, v in sorted(topo.vertices.items()):
            if v.kind == "hbm":
                ev = onoff(rng, horizon, hduty, 300.0, lambda: rng.uniform(40, 60) * GiB)
                self.hbm[vid] = (base_hbm_used.get(vid, 0), Series(ev))
        self.dram = {vid: Series(onoff(rng, horizon, dduty, 300.0, lambda: rng.uniform(10, 20) * GiB))
                     for vid, v in sorted(topo.vertices.items()) if v.kind == "dram"}
        self.incoming = defaultdict(list)    # vid -> [(t0, t1, bytes)]: workloads that took freed GPUs
        self.irng = random.Random(seed + 7)

    def take_over(self, gpus, t):
        """Parking releases GPUs for someone else (model B): it holds them for a while."""
        d, b = self.irng.expovariate(1 / 120.0), self.irng.uniform(40, 60) * GiB
        for vid in gpus:
            self.incoming[vid].append((t, t + d, b))

    def residual(self, rid, t):
        nominal = self.topo.resources[rid].nominal_GBps
        return max(nominal - self.bg[rid].at(t), 0.05 * nominal)

    def tenant_bytes(self, vid, t):
        v = self.topo.vertices[vid]
        if v.kind == "hbm":
            base, s = self.hbm[vid]
            extra = sum(b for t0, t1, b in self.incoming.get(vid, ()) if t0 <= t < t1)
            return min(v.capacity_bytes, max(base, s.at(t)) + extra) + 2 * GiB   # + allocator headroom
        if v.kind == "dram":
            return v.capacity_bytes - 25 * GiB + self.dram[vid].at(t)  # we may use <= 25 GiB/NUMA
        return 0

    def snapshot(self, t, observe):
        """observe=False: nominal (static policy); True: what the probes would have shown."""
        rs = {}
        if observe:
            for rid in self.topo.resources:
                lag = OBSERVED.get(family(rid))
                if lag is not None:
                    rs[rid] = ResourceState(self.bg[rid].at(max(0.0, t - lag)), t - lag)
        return Snapshot(self.topo, {}, rs, int(t * 1000), t)


class Flow:
    __slots__ = ("restore", "path", "queue", "cap")

    def __init__(self, restore, path, queue):
        self.restore, self.path, self.queue = restore, path, queue      # queue: [[cid, bytes_left]]
        self.cap = path.single_stream_GBps


class Sim:
    def __init__(self, graph, models, policy, level, seed, horizon=1200.0, dt=0.05, base_hbm_used=None,
                 replan_s=0.5, chunk_bytes=256 << 20):
        self.g, self.topo = graph, graph.topology
        self.policy, self.dt, self.horizon, self.replan_s = policy, dt, horizon, replan_s
        self.world = World(self.topo, level, seed, horizon, base_hbm_used or {})
        self.rng = random.Random(seed + 1)
        self.pool = KeepAlivePool()
        self.models = {}
        for m in models:
            per = m["bytes"] // m["tp"]
            shards = {sh: [(f"{m['name']}:{sh}:{i}", min(chunk_bytes, per - i * chunk_bytes))
                           for i in range(math.ceil(per / chunk_bytes))] for sh in range(m["tp"])}
            self.models[m["name"]] = dict(m, shards=shards, sizes={c: n for cs in shards.values() for c, n in cs},
                                          state="idle", next=self.rng.expovariate(1 / 30), home=m["home"],
                                          serving_on=None)
        self.active = {}            # vertex -> bytes used by serving/restoring models
        self.flows, self.restores, self.records, self.evicted = [], {}, [], 0
        self.plan_wall = []
        for name in self.models:    # every model starts parked from its home GPUs
            self._park(name, 0.0)

    # ---- capacity bookkeeping ----------------------------------------------------------------
    def free_for_keepalive(self, t):
        held = self.pool.held()
        out = {}
        for vid, v in self.topo.vertices.items():
            if v.kind == "ssd":
                continue
            out[vid] = max(0, v.capacity_bytes - self.world.tenant_bytes(vid, t) - self.active.get(vid, 0) - held.get(vid, 0))
        return out

    def enforce(self, t):
        """Tenants and active models have priority over keep-alive replicas."""
        held = self.pool.held()
        for vid, b in held.items():
            v = self.topo.vertices[vid]
            over = self.world.tenant_bytes(vid, t) + self.active.get(vid, 0) + b - v.capacity_bytes
            if over > 0:
                freed, _ = self.pool.evict(vid, over)
                self.evicted += freed

    # ---- lifecycle ---------------------------------------------------------------------------
    def _park(self, name, t):
        m = self.models[name]
        for vid in m["serving_on"] or []:
            self.active[vid] = self.active.get(vid, 0) - m["bytes"] // m["tp"]
        if m["serving_on"]:
            self.world.take_over(m["serving_on"], t)
        m["serving_on"], m["state"] = None, "idle"
        m["next"] = t + self.rng.expovariate(1 / 90)
        home = m["home"]
        free = self.free_for_keepalive(t)
        t0 = time.perf_counter()
        if self.policy == "P0":
            # Pinned CPU backup of the engine process: local NUMA first, spilling to the other NUMA
            # nodes of the same machine; all or nothing per shard (a partial backup cannot resume).
            replicas = {}
            for sh, cs in m["shards"].items():
                g = self.topo.vertices[home[sh]]
                order = sorted((v for v in self.topo.vertices.values() if v.node == g.node and v.kind == "dram"),
                               key=lambda v: (v.numa != g.numa, v.numa))
                need, fill = sum(n for _, n in cs), []
                trial = {v.id: free.get(v.id, 0) for v in order}
                for cid, n in cs:
                    v = next((v.id for v in order if trial[v.id] >= n), None)
                    if v is None:
                        fill = None
                        break
                    trial[v] -= n
                    fill.append((cid, v))
                if fill is None:
                    for cid, _ in cs:
                        replicas[cid] = [m["durable"]]
                else:
                    free.update(trial)
                    for cid, v in fill:
                        replicas[cid] = [v, m["durable"]]
        else:
            observe = self.policy not in ("P1", "P1s", "P2n")
            cands = [v for v in free if free[v] > 0]
            scen = [(1.0, dict(enumerate(home)))]
            if self.policy != "P1":                       # also prepare for landing elsewhere
                alt = self._alternatives(m, t, exclude=home[0].split("/")[0])
                scen = [(0.6, dict(enumerate(home)))] + [(0.4 / len(alt), dict(enumerate(a))) for a in alt]
            replicas, _ = park(self.world.snapshot(t, observe), self.g.paths, m["shards"], m["durable"],
                               scen, m["bytes"], free, cands)
        self.plan_wall.append(("park", time.perf_counter() - t0))
        self.pool.add(name, replicas, m["sizes"], m["durable"], now=t)

    def _alternatives(self, m, t, exclude, k=2):
        out = []
        for node in sorted({v.node for v in self.topo.vertices.values()}):
            if node == exclude:
                continue
            gpus = self._free_gpus(m, t, node)
            if gpus:
                out.append(gpus)
        return out[:k] or [m["home"]]

    def _free_gpus(self, m, t, node):
        need = m["bytes"] // m["tp"]
        ok = [vid for vid, v in sorted(self.topo.vertices.items()) if v.kind == "hbm" and v.node == node
              and v.capacity_bytes - self.world.tenant_bytes(vid, t) - self.active.get(vid, 0) >= need]
        return ok[:m["tp"]] if len(ok) >= m["tp"] else None

    def _request(self, name, t):
        m = self.models[name]
        dst = m["home"] if self._fits(m, m["home"], t) else None
        if dst is None:
            for node in sorted({v.node for v in self.topo.vertices.values()}):
                dst = self._free_gpus(m, t, node)
                if dst:
                    break
        if not dst:
            m["next"] = t + 1.0                           # no GPUs: retry later
            m.setdefault("waited", 0.0)
            m["waited"] += 1.0
            return
        need = m["bytes"] // m["tp"]
        for vid in dst:
            self.active[vid] = self.active.get(vid, 0) + need
        self.enforce(t)
        entry = self.pool.models.get(name)
        replicas = entry["replicas"] if entry else {c: [m["durable"]] for c in m["sizes"]}
        if self.policy == "P0" and dst != m["home"]:
            replicas = {c: [m["durable"]] for c in m["sizes"]}           # backup is bound to the old engine
        self.pool.drop(name)
        demands = [(cid, n, dst[sh]) for sh, cs in m["shards"].items() for cid, n in cs]
        r = {"name": name, "t0": t, "dst": dst, "demands": demands, "replicas": replicas, "done": set(),
             "from_durable": sum(n for cid, n, _ in demands if replicas[cid] == [m["durable"]]),
             "bytes": m["bytes"], "waited": m.pop("waited", 0.0)}
        self.restores[name] = r
        m["state"] = "restoring"
        self._plan(r, t)

    def _fits(self, m, gpus, t):
        need = m["bytes"] // m["tp"]
        return all(self.topo.vertices[g].capacity_bytes - self.world.tenant_bytes(g, t) - self.active.get(g, 0) >= need
                   for g in gpus)

    def _plan(self, r, t, pending=None):
        demands = pending if pending is not None else r["demands"]
        for cid, _, dst in demands:                       # already resident in the target HBM
            if dst in r["replicas"][cid]:
                r["done"].add(cid)
        if self.policy == "P0":
            def paths(s, d):
                ps = self.g.paths(s, d)
                return ps[:1]                              # one fixed route, like a plain loader
            strategy, snap = "fastest", self.world.snapshot(t, False)
        else:
            paths = self.g.paths
            strategy = "fastest" if self.policy == "P2f" else "lp"
            snap = self.world.snapshot(t, self.policy not in ("P1", "P1s"))
        t0 = time.perf_counter()
        plan = plan_fetch(snap, demands, r["replicas"], paths, strategy)
        wall = time.perf_counter() - t0
        self.plan_wall.append(("fetch", wall))
        r["plan_s"] = r.get("plan_s", 0.0) + (wall if self.policy != "P0" else 0.0)
        r["snap"], r["last_plan"] = snap, t
        r["not_before"] = t + (wall if self.policy != "P0" else 0.0)
        by_path = defaultdict(list)
        for tr in sorted(plan.transfers, key=lambda x: x.order):
            by_path[tr.path].append([tr.chunk_id, tr.nbytes])
        for p, q in by_path.items():
            self.flows.append(Flow(r["name"], p, q))
        if not plan.transfers:
            self._complete(r, t)

    def _complete(self, r, t):
        m = self.models[r["name"]]
        self.records.append({"name": r["name"], "policy": self.policy, "t": r["t0"], "restore_s": t - r["t0"],
                             "bytes": r["bytes"], "from_durable_frac": r["from_durable"] / r["bytes"],
                             "plan_s": r.get("plan_s", 0.0), "waited_s": r["waited"], "dst": r["dst"]})
        del self.restores[r["name"]]
        m["state"], m["serving_on"], m["home"] = "serving", r["dst"], r["dst"]
        m["next"] = t + self.rng.expovariate(1 / 60)

    # ---- dynamics ----------------------------------------------------------------------------
    def _replan_and_steal(self, t):
        if self.policy not in ("P2", "P2f", "P2n"):
            return
        for name, r in self.restores.items():
            mine = [f for f in self.flows if f.restore == name]
            if t - r["last_plan"] >= self.replan_s and self._changed(r, t):
                pending = []
                for f in mine:                             # keep each path's chunk in progress
                    for cid, left in f.queue[1:]:
                        pending.append((cid, left, f.path.dst))
                    del f.queue[1:]
                if pending:
                    self._plan(r, t, pending)
                r["last_plan"] = t
            if self.policy not in ("P2", "P2n"):
                continue
            idle = [f for f in mine if not f.queue]
            for f in idle:                                 # work stealing between paths of a restore
                victims = [v for v in mine if len(v.queue) > 2 and v.path.dst == f.path.dst]
                victims.sort(key=lambda v: -sum(b for _, b in v.queue))
                for v in victims:
                    movable = [c for c in v.queue[1:] if f.path.src in r["replicas"][c[0]]]
                    take = movable[len(movable) // 2:]
                    if take:
                        for c in take:
                            v.queue.remove(c)
                        f.queue.extend(take)
                        break

    def _changed(self, r, t, rel=0.2):
        old, new = r["snap"], self.world.snapshot(t, True)
        for rid in new.resources:
            a, b = old.residual_GBps(rid), new.residual_GBps(rid)
            if abs(a - b) > rel * max(a, 1.0):
                return True
        return False

    def step(self, t):
        active = [f for f in self.flows if f.queue and t >= self.restores[f.restore]["not_before"]]
        if active:
            caps = {}
            for f in active:
                for rid in f.path.resources:
                    if rid not in caps:
                        caps[rid] = self.world.residual(rid, t)
            rates = maxmin_rates([(f.cap, f.path.resources) for f in active], caps)
            for f, rate in zip(active, rates):
                budget = rate * GB * self.dt
                while budget > 0 and f.queue:
                    head = f.queue[0]
                    use = min(budget, head[1])
                    head[1] -= use
                    budget -= use
                    if head[1] <= 0:
                        f.queue.pop(0)
                        self.restores[f.restore]["done"].add(head[0])
        self.flows = [f for f in self.flows if f.queue]
        for name, r in list(self.restores.items()):
            if len(r["done"]) == len(r["demands"]) and not any(f.restore == name for f in self.flows):
                self._complete(r, t + self.dt)
        self._replan_and_steal(t)

    def run(self):
        t, last_enforce = 0.0, -1.0
        while t < self.horizon:
            for name, m in self.models.items():
                if m["next"] <= t:
                    if m["state"] == "idle":
                        self._request(name, t)
                    elif m["state"] == "serving":
                        self._park(name, t)
            if t - last_enforce >= 1.0:
                self.enforce(t)
                last_enforce = t
            self.step(t)
            t += self.dt if (self.flows or self.restores) else 0.5
        self.unfinished = len(self.restores)
        return self.records
