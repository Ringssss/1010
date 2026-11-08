"""Audit metadata-only diagnosis without inferring a GPU or XML root cause."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(run):
    raw, registration, trials = (load(run / name) for name in ("summary.json", "registration.json", "hip_trials.json"))
    events = [json.loads(line) for line in (run / "diagnosis.events.jsonl").read_text().splitlines()]
    source = ROOT / "codex_coding/src/v0.1.23.3/task2/resource_map/local_xml/diagnose_admission.py"
    old = run.parent / "local_xml_node27_r01/summary.json"
    checks = {"diagnosis_source_sha": sha(source) == registration["source_sha256"],
              "old_failed_raw_preserved": sha(old) == registration["old_summary_sha256"] and load(old)["status"] == "failed" and raw["old_raw_summary_preserved"],
              "no_payload_or_collective": raw["metadata_only"] and raw["gpu_payload_bytes"] == raw["communicators"] == 0,
              "no_startup_authorization_upgrade": raw["gpu_startup_authorized"] is False and raw["admission_replaced"] is False,
              "two_unique_trials": len(trials["cases"]) == 2 and {r["argv"][-1] for r in trials["cases"]} == {"all_visible", "gpu01_visible"},
              "same_trial_summary": raw["hip_trials"] == trials,
              "registration_each_once": registration["each_once"] and registration["cases"] == ["all_visible", "gpu01_visible"]}
    records = [("sysfs_owner", raw["sysfs_process"]), ("container_trials", raw["container_process"])]
    records.extend((r["argv"][-1], r) for r in trials["cases"])
    for name, row in records:
        owner = load(run / f"{name}.owner.json")
        process = load(run / f"{name}.process.json")
        checks[name + "/exact_registration"] = process == row and owner["pid"] == row["pid"] and owner["start_time"] == row["start_time"] and owner["argv"] == row["argv"] and owner["pgid"] == row["pid"]
        checks[name + "/exact_exit"] = row["cleanup"]["passed"] and row["actual_after"]["pid"] == row["pid"] and not row["actual_after"]["live"]
    branches = {}
    for row in trials["cases"]:
        name, pid = row["argv"][-1], row["pid"]
        own = [event for event in events if event["pid"] == pid]
        phases = [event["phase"] for event in own]
        entered = next(event for event in own if event["phase"] == name + "_child_enter")
        checks[name + "/core_zero"] = entered["core_dump_limit"] == [0, 0]
        checks[name + "/hard_bound"] = row["timeout_seconds"] == 12 and row["elapsed_ns"] < 15 * 10**9
        if name == "all_visible":
            checks[name + "/exact_failed_boundary"] = row["returncode"] == 1 and name + "_library_load_exit" in phases and name + "_hipInit_enter" in phases and name + "_alarm_expired" in phases and name + "_hipInit_exit" not in phases
            checks[name + "/visibility"] = all(value is None for value in entered["visibility"].values())
            checks[name + "/traceback_boundary"] = "library.hipInit(0)" in Path(row["log"]).read_text() and "HIP metadata alarm expired" in Path(row["log"]).read_text()
            branches[name] = "HIP library load returned; hipInit entered; 10s alarm interrupted without hipInit exit"
        else:
            checks[name + "/exact_failed_boundary"] = row["returncode"] == -11 and name + "_library_load_enter" in phases and name + "_library_load_exit" not in phases and name + "_hipInit_enter" not in phases
            checks[name + "/visibility"] = entered["visibility"] == {"HIP_VISIBLE_DEVICES": "0,1", "CUDA_VISIBLE_DEVICES": "0,1", "ROCR_VISIBLE_DEVICES": None}
            branches[name] = "SIGSEGV after HIP library load entered; no library load exit or hipInit entry recorded"
    sysfs, owners = load(run / "sysfs.json"), load(run / "owners.json")
    checks["sysfs_selected_bdfs"] = {r["bdf"] for r in sysfs["gpus"]} == {"0000:09:00.0", "0000:36:00.0"}
    checks["kfd_selected_bdfs"] = {r["bdf_from_location_id"] for r in sysfs["kfd"]} == {"0000:09:00.0", "0000:36:00.0"}
    checks["owner_attribution_incomplete_preserved"] = owners["complete_attribution"] is False and bool(owners["errors"])
    for row in raw["free_comparison"]:
        checks[f"gpu{row['gpu']}/free_metrics_separate"] = row["same_metric"] is False and row["sysfs_minus_keeper_bytes"] == row["sysfs_total_minus_used_bytes"] - row["keeper_physical_free_bytes"]
    for rank in (0, 1):
        current = load(run / f"keeper_{rank}.json")
        before = load(run.parent / "local_xml_node27_r01" / f"after.worker_{rank}.json")
        checks[f"worker{rank}/compact_fields_retained_after_diagnosis"] = all(current[k] == before[k] for k in before if k not in {"headroom_bytes", "headroom_sample_ns", "replicas"})
        previous_compact = [{k:v for k,v in r.items() if k not in {"meta", "index"}} for r in before["replicas"]]
        checks[f"worker{rank}/compact_replica_identity_retained"] = current["replicas"] == previous_compact
    passed = all(checks.values())
    return {"status": "passed" if passed else "failed", "checks": checks,
            "failed": [k for k, v in checks.items() if not v], "observed_boundaries": branches,
            "diagnostic_children_exited": passed, "basic_new_process_gpu_runtime_stable": False,
            "xml_runtime_accepted": False, "performance_ready": False,
            "causal_scope": "two different failure boundaries; no unique visibility, XML, or external-owner cause established",
            "owner_attribution_complete": False, "free_metrics_interchangeable": False,
            "post_diagnosis_catalog_scope": "compact replica identities/state/leases/epochs retained; meta/index not returned by compact endpoint",
            "gpu_operations": 0, "remote_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(exist_ok=False, parents=True)
    result = review(args.run.resolve())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (args.out / "review_source.py").write_bytes(Path(__file__).read_bytes())
    diagnostic_source = ROOT / "codex_coding/src/v0.1.23.3/task2/resource_map/local_xml/diagnose_admission.py"
    (args.out / "diagnostic_source.py").write_bytes(diagnostic_source.read_bytes())
    refs = [*args.out.iterdir(), *[p for p in args.run.iterdir() if p.is_file()]]
    (args.out / "manifest.json").write_text(json.dumps({"source_refs": [{"path": str(p.resolve()), "sha256": sha(p)} for p in refs], "gpu_operations": 0, "remote_operations": 0}, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()), "total": len(result["checks"]), "failed": result["failed"]}))
    if not passed_status(result):
        raise SystemExit(1)


def passed_status(result):
    return result["status"] == "passed"


if __name__ == "__main__":
    main()
