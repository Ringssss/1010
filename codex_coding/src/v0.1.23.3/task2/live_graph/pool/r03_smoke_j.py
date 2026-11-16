"""(j) the calls the whole-model baselines need, with none of the live machinery: static
single-source D2D pull, node-local keep (within=), local H2D wake-up, and giving a card away."""
from r03_smoke_common import NODES, cap, make, paths

M = "qwen36-35b"
pool, say = make("j", react=False)
B = cap(pool, 30)
r = pool.restore(M, "node27", [0, 1], allow=("file",), live=False, release=False)       # resident, not yet published
say(event="cold", total_s=r["total_s"], paths=paths(r), live=r["plan"]["live"], ledger=len(pool.ledger))
j27 = r["job"]
pool.publish(M, "node27", j27)
r = pool.restore(M, "node29", [0, 1], allow=("hbm",), single_source="node27", live=False, verify=True, release=False)
say(event="d2d_static", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), verify=r["verify"], src=r["src_GBps"],
    plan={k: r["plan"].get(k) for k in ("live", "src_GB", "src_tx_share", "flow_GB")}, ledger=len(pool.ledger))
j29 = r["job"]
k = pool.park(M, copies=1, tiers=("dram",), within=("node29",), budget=B, all_or_nothing=True,
              from_job=("node29", j29, pool.where(pool.models[M], [0, 1])))
say(event="keep_local_node29", placed_GiB=k and k["placed_GiB"], short=k and k["short"], s=k and k["s"],
    nodes=k and sorted({v.split("/")[0] for v in k["vertices"]}))
pool.rpc["node29"].call("job_release", job=j29)
k = pool.park(M, copies=1, tiers=("dram",), within=("node28",), budget=B, all_or_nothing=True)   # another island
say(event="keep_local_node28", placed_GiB=k and k["placed_GiB"], short=k and k["short"],
    nodes=k and sorted({v.split("/")[0] for v in k["vertices"]}))
small = {v: 2 << 30 for v in B}
k = pool.park(M, copies=1, tiers=("dram",), within=("node30",), budget=small, all_or_nothing=True)
say(event="keep_local_too_big", result=k)
say(event="wait_pinned", **pool.wait_pinned())
r = pool.restore(M, "node29", [2, 3], allow=("dram",), single_source="node29", live=False, verify=True)
say(event="h2d_wakeup_static", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), verify=r["verify"])
pool.unpublish("node27", j27)
pool.rpc["node27"].call("job_release", job=j27)
y = pool.yield_card("node27", 0, release_arena=True)
st = pool.rpc["node27"].call("stat")["arena"]
say(event="card_given_away", **y, arena_cards=sorted(st))
pool.rpc["node27"].call("arena", card=0, gib=48)
say(event="card_back", arena_cards=sorted(pool.rpc["node27"].call("stat")["arena"]))
for n in NODES:
    pool.rpc[n].call("reset")
pool.stop()
