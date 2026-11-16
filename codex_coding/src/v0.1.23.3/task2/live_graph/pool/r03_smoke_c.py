"""(c) retire demotes an instance's chunks into the pool; the next restore is all-fast.
Also: lazy exclusivity (trim counts a published instance as redundancy)."""
import time

from r03_smoke_common import NODES, cap, make, numa_report, paths
from fluidckpt.live.pool import GiB

M = "qwen36-35b"
pool, say = make("c", react=True)
B = cap(pool, 30)
r = pool.restore(M, "node27", [1, 2], allow=("file",), release=False)          # cold: nothing is parked
say(event="cold_restore", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r))
pool.publish(M, "node27", r["job"])
say(event="published", cov_fast=pool.coverage(M), cov_dram=pool.coverage(M, tiers=("dram",)))
d = pool.retire(M, "node27", r["job"], [1, 2], budget=B)
say(event="retire", **d)
say(event="after_retire", cov_fast=pool.coverage(M), cov_all=pool.coverage_all(), numa=numa_report(pool)["node27"],
    jobs=pool.rpc["node27"].call("stat")["jobs"])
say(event="wait_pinned", **pool.wait_pinned())
r = pool.restore(M, "node29", [1, 2], verify=True, release=False)
say(event="restore_after_demote", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), verify=r["verify"])
pool.publish(M, "node29", r["job"])
freed = pool.trim(10 * GiB)
say(event="trim_exclusive", freed_GiB=round(freed / GiB, 2), cov_fast=pool.coverage(M), cov_dram=pool.coverage(M, tiers=("dram",)))
d = pool.retire(M, "node29", r["job"], [1, 2], budget=B)
say(event="retire2", **d)
say(event="after_retire2", cov_fast=pool.coverage(M), cov_dram=pool.coverage(M, tiers=("dram",)))
r = pool.restore(M, "node28", [1, 2], verify=True)
say(event="restore3", total_s=r["total_s"], GBps=r["GBps"], paths=paths(r), verify=r["verify"])
say(event="events", n=len(pool.events), kinds=[e["event"] for e in pool.events])
for n in NODES:
    pool.rpc[n].call("reset")
pool.stop()
