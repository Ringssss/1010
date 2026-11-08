"""Contract, minimax, shared capacity, transition and checkpoint tests without GPUs."""
import copy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fluidckpt.cluster.control import Cluster
from fluidckpt.cluster.joint import joint_placement
from fluidckpt.cluster.managed import (aligned, normalize_request, capacity_limits, generate_candidates,
                                      physical, rows, simulate_action, solve_joint_request, state_hash)
from fluidckpt.cluster.managed_control import ManagedPlacementError

G = 1 << 30
PROFILE = {"rates": {"local_dram": 2, "local_hbm": 8, "peer": 4, "network": 1},
           "path_rates": {"network:hbm": 1}, "gpu_ingress_GBps": 1000, "gpu_egress_GBps": 1000,
           "dram_GBps": 1000, "network_node_GBps": 1000, "serial_target_execution": True,
           "network_pairs": [[i, j] for i in range(4) for j in range(4) if i//2 != j//2],
           "provenance": "synthetic capacities for deterministic logic tests"}


def make_case(n=2*G, old=False):
    ws = [{"worker": i, "node": f"node{i//2}", "gpu": i%2, "numa": i%2,
           "epoch": f"epoch{i}", "session": "test", "capabilities": list(PROFILE["rates"]),
           "budget_bytes": {"dram": 64*G, "hbm": 64*G}, "used_bytes": {"dram": 0, "hbm": 0},
           "headroom_bytes": {"dram": 60*G, "hbm": 60*G}, "transactions": [], "replicas": []} for i in range(4)]
    def add(rid, owner, rank, tier, size):
        w = ws[owner]
        r = {"id": rid, "identity": "model", "tp": rank, "off": 0, "n": size, "worker": owner,
             "node": w["node"], "numa": w["numa"], "tier": tier, "epoch": w["epoch"], "generation": 1,
             "state": "VALID", "leases": 0, "allocated_bytes": aligned(size),
             "meta": {}, "index": {"tensor": (0, n)}}
        w["replicas"].append(r); w["used_bytes"][tier] += r["allocated_bytes"]
    for i in range(2):
        add(f"base{i}", i, i, "dram", n)
        if old: add(f"old{i}", i, i, "hbm", G)
    request = {"schema_version": 1, "scope": "model", "base_ids": ["base0", "base1"],
               "managed_ids": ["old0", "old1"] if old else [], "block_bytes": G,
               "targets": [{"key": f"target{i}", "identity": "model", "tp": i, "worker": i+2, "off": 0, "n": n} for i in range(2)],
               "budget": {"tier_bytes": {"hbm": 2*G, "dram": 0}, "temporary_tier_bytes": {"hbm": 0, "dram": 0},
                   "locations": [{"worker": i, "tier": "hbm", "max_bytes": 2*G,
                                  "reserve_free_bytes": 4*G, "temporary_bytes": 0} for i in range(4)]}}
    return ws, request


class MemoryCluster(Cluster):
    """Simulate only storage state changes; real byte validation is covered by GPU fixtures."""
    def __init__(self, ws):
        super().__init__([f"worker{i}" for i in range(len(ws))], copy.deepcopy(PROFILE))
        self.ws = copy.deepcopy(ws); self.copies = 0; self.lose_reply = False; self.fail_copy = False

    def inventory(self, compact=False, telemetry=None):
        return copy.deepcopy(self.ws), copy.deepcopy(rows(self.ws, True))

    def apply_state(self, kind, r):
        self.ws = simulate_action(self.ws, {"kind": kind, "replica": r})
        group = physical(self.ws[r["worker"]], r["tier"])
        for w in self.ws:
            if physical(w, r["tier"]) == group:
                w["headroom_bytes"][r["tier"]] += aligned(r["n"])*(1 if kind == "retire" else -1)

    def retire(self, rid):
        self.apply_state("retire", next(r for r in rows(self.ws, True) if r["id"] == rid))

    def transfer(self, plan, **kwargs):
        if self.fail_copy: raise RuntimeError("copy failed before publication")
        for target in plan["targets"]:
            self.apply_state("build", target); self.copies += 1
        if self.lose_reply:
            self.lose_reply = False
            raise RuntimeError("lost reply after verified publication")
        return {"verification": {"passed": True, "checked_bytes": sum(t["n"] for t in plan["targets"])},
                "execute_wall_s": .01, "plan": plan}


def test_whole_snapshot_tail_and_concentrated_candidates():
    ws, q = make_case(2*G+17)
    q = normalize_request(q, ws); limits = capacity_limits(q, ws)
    candidates, info = generate_candidates(q, ws, PROFILE, limits)
    assert [r["blocks"] for r in info["coverage"]] == [3, 3]
    assert any(c["off"] == 2*G and c["n"] == 17 for c in candidates)
    for rank in range(2):
        assert sum(c["n"] for c in candidates if c["tp"] == rank and c["worker"] == 2) == 2*G+17
    assert limits["locations"]["2:hbm"] == 2*G
    assert all(c["candidate_only"] and "allocated_bytes" not in c and "epoch" not in c for c in candidates)


def test_joint_balances_without_per_gpu_half_budget():
    ws, q = make_case()
    p = solve_joint_request(ws, q, PROFILE)
    assert p["solver"]["status"] == "optimal"
    assert p["limits"]["locations"]["2:hbm"] == 2*G
    assert p["transition"]["final_usage"]["locations"] == {"2:hbm": G, "3:hbm": G}
    assert p["predicted_after_s"] == pytest.approx(G/1e9*(1+1/8))


def test_asymmetric_capacity_all_cache_on_one_gpu_is_legal():
    ws, q = make_case()
    for loc in q["budget"]["locations"]:
        loc["max_bytes"] = 2*G if loc["worker"] == 2 else 0
    p = solve_joint_request(ws, q, PROFILE)
    assert {r["worker"] for r in p["selected"]} == {2}
    assert {r["tp"] for r in p["selected"]} == {0, 1}
    assert p["transition"]["final_usage"]["tiers"]["hbm"] == 2*G


def test_shared_numa_capacity_is_not_counted_twice():
    ws, q = make_case(G)
    ws[2]["numa"] = ws[3]["numa"] = 0
    ws[2]["headroom_bytes"]["dram"] = ws[3]["headroom_bytes"]["dram"] = G
    q["block_bytes"] = G//2
    q["budget"]["tier_bytes"] = {"hbm": 0, "dram": 2*G}
    q["budget"]["locations"] = [{"worker": i, "tier": "dram", "max_bytes": G} for i in (2, 3)]
    p = solve_joint_request(ws, q, PROFILE)
    assert p["transition"]["final_usage"]["tiers"]["dram"] == G
    assert p["limits"]["physical"]["dram:node1:0"] == G


def test_tight_budget_retires_redundant_before_building():
    ws, q = make_case(old=True)
    p = solve_joint_request(ws, q, PROFILE)
    assert p["transition"]["actions"][0]["kind"] == "retire"
    assert p["transition"]["peak_usage"]["tiers"]["hbm"] == 2*G
    assert not {a["replica"]["id"] for a in p["transition"]["actions"]} & set(q["base_ids"])
    assert all(a["usage"]["tiers"]["hbm"] <= 2*G for a in p["transition"]["actions"])


def test_temporary_budget_allows_build_before_retire():
    ws, q = make_case(old=True)
    q["budget"]["temporary_tier_bytes"]["hbm"] = 2*G
    p = solve_joint_request(ws, q, PROFILE)
    assert p["transition"]["actions"][0]["kind"] == "build"
    assert p["transition"]["peak_usage"]["tiers"]["hbm"] == 4*G
    assert p["transition"]["final_usage"]["tiers"]["hbm"] == 2*G


def test_failure_after_checkpoint_resume_and_idempotency(tmp_path):
    ws, q = make_case(old=True); cluster = MemoryCluster(ws)
    p = cluster.plan_joint(q); path = tmp_path/"journal.json"
    with pytest.raises(ManagedPlacementError, match="injected interruption") as error:
        cluster.apply_joint(p, journal_path=path, fail_after_actions=2)
    assert error.value.journal["next_action"] == 2
    result = cluster.apply_joint(p, journal_path=path)
    assert result["status"] == "applied"
    count = cluster.copies
    assert cluster.apply_joint(p, journal_path=path)["status"] == "applied"
    assert cluster.copies == count == 2
    restore = cluster.plan_managed_restore(p, q["targets"])
    assert restore["strategy"] == "resource_lp"


def test_lost_reply_after_publication_is_reconciled_without_recopied_data():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q)
    cluster.lose_reply = True
    with pytest.raises(ManagedPlacementError, match="lost reply") as error: cluster.apply_joint(p)
    result = cluster.apply_joint(p, journal=error.value.journal)
    assert result["completed"][0]["recovered_after_publication"]
    assert cluster.copies == 2


