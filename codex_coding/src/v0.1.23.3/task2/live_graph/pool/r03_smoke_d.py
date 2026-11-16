"""(d) idle HBM as a parking tier: park into arenas, restore from them (remote and local),
make_room, yield_card, and the graph-driven yield to an HBM tenant."""
import time

from r03_smoke_common import NODES, make, paths
from fluidckpt.live.noded import Client
from fluidckpt.live.pool import GiB

M = "9g-8b"
pool, say = make("d", react=True, models=("9g-8b", "qwen36-35b"))
V = pool.topology.vertices
say(event="card_capacity_GiB", cap=round(V["node29/hbm/4"].capacity_bytes / GiB, 2), arena=pool.arena_default_gib)
pool.hbm_park["node29"] = {4, 5, 6, 7}
r = pool.park(M, copies=1, tiers=("hbm",))
say(event="park_hbm", s=r["s"], GiB=r["GiB"], GBps=r["GBps"], short=r["short"], errors=r["errors"], vertices=r["vertices"])
st = pool.rpc["node29"].call("stat")
say(event="stat", hbm_bytes=st["hbm_bytes"], arena={c: round(a["free"] / GiB, 2) for c, a in st["arena"].items()})
r = pool.restore(M, "node27", [1], allow=("hbm", "file"), verify=True)
say(event="restore_from_rhbm", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), verify=r["verify"])
r = pool.restore(M, "node29", [1], allow=("hbm", "file"), verify=True)
say(event="restore_from_lhbm", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), verify=r["verify"])
# a model lands on parking cards: parked chunks sit at the top of the arena and may stay
r = pool.restore("qwen36-35b", "node29", [4, 5], allow=("file",), release=False)
say(event="restore_onto_parking_cards", total_s=r["total_s"], paths=paths(r), parked_cov=pool.coverage(M, tiers=("hbm",)))
pool.rpc["node29"].call("job_release", job=r["job"])
y = pool.yield_card("node29", 6, release_arena=True)
say(event="yield_card", **y, parked_cov=pool.coverage(M, tiers=("hbm",)))
pool.rpc["node29"].call("arena", card=6, gib=48)
pool.hbm_park["node29"].add(6)
# graph-driven: a tenant fills card 7 beside the arena
ten = Client(NODES["node29"], 18770)
t0 = time.time()
try:
    h = ten.call("hbm", card=7, gib=14)
    say(event="tenant_hbm_14", r=h, s=round(time.time() - t0, 2))
except Exception as e:
    say(event="tenant_hbm_14_failed", error=repr(e)[:200])
time.sleep(2.0)
say(event="after_tenant", events=[{k: e.get(k) for k in ("event", "tiers", "squeezed_GiB", "freed_GiB", "arena", "react_s")}
                                  for e in pool.events if e["event"] == "evict"],
    hbm_park=sorted(pool.hbm_park["node29"]), yielded=[f"{n}/{c}" for n, c in pool.hbm_yielded])
try:
    h = ten.call("hbm", card=7, gib=56)
    say(event="tenant_hbm_56", r=h)
except Exception as e:
    say(event="tenant_hbm_56_failed", error=repr(e)[:200])
ten.call("hbm", card=7, gib=0)
ten.call("clear")
time.sleep(3.0)
rb = pool.rebalance(tiers=("hbm",))
say(event="rebalance", **rb, hbm_park=sorted(pool.hbm_park["node29"]), parked_cov=pool.coverage(M, tiers=("hbm",)))
for n in NODES:
    pool.rpc[n].call("reset")
pool.stop()
