"""Hardware smoke of the borrowed-ingress restore path and of concurrent remote-file puts.
  relay_smoke.py <manifest_dir> <out_dir> [reps]
(1) park 9g-8b, qwen36-35b and clover-35b at once from their durable files into DRAM all over
    the cluster (the expose/unexpose path that deadlocked in formal_r400_p1_hung);
(2) restore 9g-8b onto node28 card 1 and qwen36-35b onto node28 cards 1,2, relay off vs relay on
    ("loose": every idle near card of node28 may be borrowed), alternating, sha-verified;
(3) park 9g-8b in node27's idle-card HBM (cards 4-7) and restore it onto node27 card 0 (lhbm over
    xGMI on its own stream), relay off vs on.
One JSON line per event in <out_dir>/relay_smoke.log."""
import json
import os
import sys
import threading
import time

from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
DURABLE = {"9g-8b": "node28", "qwen36-35b": "node28", "clover-35b": "node30"}
man, out_dir = sys.argv[1], sys.argv[2]
reps = int(sys.argv[3]) if len(sys.argv) > 3 else 3
os.makedirs(out_dir, exist_ok=True)
logf = open(os.path.join(out_dir, "relay_smoke.log"), "a")


def say(**k):
    k.setdefault("t", round(time.time(), 3))
    line = json.dumps(k)
    logf.write(line + "\n")
    logf.flush()
    if k.get("event") not in ("restore", "park"):
        print(line[:400], flush=True)


pool = Pool(NODES, log=say)
pool.start(react=False)
for n in NODES:
    pool.rpc[n].call("reset")
for m, dn in DURABLE.items():
    pool.register(m, f"{man}/{m}.json", dn)
if any(pool.info[n].get("arena_GiB") for n in NODES):
    w = pool.warm_arenas(seconds=float(os.environ.get("WARM_S", "40")))
    say(event="arenas_warmed", out={n: {k: v for k, v in r.items() if k != "pairs"} for n, r in w.items()} if isinstance(w, dict) else w)

# (1) concurrent parks from durable files, one copy anywhere but the restore node, plus local
t0 = time.time()
res, th = {}, []
for m in DURABLE:
    def go(m=m):
        try:
            res[m] = pool.park(m, copies=1, tiers=("dram",), avoid_nodes=("node28",))
        except Exception as e:                           # noqa: BLE001
            res[m] = {"error": repr(e)}
    th.append(threading.Thread(target=go))
[x.start() for x in th]
[x.join(timeout=900) for x in th]
say(event="concurrent_park", s=round(time.time() - t0, 1), alive=[x.is_alive() for x in th],
    out={m: {k: r.get(k) for k in ("GiB", "s", "short", "errors")} for m, r in res.items()})
if any(x.is_alive() for x in th):
    say(event="FAIL", why="park did not finish in 900 s")
    sys.exit(1)


def row(case, r):
    say(event="case", case=case, model=r["model"], node=r["node"], cards=r["cards"], GiB=r["GiB"],
        total_s=r["total_s"], GBps=r["GBps"], predicted_s=r["predicted_s"], relay=r.get("relay"),
        paths_GiB={k: round(v["bytes"] / GiB, 2) for k, v in r["paths"].items()},
        failed=r["failed_fetches"], verify=r.get("verify"), xgmi_s=r.get("prof", {}).get("xgmi_s"))


# (2) remote DRAM only -> node28; relay off vs on, alternating
for m, cards in (("9g-8b", [1]), ("qwen36-35b", [1, 2])):
    for k in range(reps):
        for pol in ("off", "loose"):
            r = pool.restore(m, "node28", cards, allow=("dram",), relay=pol, verify=(k == 0))
            row(f"{m}@node28 relay={pol}", r)

# (3) parked HBM on node27's idle cards -> node27 card 0
pool.set_hbm_park({"node27": [4, 5, 6, 7]}) if hasattr(pool, "set_hbm_park") else pool.hbm_park.update({"node27": {4, 5, 6, 7}})
p = pool.park("9g-8b", copies=1, tiers=("hbm",), pin_nodes=("node27",), avoid_nodes=("node28", "node29", "node30"))
say(event="parked_hbm", GiB=p.get("GiB"), short=p.get("short"), errors=p.get("errors"))
for k in range(reps):
    for pol in ("off", "loose"):
        r = pool.restore("9g-8b", "node27", [0], allow=("dram", "hbm"), relay=pol, verify=(k == 0))
        row(f"9g-8b@node27 lhbm relay={pol}", r)
say(event="done")
pool.stop()