def test_in_memory_retry_and_reapply_use_existing_checkpoint():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q)
    with pytest.raises(ManagedPlacementError, match="injected"):
        cluster.apply_joint(p, fail_after_actions=1)
    assert cluster.apply_joint(p)["status"] == "applied"
    assert cluster.apply_joint(p)["status"] == "applied"
    assert cluster.copies == 2


def test_failure_before_publication_retries_pending_action():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q)
    cluster.fail_copy = True
    with pytest.raises(ManagedPlacementError, match="copy failed") as error: cluster.apply_joint(p)
    assert cluster.copies == 0
    cluster.fail_copy = False
    assert cluster.apply_joint(p, journal=error.value.journal)["status"] == "applied"
    assert cluster.copies == 2


def test_aborted_allocation_tombstone_requires_explicit_replan():
    class AbortedAllocation(MemoryCluster):
        aborted = False
        def transfer(self, plan, **kwargs):
            if not self.aborted:
                self.aborted = True
                target = plan["targets"][0]
                self.apply_state("build", target)
                self.apply_state("retire", target)
                raise RuntimeError("aborted allocation leaves released generation")
            return super().transfer(plan, **kwargs)
    ws, q = make_case(); cluster = AbortedAllocation(ws); p = cluster.plan_joint(q)
    with pytest.raises(ManagedPlacementError, match="aborted allocation"): cluster.apply_joint(p)
    with pytest.raises(ManagedPlacementError, match="explicit replan"): cluster.apply_joint(p)
    renewed = cluster.replan_joint(p)
    assert renewed["plan_id"] != p["plan_id"]
    assert cluster.apply_joint(renewed)["status"] == "applied"
    assert {r["id"] for r in rows(cluster.ws, True)} >= set(q["base_ids"])


