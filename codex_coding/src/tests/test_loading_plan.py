"""Hand-solvable bandwidth constraints and byte-coverage failure gates."""
import pytest
from fluidckpt.planner.loading import plan, validate_ops


def candidate(rate, resources, worker=0):
    return {"GBps": rate, "resources": resources, "op": {"kind": "h2d", "dst_w": worker, "snap": "s"}}


def test_independent_sources_split_exactly():
    demands = [{"tp": 0, "nbytes": 40_000_000_017,
                "candidates": [candidate(20, ["a"]), candidate(10, ["b"])]}]
    result = plan(demands, {"a": 20, "b": 10}, quantum=1024)
    assert result["lower_bound_s"] == pytest.approx(40_000_000_017 / 30e9)
    assert sum(op["n"] for op in result["ops"]) == 40_000_000_017
    assert result["rounded_resource_bound_s"] < 1.333334


def test_shared_gpu_ingress_cannot_be_added():
    demand = {"tp": 0, "nbytes": 40_000_000_000,
              "candidates": [candidate(20, ["in"]), candidate(10, ["in"])]}
    result = plan([demand], {"in": 20})
    assert result["lower_bound_s"] == pytest.approx(2.0)


def test_rank_identity_and_competing_memory():
    demands = [{"tp": g, "nbytes": 20_000_000_000, "candidates": [candidate(20, ["mem"], g)]}
               for g in (0, 1)]
    result = plan(demands, {"mem": 20})
    assert result["lower_bound_s"] == pytest.approx(2.0)
    assert all(op["tp"] == op["dst_w"] for op in result["ops"])


@pytest.mark.parametrize("intervals", [[(0, 5), (4, 6)], [(0, 4), (5, 5)], [(0, 9)], [(0, 11)]])
def test_corrupt_coverage_is_rejected(intervals):
    ops = [{"kind": "h2d", "dst_w": 0, "tp": 0, "off": o, "n": n} for o, n in intervals]
    with pytest.raises(ValueError):
        validate_ops(ops, {0: 10})


def test_unknown_capacity_and_missing_replica_rejected():
    with pytest.raises(ValueError):
        plan([{"tp": 0, "nbytes": 100, "candidates": [candidate(10, ["unmeasured"])]}], {})
    with pytest.raises(ValueError):
        plan([{"tp": 0, "nbytes": 100, "candidates": []}], {})
