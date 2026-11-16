"""(a) pinned slabs land on the intended NUMA node and return to the OS; (b) restore verifies."""
import sys
import time

from r03_smoke_common import NODES, cap, make, numa_report, paths

pool, say = make("ab", react=True)
rids = sorted(r for r in pool.topology.resources if "ib_tx" in r or "ib_rx" in r)[:6]
say(event="resources_sample", rids=rids)
say(event="baseline", numa=numa_report(pool))
r = pool.park("qwen36-35b", copies=1, tiers=("dram",), budget=cap(pool, 30))
say(event="park", s=r["s"], GiB=r["GiB"], GBps=r["GBps"], short=r["short"], errors=r["errors"], vertices=r["vertices"])
say(event="after_park", numa=numa_report(pool), coverage=pool.coverage("qwen36-35b"), evictions=len(pool.events))
if "warm" in sys.argv:
    w = pool.warm_arenas(seconds=40)
    say(event="warm", s={n: v["s"] for n, v in w.items()})
for node, cards in (("node27", [1, 2]), ("node28", [5, 6])):
    r = pool.restore("qwen36-35b", node, cards, verify=True)
    say(event="restore", node=node, total_s=r["total_s"], GBps=r["GBps"], predicted_s=r["predicted_s"], paths=paths(r),
        verify=r["verify"], rates=r["rates"], plan={k: r["plan"].get(k) for k in ("cap_port", "flow_GB", "src_GB", "use")})
for n in NODES:
    pool.rpc[n].call("reset")
time.sleep(1.0)
say(event="after_reset", numa=numa_report(pool), events=len(pool.events))
pool.stop()
