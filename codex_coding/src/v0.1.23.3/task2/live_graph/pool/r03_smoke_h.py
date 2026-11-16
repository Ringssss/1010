"""(h) a DRAM tenant squeezes NUMA nodes we hold chunks on: slab-wise, value-ordered eviction,
the memory really reaches the tenant, rebalance re-protects the model elsewhere."""
import threading
import time

from r03_smoke_common import NODES, cap, make, numa_report
from fluidckpt.live.noded import Client
from fluidckpt.live.pool import GiB

pool, say = make("h", react=True, models=("qwen36-35b", "9g-8b"))
B = cap(pool, 30)
pool.touch("9g-8b"); pool.touch("9g-8b"); pool.touch("qwen36-35b")          # 9g is the hotter model
for m in ("qwen36-35b", "9g-8b"):
    r = pool.park(m, copies=1, tiers=("dram",), budget=B, within=("node28",))
    say(event="park", model=m, GiB=r["GiB"], placed_GiB=r["placed_GiB"], short=r["short"], s=r["s"])
r = pool.park("9g-8b", copies=2, tiers=("dram",), budget=B)                  # second copy elsewhere: redundancy
say(event="park_copy2", GiB=r["GiB"], vertices=len(r["vertices"]))
say(event="before", cov={m: round(pool.coverage(m), 4) for m in pool.models}, held={k: round(v / GiB, 2) for k, v in pool.held.items() if k.startswith("node28/dram") and v})
ten = Client(NODES["node28"], 18770)
res = {}


def burst(nu):
    res[nu] = ten2[nu].call("dram", numa=nu, gib=50, reserve_gib=4, patience_s=10.0)
ten2 = {nu: Client(NODES["node28"], 18770) for nu in (2, 5)}
t0 = time.time()
th = [threading.Thread(target=burst, args=(nu,)) for nu in (2, 5)]
[x.start() for x in th]
[x.join() for x in th]
say(event="tenant", s=round(time.time() - t0, 2), got={nu: {k: r.get(k) for k in ("held_GiB", "stopped", "waited_s")} for nu, r in res.items()})
time.sleep(1.0)
ev = [e for e in pool.events if e["event"] == "evict"]
say(event="evictions", n=len(ev), first_after_burst_s=round(ev[0]["t"] - t0, 3) if ev else None,
    freed_GiB=round(sum(sum(e["freed_GiB"].values()) for e in ev), 2), lost=[e["lost_fast_copy"] for e in ev][:6],
    react_s=[e["react_s"] for e in ev][:8], slabs=[sum(d["slabs"] for d in e["detail"].values()) for e in ev][:8])
say(event="after_burst", cov={m: round(pool.coverage(m), 4) for m in pool.models},
    numa28=numa_report(pool)["node28"])
rb = pool.rebalance(budget=B)
say(event="rebalance_during_burst", repaired_GiB=rb["repaired_GiB"], s=rb["s"], models=rb["models"],
    cov={m: round(pool.coverage(m), 4) for m in pool.models})
time.sleep(1.0)
say(event="evictions_after_rebalance", n=len([e for e in pool.events if e["event"] == "evict"]))
ten.call("clear")
for n in NODES:
    pool.rpc[n].call("reset")
pool.stop()
