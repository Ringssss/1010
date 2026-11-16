"""Four serving nodes, the same DRAM budget for every system: shared pool vs per-node islands.

Catalog (853 GiB of real weights): GLM-5.3 in full (705 GiB, served across 16 cards on node28 +
node30), Qwen3.6-35B (67 GiB), Clover-35B (65 GiB), a 9G 8B model (15 GiB), Qwen3-0.6B (1.4 GiB).
The 35B models and the 8B model are requested on several nodes (replicas of the same weights).

Requests: model sequence of clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv; its nine
most requested models map onto the catalog by popularity rank, and each request names a node
(round-robin over the model's homes). node27/node29 serve on 4 cards, node28/node30 on 8; a request
for a model that is not active there is a restore and the LRU models of that node make room.
Restores that touch different nodes are issued at the same time (up to --wave), as they would be.

Every system gets the same DRAM: --cap GiB per NUMA vertex, i.e. 8 x cap per node.
  fluidpool  one pool over the four nodes (+ one NVMe copy of the small models on node29); chunks
             go wherever there is room; restores land straight in HBM over RDMA from any holder and
             over H2D from local chunks at the same time; reacts to tenants chunk by chunk.
  janus      each node keeps whole models it served in its own pinned memory (LRU, same budget);
             D2D fork from ONE active instance of the model on another node; else re-read.
  sglang     each engine (card group) keeps the one model it last ran, in its node's pinned memory.

Tenants (other users) follow a container series of pod_memory_util_anon.csv and appear in phases:
DRAM on two serving nodes, NVMe traffic on the durable disks, RDMA traffic into two nodes, HBM
(two serving cards on node27 and node29 are taken away), and all of them together.
"""
import argparse
import collections
import csv
import json
import os
import threading
import time

from fluidckpt.live.graph import Policy
from fluidckpt.live.noded import Client
from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
SERVE = {"node27": [0, 1, 2, 3], "node29": [0, 1, 2, 3], "node28": list(range(8)), "node30": list(range(8))}
CARDS = {"9g-8b": 1, "qwen36-35b": 2, "qwen3-0.6b": 1, "clover-35b": 2, "glm53-n28": 8, "glm53-n30": 8}
DURABLE = {"9g-8b": "node28", "qwen36-35b": "node28", "qwen3-0.6b": "node28", "clover-35b": "node30",
           "glm53-n28": "node28", "glm53-n30": "node30"}
HOMES = {"9g-8b": ["node27", "node29", "node28", "node30"],
         "qwen36-35b": ["node27", "node29", "node27", "node29", "node28", "node30"],
         "qwen3-0.6b": ["node27", "node29"],
         "clover-35b": ["node29", "node27", "node30"]}
RANK = ["9g-8b", "qwen36-35b", "qwen3-0.6b", "clover-35b", "glm53", "qwen36-35b", "clover-35b", "glm53", "9g-8b"]
PARK_ORDER = ["9g-8b", "qwen36-35b", "qwen3-0.6b", "clover-35b", "glm53-n28", "glm53-n30"]
TRACE = "/home/zhujianian/clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv"
TENANT = "/home/zhujianian/zhujianian/fluidpool_trace/pod_memory_util_anon.csv"


def requests():
    """[(label, [(model, node)])] in trace order."""
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
            out.append(("glm53", [("glm53-n28", "node28"), ("glm53-n30", "node30")]))
        else:
            node = HOMES[m][k[m] % len(HOMES[m])]
            k[m] += 1
            out.append((f"{m}@{node}", [(m, node)]))
    return out


def tenant_series(points):
    by = collections.defaultdict(list)
    for r in csv.DictReader(open(TENANT)):
        by[r["container_ip"]].append((float(r["timestamp_anon"]), float(r["value"])))
    best, best_sd = None, -1
    for k, v in by.items():
        if len(v) >= 500:
            xs = [x for _, x in v]
            mu = sum(xs) / len(xs)
            sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
            if sd > best_sd:
                best, best_sd = k, sd
    xs = [x for _, x in sorted(by[best])]
    lo, hi = min(xs), max(xs)
    return [(xs[int(i * len(xs) / points)] - lo) / max(hi - lo, 1e-9) for i in range(points)], best