def test_replan_does_not_accept_owned_id_with_different_identity():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q); cluster.apply_joint(p)
    rid = p["selected"][0]["id"]
    next(r for r in rows(cluster.ws) if r["id"] == rid)["identity"] = "other_model"
    with pytest.raises(ValueError, match="identity or extent changed"): cluster.replan_joint(p)


def test_duplicate_physical_gpu_cannot_double_its_capacity():
    ws, q = make_case(); ws[3]["gpu"] = ws[2]["gpu"]
    with pytest.raises(ValueError, match="same physical GPU"): solve_joint_request(ws, q, PROFILE)


def test_candidate_must_be_buildable_as_well_as_useful_for_restore():
    ws, q = make_case(); q = normalize_request(q, ws)
    profile = dict(PROFILE, network_pairs=[[0, 2], [1, 3]])
    candidates, info = generate_candidates(q, ws, profile, capacity_limits(q, ws))
    assert info["filtered"]["unbuildable_from_base"] > 0
    assert not any(c["tp"] == 1 and c["worker"] == 2 for c in candidates)
    assert not any(c["tp"] == 0 and c["worker"] == 3 for c in candidates)
    p = solve_joint_request(ws, q, profile)
    assert p["decision"] == "replace" and p["solver"]["status"] == "optimal"


def test_unverified_network_cannot_be_used_by_restore_lp():
    ws, q = make_case()
    with pytest.raises(ValueError, match="no executable source"):
        solve_joint_request(ws, q, dict(PROFILE, network_pairs=[]))


def test_managed_multinode_requires_explicit_network_policy():
    ws, q = make_case(); profile = dict(PROFILE); profile.pop("network_pairs")
    with pytest.raises(ValueError, match="explicit network pair policy"):
        solve_joint_request(ws, q, profile)


def test_initial_numa_binding_uses_free_domain_without_stealing_feasible_neighbor():
    from fluidckpt.memory.bindings import choose_bindings
    topology = {"nodes": [{"node": i, "memory_bytes": {"MemFree": free}, "distance": distances}
                          for i, free, distances in ((0, 4*G, "10 15 20"), (1, 2*G, "15 10 15"), (2, 4*G, "20 15 10"))],
                "gpus": [{"logical_gpu": 0, "numa_node": 1}, {"logical_gpu": 1, "numa_node": 0}]}
    plan = choose_bindings(topology, [{"gpu": 0, "nbytes": 3*G}, {"gpu": 1, "nbytes": 3*G}], guard_bytes=G)
    assert [(r["gpu"], r["numa"]) for r in plan["bindings"]] == [(0, 2), (1, 0)]
    with pytest.raises(MemoryError, match="no NUMA node"):
        choose_bindings(topology, [{"gpu": 0, "nbytes": 4*G}], guard_bytes=G)


