"""Managed placement coverage, physical budgets, versioning and resumable commits."""
import copy
import threading

import pytest

from fluidckpt.cluster.managed import (aligned, capacity_limits, check_snapshot, fits,
    generate_candidates, normalize_request, rows, simulate_action, solve_joint_request, state_hash)
from fluidckpt.cluster.managed_control import ManagedPlacementMixin, ManagedPlacementError

G = 1 << 30


def case(size=2*G+17):
    workers = [{"worker": i, "node": f"node{i//2}", "gpu": i%2, "numa": i%2,
        "session": "test", "epoch": f"epoch{i}", "transactions": [],
        "replicas": [], "budget_bytes": {"dram": 20*G, "hbm": 20*G},
        "used_bytes": {"dram": 0, "hbm": 0}, "headroom_bytes": {"dram": 20*G, "hbm": 20*G},
        "capabilities": ["network", "peer", "local_dram", "local_hbm"]} for i in range(4)]
    targets = []
    for i in range(2):
        r = {"id": f"base{i}", "identity": "model", "tp": i, "off": 0, "n": size,
             "worker": i, "tier": "dram", "numa": i, "epoch": f"epoch{i}", "generation": 1,
             "state": "VALID", "leases": 0, "allocated_bytes": aligned(size), "meta": {}, "index": {}}
        workers[i]["replicas"].append(r); workers[i]["used_bytes"]["dram"] += aligned(size)
        targets.append({"key": str(i), "identity": "model", "tp": i, "off": 0, "n": size, "worker": i+2})
    profile = {"rates": {"network": 1, "peer": 1, "local_dram": 2, "local_hbm": 8},
        "gpu_ingress_GBps": 1000, "gpu_egress_GBps": 1000, "dram_GBps": 1000,
        "network_node_GBps": 1000, "serial_target_execution": True}
    q = {"schema_version": 1, "scope": "test", "targets": targets, "base_ids": ["base0", "base1"],
        "budget": {"tier_bytes": {"hbm": 2*G}, "locations": [
            {"worker": i, "tier": "hbm", "max_bytes": 2*G} for i in (2, 3)]}}
    return workers, q, profile


def test_full_candidates_keep_exact_tail_and_concentrated_option():
    ws, q, p = case(); q = normalize_request(q, ws)
    cs, audit = generate_candidates(q, ws, p, capacity_limits(q, ws))
    for rank in range(2):
        own = sorted((c for c in cs if c["tp"] == rank and c["worker"] == rank+2), key=lambda c: c["off"])
        assert [(c["off"], c["n"]) for c in own] == [(0, G), (G, G), (2*G, 17)]
        assert sum(c["n"] for c in own) == 2*G+17
    assert audit["count"] == 12
    plan = solve_joint_request(ws, q, p)
    assert plan["solver"]["status"] == "optimal"
    assert plan["predicted_after_s"] < plan["predicted_before_s"]
    assert {r["worker"] for r in plan["selected"]} == {2, 3}
    assert plan["limits"]["locations"]["2:hbm"] == 2*G  # concentration was legal


def test_zero_budget_and_large_threshold_preserve_current():
    ws, q, p = case()
    q["budget"]["tier_bytes"]["hbm"] = 0
    plan = solve_joint_request(ws, q, p)
    assert plan["selected"] == [] and plan["transition"]["actions"] == []
    q["budget"]["tier_bytes"]["hbm"] = 2*G; q["minimum_gain_s"] = 100
    assert solve_joint_request(ws, q, p)["decision"] == "keep_current"


def test_shared_numa_is_not_double_counted():
    ws, q, p = case()
    ws[3]["numa"] = ws[2]["numa"]
    for w in ws[2:]: w["headroom_bytes"]["dram"] = G
    q["budget"] = {"tier_bytes": {"dram": 2*G}, "locations": [
        {"worker": i, "tier": "dram", "max_bytes": 2*G} for i in (2, 3)]}
    plan = solve_joint_request(ws, q, p)
    assert plan["transition"]["final_usage"]["tiers"]["dram"] <= G


def test_asymmetric_budget_and_reserved_headroom():
    ws, q, p = case()
    q["budget"]["locations"][0]["max_bytes"] = 0
    plan = solve_joint_request(ws, q, p)
    assert all(r["worker"] == 3 for r in plan["selected"])
    ws[2]["headroom_bytes"]["hbm"] = G
    q["budget"]["locations"][0]["reserve_free_bytes"] = 2*G
    with pytest.raises(ValueError, match="reserved capacity"):
        solve_joint_request(ws, q, p)


