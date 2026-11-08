"""Independently replay recorded symbolic timelines without invoking a solver."""
import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/decision_validation"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    case_dir = RESULTS / "case_cpu_r02"
    contract = json.loads((case_dir / "contract.json").read_text())
    result = json.loads((case_dir / "inspection.json").read_text())
    checks = {}
    payload = {k: v for k, v in contract.items() if k != "contract_sha256"}
    checks["contract_hash"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() == contract["contract_sha256"] == result["contract_sha256"]
    for row in contract["source_refs"]:
        checks["source/"+row["path"]] = sha(ROOT / row["path"]) == row["sha256"]
    checks["no_measured_costs_or_GPU"] = contract["measured_costs"] is None and contract["gpu_launches"] == 0 and contract["performance_ready"] is False
    checks["no_real_benefit_claim"] = result["real_joint_benefit_proven"] is False and result["real_measured_decision_delta"] == "unknown"
    checks["six_fixed_symbolic_cases"] = len(result["cpu_sensitivity"]) == 6
    candidates = {c["id"]: c for c in contract["candidates"]}
    sources = candidates | {c["id"]: c for c in contract["base"]}
    summaries = []
    for index, case in enumerate(result["cpu_sensitivity"]):
        target_worker = {0: 0, 1: 0 if case["case"].startswith("both_") else 1}
        def valid(row):
            selected = [candidates[rid] for rid in row["selected_ids"]]
            if (len(set(row["selected_ids"])) != len(selected)
                    or sum(c["n"] for c in selected) > contract["tier_budget_bytes"]["hbm"]
                    or any(sum(c["n"] for c in selected if c["worker"] == w) > cap
                           for w, cap in [(0, 16 << 20), (1, 16 << 20)])):
                return False
            ready, coverage = {0: 0., 1: 0.}, {0: [], 1: []}
            for op, timeline in zip(row["ordered_ops"], row["timeline"]):
                src = sources[op["replica_id"]]
                tp, sw, dw = src["tp"], op["src_w"], op["dst_w"]
                if src["tier"] == "hbm" and src["id"] not in row["selected_ids"]:
                    return False
                if sw != src["worker"] or dw != target_worker[tp]:
                    return False
                c = case["coefficients"]
                ms = c["base_dram_ms"] if src["tier"] == "dram" else c["local_hbm_ms"] if sw == dw else c["network_hbm_ms"]
                start = max(ready[sw], ready[dw]); end = start+ms/1000*op["n"]/(16 << 20)
                if not all(math.isclose(timeline[key], value, abs_tol=1e-12) for key, value in [("start_s", start), ("end_s", end), ("service_s", end-start)]):
                    return False
                ready[sw] = ready[dw] = end
                coverage[tp].append((op["off"], op["off"]+op["n"]))
            if len(row["ordered_ops"]) != len(row["timeline"]):
                return False
            for spans in coverage.values():
                cursor = 0
                for begin, end in sorted(spans):
                    if begin != cursor or end <= begin:
                        return False
                    cursor = end
                if cursor != 16 << 20:
                    return False
            return math.isclose(row["ordered_service_ms"], max(ready.values())*1000, abs_tol=1e-9)
        rows = case["layouts"] + [case[k] for k in ("existing_strong_simple", "existing_bandwidth_joint", "finite_oracle", "diagnostic_ordered_marginal_enumerator")]
        checks[f"case{index}/recorded_timeline_source_coverage_budget"] = all(valid(row) for row in rows)
        checks[f"case{index}/nine_legal_subsets"] = case["finite_layout_count"] == len(case["layouts"]) == len({tuple(r["selected_ids"]) for r in case["layouts"]}) == 9
        oracle = min(r["ordered_service_ms"] for r in case["layouts"])
        checks[f"case{index}/fixed_route_oracle"] = math.isclose(oracle, case["finite_oracle"]["ordered_service_ms"], abs_tol=1e-9)
        simple_ms, joint_ms = (case[k]["ordered_service_ms"] for k in ("existing_strong_simple", "existing_bandwidth_joint"))
        checks[f"case{index}/cost_alias_record"] = case["existing_simple_joint_ordered_cost_alias"] == math.isclose(simple_ms, joint_ms, abs_tol=1e-9)
        checks[f"case{index}/custom_rule_diagnostic_only"] = case["diagnostic_marginal_is_existing_baseline"] is False
        summaries.append(dict(case=case["case"], coefficients=case["coefficients"], simple_ms=simple_ms,
                              joint_ms=joint_ms, fixed_fastest_route_oracle_ms=oracle,
                              signature_alias=case["existing_simple_joint_signature_alias"]))
    measurement = json.loads((RESULTS/"measure_review_r01/measurement_contract.json").read_text())
    checks["measurement_gate_deferred_no_GPU_command"] = measurement["gate"]["case_gate_ready"] is False and measurement["prepared_reuse_plan"]["GPU_launch_command"] is None and measurement["GPU_launches"] == 0
    checks["holdout_refit_forbidden"] = measurement["holdout"]["not_fit_input"] is True and measurement["holdout"]["no_recompute_from_holdout"] is True
    for row in measurement["prepared_reuse_plan"]["source_refs"]:
        checks["measurement_source/"+row["path"]] = sha(Path(row["path"])) == row["sha256"]
    checks["refresh_bound"] = sha(Path(measurement["refresh_ref"]["path"])) == measurement["refresh_ref"]["sha256"]
    refresh = json.loads((RESULTS/"measure_refresh_r01/summary.json").read_text())
    checks["readonly_refresh_no_actions"] = all(refresh[key] == 0 for key in ("GPU_launches", "signals", "remote_writes")) and refresh["passed"] is True
    evidence = [case_dir/"contract.json", case_dir/"inspection.json", RESULTS/"review_scope_r01/dependency_audit.json",
                RESULTS/"review_scope_r01/report.md", RESULTS/"measure_review_r01/measurement_contract.json",
                RESULTS/"measure_refresh_r01/summary.json", Path(__file__)]
    return dict(schema_version="decision_validation.independent_review.v1", checks=checks,
                passed=all(checks.values()), failures=[k for k, passed in checks.items() if not passed],
                symbolic_cases=summaries, evidence_refs=[dict(path=str(p), sha256=sha(p)) for p in evidence],
                qualified_scope="CPU symbolic finite-layout comparison and minimal deferred measurement contract; fixed-fastest-route oracle only",
                real_joint_benefit_proven=False, physical_shared_contention_gain=False,
                prediction_ready=False, GPU_launches=0, action="stop strategy GPU comparison: no real decision increment established")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(dict(passed=result["passed"], checks=len(result["checks"]), failures=result["failures"])))
