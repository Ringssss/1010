"""Shared setup for the r03 smoke tests (run inside zjn-fluid-pool-27)."""
import json
import os
import sys
import threading
import time

from fluidckpt.live.graph import Policy
from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
REPO = "/home/zhujianian/zhujianian/fluidckpt_zjn"
MAN = f"{REPO}/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02/manifests"
OUT = f"{REPO}/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r03/smoke"
DURABLE = {"9g-8b": "node28", "qwen36-35b": "node28", "qwen3-0.6b": "node28", "clover-35b": "node30",
           "glm53-n28": "node28", "glm53-n30": "node30"}


def make(tag, react=True, models=("qwen36-35b",), headroom=0.15, reset=True):
    os.makedirs(OUT, exist_ok=True)
    logf = open(os.path.join(OUT, f"{tag}.log"), "a")
    lock = threading.Lock()

    def log(**k):
        k.setdefault("t", round(time.time(), 3))
        with lock:
            logf.write(json.dumps(k) + "\n")
            logf.flush()

    def say(**k):
        log(**k)
        print(json.dumps(k)[:1500], flush=True)
    pool = Pool(NODES, policy=Policy(dram_headroom_frac=headroom, dram_reclaim_frac=0.9), log=log)
    if reset:
        for n in NODES:
            pool.rpc[n].call("reset")
    pool.start(react=react)
    for m in models:
        pool.register(m, os.path.join(MAN, m + ".json"), DURABLE[m])
    return pool, say


def cap(pool, gib):
    return {vid: int(gib * GiB) for vid, v in pool.topology.vertices.items() if v.kind == "dram"}


def numa_report(pool):
    out = {}
    for n in NODES:
        st = pool.rpc[n].call("stat", numa=True)
        out[n] = {"intended_GiB": {k: round(v / GiB, 1) for k, v in sorted(st["slab_numa_intended"].items())},
                  "actual_GiB": {k: round(v / GiB, 1) for k, v in sorted(st["slab_numa_actual"].items())},
                  "rss_anon_GiB": round(st["rss_anon"] / GiB, 1), "slabs": st["slabs"], "registered": st["slabs_registered"]}
    return out


def paths(r):
    return {k: round(v["bytes"] / GiB, 2) for k, v in r["paths"].items()}
