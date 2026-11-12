"""Direct-to-HBM restores of one model: which ingress paths, which plan, how many cards.
  direct_bench.py <manifest_dir> <out_dir> <model> [card sets, e.g. 1,2/1,2,5,6/0,1,2,3,4,5,6,7]"""
import json, os, sys, time
from fluidckpt.live.pool import GiB, Pool
NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
HOME = "node28"
man, out_dir, name = sys.argv[1], sys.argv[2], sys.argv[3]
sets = [[int(x) for x in s.split(",")] for s in (sys.argv[4] if len(sys.argv) > 4 else "1,2/1,2,5,6/0,1,2,3,4,5,6,7").split("/")]
os.makedirs(out_dir, exist_ok=True)
logf = open(os.path.join(out_dir, "direct_bench.log"), "a")
def say(**k):
    k.setdefault("t", round(time.time(), 3)); line = json.dumps(k); logf.write(line + "\n"); logf.flush()
    if k.get("event") not in ("restore", "park"): print(line[:330], flush=True)
pool = Pool(NODES, log=say); pool.start(react=False)
for n in NODES: pool.rpc[n].call("reset")
m = pool.register(name, f"{man}/{name}.json", HOME)
others = tuple(n for n in NODES if n != HOME)
def med(n=3, **kw):
    rs = sorted((pool.restore(name, HOME, **kw) for _ in range(n)), key=lambda r: r["total_s"]); return rs[len(rs) // 2]
def row(case, r):
    say(event="direct", case=case, cards=r["cards"], GiB=r["GiB"], total_s=r["total_s"], plan_s=r["plan_s"], GBps=r["GBps"],
        predicted_s=r["predicted_s"], paths_GiB={k: round(v["bytes"] / GiB, 1) for k, v in r["paths"].items()},
        stolen={k: v["stolen"] for k, v in r["paths"].items() if v["stolen"]}, failed=r["failed_fetches"], verify=r.get("verify"))
p = pool.park(name, copies=1, tiers=("dram",), pin_nodes=(HOME,), avoid_nodes=others)
say(event="parked", where="home pinned", GiB=p["GiB"], s=p["s"])
p = pool.park(name, copies=4, tiers=("dram",))
say(event="parked", where="one copy on each other node", GiB=p["GiB"], s=p["s"], short=p["short"])
if any(pool.info[n].get("arena_GiB") for n in NODES) and float(os.environ.get("WARM_S", "90")) > 0:
    w = pool.warm_arenas(seconds=float(os.environ.get("WARM_S", "90")))
    for n, r in w.items():
        say(event="arena_warmup", node=n, s=r["s"], last_GBps={k: (v[-1] if v else None) for k, v in r["GBps_per_second"].items()},
            first_GBps={k: (v[0] if v else None) for k, v in r["GBps_per_second"].items()}, steps={k: len(v) for k, v in r["GBps_per_second"].items()})
ARENA = any(pool.info[n].get("arena_GiB") for n in NODES)
tag = "arena" if ARENA else "fresh HBM"
for cards in sets:
    row("local pinned DRAM only (H2D)", med(cards=cards, allow=("dram",), single_source=HOME))
    pool.drop_model(name, tiers=("dram",), nodes={HOME})
    row(f"three remote nodes, direct into {tag}, flow LP on the table [first]", med(cards=cards, allow=("dram",)))
    if not ARENA:
        row("three remote nodes, host relay (previous data plane)", med(cards=cards, allow=("dram",), direct=False, strategy="greedy"))
    row(f"three remote nodes, direct into {tag}, round-robin ports", med(cards=cards, allow=("dram",), strategy="rr"))
    row(f"three remote nodes, direct into {tag}, LP blind to the table", med(cards=cards, allow=("dram",), strategy="lp_blind"))
    row(f"one remote node (node30), direct into {tag}, flow LP", med(cards=cards, allow=("dram",), single_source="node30"))
    r = pool.restore(name, HOME, cards, allow=("dram",), verify=True)
    row(f"three remote nodes, {tag}, flow LP (verified from HBM)", r)
    for k in range(int(os.environ.get("SOAK", "0"))):       # keep traffic flowing into the same buffers
        pool.restore(name, HOME, cards, allow=("dram",))
    row(f"three remote nodes, direct into {tag}, flow LP on the table [again]", med(cards=cards, allow=("dram",)))
    pool.park(name, copies=4, tiers=("dram",), pin_nodes=(HOME,))
    row(f"local + three remote, {tag}, round-robin", med(cards=cards, allow=("dram",), strategy="rr"))
    row(f"local + three remote, {tag}, flow LP on the table", med(cards=cards, allow=("dram",)))
    row(f"local + three remote, {tag}, flow LP, no stealing", med(cards=cards, allow=("dram",), steal=False))
pool.unpark(name); pool.stop()
