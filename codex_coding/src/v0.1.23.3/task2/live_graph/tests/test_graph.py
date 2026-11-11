"""Unit tests for live/graph.py on synthetic inventories (no hardware access)."""
import time

from fluidckpt.live.graph import GiB, LiveGraph, Policy, build_topology


def inventory(node, gpus=4, numa=2, used=None):
    return {"node": node, "gpus": [{"index": i, "bdf": None, "numa": i % numa, "hbm_total": 64 * GiB} for i in range(gpus)],
            "numa": [{"id": k, "total": 64 * GiB} for k in range(numa)],
            "ssds": [{"name": "nvme0n1", "size": 1000 * GiB, "numa": 0, "mounts": ["/data"]},
                     {"name": "nvme1n1", "size": 400 * GiB, "numa": 0, "mounts": ["/", "/boot"]}],
            "nics": [{"name": f"shca_{k}", "numa": k, "rate_GBps": 50.0, "state": "4: ACTIVE"} for k in range(numa)]}


def fast(t, hbm_used=0, nvme_rd=0.0, ib_tx=0.0, gpus=4, numa=2):
    return {"t": t, "hbm_used": {i: hbm_used for i in range(gpus)},
            "dram": {k: {"free": 10 * GiB, "reclaimable": 20 * GiB} for k in range(numa)},
            "ssd": {"nvme0n1": {"free": 500 * GiB, "rd_GBps": nvme_rd, "wr_GBps": 0.0, "util": 0.0}},
            "nic": {f"shca_{k}": {"tx_GBps": ib_tx, "rx_GBps": 0.0} for k in range(numa)}}


def make():
    topo = build_topology([inventory("a"), inventory("b")])
    return topo, LiveGraph(topo, Policy(ewma=1.0))


def test_topology_shape():
    topo, _ = make()
    kinds = {}
    for v in topo.vertices.values():
        kinds[v.kind] = kinds.get(v.kind, 0) + 1
    assert kinds == {"hbm": 8, "dram": 4, "ssd": 2}          # system disk excluded
    assert "a/ssd/nvme1n1" not in topo.vertices
    # every edge's resources exist and carry a provenance
    for e in topo.edges:
        for r in e.resources:
            assert topo.resources[r].source
    ib = [e for e in topo.edges if e.kind == "ib"]
    assert all(topo.vertices[e.src].node != topo.vertices[e.dst].node for e in ib)


def test_free_bytes_and_headroom():
    _, g = make()
    g.ingest("a", fast=fast(time.time(), hbm_used=40 * GiB))
    s = g.snapshot()
    assert s.vertices["a/hbm/0"].free_bytes == 64 * GiB - 40 * GiB - g.policy.hbm_headroom
    # dram: free + half of reclaimable - 10% of total
    assert s.vertices["a/dram/0"].free_bytes == int(10 * GiB + 10 * GiB - 0.1 * 64 * GiB)
    assert s.vertices["a/ssd/nvme0n1"].free_bytes == int(500 * GiB - 100 * GiB)


def test_background_excludes_own_traffic():
    _, g = make()
    g.own_traffic({"a/ib_tx/shca_0": 30.0})
    g.ingest("a", fast=fast(time.time(), ib_tx=40.0))
    s = g.snapshot()
    assert abs(s.resources["a/ib_tx/shca_0"].background_GBps - 10.0) < 1e-9
    assert abs(s.residual_GBps("a/ib_tx/shca_0") - 40.0) < 1e-9     # 50 nominal - 10 others
    assert abs(s.resources["a/ib_tx/shca_1"].background_GBps - 40.0) < 1e-9


def test_stale_state_is_derated():
    _, g = make()
    t = time.time()
    g.ingest("a", fast=fast(t - 10, nvme_rd=1.0))
    s = g.snapshot(now=t)
    st = s.resources["a/nvme_rd/nvme0n1"]
    assert st.stale and s.vertices["a/hbm/0"].stale
    assert abs(s.residual_GBps("a/nvme_rd/nvme0n1") - (7.0 - 1.0) * 0.5) < 1e-9


def test_paths_cached_across_state_updates():
    _, g = make()
    p1 = g.paths("b/dram/0", "a/hbm/1")
    assert p1 and all(p.src == "b/dram/0" and p.dst == "a/hbm/1" for p in p1)
    assert all(not any(g.topology.vertices[e.dst].kind == "ssd" for e in p.edges[:-1]) for p in p1)
    g.ingest("a", fast=fast(time.time(), hbm_used=10 * GiB))
    assert g.paths("b/dram/0", "a/hbm/1") is p1


def test_paths_never_detour_and_are_undominated():
    topo = build_topology([inventory("a"), inventory("b"), inventory("c")])
    g = LiveGraph(topo)
    for p in g.paths("b/hbm/0", "a/hbm/1", k=50):
        nodes = [topo.vertices[e.dst].node for e in p.edges]
        assert "c" not in nodes
    ps = g.paths("a/dram/0", "a/hbm/1", k=50)
    sets = [set(p.resources) for p in ps]
    assert not any(i != j and sets[i] <= sets[j] for i in range(len(sets)) for j in range(len(sets)))
    assert ps[0].edges[0].kind == "h2d" and len(ps[0].edges) == 1


def test_changed_since():
    _, g = make()
    g.ingest("a", fast=fast(time.time()))
    s0 = g.snapshot()
    g.ingest("a", fast=fast(time.time(), ib_tx=2.0))           # 4% of 50 GB/s: below 20% threshold
    assert g.changed_since(s0) == []
    g.ingest("a", fast=fast(time.time(), ib_tx=30.0))
    assert "a/ib_tx/shca_0" in g.changed_since(s0)
    g.ingest("a", fast=fast(time.time(), hbm_used=50 * GiB, ib_tx=30.0))
    assert "a/hbm/0" in g.changed_since(s0)
