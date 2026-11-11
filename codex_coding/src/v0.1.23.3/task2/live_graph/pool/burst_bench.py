"""Controlled burst experiments on one large model (run inside zjn-fluid-pool-27).

  scale      35B (67 GiB) into two cards: cold NVMe / node-local pinned DRAM / three remote nodes.
  loss       a source node is told to give its memory back in the middle of a restore: the
             restore must still finish, byte-exact, and the memory must be released first.
  ib         another tenant's RDMA traffic lands on two HCA ports of the destination during the
             restore: static plan (nominal capacities, no stealing) vs live plan + stealing.
  dram       a tenant takes DRAM on a node that holds chunks: time from the tenant's first byte
             to our memory being back, and the restore latency before / during / after.
  graph      cost of keeping the graph current: poll latency, snapshot, placement, planning.

Every result is one JSON line in <out>/burst_bench.log.
"""
import json
import os
import sys
import threading
import time

from fluidckpt.live.graph import Policy
from fluidckpt.live.noded import Client
from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
HOME = "node28"
man, out_dir, which = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
os.makedirs(out_dir, exist_ok=True)
logf = open(os.path.join(out_dir, "burst_bench.log"), "a")


def say(**k):
    k.setdefault("t", round(time.time(), 3))
    line = json.dumps(k)
    logf.write(line + "\n")
    logf.flush()
    if k.get("event") != "restore" and k.get("event") != "park":
        print(line[:420], flush=True)


pool = Pool(NODES, policy=Policy(dram_headroom_frac=0.30), log=say)
pool.start(react=True)
tenant = {n: Client(ip, 18770) for n, ip in NODES.items()}
for t in tenant.values():
    t.call("clear")
others = tuple(n for n in NODES if n != HOME)


def brief(r, **kw):
    return {"model": r["model"], "GiB": r["GiB"], "total_s": r["total_s"], "plan_s": r["plan_s"], "GBps": r["GBps"],
            "paths_GiB": {k: round(v["bytes"] / GiB, 1) for k, v in r["paths"].items()},
            "stolen": {k: v["stolen"] for k, v in r["paths"].items() if v["stolen"]},
            "failed_fetches": r["failed_fetches"], "verify": r.get("verify"), **kw}


