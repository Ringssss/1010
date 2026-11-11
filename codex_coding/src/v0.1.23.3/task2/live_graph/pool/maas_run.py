"""Trace-driven MaaS keep-alive comparison on the real four-node cluster.

What is replayed
  Requests: the model sequence of clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv
  (26,823 requests, 87 base models), restricted to its nine most requested models, which are
  mapped by popularity rank onto nine real weight sets (1.4 - 67 GiB, 225 GiB in total).
  Tenants: the DRAM level of the bursts follows a container series of pod_memory_util_anon.csv.
  Two serving cards on node28 hold the active models; a request for an inactive model is a
  restore, the LRU active model is released to make room. Only restores are measured.

What is compared (same executor, same durable files, same citizenship policy for all three)
  fluidpool  chunks in the DRAM of all four nodes + NVMe of two + idle HBM; live graph; reacts
             to tenants chunk by chunk and re-protects in the background.
  janus      core of Janus (SOSP'26): LIGHT_SLEEP = whole model in node-local pinned host memory
             (LRU pool), D2D fork = pull from ONE active peer instance, DEEP_SLEEP = re-read.
  sglang     SGLang release/resume: each engine (card slot) keeps the one model it last ran, whole,
             in node-local pinned host memory; anything else is a cold load.

Every latency is measured: time from the restore call to all weight bytes in the target HBM.
"""
import argparse
import collections
import csv
import json
import os
import sys
import time

from fluidckpt.live.graph import Policy
from fluidckpt.live.noded import Client
from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
HOME, PEER = "node28", "node30"
SERVE_CARDS = [1, 2]                     # NUMA 3 and 2: no HCA port shares their PCIe root
RANK = ["9g-8b", "glm-b", "qwen3-0.6b", "glm-a", "glm-d", "glm-f", "glm-c", "glm-e", "qwen36-35b"]
PEER_MODELS = ["9g-8b", "glm-b"]         # the two most popular models also run on node30
TRACE = "/home/zhujianian/clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv"
TENANT = "/home/zhujianian/zhujianian/fluidpool_trace/pod_memory_util_anon.csv"


def request_sequence():
    rows = list(csv.DictReader(open(TRACE)))
    cnt = collections.Counter(r["checkpoint_model_version_id"] for r in rows)
    top = [m for m, _ in cnt.most_common(len(RANK))]
    name = dict(zip(top, RANK))
    rows.sort(key=lambda r: r["gmt_create"])
    return [name[r["checkpoint_model_version_id"]] for r in rows if r["checkpoint_model_version_id"] in name]


def tenant_series(points):
    """The burstiest container of the trace, normalised to [0, 1] and resampled."""
    by = collections.defaultdict(list)
    for r in csv.DictReader(open(TENANT)):
        by[r["container_ip"]].append((float(r["timestamp_anon"]), float(r["value"])))
    best, best_sd = None, -1
    for k, v in by.items():
        if len(v) < 500:
            continue
        xs = [x for _, x in v]
        mu = sum(xs) / len(xs)
        sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
        if sd > best_sd:
            best, best_sd = k, sd
    v = sorted(by[best])
    xs = [x for _, x in v]
    lo, hi = min(xs), max(xs)
    step = len(xs) / points
    return [(xs[int(i * step)] - lo) / max(hi - lo, 1e-9) for i in range(points)], best


# ---------------------------------------------------------------- phases (bursts)
PHASES = [
    {"name": "calm"},
    {"name": "dram_home", "dram": (HOME, [0, 2, 5, 7])},
    {"name": "ssd_home", "ssd": HOME},
    {"name": "ib_home", "ib": (HOME, "node29", [0, 1])},
    {"name": "dram_remote", "dram": (PEER, [1, 3, 4, 6])},
    {"name": "peer_gone", "peer_gone": True},
    {"name": "all", "dram": (HOME, [0, 2, 5, 7]), "ssd": HOME, "ib": (HOME, "node29", [0, 1]), "peer_gone": True},
]


