"""Emit a conservative new operational delta without altering the frozen map."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    inputs = {name: RESULTS / directory / "summary.json" for name, directory in {
        "control": "resume_audit_control_r04", "local_failure": "resume_audit_local_r01",
        "local_catalog": "resume_audit_local_catalog_r02", "diagnosis": "resume_audit_diagnosis_r02"}.items()}
    values = {name: json.loads(path.read_text()) for name, path in inputs.items()}
    if any(value["failed"] for value in values.values()):
        raise ValueError("cannot emit verified delta from failed audit")
    control = values["control"]["state_delta"]
    nodes = control["nodes"]
    for node, row in nodes.items():
        row["control_reachable"] = row["host_control_reachable"] and row["owned_container_control_available"]
        row["basic_new_process_gpu_runtime_stable"] = False if node == "node27" else None
    nodes["node27"].update(local_keeper_full_catalog_preserved=True,
        local_keeper_full_catalog_evidence_ref=ref(inputs["local_catalog"]),
        local_xml_communication_ranks_started=0, diagnostic_children_exited=True,
        local_physical_free_recovery_verified=False, local_parked_engine_postflight_verified=False)
    nodes["node29"]["pending_exact_targets"] = control["pending_exact_targets"]
    delta = {"schema_version": "resource-map-resume-delta/1", "audit_status": "passed",
        "operational_outcome": "blocked", "base_map_ref": ref(RESULTS / "build_r04/resource_map.json"),
        "current_operational_status": {"nodes": nodes, "protected_postflight_complete": False,
            "protected_state_corrupted": None, "scope": "node27 control and local catalog narrow evidence; node29 remains unverified",
            "evidence_ref": ref(inputs["control"])},
        "runtime_config_updates": {}, "xml_runtime_accepted_by_node": {"node27": False, "node29": False},
        "runtime_attempts": [
            {"kind": "local_xml_functional", "run_ref": ref(RESULTS / "local_xml_node27_r01/summary.json"),
             "status": "failed_prelaunch", "communication_rank_processes_started": 0,
             "input_acceptance": "unverified", "performance_samples": 0},
            {"kind": "new_process_hip_metadata", "run_ref": ref(RESULTS / "local_xml_diagnosis_r01/summary.json"),
             "status": "failed_at_distinct_boundaries", "payload_bytes": 0, "communicators": 0,
             "observed_boundaries": values["diagnosis"]["observed_boundaries"], "children_exited": True}],
        "pending_exact_targets": control["pending_exact_targets"], "structure_ready": True,
        "runtime_ready": False, "performance_ready": False,
        "measurements_modified": False, "availability_live_claim": False,
        "source_refs": [ref(path) for path in inputs.values()],
        "guardrails": ["no prior rate assigned to owned XML", "no retained graph leaf promoted to transport certification",
                       "no node29 boot/container continuity inferred from ICMP", "no full protected postflight inferred from keeper catalog"],
        "gpu_operations": 0, "remote_operations": 0}
    out = RESULTS / "resume_audit_state_delta_r01"
    out.mkdir(exist_ok=False)
    (out / "state_delta.json").write_text(json.dumps(delta, ensure_ascii=False, indent=2) + "\n")
    (out / "source.py").write_bytes(Path(__file__).read_bytes())
    (out / "manifest.json").write_text(json.dumps({"source_refs": [ref(Path(__file__).resolve()), ref(out / "source.py"), ref(out / "state_delta.json"), *delta["source_refs"]],
                                                   "gpu_operations": 0, "remote_operations": 0}, indent=2) + "\n")
    print(json.dumps({"out": str(out), "audit_status": "passed", "operational_outcome": "blocked"}))


if __name__ == "__main__":
    main()
