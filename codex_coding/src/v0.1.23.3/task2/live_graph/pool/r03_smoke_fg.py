"""(f) the whole catalog parked with the react thread on: no self-eviction, coverage 1.0, slabs on
their NUMA nodes. (g) concurrent restores: restore_wave (live, ledger) vs independent static plans."""
import sys
import threading
import time

from r03_smoke_common import DURABLE, NODES, cap, make, numa_report, paths
from fluidckpt.live.pool import GiB

ORDER = ["9g-8b", "qwen36-35b", "qwen3-0.6b", "clover-35b", "glm53-n28", "glm53-n30"]
pool, say = make("fg", react=True, models=ORDER)
B = cap(pool, 30)
t0 = time.time()
for m in ORDER:
    r = pool.park(m, copies=1, tiers=("dram",), prefer=DURABLE[m], budget=B)
    say(event="park", model=m, s=r["s"], GiB=r["GiB"], GBps=r["GBps"], short=r["short"], errors=r["errors"][:2],
        reserve_s_max=r["reserve_s_max"], home_share=round(sum(v for k, v in r["vertices"].items() if k.startswith(DURABLE[m])) / max(r["GiB"], 1e-9), 3))
say(event="parked_all", s=round(time.time() - t0, 1), coverage_all=pool.coverage_all(),
    coverage={m: round(pool.coverage(m), 4) for m in ORDER},
    self_evictions=sum(1 for e in pool.events if e["event"] == "evict"))
rep = numa_report(pool)
bad = {n: {k: (v, r["actual_GiB"].get(k)) for k, v in r["intended_GiB"].items() if abs(v - r["actual_GiB"].get(k, 0)) > 0.6}
       for n, r in rep.items()}
say(event="numa", per_node={n: {"intended_total": round(sum(r["intended_GiB"].values()), 1),
                                "actual_total": round(sum(r["actual_GiB"].values()), 1), "rss_anon_GiB": r["rss_anon_GiB"],
                                "slabs": r["slabs"], "registered": r["registered"]} for n, r in rep.items()}, mismatch=bad)
say(event="wait_pinned", **pool.wait_pinned())
time.sleep(3)
say(event="settled", self_evictions=sum(1 for e in pool.events if e["event"] == "evict"), coverage_all=pool.coverage_all())
w = pool.warm_arenas(seconds=40)
say(event="warm", s={n: v["s"] for n, v in w.items()})
time.sleep(1.0)
WAVE = [("qwen36-35b", "node27", [0, 1]), ("clover-35b", "node29", [0, 1]), ("9g-8b", "node30", [1])]


def independent(live):
    res = [None] * len(WAVE)

    def go(k, m, n, cards):
        res[k] = pool.restore(m, n, cards, live=live)
    th = [threading.Thread(target=go, args=(k, *w)) for k, w in enumerate(WAVE)]
    t = time.time()
    [x.start() for x in th]
    [x.join() for x in th]
    return res, time.time() - t


def brief(rs):
    return [{"m": r["model"], "node": r["node"], "s": r["total_s"], "pred": r["predicted_s"], "plan_s": r["plan_s"],
             "paths": paths(r)} for r in rs]


for rep_i in range(2):
    rs, wall = independent(False)
    say(event="independent_static", rep=rep_i, wall_s=round(wall, 3), max_s=max(r["total_s"] for r in rs), restores=brief(rs))
    time.sleep(1.5)
    t = time.time()
    rs = pool.restore_wave(WAVE)
    wall = time.time() - t
    if any(isinstance(r, Exception) for r in rs):
        say(event="wave_failed", errors=[repr(r) for r in rs if isinstance(r, Exception)])
    else:
        say(event="restore_wave", rep=rep_i, wall_s=round(wall, 3), max_s=max(r["total_s"] for r in rs), restores=brief(rs))
    time.sleep(1.5)
r = pool.restore("clover-35b", "node27", [2, 3], verify=True)
say(event="verify_clover", s=r["total_s"], verify=r["verify"], paths=paths(r))
if "glm" in sys.argv:
    for rep_i in range(2):
        t = time.time()
        rs = pool.restore_wave([("glm53-n28", "node28", list(range(8))), ("glm53-n30", "node30", list(range(8)))])
        wall = time.time() - t
        say(event="glm_wave", rep=rep_i, wall_s=round(wall, 3),
            restores=[{"m": r["model"], "s": r["total_s"], "pred": r["predicted_s"], "GBps": r["GBps"], "plan_s": r["plan_s"],
                       "paths": paths(r), "src_GB": r["plan"].get("src_GB")} if not isinstance(r, Exception) else repr(r) for r in rs])
        time.sleep(2)
say(event="end", self_evictions=sum(1 for e in pool.events if e["event"] == "evict"), coverage_all=pool.coverage_all())
for n in NODES:
    pool.rpc[n].call("reset")
time.sleep(1)
say(event="after_reset", rss={n: r["rss_anon_GiB"] for n, r in numa_report(pool).items()})
pool.stop()