class Bursts:
    def __init__(self, amp_gib, log):
        self.t = {n: Client(ip, 18770) for n, ip in NODES.items()}
        self.amp, self.log = amp_gib, log
        self.dram_now = {}

    def clear(self):
        for c in self.t.values():
            c.call("clear")
        self.dram_now = {}

    def start(self, ph, seconds=3600):
        if "ssd" in ph:
            self.t[ph["ssd"]].call("ssd", mode="write", seconds=seconds, streams=4)
            self.t[ph["ssd"]].call("ssd", mode="read", seconds=seconds, streams=4)
        if "ib" in ph:
            dst, src, ports = ph["ib"]
            self.t[dst].call("ib", peer=NODES[src], ports=ports, seconds=seconds)
        self.log(event="burst_start", phase=ph["name"])

    def dram(self, ph, level):
        if "dram" not in ph:
            return
        node, numas = ph["dram"]
        gib = int(round(level * self.amp))
        if self.dram_now.get(node) == gib:
            return
        t0 = time.time()
        for nu in numas:
            self.t[node].call("dram", numa=nu, gib=gib)
        self.dram_now[node] = gib
        self.log(event="tenant_dram", node=node, numas=numas, GiB_each=gib, s=round(time.time() - t0, 2), t=t0)


# ---------------------------------------------------------------- systems
class System:
    name = "?"

    def __init__(self, pool, cap, log):
        self.pool, self.log = pool, log
        V = pool.topology.vertices
        self.cap_all = {vid: cap for vid, v in V.items() if v.kind == "dram"}
        self.cap_home = {vid: cap for vid, v in V.items() if v.kind == "dram" and v.node == HOME}
        self.peer_alive = True

    def setup(self):
        pass

    def deactivate(self, m, cards):
        pass

    def tick(self):
        pass

    def background(self):
        pass

    def peer_gone(self):
        """The peer instances on node30 are scaled down: their HBM copies are gone."""
        if self.peer_alive:
            for m in PEER_MODELS:
                self.pool.drop_model(m, tiers=("hbm",), nodes={PEER})
            self.peer_alive = False

    def peer_setup(self):
        for m in PEER_MODELS:
            self.pool.park(m, copies=1, tiers=("hbm",), avoid_nodes=tuple(n for n in NODES if n != PEER))

    def _go(self, m, cards, kind, **kw):
        r = self.pool.restore(m, HOME, cards, release=False, **kw)
        r["kind"] = kind
        return r

    def react_whole(self):
        """Whole-model systems: a squeezed vertex costs every model that has bytes on it, oldest
        first, until the deficit is covered."""
        sq = {v: d for v, d in self.pool.pressure().items() if v.startswith(HOME + "/dram/")}
        if not sq:
            return
        lost = []
        for m in sorted(self.pool.models.values(), key=lambda m: m.last_used):
            if not sq:
                break
            hit = [v for v in sq if any(v in self.pool.rep[c.id] for c in m.chunks)]
            if not hit:
                continue
            out = self.pool.drop_model(m.name, tiers=("dram",), nodes={HOME})
            lost.append(m.name)
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
        p = self.pool
        for m in RANK:                                   # one DRAM copy of everything, popular first
            p.park(m, copies=1, tiers=("dram",), prefer=HOME, pin_nodes=(HOME,), budget=self.cap_all)
        for m in RANK:                                   # one NVMe copy off the serving node
            p.park(m, copies=1, tiers=("ssd",), avoid_nodes=("node27", HOME))
        for m in RANK:                                   # whatever DRAM is left: second copies
            p.park(m, copies=2, tiers=("dram",), pin_nodes=(HOME,), budget=self.cap_all)
        self.peer_setup()

    def activate(self, m, cards):
        return self._go(m, cards, "fluid", allow=("dram", "ssd", "hbm", "file"))

    def background(self):
        """Re-protect: models that lost DRAM chunks get them back wherever there is room now."""
        p = self.pool
        if p.pressure():
            return
        for m in RANK:
            if p.coverage(m, tiers=("dram",)) < 0.999:
                r = p.park(m, copies=1, tiers=("dram",), pin_nodes=(HOME,), budget=self.cap_all)
                self.log(event="repair", model=m, GiB=r["GiB"], s=r["s"], short=r["short"])
                return