PHASES = [
    {"name": "calm"},
    {"name": "dram", "dram": ["node28", "node27"]},
    {"name": "ssd", "ssd": ["node28", "node30"]},
    {"name": "ib", "ib": [("node28", "node29", [0, 1]), ("node27", "node30", [2, 3])]},
    {"name": "hbm", "hbm": {"node27": [2, 3], "node29": [2, 3]}},
    {"name": "all", "dram": ["node28", "node27"], "ssd": ["node28", "node30"],
     "ib": [("node28", "node29", [0, 1]), ("node27", "node30", [2, 3])], "hbm": {"node27": [2, 3], "node29": [2, 3]}},
]


class Bursts:
    def __init__(self, amp, log):
        self.amp, self.log, self.level = amp, log, {}

    def t(self, node):
        return Client(NODES[node], 18770)

    def clear(self):
        for n in NODES:
            self.t(n).call("clear")
        self.level = {}

    def start(self, ph):
        for n in ph.get("ssd", []):
            self.t(n).call("ssd", mode="write", seconds=7200, streams=4)
            self.t(n).call("ssd", mode="read", seconds=7200, streams=4)
        for dst, src, ports in ph.get("ib", []):
            self.t(dst).call("ib", peer=NODES[src], ports=ports, seconds=7200)
        for n, cards in ph.get("hbm", {}).items():
            for c in cards:
                self.t(n).call("hbm", card=c, gib=56)
        self.log(event="burst_start", phase=ph["name"])

    def dram(self, ph, level, tick=None):
        gib = int(round(level * self.amp))
        for node in ph.get("dram", []):
            if self.level.get(node) == gib:
                continue
            t0, held = time.time(), {}

            def one(nu):
                held[nu] = self.t(node).call("dram", numa=nu, gib=gib, reserve_gib=4, patience_s=8.0)["held_GiB"]
            th = [threading.Thread(target=one, args=(nu,)) for nu in range(8)]
            [x.start() for x in th]
            while any(x.is_alive() for x in th):         # the tenant waits for memory; whole-model
                if tick:                                 # systems get to yield while it does
                    tick()
                time.sleep(0.3)
            [x.join() for x in th]
            self.level[node] = gib
            self.log(event="tenant_dram", node=node, wanted_GiB_each=gib, held_GiB_total=sum(held.values()),
                     s=round(time.time() - t0, 2))


# ---------------------------------------------------------------- systems
class System:
    name = "?"
    publishes = True

    def __init__(self, pool, cap, log):
        self.pool, self.log = pool, log
        V = pool.topology.vertices
        self.cap = {vid: cap for vid, v in V.items() if v.kind == "dram"}

    def setup(self):
        pass

    def tick(self):
        pass

    def background(self):
        pass

    def deactivate(self, m, node, cards, job):
        pass

    def _go(self, m, node, cards, kind, **kw):
        r = self.pool.restore(m, node, cards, release=False, **kw)
        r["kind"] = kind
        return r

    def local_warm(self, m, node):
        return self.pool.coverage(m, tiers=("dram",), nodes={node}) > 0.9999

    def keep_local(self, m, node, cards, job):
        """Whole model into this node's pinned memory, straight from the HBM it is leaving."""
        p = self.pool
        others = tuple(n for n in NODES if n != node)
        return p.park(m, copies=1, tiers=("dram",), pin_nodes=(node,), budget=self.cap, avoid_nodes=others,
                      all_or_nothing=True, from_job=(node, job, p.where(p.models[m], cards)))

    def react_whole(self):
        """Whole-model systems under pressure: a squeezed NUMA vertex costs every model with bytes
        on it (on that node), oldest first, until the deficit is covered."""
        p = self.pool
        sq = dict(p.pressure())
        lost = []
        for m in sorted(p.models.values(), key=lambda m: m.last_used):
            if not sq:
                break
            for node in {v.split("/")[0] for v in sq}:
                hit = [v for v in sq if v.startswith(node + "/") and any(v in p.rep[c.id] for c in m.chunks[::7])]
                if not hit:
                    continue
                out = p.drop_model(m.name, tiers=("dram",), nodes={node})
                lost.append(f"{m.name}@{node}")
                for v, o in out.items():
                    if v in sq:
                        sq[v] -= o["freed"]
                        if sq[v] <= 0:
                            del sq[v]
        if lost:
            self.log(event="evict_whole", system=self.name, models=lost)


