"""Derive a failure-only runtime delta without changing physical or rate inputs."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.topology.resource_map import planner_readiness, validate_resource_map


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path)}


def verify_ref(value):
    if sha(value["path"]) != value["sha256"]:
        raise ValueError("source reference SHA mismatch: " + value["path"])


def frozen_sources(manifest):
    value = read(manifest)
    references = [ref(manifest)]
    files = value["files"]
    if not isinstance(files, list):
        raise ValueError("source snapshot requires files with saved paths")
    for row in files:
        saved = {"path": row["saved"], "sha256": row["sha256"]}
        verify_ref(saved)
        references.append(saved)
    return references


def derive(original, control, local, diagnosis, control_audit, catalog_audit, events, references):
    if local.get("status") != "failed" or local.get("runtime_accepted") is not False:
        raise ValueError("failure adapter requires unaccepted failed local startup")
    if any(row.get("start_reply") for row in local.get("processes", [])):
        raise ValueError("this failure delta requires zero started communication ranks")
    if diagnosis.get("gpu_payload_bytes") != 0 or diagnosis.get("communicators") != 0:
        raise ValueError("diagnosis is outside metadata-only scope")
    if control_audit.get("status") != "passed" or catalog_audit.get("status") != "passed":
        raise ValueError("independent control/catalog audit is not passed")
    if not catalog_audit.get("catalog_protection_confirmed"):
        raise ValueError("catalog identity protection is unconfirmed")
    phases = {row["phase"] for row in events}
    required = {"all_visible_library_load_exit", "all_visible_hipInit_enter", "all_visible_alarm_expired",
                "gpu01_visible_library_load_enter"}
    if not required <= phases or "gpu01_visible_hipInit_enter" in phases:
        raise ValueError("diagnostic boundary differs from the audited failure")
    cases = diagnosis["hip_trials"]["cases"]
    if [row["returncode"] for row in cases] != [1, -11] or not diagnosis["hip_trials"]["passed_cleanup"]:
        raise ValueError("metadata failure/exit evidence differs")
    mapping = copy.deepcopy(original)
    state = control_audit["state_delta"]
    config = mapping["runtime_configs"]["owned_xml/node27"]
    if config.get("runtime_observed") or config.get("runtime_accepted"):
        raise ValueError("failure delta cannot replace an accepted runtime")
    config.pop("fingerprint", None)
    config.update(runtime_observed=False, runtime_accepted=False, observed_resource_ids=[],
        fresh_performance_eligible=False, scope="CPU physical input; new node27 device/runtime initialization unconfirmed")
    config["runtime_validation"].update(runtime_observed=False, runtime_accepted=False,
        observed_gpu_bdfs=[], observed_hca_ports=[], actual_graph_refs=[],
        status="device_initialization_unconfirmed", requested_primitive=local["collective_primitive"],
        reason="local admission failed before starting either communication rank; metadata failures do not certify XML acceptance",
        diagnostic_failures={
            "all_visible": {"last_completed_boundary": "library_load", "entered_boundary": "hipInit",
                "termination": "metadata_10s_alarm", "returncode": 1},
            "gpu01_visible": {"last_entered_boundary": "library_load", "hipInit_enter_observed": False,
                "termination": "SIGSEGV", "returncode": -11},
            "causal_attribution": "unconfirmed; these outcomes do not establish visibility as the cause"},
        new_failure_source_refs=references)
    config["fingerprint"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    operational = mapping["current_operational_status"]
    operational.update(scope="bounded control, node27 source catalog identity and metadata-initialization failure evidence",
        protected_postflight_complete=False, protected_state_corrupted=None,
        latest_control_evidence_ref=next(r for r in references if r["path"].endswith("resume_control_r02/summary.json")))
    for node in ("node27", "node29"):
        row = state["nodes"][node]
        operational["nodes"][node].update(host_control_reachable=row["host_control_reachable"],
            owned_container_control_available=row["owned_container_control_available"],
            control_reachable=row["owned_container_control_available"],
            own_probe_cleanup_confirmed=row["own_probe_cleanup_confirmed"],
            boot_identity_verified=row["boot_identity_verified"], latest_control_evidence_ref=row["evidence_ref"])
    operational["nodes"]["node27"].update(device_initialization_status="unconfirmed",
        runtime_initialization_status="unconfirmed", source_catalog_protection={
            "confirmed": True, "full_base_bytes": catalog_audit["full_base_bytes"],
            "scope": catalog_audit["source_content_scope"], "weight_content_reread": False,
            "complete_protected_postflight_verified": False, "parked_engine_postflight_verified": False,
            "physical_free_recovery_verified": False},
        free_metric_comparison=copy.deepcopy(diagnosis["free_comparison"]))
    # The current unavailable node retains the original exact pending owner ledger.
    if operational["nodes"]["node29"].get("pending_exact_targets") != original["current_operational_status"]["nodes"]["node29"]["pending_exact_targets"]:
        raise ValueError("pending exact targets changed")
    mapping["runtime_validation_attempts"].append({"source_refs": references, "status": "failed",
        "runtime_observed": False, "runtime_accepted": False, "started_communication_ranks": 0,
        "interpretation": "node27 admission/device initialization unconfirmed; no actual XML runtime graph"})
    existing = {r["path"] for r in mapping["source_refs"]}
    mapping["source_refs"].extend(r for r in references if r["path"] not in existing)
    mapping["built_at"] = datetime.now(timezone.utc).isoformat()
    mapping["planning_readiness"] = planner_readiness(mapping)
    mapping["resume_delta"] = {"kind": "failure_only", "new_runtime_acceptances": 0,
        "new_performance_measurements": 0, "availability_refreshed": False,
        "physical_data_revalidated_by_new_HIP": False, "source_refs": references}
    for key in ("physical", "availability", "measurements", "historical_profile_refs", "session_ref"):
        if mapping[key] != original[key]:
            raise ValueError("failure delta changed protected map input: " + key)
    if mapping["runtime_configs"]["owned_xml/node29"] != original["runtime_configs"]["owned_xml/node29"]:
        raise ValueError("failure delta changed node29 runtime scope")
    if not validate_resource_map(mapping)["passed"] or mapping["planning_readiness"]["runtime_ready"] or mapping["planning_readiness"]["performance_ready"]:
        raise ValueError("failure delta escaped its nonready boundary")
    return mapping


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, action="append", required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != RESULTS or not out.name.startswith("resume_map_"):
        parser.error("require a new resource_map/resume_map_* result")
    paths = {"original": RESULTS / "build_r04/resource_map.json", "control": RESULTS / "resume_control_r02/summary.json",
        "local": RESULTS / "local_xml_node27_r01/summary.json", "diagnosis": RESULTS / "local_xml_diagnosis_r01/summary.json",
        "control_audit": RESULTS / "resume_audit_control_r04/summary.json", "catalog_audit": RESULTS / "resume_audit_local_catalog_r02/summary.json"}
    references = [ref(p) for p in paths.values()]
    event_path = RESULTS / "local_xml_diagnosis_r01/diagnosis.events.jsonl"
    references.append(ref(event_path))
    for manifest in args.source_manifest:
        references.extend(frozen_sources(manifest))
    for name in ("control_audit", "catalog_audit"):
        for reference in read(paths[name]).get("source_refs", []):
            verify_ref(reference)
            references.append(reference)
    references = list({r["path"]: r for r in references}.values())
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    mapping = derive(**{name: read(path) for name, path in paths.items()}, events=events, references=references)
    out.mkdir(parents=True, exist_ok=False)
    (out / "resource_map.json").write_text(json.dumps(mapping, indent=2) + "\n")
    (out / "source.py").write_bytes(Path(__file__).read_bytes())
    summary = {"status": "passed", "delta_kind": "failure_only", "runtime_ready": False,
        "performance_ready": False, "owned_xml_runtime_accepted": {node: False for node in ("node27", "node29")},
        "new_runtime_acceptances": 0, "new_performance_measurements": 0, "source_refs": references,
        "source_inputs_unchanged": all(sha(r["path"]) == r["sha256"] for r in references),
        "map_ref": ref(out / "resource_map.json"), "source_ref": ref(out / "source.py")}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "manifest.json").write_text(json.dumps({"files": {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}}, indent=2) + "\n")
    print(json.dumps({"out": str(out), "runtime_ready": False, "performance_ready": False}))


if __name__ == "__main__":
    main()