def test_worker_epoch_change_refused_before_mutation():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q)
    cluster.ws[2]["epoch"] = "restarted"
    with pytest.raises(ManagedPlacementError, match="stale"): cluster.apply_joint(p)
    assert cluster.copies == 0


def test_physical_headroom_change_refused():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q)
    cluster.ws[2]["headroom_bytes"]["hbm"] = G
    with pytest.raises(ManagedPlacementError, match="reserved capacity"): cluster.apply_joint(p)
    assert cluster.copies == 0


def test_changed_plan_or_demand_refused():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q)
    wrong = copy.deepcopy(p); wrong["selected"][0]["n"] += 1
    with pytest.raises(ValueError, match="modified"): cluster.apply_joint(wrong)
    cluster.apply_joint(p)
    targets = copy.deepcopy(q["targets"]); targets[0]["worker"] = 3
    with pytest.raises(ValueError, match="demand differs"): cluster.plan_managed_restore(p, targets)


def test_solver_failure_preserves_layout_and_reports_fallback():
    ws, q = make_case(old=True)
    with patch("fluidckpt.cluster.managed.joint_placement", side_effect=RuntimeError("time limit without incumbent")):
        p = solve_joint_request(ws, q, PROFILE)
    assert p["decision"] == "fallback_keep_current"
    assert "time limit" in p["fallback_reason"]
    assert p["transition"]["actions"] == []


def test_replan_does_not_thrash_an_already_optimal_layout():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q); cluster.apply_joint(p)
    q["managed_ids"] = [r["id"] for r in p["selected"]]
    again = cluster.plan_joint(q)
    assert again["decision"] == "keep_current" and again["transition"]["actions"] == []


def test_executor_and_cost_model_cannot_silently_diverge():
    ws, q = make_case(); cluster = MemoryCluster(ws)
    with pytest.raises(ValueError, match="executor-aware"):
        solve_joint_request(ws, q, dict(PROFILE, serial_target_execution=False))
    p = cluster.plan_joint(q); cluster.executor = "double_buffer"
    with pytest.raises(ValueError, match="executor changed"): cluster.apply_joint(p)


def test_restore_rejects_catalog_change_after_publication():
    ws, q = make_case(); cluster = MemoryCluster(ws); p = cluster.plan_joint(q); cluster.apply_joint(p)
    cluster.retire(p["selected"][0]["id"])
    with pytest.raises(ValueError, match="layout changed"): cluster.plan_managed_restore(p, q["targets"])


@pytest.mark.parametrize("fault", ["base", "overlap", "busy", "bytes", "candidates", "peak"])
def test_contract_rejections(fault):
    ws, q = make_case(old=True)
    if fault == "base": q["base_ids"] = ["base0"]
    if fault == "overlap": q["managed_ids"].append("base0")
    if fault == "busy": ws[0]["transactions"] = ["inflight"]
    if fault == "bytes": q["budget"]["tier_bytes"]["hbm"] = -1
    if fault == "candidates": q["max_candidates"] = 1
    if fault == "peak": q["budget"]["tier_bytes"]["hbm"] = G
    with pytest.raises(ValueError): solve_joint_request(ws, q, PROFILE)


def test_solver_limit_incumbent_is_labelled_and_invalid_vector_rejected():
    from scipy.optimize import milp as real_milp
    ws, q = make_case(); q = normalize_request(q, ws); limits = capacity_limits(q, ws)
    candidates, _ = generate_candidates(q, ws, PROFILE, limits)
    def limited(*args, **kwargs):
        solved = real_milp(*args, **kwargs)
        return SimpleNamespace(**{**dict(solved), "success": False, "status": 1, "message": "synthetic limit with incumbent"})
    with patch("scipy.optimize.milp", side_effect=limited):
        solved = joint_placement(rows(ws, True), candidates, q["targets"], ws, PROFILE, limits["tiers"], limits["locations"])
    assert solved["status"] == "feasible_limit" and solved["rounded_plan_s"] > 0
    def invalid(*args, **kwargs):
        value = limited(*args, **kwargs); value.x[0] = .25
        return value
    with patch("scipy.optimize.milp", side_effect=invalid), pytest.raises(RuntimeError, match="feasibility"):
        joint_placement(rows(ws, True), candidates, q["targets"], ws, PROFILE, limits["tiers"], limits["locations"])
