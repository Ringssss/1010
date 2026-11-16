"""(i) another tenant's RDMA traffic on the *source* side: node28 pulls from node29 over ports
0 and 1 (node29's sending side and node28's receiving side of those ports are busy). A restore
into node27 cards 0,1 planned live (source tx residuals, ledger) vs static (live=False)."""
import time

from r03_smoke_common import NODES, cap, make, paths
from fluidckpt.live.noded import Client

M = "qwen36-35b"
pool, say = make("i", react=True)
r = pool.park(M, copies=1, tiers=("dram",), budget=cap(pool, 30), avoid_nodes=("node27",))
say(event="park", GiB=r["GiB"], s=r["s"], nodes=sorted({v.split("/")[0] for v in r["vertices"]}))
say(event="wait_pinned", **pool.wait_pinned())
w = pool.warm_arenas(seconds=30)
time.sleep(1.5)


def one(tag, live):
    r = pool.restore(M, "node27", [0, 1], live=live)
    say(event=tag, live=live, total_s=r["total_s"], GBps=r["GBps"], predicted_s=r["predicted_s"], paths=paths(r),
        cap_port=r["plan"]["cap_port"], src_tx=r["plan"].get("src_tx_share"), src_GB=r["plan"].get("src_GB"),
        flow=r["plan"].get("flow_GB"), pair=r["pair_GBps"], src=r.get("src_GBps"))
    time.sleep(1.5)


for k in range(2):
    one("calm", True)
    one("calm", False)
ten = Client(NODES["node28"], 18770)
ten.call("ib", peer=NODES["node29"], ports=[0, 1], seconds=600)
ten2 = Client(NODES["node30"], 18770)
ten2.call("ib", peer=NODES["node29"], ports=[2], seconds=600)
time.sleep(3.0)
snap = pool.snapshot()
say(event="burst_on", tx_residual={f"{s}:{p}": round(snap.residual_GBps(f"{s}/ib_tx/shca_{p}"), 1) for s in NODES for p in range(4)},
    rx_residual={f"{s}:{p}": round(snap.residual_GBps(f"{s}/ib_rx/shca_{p}"), 1) for s in NODES for p in range(4)})
for k in range(3):
    one("burst", True)
    one("burst", False)
ten.call("clear")
ten2.call("clear")
for n in NODES:
    pool.rpc[n].call("reset")
pool.stop()
