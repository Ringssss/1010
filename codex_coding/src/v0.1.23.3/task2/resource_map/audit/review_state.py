"""Verify that planner state boundaries agree with failed startup evidence."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def load(path):
    return json.loads(Path(path).read_text())


def review(mapping):
    spec = importlib.util.spec_from_file_location("state_api", ROOT / "main_lib/fluidckpt/topology/resource_map.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    operational = mapping["current_operational_status"]
    recovery = load(operational["evidence_ref"]["path"])
    checks = {}
    for node in ("node27", "node29"):
        config = mapping["runtime_configs"][f"owned_xml/{node}"]
        validation = config["runtime_validation"]
        checks[node + "/runtime_unaccepted_preserved"] = not config["runtime_accepted"] and not config["runtime_observed"]
        checks[node + "/no_observed_scope_invented"] = not config["observed_resource_ids"] and not validation["observed_gpu_bdfs"] and not validation["observed_hca_ports"] and not validation["actual_graph_refs"]
        checks[node + "/transport_unknown"] = validation["actual_gdr_modes"] is None
        checks[node + "/requested_scope_only"] = validation["requested_gpu_bdfs"] == ["0000:09:00.0", "0000:36:00.0"] and validation["requested_hca_ports"] == [{"hca": "shca_0", "port": 1}]
        for ref in validation["source_refs"]:
            checks["sha/" + ref["path"]] = hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest() == ref["sha256"]
    checks["old_availability_not_live"] = mapping["availability"]["live_status_claim"] is False
    checks["remote_cleanup_status_matches_recovery"] = operational["nodes"]["node29"]["own_probe_cleanup_confirmed"] == recovery["exact_remote_cleanup_passed"]
    checks["pending_exact_targets_matches_recovery"] = operational["nodes"]["node29"]["pending_exact_targets"] == recovery["pending_exact_targets"]
    checks["protected_corruption_not_inferred"] = operational["protected_state_corrupted"] is None
    checks["postflight_incomplete_not_hidden"] = operational["protected_postflight_complete"] is False
    checks["failed_source_raw_unchanged"] = recovery["original_summary_unchanged"] is True
    request = mapping["planning_diagnostic"]["request"]
    readiness = api.planner_readiness(mapping, request)
    checks["diagnostic_structure_passed"] = readiness["structure_ready"] is True
    checks["diagnostic_runtime_performance_blocked"] = not readiness["runtime_ready"] and not readiness["performance_ready"] and not readiness["ready"]
    checks["diagnostic_null_budgets"] = all(x is None for x in request["bytes_budget_by_resource"].values())
    try:
        api.planning_input(mapping, request)
        checks["planning_input_raises"] = False
    except api.PlanningNotReady:
        checks["planning_input_raises"] = True
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "failed": [k for k, v in checks.items() if not v], "actual_readiness": readiness,
            "runtime_accepted": False, "own_remote_cleanup_confirmed": recovery["exact_remote_cleanup_passed"],
            "pending_exact_targets": recovery["pending_exact_targets"], "gpu_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = review(load(args.map))
    (args.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()), "total": len(result["checks"]), "failed": result["failed"]}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
