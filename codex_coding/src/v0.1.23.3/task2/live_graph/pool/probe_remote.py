"""Where does a remote-DRAM restore spend its time? Park on chosen nodes, restore, print prof."""
import json, sys, time
from fluidckpt.live.pool import Pool
NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
man, name = sys.argv[1], sys.argv[2]
pool = Pool(NODES, log=lambda **k: None); pool.start(react=False)
m = pool.register(name, f"{man}/{name}.json", "node28")
for srcs in (("node30",), ("node29", "node30"), ("node27", "node29", "node30")):
    pool.park(name, copies=len(srcs), tiers=("dram",), avoid_nodes=tuple(n for n in NODES if n not in srcs))
    for card, avoid in ((0, False), (0, True), (1, True), (4, True)):
      for rep in range(3):
        r = pool.restore(name, "node28", [card], allow=("dram",), avoid_shared_root=avoid)
        if rep: print(json.dumps({"srcs": srcs, "card": card, "ports": r["ports"], "total_s": r["total_s"], "exec_s": r["exec_s"], "alloc_s": r["alloc_s"],
                          "GBps": r["GBps"], "prof": r["prof"], "rates": r["rates"]}), flush=True)
    pool.unpark(name)
pool.stop()