class Fluid(System):
    name = "fluidpool"

    def setup(self):
        p, allpin = self.pool, tuple(NODES)
        for m in PARK_ORDER:
            p.park(m, copies=1, tiers=("dram",), prefer=DURABLE[m], pin_nodes=allpin, budget=self.cap)
        for m in PARK_ORDER[:4]:                         # NVMe copies of the small models, off their disk
            p.park(m, copies=1, tiers=("ssd",), avoid_nodes=tuple(n for n in NODES if n != "node29"))
        for m in PARK_ORDER[:4]:                         # what DRAM is left: second copies
            p.park(m, copies=2, tiers=("dram",), pin_nodes=allpin, budget=self.cap)

    def activate(self, m, node, cards):
        return self._go(m, node, cards, "fluid", allow=("dram", "ssd", "hbm", "file"))

    def background(self):
        p = self.pool
        if p.pressure():
            return
        for m in PARK_ORDER:
            if p.coverage(m, tiers=("dram",)) >= 0.999:
                continue
            r = p.park(m, copies=1, tiers=("dram",), pin_nodes=tuple(NODES), budget=self.cap)
            if r["GiB"] == 0 and r["short"]:
                freed = p.trim(int((1 - p.coverage(m, tiers=("dram",))) * p.models[m].nbytes))
                self.log(event="trim", model=m, freed_GiB=round(freed / GiB, 2))
                if freed:
                    r = p.park(m, copies=1, tiers=("dram",), pin_nodes=tuple(NODES), budget=self.cap)
            if r["GiB"] > 0:
                self.log(event="repair", model=m, GiB=r["GiB"], s=r["s"], short=r["short"])
                return


class Janus(System):
    name = "janus"

    def deactivate(self, m, node, cards, job):
        p = self.pool
        if self.local_warm(m, node):
            return
        while True:
            if self.keep_local(m, node, cards, job) is not None:
                return
            victims = [x for x in sorted(p.models.values(), key=lambda x: x.last_used)
                       if x.name != m and p.coverage(x.name, tiers=("dram",), nodes={node}) > 0]
            if not victims:
                return                                   # larger than the node's pool: DEEP_SLEEP
            p.drop_model(victims[0].name, tiers=("dram",), nodes={node})

    def tick(self):
        self.react_whole()

    def activate(self, m, node, cards):
        p = self.pool
        if self.local_warm(m, node):
            return self._go(m, node, cards, "h2d_wakeup", allow=("dram",), single_source=node)
        for peer in NODES:                               # D2D fork: one active instance elsewhere
            if peer != node and p.coverage(m, tiers=("hbm",), nodes={peer}) > 0.9999:
                return self._go(m, node, cards, "d2d_fork", allow=("hbm",), single_source=peer)
        return self._go(m, node, cards, "cold", allow=("file",))


class SGLang(System):
    name = "sglang"
    publishes = False

    def __init__(self, *a):
        super().__init__(*a)
        self.slot = {}                                   # (node, card) -> model that engine keeps

    def deactivate(self, m, node, cards, job):
        p = self.pool
        for c in cards:                                  # the previous engines on these cards are gone
            old = self.slot.get((node, c))
            if old and old != m:
                p.drop_model(old, tiers=("dram",), nodes={node})
                for k in [k for k, v in self.slot.items() if v == old and k[0] == node]:
                    del self.slot[k]
        if not self.local_warm(m, node) and self.keep_local(m, node, cards, job) is None:
            return
        for c in cards:
            self.slot[(node, c)] = m

    def tick(self):
        self.react_whole()
        for k, m in list(self.slot.items()):
            if not self.local_warm(m, k[0]):
                del self.slot[k]

    def activate(self, m, node, cards):
        if self.local_warm(m, node):
            return self._go(m, node, cards, "resume", allow=("dram",), single_source=node)
        return self._go(m, node, cards, "cold", allow=("file",))


SYSTEMS = {"fluidpool": Fluid, "janus": Janus, "sglang": SGLang}


