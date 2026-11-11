"""Tests for live/flow.py on hand-checkable topologies."""
import time

import pytest

from fluidckpt.live.flow import plan_fetch
from fluidckpt.live.types import Edge, Path, Resource, ResourceState, Snapshot, Topology, Vertex

GB = 1e9


def topo(edges, res):
    vs = {}
    for e in edges:
        for v in (e.src, e.dst):
            vs[v] = Vertex(v, "n", "dram" if v != "d" else "hbm", v, 1 << 40)
    return Topology(vs, edges, {r: Resource(r, c) for r, c in res.items()})


def snap(t, background=None):
    rs = {r: ResourceState(b, time.time()) for r, b in (background or {}).items()}
    return Snapshot(t, {}, rs, 1, time.time())


def paths_of(t):
    def f(src, dst):
        return [Path((e,)) for e in t.edges if e.src == src and e.dst == dst]
    return f


def chunks(n, size, src, dst="d"):
    demands = [(f"{src}{i}", size, dst) for i in range(n)]
    return demands, {cid: [src] if isinstance(src, str) else list(src) for cid, _, _ in demands}


@pytest.mark.parametrize("strategy", ["lp", "greedy", "fastest"])
def test_single_path(strategy):
    t = topo([Edge("a", "d", "h2d", ("ra",), 100.0)], {"ra": 10.0})
    dem, rep = chunks(10, 1 * GB, "a")
    p = plan_fetch(snap(t), dem, rep, paths_of(t), strategy)
    assert len(p.transfers) == 10 and abs(p.predicted_s - 1.0) < 1e-9


def test_two_disjoint_sources_add_bandwidth():
    t = topo([Edge("a", "d", "h2d", ("ra",), 100.0), Edge("b", "d", "h2d", ("rb",), 100.0)],
             {"ra": 10.0, "rb": 30.0})
    dem = [(f"c{i}", 1 * GB, "d") for i in range(40)]
    rep = {cid: ["a", "b"] for cid, _, _ in dem}
    lp = plan_fetch(snap(t), dem, rep, paths_of(t), "lp")
    assert abs(lp.lower_bound_s - 1.0) < 1e-6 and abs(lp.predicted_s - 1.0) < 1e-6
    fast = plan_fetch(snap(t), dem, rep, paths_of(t), "fastest")
    assert abs(fast.predicted_s - 40 / 30) < 1e-6                 # everything on the faster source
    assert {tr.chunk_id for tr in lp.transfers} == {cid for cid, _, _ in dem}


def test_shared_bottleneck_caps_multipath():
    t = topo([Edge("a", "d", "h2d", ("ra", "in"), 100.0), Edge("b", "d", "h2d", ("rb", "in"), 100.0)],
             {"ra": 30.0, "rb": 30.0, "in": 40.0})
    dem = [(f"c{i}", 1 * GB, "d") for i in range(40)]
    rep = {cid: ["a", "b"] for cid, _, _ in dem}
    p = plan_fetch(snap(t), dem, rep, paths_of(t), "lp")
    assert abs(p.predicted_s - 1.0) < 1e-6                        # 40 GB over the 40 GB/s ingress


def test_background_load_moves_traffic():
    t = topo([Edge("a", "d", "h2d", ("ra",), 100.0), Edge("b", "d", "h2d", ("rb",), 100.0)],
             {"ra": 40.0, "rb": 40.0})
    dem = [(f"c{i}", 1 * GB, "d") for i in range(40)]
    rep = {cid: ["a", "b"] for cid, _, _ in dem}
    p = plan_fetch(snap(t, {"ra": 30.0}), dem, rep, paths_of(t), "lp")   # a has 10 GB/s left
    by_src = {}
    for tr in p.transfers:
        by_src[tr.path.src] = by_src.get(tr.path.src, 0) + tr.nbytes
    assert by_src["b"] == 32 * GB and by_src["a"] == 8 * GB
    assert abs(p.predicted_s - 0.8) < 1e-6


def test_in_place_and_unroutable():
    t = topo([Edge("a", "d", "h2d", ("ra",), 100.0)], {"ra": 10.0})
    p = plan_fetch(snap(t), [("x", GB, "d")], {"x": ["d"]}, paths_of(t))
    assert p.transfers == [] and p.predicted_s == 0
    with pytest.raises(ValueError):
        plan_fetch(snap(t), [("y", GB, "d")], {"y": ["zz"]}, lambda s, d: [])


def test_rounding_covers_every_chunk_once_with_mixed_sizes():
    t = topo([Edge("a", "d", "h2d", ("ra",), 100.0), Edge("b", "d", "h2d", ("rb",), 100.0)],
             {"ra": 10.0, "rb": 10.0})
    dem = [(f"c{i}", (i % 5 + 1) * 64 * 2**20, "d") for i in range(101)]
    rep = {cid: ["a", "b"] for cid, _, _ in dem}
    p = plan_fetch(snap(t), dem, rep, paths_of(t), "lp")
    assert sorted(tr.chunk_id for tr in p.transfers) == sorted(cid for cid, _, _ in dem)
    assert p.predicted_s <= p.lower_bound_s * 1.02                # rounding loss < 2%
