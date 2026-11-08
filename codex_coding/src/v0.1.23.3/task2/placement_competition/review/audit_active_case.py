"""Audit one frozen competition without rerunning a planner or solver."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

from independent_costs import feasible_id_sets, pareto, physical_path, recompute


ROOT = Path(__file__).resolve().parents[6]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b):
    return abs(a - b) < 1e-8


def audit(directory, prior_directory):
    contract = json.loads((directory / "contract.json").read_text())
    comparison = json.loads((directory / "comparison.json").read_text())
    prior = json.loads((prior_directory / "contract.json").read_text())
    case, profile = contract["case"], contract["symbolic_profile"]
    payload = {k: v for k, v in contract.items() if k != "contract_sha256"}
    canonical = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    targets = case["restore_scenarios"][0]["targets"]
    workers = {w["worker"]: w for w in case["workers"]}
    candidates = {c["id"]: c for c in case["candidates"]}
    unit = contract["budget_ledger"]["block_bytes"]
    checks = {
        "development_fixed": contract["development"] == "v0.1.23.3/task2",
        "canonical_contract_hash": canonical == contract["contract_sha256"],
        "comparison_contract_hash": comparison["contract_sha256"] == canonical,
        "current_frozen_source_refs": all(sha(ROOT / r["path"]) == r["sha256"] for r in contract["source_refs"]),
        "exact_builder_snapshot": sha(directory / "build_case.source.py") == contract["source_refs"][0]["sha256"],
        "four_rank_target_coverage": len(targets) == 4 and {t["tp"] for t in targets} == set(range(4))
                                      and len({t["worker"] for t in targets}) == 4
                                      and all(t["off"] == 0 and t["n"] == unit for t in targets),
        "complete_immutable_base": len(case["base"]) == 4 and Counter(b["tp"] for b in case["base"]) == Counter(range(4))
                                     and all(b["off"] == 0 and b["n"] == unit and b["tier"] == "dram" for b in case["base"]),
        "every_block_two_distinct_pool_candidates": len(candidates) == 8 and all(
            {c["worker"] for c in candidates.values() if c["tp"] == r} == {0, 1} for r in range(4)),
        "candidates_content_and_extent_match_targets": all(c["off"] == 0 and c["n"] == unit
            and c["identity"] == targets[c["tp"]]["identity"] and c["tier"] == "hbm" for c in candidates.values()),
        "remote_candidates_exist_for_every_block": all(any(c["tp"] == t["tp"] and c["worker"] != t["worker"]
                                                           for c in candidates.values()) for t in targets),
        "worker_node_gpu_unique": len({(w["node"], w["gpu"]) for w in workers.values()}) == 6,
        "replica_worker_metadata_consistent": all(all(r[k] == workers[r["worker"]][k] for k in ("node", "gpu", "numa", "epoch"))
                                                   for r in case["base"] + case["candidates"]),
        "copy_and_fallback_opportunities_enabled": contract["decision_contract"]["duplicate_copies_allowed"] is True
                                                    and contract["decision_contract"]["full_dram_fallback_for_all"] is True,
        "all_strategies_present": set(comparison["policies"]) == set(contract["baseline_rules"]) - {"finite_reference"},
        "original_paired_controls_preserved": {"paired_fastest", "paired_resource_lp", "marginal_resource_lp"}
                                                <= set(comparison["policies"]["existing_strong_simple"]["controls"]),
        "exchange_baseline_and_balancing_present": {"balanced_exchange", "bottleneck_balanced"} <= set(comparison["policies"]),
        "rates_candidates_budgets_unchanged_between_runs": all(prior[k] == contract[k] for k in ("case", "symbolic_profile", "path_contract", "competition_witness", "budget_ledger")),
        "initial_builder_snapshot_matches_initial_sha": sha(prior_directory / "build_case.source.py") == prior["source_refs"][0]["sha256"],
        "first_run_contract_precedes_revision_contract": prior["created_at_utc"] < contract["created_at_utc"],
        "initial_and_revision_builders_write_contract_before_run": all(
            (p.read_text().index('write(args.out/"contract.json",contract)') < p.read_text().index('comparison = run(case,profile)'))
            for p in (prior_directory / "build_case.source.py", directory / "build_case.source.py")),
        "cache_base_target_staging_separate": contract["budget_ledger"]["base_dram_bytes"] == contract["budget_ledger"]["target_hbm_bytes"] == 4 * unit
            and contract["budget_ledger"]["staging_hbm_bytes"] == 6 * (64 << 20)
            and contract["budget_ledger"]["runtime_bytes"] is None
            and contract["budget_ledger"]["physical_peak_bytes"] is None,
        "physical_qualification_remains_false": all(v is False for v in contract["readiness"].values())
            and contract["budget_ledger"]["physical_budget_admitted"] is False and contract["gpu_command"] is None,
        "physical_capacity_and_rates_unknown": all(p["physical_rate_GBps"] is None and p["physical_capacity_GBps"] is None for p in contract["path_contract"]),
        "no_gpu_ssh_or_product_changes": contract["gpu_launches"] == contract["ssh_operations"] == contract["product_changes"] == 0,
        "no_real_joint_advantage_claim": comparison["real_joint_advantage_proven"] is False,
    }
    active_revision = "revision_contract" in contract
    if active_revision:
        expected_profile = copy.deepcopy(prior["symbolic_profile"])
        changed_keys = [path["path"] for path in prior["path_contract"] if path["replica_id"] in ("cache/r2/pool1", "cache/r3/pool1")]
        for key in changed_keys:
            expected_profile["directed_path_rates"][key] = 1.5 * unit / 1e9
        checks.pop("rates_candidates_budgets_unchanged_between_runs")
        checks["active_revision_only_two_declared_path_rates_changed"] = (case == prior["case"] and profile == expected_profile
            and contract["budget_ledger"] == prior["budget_ledger"] and contract["baseline_rules"] == prior["baseline_rules"])
        revision = contract["revision_contract"]
        checks["revision_bound_to_preserved_negative_case"] = revision["prior_contract_sha256"] == prior["contract_sha256"]
        checks["derivation_frozen_in_contract"] = sha(ROOT / revision["derivation_path"]) == revision["derivation_sha256"]
        checks["revision_not_parameter_sweep_or_required_joint_win"] = revision["parameter_sweep"] is False and revision["joint_win_required"] is False
    paths_ok = True
    by_replica = {r["id"]: r for r in case["base"] + case["candidates"]}
    for path in contract["path_contract"]:
        key, kind, rate, resources = physical_path(by_replica[path["replica_id"]], targets[path["target_rank"]], workers, profile)
        paths_ok &= key == path["path"] and kind == path["kind"] and close(float(rate) * 1e9 / unit, path["symbolic_rate_units_per_time"])
        paths_ok &= set(resources) == set(path["resources"]) and all(close(float(v) * 1e9 / unit, path["resources"][k]) for k, v in resources.items())
    checks["all_twelve_paths_independently_reconstructed"] = paths_ok and len(contract["path_contract"]) == 12
    feasible = feasible_id_sets(case)
    actual = [tuple(sorted(row["selected_ids"])) for row in comparison["finite_layouts"]]
    checks["all_121_subsets_exact_once_including_duplicate_copies"] = len(actual) == len(set(actual)) == len(feasible) == 121 and set(actual) == feasible
    checks["duplicate_copies_really_present_in_reference"] = any(
        len({candidates[cid]["tp"] for cid in ids}) < len(ids) for ids in feasible)
    computed, failures = [], []
    row_flags = {name: True for name in ("selected_budget_and_no_repeated_ids", "profile_and_full_target_unchanged", "operation_coverage_and_source_valid", "resource_score_recomputed", "worker_dag_and_timeline_recomputed", "receiver_completion_and_primary_recomputed", "declared_dag_schedule_capacity_feasible", "queue_predecessors_recomputed", "ledger_recomputed", "no_physical_finite_execution_claim")}
    rows = list(comparison["policies"].items()) + [(f"finite/{i}", row) for i, row in enumerate(comparison["finite_layouts"])]
    rows += [("original_control/" + name, row) for name, row in comparison["policies"]["existing_strong_simple"]["controls"].items()]
    for name, row in rows:
        ids, plan = row["selected_ids"], row["plan"]
        result = recompute(case, profile, ids, plan)
        local_checks = {
            "selected_budget_and_no_repeated_ids": tuple(sorted(ids)) in feasible and len(ids) == len(set(ids)),
            "profile_and_full_target_unchanged": plan["profile"] == profile and plan["targets"] == targets,
            "operation_coverage_and_source_valid": result["coverage"] and not result["operation_inconsistencies"],
            "resource_score_recomputed": close(result["resource_bound"], row["resource_lp_bound_symbolic_time"])
                                          and close(result["resource_bound"], plan["estimated_transfer_s"]),
            "worker_dag_and_timeline_recomputed": close(result["worker_dag"], row["worker_dependency_score"]["estimated_execute_s"])
                and len(result["timeline"]) == len(row["worker_dependency_score"]["timeline"])
                and all(a["op_id"] == b["op_id"] and close(a["start"], b["start_s"]) and close(a["end"], b["end_s"])
                        for a, b in zip(result["timeline"], row["worker_dependency_score"]["timeline"])),
            "receiver_completion_and_primary_recomputed": set(result["target_receiver_finish"]) == set(row["target_receiver_finish_symbolic_times"])
                and all(close(v, row["target_receiver_finish_symbolic_times"][k]) for k, v in result["target_receiver_finish"].items())
                and close(result["worker_dag"], row["common_cpu_primary_symbolic_time"])
                and row["rank_key"] == [round(result["worker_dag"], 10), sorted((round(v, 10) for v in result["target_receiver_finish"].values()), reverse=True), len(ids) * unit],
            "declared_dag_schedule_capacity_feasible": result["declared_symbolic_dag_schedule_capacity_feasible"],
            "no_physical_finite_execution_claim": row["shared_resource_finite_schedule_verified"] is False and row["predicted_real_T_ready_s"] is None,
        }
        last, queue_ok = {}, len(row["executable_queue"]) == len(plan["ops"])
        for index, (op, q) in enumerate(zip(plan["ops"], row["executable_queue"])):
            involved = {op["src_w"], op["dst_w"]}
            expected = sorted({last[w] for w in involved if w in last})
            queue_ok &= q["queue_position"] == index and q["op"] == op and q["depends_on_queue_positions"] == expected
            for w in involved:
                last[w] = index
        local_checks["queue_predecessors_recomputed"] = queue_ok
        ledger = row["ledger"]
        location = {key: sum(candidates[cid]["allocated_bytes"] for cid in ids if f"{candidates[cid]['worker']}:hbm" == key)
                    for key in case["budget"]["location_budgets"]}
        unique = len({(candidates[cid]["identity"], candidates[cid]["tp"]) for cid in ids}) * unit
        local_checks["ledger_recomputed"] = (ledger["location_bytes"] == location
            and ledger["allocated_hbm_bytes"] == len(ids) * unit and ledger["unique_logical_bytes"] == unique
            and ledger["duplicated_logical_bytes"] == len(ids) * unit - unique)
        for flag, passed in local_checks.items():
            row_flags[flag] &= bool(passed)
            if not passed:
                failures.append({"row": name, "check": flag})
        computed.append({"name": name, "selected_ids": ids, "cache_bytes": len(ids) * unit, **result})
    checks.update({"all_rows/" + key: value for key, value in row_flags.items()})
    finite_computed = [r for r in computed if r["name"].startswith("finite/")]
    resource_front = pareto(finite_computed, "resource_bound")
    dag_front = pareto(finite_computed, "worker_dag")
    actual_front = sorted((r["cache_hbm_bytes"], round(r["resource_lp_bound_symbolic_time"], 10)) for r in comparison["pareto"])
    checks["independent_resource_pareto"] = resource_front == actual_front == [(0, 1.0), (4 * unit, .5)]
    checks["independent_fixed_worker_dag_pareto"] = dag_front == [(0, 2.0), (2 * unit, 1.0), (4 * unit, round(5/6, 10))]
    budget_front = comparison["finite_service_reference"]["budget_frontier"]
    checks["budget_0_to_4_block_best_common_primary"] = all(
        close(min(row["worker_dag"] for row in finite_computed if row["cache_bytes"] <= n * unit), reported["common_cpu_primary_symbolic_time"])
        and reported["allowed_cache_hbm_bytes"] == n * unit
        for n, reported in enumerate(budget_front)) and len(budget_front) == 5
    checks["local_completion_not_source_tail"] = close(comparison["policies"]["balanced_exchange"]["target_receiver_finish_symbolic_times"]["0"], 1/3)
    policies = comparison["policies"]
    checks["joint_equals_strong_exchange_layout_and_scores"] = (policies["existing_joint"]["selected_ids"] == policies["balanced_exchange"]["selected_ids"]
        and close(policies["existing_joint"]["resource_lp_bound_symbolic_time"], .5)
        and close(policies["existing_joint"]["worker_dependency_score"]["estimated_execute_s"], 5/6))
    checks["base_two_helpers_not_independent_four_channels"] = close(policies["base_only"]["worker_dependency_score"]["estimated_execute_s"], 2)
    if active_revision:
        fastest = policies["capacity_aware_fastest"]
        exchange = policies["balanced_exchange"]
        checks["actual_fixed_route_matches_preregistered_fastest_11_over_12"] = close(fastest["common_cpu_primary_symbolic_time"], 11/12)
        checks["actual_fixed_route_matches_preregistered_exchange_5_over_6"] = close(exchange["common_cpu_primary_symbolic_time"], 5/6)
        checks["both_actual_cache_equal_16MiB"] = fastest["ledger"]["allocated_hbm_bytes"] == exchange["ledger"]["allocated_hbm_bytes"] == 4 * unit
        checks["fastest_desired_route_actually_selected"] = [op["replica_id"] for op in fastest["plan"]["ops"]] == ["cache/r0/pool0", "cache/r1/pool1", "cache/r2/pool0", "cache/r3/pool1"]
        checks["exchange_desired_route_actually_selected"] = [op["replica_id"] for op in exchange["plan"]["ops"]] == ["cache/r0/pool1", "cache/r1/pool1", "cache/r2/pool0", "cache/r3/pool0"]
    return {"checks": checks, "row_failures": failures, "contract_sha256": canonical,
            "resource_pareto": resource_front, "fixed_worker_dag_pareto": dag_front,
            "scope": "Frozen rounded resource-LP policy; independent operation loads, worker queues, target receiver finishes and event-capacity feasible symbolic schedule witnesses. No finite execution optimum or physical inference.",
            "parameter_registration": {"prior_contract_sha256": prior["contract_sha256"], "revision_contract_sha256": canonical,
                "active_revision": active_revision,
                "note": "Original parameters were registered in r01. Active revision separately registers exactly two non-dominated rates before its own solve; prior negative case remains preserved." if active_revision
                        else "r03 hash is a revision before its own solve; initial unchanged parameter registration is r01."},
            "physical_budget_admitted": False, "GPU_command": None, "GPU_calls": 0, "SSH_actions": 0,
            "product_mutations": 0, "independent_recomputed_rows": computed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--prior-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = audit(args.case_dir, args.prior_dir)
    computed = result.pop("independent_recomputed_rows")
    (args.out / "recomputed_rows.json").write_text(json.dumps(computed, ensure_ascii=False, indent=2) + "\n")
    sources = [args.case_dir / p for p in ("contract.json", "comparison.json", "build_case.source.py")]
    sources += [args.prior_dir / "contract.json", args.prior_dir / "build_case.source.py", Path(__file__), Path(__file__).with_name("independent_costs.py")]
    result["source_refs"] = [{"path": str(p.resolve().relative_to(ROOT)), "sha256": sha(p)} for p in sources]
    result["qualification"] = "passed_symbolic_multi_location_competition_physical_unadmitted" if all(result["checks"].values()) else "failed"
    (args.out / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": sum(result["checks"].values()), "total": len(result["checks"]), "failures": result["row_failures"],
                      "resource_pareto": result["resource_pareto"], "fixed_worker_dag_pareto": result["fixed_worker_dag_pareto"]}))
    if not all(result["checks"].values()):
        raise SystemExit("Case review failed")


if __name__ == "__main__":
    main()
