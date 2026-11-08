"""Verify a derived failure map keeps physical inputs and rates unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.topology.resource_map import PlanningNotReady, planner_readiness, planning_input, validate_resource_map


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(path):
    original = load(RESULTS / "build_r04/resource_map.json")
    current = load(path)
    checks = {}
    for key in ("physical", "availability", "measurements", "historical_profile_refs", "session_ref", "planning_diagnostic"):
        checks["unchanged/" + key] = original[key] == current[key]
    for config_id, config in original["runtime_configs"].items():
        if config_id != "owned_xml/node27":
            checks["unchanged/config/" + config_id] = config == current["runtime_configs"][config_id]
    config = current["runtime_configs"]["owned_xml/node27"]
    validation = config["runtime_validation"]
    checks["owned_unaccepted"] = config["runtime_observed"] is False and config["runtime_accepted"] is False
    checks["observed_scope_empty"] = not config["observed_resource_ids"] and not validation["observed_gpu_bdfs"] and not validation["observed_hca_ports"] and not validation["actual_graph_refs"]
    checks["transport_unknown"] = validation["actual_gdr_modes"] is None
    checks["fingerprint_updated"] = config["fingerprint"] != original["runtime_configs"]["owned_xml/node27"]["fingerprint"]
    plain = {k:v for k,v in config.items() if k != "fingerprint"}
    checks["fingerprint_exact"] = hashlib.sha256(json.dumps(plain, sort_keys=True).encode()).hexdigest() == config["fingerprint"]
    checks["owned_input_unchanged"] = config["input_ref"] == original["runtime_configs"]["owned_xml/node27"]["input_ref"]
    op = current["current_operational_status"]
    checks["node29_pending_unchanged"] = op["nodes"]["node29"]["pending_exact_targets"] == original["current_operational_status"]["nodes"]["node29"]["pending_exact_targets"]
    checks["node29_cleanup_control_unconfirmed"] = op["nodes"]["node29"]["own_probe_cleanup_confirmed"] is False and op["nodes"]["node29"]["control_reachable"] is False
    checks["no_full_protection_upgrade"] = op["protected_postflight_complete"] is False and op["protected_state_corrupted"] is None
    checks["source_catalog_scope_only"] = op["nodes"]["node27"]["source_catalog_protection"]["confirmed"] is True and op["nodes"]["node27"]["source_catalog_protection"]["weight_content_reread"] is False
    checks["old_attempts_unchanged"] = current["runtime_validation_attempts"][:-1] == original["runtime_validation_attempts"]
    attempt = current["runtime_validation_attempts"][-1]
    checks["new_attempt_failed_prelaunch"] = attempt["status"] == "failed" and attempt["started_communication_ranks"] == 0 and attempt["runtime_accepted"] is False
    checks["no_new_live_availability"] = current["availability"]["live_status_claim"] is False
    refs = {}
    def walk(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                refs[value["path"]] = value["sha256"]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(current)
    remote_refs = [name for name in refs if name.startswith("/") and not name.startswith(str(ROOT) + "/")]
    local_refs = {name: digest for name, digest in refs.items() if name not in remote_refs}
    for name, digest in local_refs.items():
        checks["source_sha/" + name] = sha(name) == digest
    for name, digest in load(path.parent / "manifest.json")["files"].items():
        checks["map_manifest/" + name] = sha(path.parent / name) == digest
    checks["structural_validation"] = validate_resource_map(current)["passed"]
    readiness = planner_readiness(current, current["planning_diagnostic"]["request"])
    checks["readiness_not_upgraded"] = readiness["structure_ready"] and not readiness["runtime_ready"] and not readiness["performance_ready"]
    try:
        planning_input(current, current["planning_diagnostic"]["request"])
        checks["planning_input_rejected"] = False
    except PlanningNotReady:
        checks["planning_input_rejected"] = True
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "failed": [k for k,v in checks.items() if not v], "readiness": readiness,
            "runtime_accepted": False, "performance_ready": False, "unique_refs_checked": len(local_refs),
            "historical_remote_library_paths_not_rehashed": remote_refs,
            "gpu_operations": 0, "remote_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(exist_ok=False, parents=True)
    result = review(args.map.resolve())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (args.out / "review_source.py").write_bytes(Path(__file__).read_bytes())
    refs = [args.map.resolve(), args.map.parent / "manifest.json", args.out / "summary.json", args.out / "review_source.py",
            RESULTS / "resume_audit_diagnosis_r02/summary.json", RESULTS / "resume_audit_state_delta_r01/state_delta.json"]
    (args.out / "manifest.json").write_text(json.dumps({"source_refs": [{"path": str(p.resolve()), "sha256": sha(p)} for p in refs], "gpu_operations": 0, "remote_operations": 0}, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()), "total": len(result["checks"]), "failed": result["failed"]}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
