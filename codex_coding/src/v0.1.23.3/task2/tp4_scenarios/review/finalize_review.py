"""Bind independent TP4 contract, dependency and deployment review evidence."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(feasibility):
    checks = {"deployment_unadmitted": feasibility["ready"] is False,
              "original_budget_not_raised": all(m["task_dram_quota_bytes"] == m["task_hbm_quota_bytes"] == 2 << 30 for m in feasibility["metadata"].values()),
              "rates_and_concurrent_capacity_unknown": feasibility["hardware"]["rate_matrix"] is None and feasibility["hardware"]["concurrent_four_pair_capacity"] is None,
              "multiworker_runtime_and_peak_unknown": feasibility["runtime_evidence"]["actual_multiworker_runtime_increment"] is None and feasibility["runtime_evidence"]["full_physical_peak_verified"] is False,
              "keeper_reuse_unadmitted": feasibility["keeper_reuse"]["admissible"] is False and feasibility["keeper_reuse"]["admitted_reusable_workers"] == 0,
              "no_gpu_writes_signals": feasibility["GPU_calls"] == feasibility["new_GPU_workers"] == feasibility["remote_writes"] == 0 and feasibility["signals"] == []}
    for candidate in feasibility["candidates"]:
        prefix = candidate["candidate_id"]
        checks[prefix + "/no_launch_or_readiness"] = candidate["ready"] is False and candidate["launch_command"] is None
        checks[prefix + "/distinct_node_gpu"] = len({(r["node"], r["gpu"]) for r in candidate["rolemap"]}) == candidate["physical_GPU_count"]
        for key in ("bounded_bytes_illustration_ledger", "bounded_C0_full_cache_ledger"):
            ledger = candidate[key]
            rows = ledger["rows"]
            checks[prefix + "/" + key + "/content_accounting"] = sum(r["base_dram_bytes"] for r in rows) == ledger["common_base_dram_bytes"] == 32 << 20 and sum(r["target_hbm_bytes"] for r in rows) == ledger["common_target_hbm_bytes"] == 32 << 20 and sum(r["cache_hbm_bytes"] for r in rows) == ledger["all_prefix_cache_hbm_bytes"]
            arithmetic = True
            for row in rows:
                arithmetic &= row["projected_new_runtime_dram_bytes"] == row["new_workers"] * row["sampled_single_worker_runtime_dram_bytes"]
                arithmetic &= row["conservative_projected_dram_bytes"] == row["projected_new_runtime_dram_bytes"] + row["base_dram_bytes"] + row["verification_dram_scratch_provisional_bytes"] + row["dynamic_margin_bytes"]
                arithmetic &= row["conservative_projected_hbm_bytes"] == row["projected_new_runtime_hbm_including_staging_bytes"] + row["cache_hbm_node_allowance_bound_bytes"] + row["target_hbm_bytes"] + row["dynamic_margin_bytes"]
                arithmetic &= row["staging_hbm_bytes"] == row["new_workers"] * (64 << 20)
                arithmetic &= row["physical_lower_bound"] is False and row["budget_admission_proven"] is False and row["actual_incremental_runtime_bytes"] is None
            checks[prefix + "/" + key + "/projection_arithmetic_not_physical_lower_bound"] = bool(arithmetic)
        exact_caps = True
        target_nodes = {int(role["role"][1]): role["node"] for role in candidate["rolemap"] if role["role"].startswith("T")}
        for ledger in candidate["contract_case_budget_ledgers"]:
            caps = ledger["cache_units_by_target"]
            for row in ledger["rows"]:
                expected = sum(caps[rank] * (4 << 20) for rank, node in target_nodes.items() if node == row["node"])
                exact_caps &= row["cache_hbm_node_allowance_bound_bytes"] == expected
        checks[prefix + "/exact_contracted_location_allowances"] = bool(exact_caps)
    ref = feasibility["final_contract_ref"]
    contract = read(ROOT / ref["path"])
    checks["final_contract_binding"] = digest(ROOT / ref["path"]) == ref["sha256"] and contract["contract_sha256"] == feasibility["final_contract_embedded_sha256"]
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feasibility-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    base = ROOT / "codex_coding/results/v0.1.23.3/task2/tp4_scenarios"
    contract_review = base / "review_contract_r04/qualification.json"
    dependency_review = base / "review_dependency_r01/checks.json"
    feasibility_path = args.feasibility_dir / "deployment_feasibility.json"
    c, d, f = read(contract_review), read(dependency_review), read(feasibility_path)
    checks = review(f)
    source_paths = [contract_review, dependency_review, feasibility_path, args.feasibility_dir / "deployment_feasibility.md", Path(__file__)]
    for node in (28, 30):
        source_paths.append(ROOT / f["metadata"][f"node{node}"]["ref"]["path"])
    result = dict(qualification="passed_cpu_preparation_physical_deployment_unadmitted",
                  development="v0.1.23.3/task2", contract_sha256=c["contract_sha256"],
                  contract_checks_passed=sum(c["checks"].values()), contract_checks_total=len(c["checks"]),
                  dependency_checks_passed=sum(d["checks"].values()), dependency_checks_total=len(d["checks"]),
                  feasibility_checks=checks, findings=c["findings"],
                  findings_optimum_semantics="Optimum of fixed symbolic resource/target capacity score only; neither finite scheduling optimum nor measured T_ready. Shared-resource constraints and source/target DAG must hold in one future execution schedule.",
                  all_checks_passed=all(c["checks"].values()) and all(d["checks"].values()) and all(checks.values()),
                  physical_deployment_ready=False, calibrated_cost_ready=False, real_T_ready_measured=False,
                  joint_gain_over_strong_simple_plus_balanced=False,
                  review_gpu_calls=0, review_SSH_actions=0, review_product_changes=0,
                  feasibility_metadata_SSH_calls=f["SSH_metadata_calls"],
                  gpu_launch_command=None,
                  source_refs=[dict(path=str(p.resolve().relative_to(ROOT)), sha256=digest(p)) for p in source_paths],
                  preserved_review_failures=dict(review_contract_r01="Reviewer assumed staging unknown; delivery explicitly reserved 512MiB unallocated staging. Corrected review assumption.",
                    review_contract_r02="Builder source changed after intermediate contract source hash was frozen.",
                    review_contract_r03="Intermediate C2 duplicated (node,gpu), plus changed builder source; final r03 contract corrects both.",
                    review_final_r01="Reviewer summed illustration cache rather than disclosed per-node allowance bound in conservative HBM projection. Corrected arithmetic criterion; old result retained."))
    if not result["all_checks_passed"]:
        result["qualification"] = "failed"
    (args.out / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(all_checks_passed=result["all_checks_passed"], feasibility_checks_passed=sum(checks.values()), feasibility_checks_total=len(checks)), ensure_ascii=False))
    if not result["all_checks_passed"]:
        raise SystemExit("Final deployment review failed")


if __name__ == "__main__":
    main()
