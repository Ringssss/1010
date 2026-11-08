"""Narrow local review of the current node28 setup and optional fresh init/copy."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "audit"))
from review_outer_guard import keeper_signature, memory_headroom

RESULTS = HERE.parents[4] / "results/v0.1.23.3/task2/three_node_recovery"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    paths = {"spec": RESULTS / "admission28_plan_r01/node28.spec.json",
             "setup": RESULTS / "admission28_setup_r01/stdout.json",
             "before": RESULTS / "admission28_guard_before_init_r01/stdout.json"}
    if args.after:
        paths["after"] = args.after / "stdout.json"
    data = {k: json.loads(p.read_text()) for k, p in paths.items()}
    spec, setup, before = data["spec"], data["setup"], data["before"]
    checks = {"setup_docker_success": setup["run"]["returncode"] == 0}
    for label in ("before", "after"):
        if label not in data:
            continue
        guard = data[label]
        ci = guard["container"]
        hc = ci["HostConfig"]
        checks[label + "/identity"] = all(guard["container_identity_checks"].values()) and guard["boot_id"] == spec["boot_id"] and guard["hostname"] == spec["hostname"] and ci["id"] == setup["container"]["id"] and ci["State"]["StartedAt"] == setup["container"]["State"]["StartedAt"] and ci["image_id"] == spec["image_id"] and ci["State"]["Running"] is True
        checks[label + "/runtime_config"] = hc["Memory"] == 8 << 30 and hc["IpcMode"] == "private" and hc["ShmSize"] == 1 << 30 and not hc["Privileged"] and hc["PidsLimit"] == 512 and hc["CapAdd"] == ["IPC_LOCK"]
        checks[label + "/headroom"] = memory_headroom(guard) >= (2 << 30) + (512 << 20)
        checks[label + "/assets"] = all(hashlib.sha256(base64.b64decode(a["base64"], validate=True)).hexdigest() == a["sha256"] == guard["source_assets"][a["relative_path"]]["sha256"] for a in spec["assets"])
        discoveries = {r["pid"]: r for r in guard["keeper_process_discovery"]}
        checks[label + "/all13_old_processes"] = len(spec["known_live_workers"]) == 13 and all(discoveries.get(r["pid"], {}).get("owner_matches") is True and discoveries[r["pid"]]["start_time_ticks"] == r["start_time_ticks"] for r in spec["known_live_workers"])
        protected = {c["id"]: c for c in guard["protected_containers"]}
        checks[label + "/old_containers"] = all(r["id"] in protected and protected[r["id"]]["State"]["StartedAt"] == r["started"] and protected[r["id"]]["State"]["Running"] for r in spec["protected_container_expectations"])
        keepers = {r["endpoint"]: r for r in guard["keepers"]}
        checks[label + "/catalogs_idle_preserved"] = set(keepers) == set(spec["expected_keeper_catalogs"]) and all(r["passed"] and r["catalog_sha256"] == keeper_signature(r["body"]) == spec["expected_keeper_catalogs"][ep] and not r["body"]["transactions"] and all(x["leases"] == 0 for x in r["body"]["replicas"]) for ep, r in keepers.items())
    runtime = None
    stage = None
    if args.run:
        for name in ("registration.json", "child.owner.json", "child.process.json", "summary.json", "source.py", "child.events.jsonl", "child.log"):
            paths["run/" + name] = args.run / name
        reg = json.loads(paths["run/registration.json"].read_text())
        summary = json.loads(paths["run/summary.json"].read_text())
        process = json.loads(paths["run/child.process.json"].read_text())
        owner = json.loads(paths["run/child.owner.json"].read_text())
        events = [json.loads(line) for line in paths["run/child.events.jsonl"].read_text().splitlines()]
        stage = reg["stage"]
        checks["run/identity_source"] = reg["container_id"] == before["container"]["id"] and reg["container_started_at"] == before["container"]["State"]["StartedAt"] and reg["bdf"] == spec["bdf"] and reg["host_boot_id"] == spec["boot_id"] and sha(paths["run/source.py"]) == reg["source_sha256"] == next(a["sha256"] for a in spec["assets"] if a["relative_path"].endswith("/init_gate.py"))
        checks["run/process_summary"] = summary["process"] == process and process["hard_deadline_seconds"] == 15
        checks["run/cleanup"] = process["cleanup"]["passed"] and not process["cleanup"].get("pending_group_members") and not process["actual_after"]["live"]
        checks["run/owner_event_identity"] = process["owner"] == owner and all(row["pid"] == owner["pid"] for row in events) and events[0]["process"]["start_time"] == owner["start_time"] and events[0]["core_limit"] == [0, 0]
        if process["cleanup"]["signals"]:
            checks["run/exact_signal_identity"] = all(process["cleanup"]["before"].get(k) == owner.get(k) for k in ("pid", "start_time", "pgid", "sid", "argv"))
        payload = summary.get("child_result")
        expected_pass = process["returncode"] == 0 and not process["timed_out"] and not process["registration_error"] and process["init_deadline_passed"] and checks["run/cleanup"] and payload is not None and payload["status"] == "passed"
        checks["run/pass_classification"] = summary["passed"] == expected_pass
        if expected_pass:
            checks["run/actual_hip_admission"] = payload["actual_bdf"] == spec["bdf"] and payload["hip_free_bytes"] > (2 << 30) + (1 << 20)
            if stage == "copy":
                correct = payload["correctness"]
                expected_sha = hashlib.sha256(bytes(i % 251 for i in range(1 << 20))).hexdigest()
                checks["run/full_copy_sha"] = correct["passed"] and correct["verified_bytes"] == 1 << 20 and correct["sha256"] == correct["expected_sha256"] == expected_sha
        runtime = summary["passed"]
    result = {"evidence_consistent": all(checks.values()), "checks": checks, "failed": [k for k, v in checks.items() if not v], "runtime_stage": stage, "runtime_passed": runtime, "copy_passed": runtime if stage == "copy" else None, "xml_runtime_accepted": None, "performance_ready": False, "postflight_present": bool(args.after), "scope": "Current node28 metadata, exact old process identities and keeper catalog; no full weight content reread or cluster-wide claim.", "input_refs": [{"path": str(p.resolve()), "sha256": sha(p)} for p in paths.values()]}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("evidence_consistent", "failed", "runtime_stage", "runtime_passed", "postflight_present")}))
    if not result["evidence_consistent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
