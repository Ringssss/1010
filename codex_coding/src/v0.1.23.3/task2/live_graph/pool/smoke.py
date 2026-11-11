"""First end-to-end pass: park one model across the cluster, restore it from several nodes at
once, verify every chunk out of HBM. Run inside zjn-fluid-pool-27."""
import json, sys, time
from fluidckpt.live.pool import Pool, GiB
NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
man, name = sys.argv[1], sys.argv[2]
t = time.time(); pool = Pool(NODES); pool.start(react=False)
print(json.dumps({"event": "pool_up", "s": round(time.time() - t, 2), "vertices": len(pool.topology.vertices),
                  "edges": len(pool.topology.edges)}), flush=True)
m = pool.register(name, f"{man}/{name}.json", "node28")
snap = pool.snapshot()
free = {}
for vid, v in pool.topology.vertices.items():
    free.setdefault((v.node, v.kind), 0); free[(v.node, v.kind)] += pool.free(vid, snap)
print(json.dumps({"event": "free_GiB", **{f"{n}/{k}": round(b / GiB, 1) for (n, k), b in sorted(free.items())}}), flush=True)
pool.park(name, copies=2, tiers=("dram",), prefer="node28", pin_nodes=("node28",))
for kw in ({"allow": ("dram",), "strategy": "greedy"}, {"allow": ("dram",), "strategy": "lp"},
           {"allow": ("dram",), "strategy": "greedy", "steal": False},
           {"allow": ("file",)},):
    for rep in range(2):
        pool.restore(name, "node28", [0], verify=(rep == 0), **kw)
# remote-only: drop node28's copies and pull everything over the network
per = {}
for c in m.chunks:
    for v in list(pool.rep[c.id]):
        if v.startswith("node28/"):
            per.setdefault(v, [])
            if c.id not in per[v]: per[v].append(c.id)
print(json.dumps({"event": "drop_local", "out": pool._drop(per)}), flush=True)
pool.park(name, copies=3, tiers=("dram",), avoid_nodes=("node28",))
for rep in range(3):
    pool.restore(name, "node28", [0], allow=("dram",), verify=(rep == 0))
pool.unpark(name)
print(json.dumps({"event": "stat", **{n: pool.rpc[n].call("stat") for n in NODES}}), flush=True)
pool.stop()
