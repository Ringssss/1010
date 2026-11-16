"""(e) striped slow tier: NVMe copies on several disks, restore from slow sources only."""
from r03_smoke_common import NODES, make, paths

M = "qwen36-35b"
pool, say = make("e", react=True)
r = pool.restore(M, "node27", [1, 2], allow=("file",))
say(event="single_disk", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), cap_slow=r["plan"]["cap_slow"])
s = pool.stripe(M)
say(event="stripe", s=s["s"], GiB=s["GiB"], GBps=s.get("GBps"), short=s["short"], vertices=s["vertices"], errors=s["errors"])
for node, cards in (("node27", [1, 2]), ("node29", [1, 2])):
    r = pool.restore(M, node, cards, allow=("ssd", "file"), verify=node == "node27")
    say(event="striped", node=node, total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), cap_slow=r["plan"]["cap_slow"],
        rates={k: v for k, v in r["rates"].items() if not k.startswith(("net", "dram"))}, verify=r.get("verify"))
for n in NODES:
    pool.rpc[n].call("reset")
pool.stop()