# ---------------------------------------------------------------- driver
def run(a):
    out_dir = os.path.join(a.out, a.system)
    os.makedirs(out_dir, exist_ok=True)
    logf = open(os.path.join(out_dir, "run.log"), "a")
    llock = threading.Lock()

    def log(**k):
        k.setdefault("t", round(time.time(), 3))
        line = json.dumps(k)
        with llock:
            logf.write(line + "\n")
            logf.flush()
            if k.get("event") in ("request", "phase", "summary", "tenant_dram", "evict_whole", "repair", "setup_done"):
                print(line[:300], flush=True)

    # The four nodes are ours for this run: clean page cache counts as room (0.9 of it).
    pool = Pool(NODES, policy=Policy(dram_headroom_frac=a.headroom, dram_reclaim_frac=0.9), log=log)
    for n in NODES:
        pool.rpc[n].call("reset")
    pool.start(react=(a.system == "fluidpool"))
    for m in CARDS:
        pool.register(m, os.path.join(a.manifests, m + ".json"), DURABLE[m])
    bursts = Bursts(a.amp, log)
    bursts.clear()
    sysm = SYSTEMS[a.system](pool, int(a.cap * GiB), log)
    t0 = time.time()
    sysm.setup()
    log(event="setup_done", system=a.system, s=round(time.time() - t0, 1),
        slab_GiB={n: round(pool.rpc[n].call("stat")["slab_bytes"] / GiB, 1) for n in NODES},
        coverage={m: round(pool.coverage(m, tiers=("dram",)), 3) for m in CARDS})
    for n in NODES:
        log(event="paths_warmed", node=n, s=pool.warm_paths(n, SERVE[n]))

    reqs = requests()
    series, container = tenant_series(a.per_phase)
    active = {n: collections.OrderedDict() for n in NODES}      # node -> {model: (cards, job)}
    blocked = {n: set() for n in NODES}
    total = a.warmup + a.per_phase * len(PHASES)
    done, hits, phase_i, in_phase, recs, verified = 0, 0, -2, 0, [], set()

    def free_cards(node):
        used = {c for cards, _ in active[node].values() for c in cards}
        return [c for c in SERVE[node] if c not in used and c not in blocked[node]]

    def release(node, m):
        cards, job = active[node].pop(m)
        if sysm.publishes:
            pool.unpublish(node, job)
        sysm.deactivate(m, node, cards, job)
        pool.rpc[node].call("job_release", job=job)

    def is_active(apps):
        return all(m in active[n] for m, n in apps)

    i = 0
    while done < total and i < len(reqs):
        # ---- phase bookkeeping
        k = -1 if done < a.warmup else (done - a.warmup) // a.per_phase
        if k != phase_i:
            bursts.clear()
            for n in NODES:
                for c in blocked[n]:
                    if a.arena:
                        pool.rpc[n].call("arena", card=c, gib=a.arena)
                blocked[n].clear()
            time.sleep(1.0)
            phase_i, in_phase = k, 0
            if k >= 0:
                ph = PHASES[k]
                for n, cards in ph.get("hbm", {}).items():       # a tenant takes serving cards
                    for m in [m for m, (cs, _) in active[n].items() if set(cs) & set(cards)]:
                        release(n, m)
                    blocked[n] = set(cards)
                    for c in cards:                      # the pool yields the card's HBM too
                        if a.arena:
                            pool.rpc[n].call("arena", card=c, gib=0)
                bursts.start(ph)
                log(event="phase", system=a.system, phase=ph["name"], at=done)
        ph = PHASES[phase_i] if phase_i >= 0 else {"name": "warmup"}
        if "dram" in ph and in_phase % a.step == 0 and in_phase // a.step != getattr(run, "_lvl", None):
            run._lvl = in_phase // a.step
            bursts.dram(ph, series[min(in_phase, len(series) - 1)], tick=sysm.tick)
            time.sleep(a.settle)
        # ---- next wave: restores on disjoint nodes, in trace order
        wave, busy = [], set()
        while i < len(reqs) and len(wave) < a.wave and done + len(wave) < total:
            label, apps = reqs[i]
            if is_active(apps):
                for m, n in apps:
                    active[n].move_to_end(m)
                hits += 1
                i += 1
                continue
            nodes = {n for _, n in apps}
            if nodes & busy:
                break
            if any(CARDS[m] > len(SERVE[n]) - len(blocked[n]) for m, n in apps):
                i += 1                                   # cannot be served while a tenant holds the cards
                continue
            wave.append((label, apps))
            busy |= nodes
            i += 1
        if not wave:
            continue
        # ---- make room (not timed), then restore the whole wave at once
        plan = []
        for label, apps in wave:
            for m, n in apps:
                if m in active[n]:
                    continue
                while len(free_cards(n)) < CARDS[m]:
                    release(n, next(iter(active[n])))
                cards = free_cards(n)[:CARDS[m]]
                active[n][m] = (cards, None)             # reserve
                plan.append((label, m, n, cards))
        sysm.tick()
        res, errs = {}, []

        def go(label, m, n, cards):
            try:
                res[(label, m)] = sysm.activate(m, n, cards)
            except Exception as e:                       # noqa: BLE001
                errs.append(f"{m}@{n}: {e!r}")
        th = [threading.Thread(target=go, args=x) for x in plan]
        tw = time.time()
        [x.start() for x in th]
        [x.join() for x in th]
        wall = time.time() - tw
        if errs:
            log(event="restore_failed", errors=errs)
            raise SystemExit("; ".join(errs))
        for label, m, n, cards in plan:
            r = res[(label, m)]
            active[n][m] = (cards, r["job"])
            if sysm.publishes:
                pool.publish(m, n, r["job"])
        for label, apps in wave:
            parts = [res[(label, m)] for m, n in apps if (label, m) in res]
            lat = max(r["total_s"] for r in parts)
            gib = sum(r["GiB"] for r in parts)
            vbad = None
            key = (a.system, label.split("@")[0])
            if key not in verified or (done % a.verify_every == 0 and gib < 80):
                verified.add(key)
                vbad = sum(pool.rpc[n].call("job_verify", job=res[(label, m)]["job"])["bad"] for m, n in apps if (label, m) in res)
            paths = collections.Counter()
            for r in parts:
                for kk, v in r["paths"].items():
                    paths["net" if kk.startswith("net:") else kk.split(":")[0]] += v["bytes"] / GiB
            rec = {"event": "request", "system": a.system, "i": done, "phase": ph["name"], "what": label, "GiB": round(gib, 1),
                   "kind": "+".join(sorted({r["kind"] for r in parts})), "latency_s": round(lat, 3),
                   "wave": len(wave), "wave_wall_s": round(wall, 3), "GBps": round(gib * GiB / 1e9 / lat, 1),
                   "plan_s": round(max(r["plan_s"] for r in parts), 4), "failed_fetches": sum(r["failed_fetches"] for r in parts),
                   "paths_GiB": {kk: round(v, 1) for kk, v in paths.items()}, "verify_bad": vbad}
            log(**rec)
            recs.append(rec)
            done += 1
            in_phase += 1
        sysm.background()

    bursts.clear()
    for n in NODES:
        for m in list(active[n]):
            cards, job = active[n].pop(m)
            if job:
                pool.rpc[n].call("job_release", job=job)
        pool.rpc[n].call("reset")
    summary = summarize(recs, a.system, hits, len(pool.events))
    log(event="summary", **summary)
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    pool.stop()


