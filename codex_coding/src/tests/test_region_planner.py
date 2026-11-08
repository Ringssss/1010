"""Partial replica coverage and shared physical path constraints."""
import pytest
from fluidckpt.planner.regions import make_regions, plan_regions


def source(name, off, size, numa=0, rank=0):
    return {"replica_id": name, "generation": 1, "identity": f"m{rank}", "rank": rank,
            "logical_offset": off, "length": size, "worker": rank, "numa": numa,
            "state": "VALID", "build": {"primary": name.startswith("base")}}


def demand(n, rank=0):
    return {"tp": rank, "identity": f"m{rank}", "dst_w": rank, "gpu": rank,
            "nbytes": n, "distances": {"0": 10, "3": 15}}


def profile():
    return {"paths": {"0:0": {"GBps": 20, "resources": ["in0"]},
                      "3:0": {"GBps": 20, "resources": ["in0"]}}, "resources": {"in0": 20}}


@pytest.mark.parametrize("strategy", ["fixed", "nearest", "fastest", "greedy", "resource_lp"])
def test_partial_replica_offsets_and_tail(strategy):
    replicas = [source("base", 0, 100_000_017), source("extra", 20_000_000, 30_000_000, 3)]
    result = plan_regions([demand(100_000_017)], replicas, profile(), strategy, quantum=4096)
    assert result["regions"] == 3
    assert sum(o["n"] for o in result["ops"]) == 100_000_017
    for op in result["ops"]:
        r = next(r for r in replicas if r["replica_id"] == op["replica_id"])
        assert op["source_offset"] == op["off"] - r["logical_offset"]
        assert op["source_offset"] + op["n"] <= r["length"]
    assert result["rounded_resource_bound_s"] >= 100_000_017 / 20e9 - 1e-9


def test_regions_do_not_multiply_path_capacity():
    replicas = [source("base", 0, 40_000_000_000), source("extra", 5_000_000_000, 10_000_000_000)]
    result = plan_regions([demand(40_000_000_000)], replicas, profile())
    assert result["lower_bound_s"] == pytest.approx(2.0)


def test_uncovered_or_wrong_identity_fails():
    with pytest.raises(ValueError):
        make_regions([demand(100)], [source("partial", 1, 99)], profile())
    bad = source("base", 0, 100); bad["identity"] = "wrong"
    with pytest.raises(ValueError): make_regions([demand(100)], [bad], profile())


def test_shared_source_reroutes_one_demand():
    p = {"paths": {"0:0": {"GBps": 20, "resources": ["mem0", "gpu0"]},
                   "0:1": {"GBps": 20, "resources": ["mem0", "gpu1"]},
                   "3:1": {"GBps": 20, "resources": ["mem3", "gpu1"]}},
         "resources": {"mem0": 20, "mem3": 20, "gpu0": 20, "gpu1": 20}}
    replicas = [source("base0", 0, 20_000_000_000), source("base1", 0, 20_000_000_000, rank=1),
                source("extra1", 0, 20_000_000_000, 3, rank=1)]
    ds = [demand(20_000_000_000, rank=g) for g in (0, 1)]
    assert plan_regions(ds, replicas, p)["lower_bound_s"] == pytest.approx(1.0)
    assert plan_regions(ds, replicas, p, "fixed")["rounded_resource_bound_s"] == pytest.approx(2.0)