def reps(n, **kw):
    rs = [pool.restore(**kw) for _ in range(n)]
    return sorted(rs, key=lambda r: r["total_s"])[len(rs) // 2]


if "scale" in which:
    name, cards = "qwen36-35b", [1, 2]
    pool.register(name, f"{man}/{name}.json", HOME)
    r = pool.restore(name, HOME, cards, allow=("file",), verify=True)
    say(event="scale", case="cold: local NVMe, O_DIRECT, 8 readers", **brief(r))
    pool.park(name, copies=1, tiers=("dram",), pin_nodes=(HOME,), avoid_nodes=others)
    say(event="scale", case="node-local pinned DRAM (best case of a single-node keep-alive)",
        **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram",))))
    pool.park(name, copies=4, tiers=("dram",))            # one more copy on each of the other three
    pool.drop_model(name, tiers=("dram",), nodes={HOME})
    r = pool.restore(name, HOME, cards, allow=("dram",), verify=True)
    say(event="scale", case="three remote nodes' DRAM, nothing local (first, verified)", **brief(r))
    say(event="scale", case="three remote nodes' DRAM, nothing local (median of 3)",
        **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram",))))
    for src in others:
        say(event="scale", case=f"one remote node only ({src})",
            **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram",), single_source=src)))

    if "loss" in which:
        # node30 gives its copy back 0.25 s into the restore (as if a tenant needed the memory)
        res = {}
        th = threading.Thread(target=lambda: res.update(r=pool.restore(name, HOME, cards, allow=("dram",), verify=True)))
        th.start()
        time.sleep(0.25)
        t0 = time.time()
        d = pool.drop_model(name, tiers=("dram",), nodes={"node30"})
        t_drop = time.time() - t0
        th.join()
        say(event="loss", case="node30 drops its copy 0.25 s into the restore",
            drop_s=round(t_drop, 3), freed_GiB=round(sum(o["freed"] for o in d.values()) / GiB, 1),
            max_revoke_s=max(o["revoke_s"] for o in d.values()), **brief(res["r"]))
        pool.park(name, copies=3, tiers=("dram",), avoid_nodes=(HOME,))

    if "ib" in which:
        for ports in ([0, 1], [0, 1, 2]):
            tenant[HOME].call("ib", peer=NODES["node29"], ports=ports, seconds=600)
            time.sleep(2.0)
            for label, kw in (("static plan, no stealing", {"live": False, "steal": False}),
                              ("live plan + stealing", {"live": True, "steal": True})):
                say(event="ib", case=f"tenant RDMA into {HOME} ports {ports}: {label}",
                    **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram",), **kw)))
            tenant[HOME].call("clear")
            time.sleep(1.5)
        say(event="ib", case="no tenant traffic (reference)",
            **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram",))))
    pool.unpark(name)

if "dram" in which:
    name, cards = "glm-c", [1]
    pool.register(name, f"{man}/{name}.json", HOME)
    cap = {v: 8 * GiB for v in pool.topology.vertices}
    pool.park(name, copies=2, tiers=("dram",), budget=cap, avoid_nodes=(HOME,))
    held = {v: round(b / GiB, 1) for v, b in pool.held.items() if b > 0}
    say(event="dram", case="placement (2 copies, <= 8 GiB per NUMA vertex)", held_GiB=held)
    say(event="dram", case="before the burst", **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram", "file"))))
    victims = sorted(v for v in held if v.startswith("node30/"))[:3]
    numas = [int(v.split("/")[2]) for v in victims]
    n_ev = len(pool.events)
    t0 = time.time()
    done = {}
    th = [threading.Thread(target=lambda nu=nu: done.update({nu: tenant["node30"].call("dram", numa=nu, gib=36)}))
          for nu in numas]
    [x.start() for x in th]
    during = pool.restore(name, HOME, cards, allow=("dram", "file"), verify=True)
    say(event="dram", case=f"restore issued while a tenant takes 36 GiB on node30 NUMA {numas}", **brief(during))
    [x.join() for x in th]
    time.sleep(1.5)
    evs = pool.events[n_ev:]
    say(event="dram", case="reaction", tenant_ramp_s=round(max(d["t_done"] for d in done.values()) - t0, 2),
        evictions=len(evs), first_evict_after_s=None if not evs else round(evs[0]["t"] - t0, 3),
        react_s=[e["react_s"] for e in evs], freed_GiB=round(sum(sum(e["freed_GiB"].values()) for e in evs), 1),
        coverage_after=round(pool.coverage(name, tiers=("dram",)), 4))
    say(event="dram", case="while the tenant holds the memory", **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram", "file"))))
    t = time.time()
    r = pool.park(name, copies=2, tiers=("dram",), budget=cap, avoid_nodes=(HOME,))
    say(event="dram", case="re-protect elsewhere", GiB=r["GiB"], s=r["s"], short=r["short"],
        to={v: g for v, g in r["vertices"].items()})
    say(event="dram", case="after re-protection (tenant still there)", **brief(reps(3, name=name, node=HOME, cards=cards, allow=("dram", "file"))))
    tenant["node30"].call("clear")
    pool.unpark(name)

if "graph" in which:
    import statistics
    time.sleep(5)
    ms = sorted(pool.poll_ms)
    t = time.perf_counter()
    for _ in range(200):
        pool.graph._snap = None
        pool.snapshot()
    snap_ms = (time.perf_counter() - t) / 200 * 1e3
    t = time.perf_counter()
    for _ in range(50):
        pool.pressure()
    press_ms = (time.perf_counter() - t) / 50 * 1e3
    say(event="graph", vertices=len(pool.topology.vertices), edges=len(pool.topology.edges),
        resources=len(pool.topology.resources), polls=len(ms), poll_ms_p50=round(statistics.median(ms), 2),
        poll_ms_p99=round(ms[int(0.99 * len(ms))], 2), snapshot_ms=round(snap_ms, 3), pressure_scan_ms=round(press_ms, 3))

for t in tenant.values():
    t.call("clear")
pool.stop()
