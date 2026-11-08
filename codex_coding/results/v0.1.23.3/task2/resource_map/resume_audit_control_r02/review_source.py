"""Review local control evidence and emit a conservative operational delta."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(directory):
    baseline = load(RESULTS / "resume_audit_preconditions_r01/summary.json")
    control = load(directory / "summary.json")
    task = load(directory / "task.json")
    old_run = RESULTS / "xml_startup_owned_r02"
    raw = load(old_run / "summary.json")
    checks = {"zero_signals": control["signals"] == 0,
              "zero_gpu_operations": control["gpu_operations"] == 0,
              "complete_readonly_collection": control["status"] == "read_only_complete",
              "source_snapshot_sha": sha(directory / "source.py") == control["script_sha256"],
              "old_failure_preserved": raw["status"] == "failed",
              "old_raw_sha": sha(old_run / "summary.json") == control["old_raw_summary_sha256"],
              "old_raw_unchanged_claim": control["old_raw_summary_unchanged"] is True,
              "session_sha": sha(task["session"]) == task["session_sha256"],
              "no_global_cleanup_upgrade": control["exact_cleanup_confirmed"] is False,
              "no_global_protection_upgrade": control["protected_postflight_complete"] is False,
              "no_corruption_inference": control["protected_state_corrupted"] is None,
              "pending_exact_targets_preserved": control["pending_exact_targets"] == baseline["exact_targets"]}
    for ref in baseline["frozen_refs"]:
        checks["frozen/" + ref["path"]] = sha(ref["path"]) == ref["sha256"]
    for node in ("node27", "node29"):
        expected_identity = task["nodes"][node]["identity"]
        old_host = load(RESULTS / "provenance_r01" / node / "host.json")
        checks[node + "/old_identity_basis"] = (
            expected_identity["boot_id"] == old_host["boot_id"]
            and expected_identity["container_id"] == old_host["container"]["id"]
            and expected_identity["container_started_at"] == old_host["container"]["started"])
        for owner in task["nodes"][node]["owners"]:
            path = Path(owner["spec"])
            checks[node + "/spec/" + path.name] = sha(path) == owner["spec_sha256"]
            checks[node + "/state/" + path.name] = sha(path.with_suffix(".pid.json")) == owner["state_sha256"]
            if owner["kind"] == "old_probe":
                launched = next(row["start_reply"] for row in raw["processes"] if row["spec"] == str(path))
                checks[node + "/exact_launch/" + path.name] = all(owner[k] == launched[k] for k in ("pid", "start_time", "spec"))
    nodes = {}
    for node in ("node27", "node29"):
        rows = [r for r in control["ssh"] if r.get("evidence", {}).get("node") == node]
        valid = []
        for index, row in enumerate(rows):
            evidence = row["evidence"]
            checks[f"{node}/ssh{index}/raw_json"] = row["returncode"] == 0 and json.loads(row["stdout"]) == evidence
            expected = task["nodes"][node]["identity"]
            identity = evidence["identity"]
            continuity = all(identity.get(k) == expected[k] for k in ("boot_id", "container_id", "container_started_at"))
            checks[f"{node}/ssh{index}/continuity"] = evidence["identity_guard"]["passed"] == continuity
            if evidence.get("container_evidence"):
                inside = evidence["container_evidence"]
                checks[f"{node}/ssh{index}/container_raw_json"] = json.loads(evidence["container_probe"]["stdout"]) == inside
                checks[f"{node}/ssh{index}/container_zero_work"] = inside["signals"] == 0 and inside["gpu_operations"] == 0
                checks[f"{node}/ssh{index}/current_namespace_recorded"] = inside["pid_namespace"].startswith("pid:[")
                for owner in inside["owners"]:
                    key = f"{node}/owner/{owner['pid']}"
                    registration = load(Path(owner["spec"]).with_suffix(".pid.json"))
                    checks[key + "/registration"] = all(owner[k] == registration[k] for k in ("pid", "start_time", "spec"))
                    checks[key + "/hashes"] = owner["hashes_passed"] and owner["registration_passed"]
                    checks[key + "/zero_signal"] = owner["signal_sent"] is False
                    actual = owner["actual"]
                    if not actual["exists"]:
                        expected_class = "exit_evidence_absent"
                    elif actual.get("start_time") != owner["start_time"]:
                        expected_class = "pid_reused_blocked"
                    elif actual.get("state") == "Z":
                        expected_class = "exit_evidence_zombie"
                    elif actual.get("pgid") != owner["pid"] or actual.get("argv") != owner["command"]:
                        expected_class = "owner_mismatch_blocked"
                    else:
                        expected_class = "exact_live_cleanup_eligible"
                    checks[key + "/independent_classification"] = owner["classification"] == expected_class
                    checks[key + "/command_matches_spec"] = owner["command"] == load(owner["spec"])["command"]
                    if owner["classification"].startswith("exit_evidence_"):
                        checks[key + "/exit_evidence"] = continuity and not actual["live"] and (
                            not actual["exists"] or actual["start_time"] == owner["start_time"])
                    if owner["kind"] == "protected":
                        checks[key + "/not_cleanup_target"] = owner["cleanup_eligible"] is False
                if continuity:
                    valid.append(evidence)
        host_reachable = bool(rows)
        own_available = any(e.get("control_available") for e in valid)
        exits = [o for e in valid for o in e.get("container_evidence", {}).get("owners", [])
                 if o["kind"] == "old_probe" and o["classification"].startswith("exit_evidence_")]
        nodes[node] = {"host_control_reachable": host_reachable,
                       "owned_container_control_available": own_available,
                       "own_probe_cleanup_confirmed": len({o["pid"] for o in exits}) == 2,
                       "boot_identity_verified": bool(valid),
                       "current_identity": valid[0]["identity"] if valid else None,
                       "evidence_ref": {"path": str(directory / "summary.json"), "sha256": sha(directory / "summary.json")}}
    for row in control["http"]:
        if row["passed"]:
            payload = load(row["raw_path"])
            checks["http/" + row["endpoint"]] = sha(row["raw_path"]) == row["response_sha256"] and all(
                payload.get(k) == row[k] for k in ("worker", "session", "transactions"))
    delta = {"scope": "read-only control and old-probe exit evidence; no full protected postflight",
             "nodes": nodes, "pending_exact_targets": control["pending_exact_targets"],
             "protected_postflight_complete": False, "protected_state_corrupted": None,
             "runtime_accepted": False, "performance_ready": False,
             "old_failed_summary_preserved": True,
             "source_freeze_limit": "snapshot SHA checked locally; this run does not certify remote worker source SHA separately"}
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "failed": [k for k, v in checks.items() if not v], "state_delta": delta,
            "gpu_operations": 0, "remote_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = review(args.control.resolve())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()),
                      "total": len(result["checks"]), "failed": result["failed"]}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
