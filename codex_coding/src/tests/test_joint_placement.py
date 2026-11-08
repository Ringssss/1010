"""Check the finite-candidate solver against exhaustive feasible placements."""
import itertools
import pytest

from fluidckpt.cluster.joint import joint_placement
from fluidckpt.cluster.planning import restore_plan

G = 1 << 30


def case():
    workers = [{"worker": i, "node": f"node{i//2}", "numa": i%2,
                "capabilities": ["network", "peer", "local_dram", "local_hbm"]} for i in range(4)]
    profile = {"rates": {"network": 1, "peer": 1, "local_dram": 2, "local_hbm": 8},
               "gpu_ingress_GBps": 1000, "gpu_egress_GBps": 1000, "dram_GBps": 1000,
               "network_node_GBps": 1000, "serial_target_execution": True}
    base = [{"id": f"base{i}", "identity": "model", "tp": i, "worker": i, "tier": "dram", "numa": i,
             "off": 0, "n": 2*G, "state": "VALID", "generation": 1} for i in range(2)]
    targets = [{"key": str(i), "identity": "model", "tp": i, "worker": i+2, "off": 0, "n": 2*G} for i in range(2)]
    candidates = [dict(base[i], id=f"cache{i}{block}", worker=i+2, tier="hbm", off=block*G, n=G)
                  for i in range(2) for block in range(2)]
    return base, candidates, targets, workers, profile


def test_milp_matches_enumeration_and_balances_ranks():
    base, candidates, targets, workers, profile = case()
    result = joint_placement(base, candidates, targets, workers, profile, {"hbm": 2*G}, {"2:hbm": 2*G, "3:hbm": 2*G})
    values = [restore_plan(base+list(subset), targets, workers, profile, "resource_lp")["estimated_transfer_s"]
              for n in range(3) for subset in itertools.combinations(candidates, n)]
    assert result["objective_s"] == pytest.approx(min(values))
    assert result["rounded_plan_s"] == pytest.approx(min(values))
    assert {r["worker"] for r in result["selected"]} == {2, 3}
    assert result["tier_bytes"]["hbm"] == 2*G


def test_serial_cost_adds_paths_instead_of_assuming_overlap():
    base, candidates, targets, workers, profile = case()
    plan = restore_plan(base[:1]+candidates[:1], targets[:1], workers, profile, "resource_lp")
    assert plan["estimated_transfer_s"] == pytest.approx(G/1e9*(1+1/8))
    profile["serial_target_execution"] = False
    fluid = restore_plan(base[:1]+candidates[:1], targets[:1], workers, profile, "resource_lp")
    assert fluid["estimated_transfer_s"] < plan["estimated_transfer_s"]


def test_zero_budget_keeps_complete_base():
    base, candidates, targets, workers, profile = case()
    result = joint_placement(base, candidates, targets, workers, profile, {"hbm": 0}, {"2:hbm": 0, "3:hbm": 0})
    assert result["selected"] == []
    assert result["objective_s"] == pytest.approx(2*G/1e9)


def test_missing_capacity_and_unbuildable_region_rejected():
    base, candidates, targets, workers, profile = case()
    with pytest.raises(ValueError, match="capacity budget"):
        joint_placement(base, candidates, targets, workers, profile, {"hbm": G}, {})
    candidates[0]["identity"] = "wrong-model"
    with pytest.raises(ValueError, match="build source"):
        joint_placement(base, candidates, targets, workers, profile, {"hbm": G}, {"2:hbm": G, "3:hbm": G})