def test_missing_base_busy_catalog_and_candidate_limit_rejected():
    ws, q, p = case()
    bad = copy.deepcopy(q); bad["base_ids"] = ["base0"]
    with pytest.raises(ValueError, match="complete demand"): solve_joint_request(ws, bad, p)
    ws[0]["replicas"][0]["leases"] = 1
    with pytest.raises(ValueError, match="busy replica"): check_snapshot(ws)
    ws[0]["replicas"][0]["leases"] = 0
    q["max_candidates"] = 1
    with pytest.raises(ValueError, match="candidate limit"): solve_joint_request(ws, q, p)


class FakeCluster(ManagedPlacementMixin):
    """Use real plan/checkpoint code with a deterministic in-memory worker catalog."""
    def __init__(self, ws, profile):
        self.ws = copy.deepcopy(ws); self.profile = profile
        self.lock = threading.RLock(); self.active = {}; self.placement_journals = {}
        self.executor = "serial"; self.planner_endpoint = None
        self.builds = 0

    def inventory(self, compact=False):
        return copy.deepcopy(self.ws), copy.deepcopy(rows(self.ws, True))

    def retire(self, rid):
        r = next(r for r in rows(self.ws, True) if r["id"] == rid)
        self.ws = simulate_action(self.ws, {"kind": "retire", "replica": r})

    def plan_restore(self, targets, strategy, source_ids):
        return {"targets": targets}

    def transfer(self, plan):
        for t in plan["targets"]:
            self.ws = simulate_action(self.ws, {"kind": "build", "replica": t})
            self.builds += 1
        return {"verification": {"passed": True, "checked_bytes": sum(t["n"] for t in plan["targets"])},
                "execute_wall_s": 0, "plan": {"strategy": "resource_lp", "fallback_reason": None}}


def test_no_temporary_space_retires_redundant_copy_before_build_and_resumes(tmp_path):
    ws, q, p = case()
    old = dict(ws[0]["replicas"][0], id="old", n=2*G, tier="hbm", allocated_bytes=2*G)
    ws[0]["replicas"].append(old); ws[0]["used_bytes"]["hbm"] = 2*G
    q["managed_ids"] = ["old"]
    q["budget"]["locations"].append({"worker": 0, "tier": "hbm", "max_bytes": 2*G})
    cluster = FakeCluster(ws, p); plan = cluster.plan_joint(q)
    assert plan["transition"]["actions"][0]["kind"] == "retire"
    assert fits(plan["transition"]["peak_usage"], plan["limits"], peak=True)
    path = tmp_path/"journal.json"
    with pytest.raises(ManagedPlacementError, match="injected"):
        cluster.apply_joint(plan, journal_path=path, fail_after_actions=1)
    assert {r["id"] for r in rows(cluster.ws, True)} >= {"base0", "base1"}
    journal = cluster.apply_joint(plan, journal_path=path)
    assert journal["status"] == "applied"
    count = cluster.builds
    assert cluster.apply_joint(plan, journal_path=path)["status"] == "applied"
    assert cluster.builds == count
    assert cluster.plan_managed_restore(plan, q["targets"])["placement_plan_id"] == plan["plan_id"]


def test_stale_epoch_and_modified_plan_are_rejected_before_build():
    ws, q, p = case(); cluster = FakeCluster(ws, p); plan = cluster.plan_joint(q)
    changed = copy.deepcopy(plan); changed["selected"][0]["n"] += 1
    with pytest.raises(ValueError, match="modified"): cluster.apply_joint(changed)
    cluster.ws[2]["epoch"] = "restarted"
    with pytest.raises(ManagedPlacementError, match="stale"): cluster.apply_joint(plan)
    assert cluster.builds == 0


def test_solver_failure_is_explicit_and_keeps_valid_layout(monkeypatch):
    ws, q, p = case()
    def fail(*a, **kw): raise RuntimeError("time limit without incumbent")
    monkeypatch.setattr("fluidckpt.cluster.managed.joint_placement", fail)
    plan = solve_joint_request(ws, q, p)
    assert plan["decision"] == "fallback_keep_current"
    assert "time limit" in plan["fallback_reason"]
    assert plan["transition"]["actions"] == []