def summarize(recs, system, hits, evictions):
    out = {"system": system, "hits": hits, "evictions": evictions, "phases": {}}
    groups = collections.OrderedDict()
    for r in recs:
        if r["phase"] != "warmup":
            groups.setdefault(r["phase"], []).append(r)
            groups.setdefault("ALL", []).append(r)
    for ph, rs in groups.items():
        lat = sorted(r["latency_s"] for r in rs)
        out["phases"][ph] = {"n": len(rs), "mean_s": round(sum(lat) / len(lat), 3), "p50_s": lat[len(lat) // 2],
                             "p95_s": lat[min(len(lat) - 1, int(0.95 * len(lat)))], "max_s": lat[-1],
                             "kinds": dict(collections.Counter(r["kind"] for r in rs))}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("system", choices=list(SYSTEMS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifests", required=True)
    ap.add_argument("--cap", type=float, default=30, help="GiB of DRAM per NUMA vertex (x8 per node), same for all systems")
    ap.add_argument("--headroom", type=float, default=0.15, help="share of each NUMA node left to others")
    ap.add_argument("--amp", type=float, default=36, help="peak GiB a tenant asks for per NUMA node")
    ap.add_argument("--per-phase", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=8)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--settle", type=float, default=1.0)
    ap.add_argument("--wave", type=int, default=3)
    ap.add_argument("--arena", type=float, default=48, help="GiB of HBM arena per card the daemons were started with")
    ap.add_argument("--verify-every", type=int, default=9)
    run(ap.parse_args())
