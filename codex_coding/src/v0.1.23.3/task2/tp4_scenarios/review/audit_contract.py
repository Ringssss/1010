"""Audit a frozen TP4 delivery with independent count-based capacity bounds."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit(directory):
    contract = json.loads((directory / "contract.json").read_text())
    comparison = json.loads((directory / "comparison.json").read_text())
    payload = {k: v for k, v in contract.items() if k != "contract_sha256"}
    checks = {"canonical_contract_hash": canonical(payload) == contract["contract_sha256"],
              "comparison_bound_to_contract": comparison["contract_sha256"] == contract["contract_sha256"],
              "frozen_sources_match": all(sha(ROOT / r["path"]) == r["sha256"] for r in contract["source_refs"]),
              "real_rates_and_execution_stay_unknown": all(v is False for v in contract["readiness"].values()),
              "no_gpu_command": contract["gpu_command"] is None}
    descriptors = {d["case"]["case_id"]: d for d in contract["cases"]}
    rows = {d["case_id"]: d for d in comparison["cases"]}
    findings = []
    for name, descriptor in descriptors.items():
        case = descriptor["case"]
        targets = case["restore_scenarios"][0]["targets"]
        unit = descriptor["budget_ledger"]["block_bytes"]
        wm = {w["worker"]: w for w in case["workers"]}
        checks[name + "/distinct_worker_node_gpu_and_replica_consistency"] = len({(w["node"], w["gpu"]) for w in case["workers"]}) == len(case["workers"]) and all(all(r[k] == wm[r["worker"]][k] for k in ("node", "gpu", "numa")) for r in case["base"] + case["candidates"])
        checks[name + "/four_distinct_targets_and_tp"] = len(targets) == 4 and len({t["worker"] for t in targets}) == 4 and {t["tp"] for t in targets} == {0, 1, 2, 3}
        checks[name + "/immutable_full_base"] = len(case["base"]) == 4 and all(b["n"] == 2 * unit and b["off"] == 0 and b["tier"] == "dram" for b in case["base"])
        checks[name + "/exact_two_blocks_each_rank"] = Counter(c["tp"] for c in case["candidates"]) == Counter({i: 2 for i in range(4)}) and all(c["n"] == unit and c["off"] in (0, unit) and c["worker"] == c["tp"] for c in case["candidates"])
        ledger = descriptor["budget_ledger"]
        checks[name + "/base_cache_target_staging_separate"] = case["budget"]["base_dram_bytes"] == case["budget"]["target_allocation_bytes"] == 8 * unit and case["budget"]["staging_bytes"] == ledger["staging_hbm_bytes"] == len(case["workers"]) * ledger["staging_hbm_bytes_per_worker"] and ledger["runtime_bytes"] is None
        checks[name + "/all_policies_have_same_finite_opportunity"] = set(rows[name]["policies"]) == set(contract["baseline_rules"])
        checks[name + "/physical_path_capacities_unknown"] = all(p["physical_rate_GBps"] is None and p["physical_path_binding"] is None for p in descriptor["path_contract"]["paths"]) and all(r["physical_capacity_GBps"] is None for r in descriptor["path_contract"]["resources"])

        # Local copies are faster than their unique remote fallback. With k_i
        # cached blocks, each target has unavoidable service 2 - 3*k_i/4.
        # C2 additionally sends the uncached rank-0/1 blocks across one unit cut.
        analytical = []
        for finite in rows[name]["finite_layouts"]:
            counts = Counter(int(cid.split("/")[1]) for cid in finite["selected_ids"])
            target_bound = max(2 - .75 * counts[i] for i in range(4))
            bound = max(target_bound, 4 - counts[0] - counts[1]) if name.startswith("C2") else target_bound
            analytical.append(dict(ids=finite["selected_ids"], cache_blocks=len(finite["selected_ids"]), bound=bound))
            if abs(finite["resource_lp_bound_symbolic_time"] - bound) > 1e-7:
                checks[name + "/independent_capacity_formula"] = False
        checks.setdefault(name + "/independent_capacity_formula", True)
        optimum = min(r["bound"] for r in analytical)
        minimum_blocks = min(r["cache_blocks"] for r in analytical if r["bound"] == optimum)
        expected = {"C0_local_sufficient": (.5, 8), "C1_equal_plateau_control": (1.25, 4),
                    "C1_uneven_preregistered": (2., 0), "C2_shared_source_export": (2., 2)}[name]
        checks[name + "/optimum_and_minimum_cache"] = (optimum, minimum_blocks) == expected
        dominated = [policy for policy, row in rows[name]["policies"].items()
                     if abs(row["resource_lp_bound_symbolic_time"] - optimum) < 1e-7 and len(row["selected_ids"]) > minimum_blocks]
        findings.append(dict(case_id=name, independent_optimum_symbolic_time=optimum,
                             minimum_cache_blocks_at_optimum=minimum_blocks,
                             minimum_cache_bytes_at_optimum=minimum_blocks * unit,
                             policies_dominated_in_cache_vs_primary_time=dominated,
                             secondary_rank_time_semantics="Improving noncritical target times can consume cache without reducing all-rank maximum."))

    c1, c2 = descriptors["C1_uneven_preregistered"], descriptors["C2_shared_source_export"]
    checks["C1_C2_same_budget_candidates_targets_rates"] = all(c1["case"][key] == c2["case"][key] for key in ("budget", "candidates", "restore_scenarios")) and c1["symbolic_profile"] == c2["symbolic_profile"]
    p1 = {p["replica_id"]: p for p in c1["path_contract"]["paths"]}
    p2 = {p["replica_id"]: p for p in c2["path_contract"]["paths"]}
    incidence_ok = True
    for rid, path in p1.items():
        expected_ids = ["source_export/U" if item in ("source_export/0", "source_export/1") else item for item in path["incidence"]]
        incidence_ok &= expected_ids == p2[rid]["incidence"] and path["symbolic_rate_units_per_time"] == p2[rid]["symbolic_rate_units_per_time"]
    checks["C1_C2_only_source_export_incidence_merge"] = bool(incidence_ok)
    checks["balanced_rule_is_singleton_lexicographic_not_oracle"] = "singleton" in contract["baseline_rules"]["bottleneck_balanced"] and "No deletion, swaps, bundles or oracle lookahead" in contract["baseline_rules"]["bottleneck_balanced"]
    return dict(checks=checks, findings=findings, contract_sha256=contract["contract_sha256"],
                qualification="passed_symbolic_contract_with_cache_pareto_findings" if all(checks.values()) else "failed",
                real_rate_ready=False, physical_budget_admitted=False,
                real_model_T_ready_measured=False, new_GPU_samples=0,
                review_SSH_actions=0, product_mutations=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = audit(args.contract_dir)
    refs = [args.contract_dir / "contract.json", args.contract_dir / "comparison.json", Path(__file__)]
    result["source_refs"] = [dict(path=str(p.resolve().relative_to(ROOT)), sha256=sha(p)) for p in refs]
    (args.out / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(dict(passed=sum(result["checks"].values()), total=len(result["checks"]), findings=result["findings"]), ensure_ascii=False))
    if not all(result["checks"].values()):
        raise SystemExit("Contract review failed")


if __name__ == "__main__":
    main()