class Janus(System):
    name = "janus"

    def setup(self):
        self.peer_setup()

    def _warm(self, m):
        return self.pool.coverage(m, tiers=("dram",), nodes={HOME}) > 0.9999

    def deactivate(self, m, cards):
        """LIGHT_SLEEP: keep the whole model in node-local pinned memory, LRU-evicting others."""
        p = self.pool
        if self._warm(m):
            return
        while True:
            r = p.park(m, copies=1, tiers=("dram",), pin_nodes=(HOME,), budget=self.cap_home,
                       avoid_nodes=tuple(n for n in NODES if n != HOME), all_or_nothing=True)
            if r is not None:
                return
            victims = [x for x in sorted(p.models.values(), key=lambda x: x.last_used)
                       if x.name != m and p.coverage(x.name, tiers=("dram",), nodes={HOME}) > 0]
            if not victims:
                return                                   # larger than the whole pool: DEEP_SLEEP
            p.drop_model(victims[0].name, tiers=("dram",), nodes={HOME})

    def tick(self):
        self.react_whole()

    def activate(self, m, cards):
        if self._warm(m):
            return self._go(m, cards, "h2d_wakeup", allow=("dram",), single_source=HOME)
        if self.peer_alive and m in PEER_MODELS and self.pool.coverage(m, tiers=("hbm",), nodes={PEER}) > 0.9999:
            return self._go(m, cards, "d2d_fork", allow=("hbm",), single_source=PEER)
        return self._go(m, cards, "cold", allow=("file",))


class SGLang(System):
    name = "sglang"

    def __init__(self, *a):
        super().__init__(*a)
        self.slot = {}                                   # card -> model whose backup that engine holds

    def _warm(self, m):
        return self.pool.coverage(m, tiers=("dram",), nodes={HOME}) > 0.9999

    def deactivate(self, m, cards):
        """release_memory_occupation: this engine backs its own weights up in host memory. The
        engine that used the card before is gone, and so is its backup."""
        p = self.pool
        for c in cards:
            old = self.slot.get(c)
            if old and old != m:
                p.drop_model(old, tiers=("dram",), nodes={HOME})
                for k in [k for k, v in self.slot.items() if v == old]:
                    del self.slot[k]
        if not self._warm(m):
            r = p.park(m, copies=1, tiers=("dram",), pin_nodes=(HOME,), budget=self.cap_home,
                       avoid_nodes=tuple(n for n in NODES if n != HOME), all_or_nothing=True)
            if r is None:
                return
        for c in cards:
            self.slot[c] = m

    def tick(self):
        self.react_whole()
        for c, m in list(self.slot.items()):
            if not self._warm(m):
                del self.slot[c]

    def activate(self, m, cards):
        if self._warm(m):
            return self._go(m, cards, "resume", allow=("dram",), single_source=HOME)
        return self._go(m, cards, "cold", allow=("file",))


SYSTEMS = {"fluidpool": Fluid, "janus": Janus, "sglang": SGLang}


