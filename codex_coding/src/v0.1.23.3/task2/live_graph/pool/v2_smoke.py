"""Hardware smoke of FluidPool v2 mechanisms (before the formal v2 run).
  v2_smoke.py <manifest_dir> <out_dir>
(a) prestage: extra replicas of 9g-8b and qwen36-35b in node27's idle HBM (cards 4-7); restore
    9g-8b -> node27 card 0 and qwen36-35b -> node27 cards 0,1 with and without the prestaged
    copies allowed (allow hbm vs dram only), relay idle; sha-verified.
(b) demotion: qwen36-35b restored onto node28 cards 1,2 and published; its DRAM copies dropped
    so the instance is the only fast copy; retire with demote_hbm_first off vs on (lands in
    node27/29 idle arenas over RDMA); demote seconds compared.
(c) owner first mid-restore: 9g-8b -> node28 card 1 with relay; 40 ms in, the arena of one
    borrowed card is released (as for a tenant); the restore must finish, verified, and the
    release must not wait for it.
One JSON line per event in <out_dir>/v2_smoke.log."""
import json
import os
import sys
import threading
import time

from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
DURABLE = {"9g-8b": "node28", "qwen36-35b": "node28"}
man, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
logf = open(os.path.join(out_dir, "v2_smoke.log"), "a")


def say(**k):
    k.setdefault("t", round(time.time(), 3))
    line = json.dumps(k)
    logf.write(line + "\n")
    logf.flush()
    if k.get("event") not in ("restore", "park"):
        print(line[:420], flush=True)


def row(case, r):
    say(event="case", case=case, total_s=r["total_s"], GBps=r["GBps"], predicted_s=r["predicted_s"],
        relay=r.get("relay"), paths_GiB={k: round(v["bytes"] / GiB, 2) for k, v in r["paths"].items()},
        failed=r["failed_fetches"], verify=(r.get("verify") or {}).get("bad"))


pool = Pool(NODES, log=say)
pool.start(react=False)
for n in NODES:
    pool.rpc[n].call("reset")
for m, dn in DURABLE.items():
    pool.register(m, f"{man}/{m}.json", dn)
pool.hbm_park.update({"node27": {4, 5, 6, 7}, "node29": {4, 5, 6, 7}})
w = pool.warm_arenas(seconds=float(os.environ.get("WARM_S", "40")))
for m in DURABLE:                                    # primary copies: DRAM on node27/29/30
    r = pool.park(m, copies=1, tiers=("dram",), avoid_nodes=("node28",))
    say(event="parked", model=m, GiB=r["GiB"], short=r["short"])

# (a) prestage on node27 and restore from it
pool.relay_policy = "idle"
for m in DURABLE:
    t0, got = time.time(), 0
    for _ in range(32):
        g = pool.prestage(m, "node27", max_bytes=16 * GiB)
        if not g:
            break
        got += g
    say(event="prestaged", model=m, GiB=round(got / GiB, 2), s=round(time.time() - t0, 2), staged=round(pool.staged(m, "node27"), 3))
for k in range(3):
    for m, cards in (("9g-8b", [0]), ("qwen36-35b", [0, 1])):
        for allow in (("dram",), ("dram", "hbm")):
            r = pool.restore(m, "node27", cards, allow=allow, verify=(k == 0))
            row(f"{m}@node27{cards} allow={'+'.join(allow)}", r)

# (b) demotion speed: HBM-first vs DRAM-first, instance is the only fast copy
for mode in (False, True, False, True):
    pool.demote_hbm_first = mode
    pool.relay_policy = "off"
    r = pool.restore("qwen36-35b", "node28", [1, 2], allow=("dram",), release=False)
    pool.publish("qwen36-35b", "node28", r["job"])
    drop = pool.drop_model("qwen36-35b", tiers=("dram", "hbm"), nodes=None)
    d = pool.retire("qwen36-35b", "node28", r["job"], [1, 2])
    say(event="demote_case", hbm_first=mode, need_GiB=d["need_GiB"], demoted_GiB=d["demoted_GiB"],
        left_slow_GiB=d["left_slow_GiB"], demote_s=d["demote_s"], s=d["s"])
    pool.settle(max_bytes=128 * GiB)                  # what the background does after an hbm-first demote
    pool.park("qwen36-35b", copies=1, tiers=("dram",), avoid_nodes=("node28",))

# (c) a borrowed relay card is taken mid-restore
pool.relay_policy = "idle"
picked = pool.relay_pick("node28", [1])
say(event="relay_pick", relay=picked)
if picked:
    victim = sorted(picked.values())[0]
    res = {}

    def go():
        res["r"] = pool.restore("9g-8b", "node28", [1], allow=("dram",), verify=True)
    th = threading.Thread(target=go)
    th.start()
    time.sleep(0.04)
    t0 = time.time()
    rel = pool.rpc["node28"].call("arena", card=victim, gib=0)
    rel_s = time.time() - t0
    th.join(120)
    r = res.get("r")
    say(event="owner_first", card=victim, release_s=round(rel_s, 4), restored=r is not None,
        total_s=r and r["total_s"], bad=r and (r.get("verify") or {}).get("bad"),
        paths_GiB=r and {k: round(v["bytes"] / GiB, 2) for k, v in r["paths"].items()})
    pool.rpc["node28"].call("arena", card=victim, gib=48)
say(event="done")
pool.stop()
