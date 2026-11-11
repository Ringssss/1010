"""Tests for live/placement.py on the real four-node inventories and on a tiny graph."""
import json
import os
import time

import pytest

from fluidckpt.live.graph import GiB, LiveGraph, build_topology
from fluidckpt.live.placement import KeepAlivePool, park
from fluidckpt.live.types import ResourceState, Snapshot

HERE = os.path.dirname(__file__)
RDIR = os.path.join(HERE, "../../../../../results/v0.1.23.3/task2/live_graph/graph_r02")


@pytest.fixture(scope="module")
def graph():
    probes = [json.load(open(os.path.join(RDIR, f"probe_n{n}.json"))) for n in (27, 28, 29, 30)]
    return LiveGraph(build_topology([p["inventory"] for p in probes]))


def model(nshards=2, chunks=40, size=256 << 20):
    return {sh: [(f"s{sh}c{i}", size) for i in range(chunks)] for sh in range(nshards)}


def snapshot(g, background=None):
    now = time.time()
    rs = {r: ResourceState(b, now) for r, b in (background or {}).items()}
    return Snapshot(g.topology, {}, rs, 1, now)


def test_park_respects_capacity_and_budget(graph):
    shards = model()
    total = sum(n for cs in shards.values() for _, n in cs)
    cands = [v for v in graph.topology.vertices if "/dram/" in v and v.startswith("n27")]
    free = {v: 3 * GiB for v in cands}
    scen = [(1.0, {0: "n27/hbm/4", 1: "n27/hbm/5"})]
    rep, info = park(snapshot(graph), graph.paths, shards, "n28/ssd/nvme0n1", scen,
                     budget_bytes=total // 2, free_bytes=free, candidates=cands)
    assert info["status"] == "optimal"
    placed = info["placed_bytes"]
    assert all(b <= free[v] + (256 << 20) for v, b in placed.items())       # rounding: one chunk slack
    assert sum(placed.values()) <= total // 2 + 2 * (256 << 20)
    assert all("n28/ssd/nvme0n1" in vs for vs in rep.values())              # durable kept everywhere


def test_park_spreads_over_sources_and_avoids_busy_ones(graph):
    shards = model(nshards=1, chunks=64)
    cands = [f"n27/dram/{k}" for k in range(8)]
    free = {v: 100 * GiB for v in cands}
    scen = [(1.0, {0: "n27/hbm/4"})]
    rep, info = park(snapshot(graph), graph.paths, shards, "n28/ssd/nvme0n1", scen, 1 << 40, free, cands)
    # GPU ingress (PCIe 47.5 + P2P relays 165) exceeds one NUMA node's 95 GB/s read bandwidth
    assert len(info["placed_bytes"]) >= 3
    busy = {f"n27/memrd/{k}": 90.0 for k in range(4)}                       # NUMA 0-3 nearly saturated
    rep2, info2 = park(snapshot(graph, busy), graph.paths, shards, "n28/ssd/nvme0n1", scen, 1 << 40, free, cands)
    on_busy = sum(b for v, b in info2["placed_bytes"].items() if int(v.split("/")[-1]) < 4)
    assert on_busy < sum(info2["placed_bytes"].values()) * 0.25


def test_pool_evict_lru_and_keep_durable():
    pool = KeepAlivePool()
    pool.add("old", {"a": ["n27/dram/0", "ssd"], "b": ["n27/dram/0", "ssd"]}, {"a": 10, "b": 10}, "ssd", now=1)
    pool.add("new", {"c": ["n27/dram/0", "ssd"]}, {"c": 10}, "ssd", now=5)
    assert pool.held()["n27/dram/0"] == 30
    freed, lost = pool.evict("n27/dram/0", 15)
    assert freed == 20 and [m for m, _ in lost] == ["old", "old"]
    assert pool.models["new"]["replicas"]["c"] == ["n27/dram/0", "ssd"]
    assert pool.evict("ssd", 100) == (0, [])