# ---------------------------------------------------------------- driver
def run(system_name, a):
    out_dir = os.path.join(a.out, system_name)
    os.makedirs(out_dir, exist_ok=True)
    logf = open(os.path.join(out_dir, "run.log"), "a")

    def log(**k):
        k.setdefault("t", round(time.time(), 3))
        line = json.dumps(k)
        logf.write(line + "\n")
        logf.flush()
        if k.get("event") in ("restore_event", "phase", "summary", "tenant_dram", "evict", "evict_whole", "repair"):
            print(line[:260], flush=True)

    pool = Pool(NODES, policy=Policy(dram_headroom_frac=a.headroom), log=log)
    pool.start(react=(system_name == "fluidpool"))
    for m in RANK:
        pool.register(m, os.path.join(a.manifests, m + ".json"), HOME)
    for n in NODES:                                      # start from an empty pool
        st = pool.rpc[n].call("stat")
        if st["dram_chunks"] or st["hbm_chunks"] or st["jobs"]:
            raise SystemExit(f"{n} is not empty: restart the node daemons first ({st})")
    bursts = Bursts(a.amp, log)
    bursts.clear()
    sysm = SYSTEMS[system_name](pool, int(a.cap * GiB), log)
    t0 = time.time()
    sysm.setup()
    log(event="setup_done", system=system_name, s=round(time.time() - t0, 1),
        stat={n: {k: v for k, v in pool.rpc[n].call("stat").items() if k in ("dram_chunks", "slab_bytes", "ssd_bytes", "hbm_bytes")}
              for n in NODES})

    seq = request_sequence()
    series, container = tenant_series(a.per_phase)
    log(event="workload", requests=len(seq), tenant_container=container, per_phase=a.per_phase, warmup=a.warmup)
    active, free_cards = collections.OrderedDict(), list(SERVE_CARDS)
    n_restore, hits, phase_i, in_phase, recs = 0, 0, -1, 0, []
    total_events = a.warmup + a.per_phase * len(PHASES)

    for m in seq:
        if n_restore >= total_events:
            break
        if m in active:
            active.move_to_end(m)
            hits += 1
            continue
        # ---- phase bookkeeping, before the restore that opens it
        k = -1 if n_restore < a.warmup else (n_restore - a.warmup) // a.per_phase
        if k != phase_i:
            bursts.clear()
            time.sleep(1.0)
            phase_i, in_phase = k, 0
            if k >= 0:
                ph = PHASES[k]
                if ph.get("peer_gone"):
                    sysm.peer_gone()
                bursts.start(ph)
                log(event="phase", system=system_name, phase=ph["name"], at_restore=n_restore)
        ph = PHASES[phase_i] if phase_i >= 0 else {"name": "warmup"}
        if "dram" in ph and in_phase % a.step == 0:
            bursts.dram(ph, series[in_phase])
            time.sleep(a.settle)                         # the graph samples at 10 Hz
        # ---- make room on the serving cards
        need = 2 if pool.models[m].nbytes > 56 * GiB else 1
        while len(free_cards) < need:
            old, (cards, job) = active.popitem(last=False)
            pool.rpc[HOME].call("job_release", job=job)
            sysm.deactivate(old, cards)
            free_cards += cards
        cards = sorted(free_cards)[:need]
        free_cards = [c for c in free_cards if c not in cards]
        sysm.tick()
        # ---- the measured restore
        try:
            r = sysm.activate(m, cards) if n_restore % a.verify_every else None
            if r is None:
                r = sysm.activate(m, cards)
                r["verify"] = pool.rpc[HOME].call("job_verify", job=r["job"])
        except Exception as e:                           # noqa: BLE001
            log(event="restore_failed", model=m, error=repr(e))
            raise
        active[m] = (cards, r["job"])
        rec = {"event": "restore_event", "system": system_name, "i": n_restore, "phase": ph["name"], "model": m,
               "GiB": r["GiB"], "kind": r["kind"], "latency_s": r["total_s"], "plan_s": r["plan_s"],
               "exec_s": r["exec_s"], "GBps": r["GBps"], "failed_fetches": r["failed_fetches"], "fail_why": r.get("fail_why"),
               "paths": {k: round(v["bytes"] / GiB, 2) for k, v in r["paths"].items()},
               "verify_bad": r.get("verify", {}).get("bad")}
        log(**rec)
        recs.append(rec)
        n_restore += 1
        in_phase += 1
        sysm.background()

    bursts.clear()
    for m, (cards, job) in active.items():
        pool.rpc[HOME].call("job_release", job=job)
    summary = summarize(recs, system_name, hits, pool.events)
    log(event="summary", **summary)
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    pool.stop()


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def summarize(recs, system, hits, evictions):
    out = {"system": system, "hits": hits, "evictions": len(evictions), "phases": {}}
    groups = collections.OrderedDict()
    for r in recs:
        if r["phase"] != "warmup":
            groups.setdefault(r["phase"], []).append(r)
            groups.setdefault("ALL", []).append(r)
    for ph, rs in groups.items():
        lat = [r["latency_s"] for r in rs]
        gib = sum(r["GiB"] for r in rs)
        out["phases"][ph] = {"n": len(rs), "mean_s": round(sum(lat) / len(lat), 3), "p50_s": round(pct(lat, 0.5), 3),
                             "p95_s": round(pct(lat, 0.95), 3), "max_s": round(max(lat), 3),
                             "GiB": round(gib, 1), "agg_GBps": round(gib * GiB / 1e9 / sum(lat), 2),
                             "kinds": dict(collections.Counter(r["kind"] for r in rs)),
                             "verified": sum(1 for r in rs if r["verify_bad"] is not None),
                             "verify_bad": sum(r["verify_bad"] or 0 for r in rs)}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("system", choices=list(SYSTEMS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifests", required=True)
    ap.add_argument("--cap", type=float, default=8, help="GiB of DRAM keep-alive may hold per NUMA vertex")
    ap.add_argument("--headroom", type=float, default=0.30, help="share of each NUMA node left to others")
    ap.add_argument("--amp", type=float, default=32, help="peak GiB a tenant burst takes per NUMA node")
    ap.add_argument("--per-phase", type=int, default=24)
    ap.add_argument("--warmup", type=int, default=12)
    ap.add_argument("--step", type=int, default=4, help="restores between tenant level changes")
    ap.add_argument("--settle", type=float, default=0.8)
    ap.add_argument("--verify-every", type=int, default=8)
    run(ap.parse_args().system, ap.parse_args())
