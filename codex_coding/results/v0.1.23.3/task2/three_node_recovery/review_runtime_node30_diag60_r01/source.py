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
    parser.add_argument("--before", type=Path)
    parser.add_argument("--node", choices=("node28", "node30"), default="node28")
    parser.add_argument("--diagnostic60", action="store_true")
    variant = parser.add_mutually_exclusive_group()
    variant.add_argument("--hostipc", action="store_true")
    variant.add_argument("--sysnice", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    paths = {"spec": RESULTS / "admission28_plan_r01/node28.spec.json",
             "setup": RESULTS / "admission28_setup_r01/stdout.json",
             "before": RESULTS / "admission28_guard_before_init_r01/stdout.json"}
    if args.node == "node30":
        paths.update(spec=RESULTS / "inventory_setup_plan_r06/node30.spec.json",
                     setup=RESULTS / "inventory_setup_node30_r01/stdout.json",
                     before=RESULTS / "diagnosis30_guard_reused_before_r01/stdout.json")
        if args.hostipc or args.sysnice:
            parser.error("hostIPC/SYS_NICE reviews are limited to their node28 runs")
    if args.hostipc or args.sysnice:
        prefix = "admission28_hostipc" if args.hostipc else "admission28_sysnice"
        paths.update(spec=RESULTS / (prefix + "_plan_r01/node28.spec.json"),
                     setup=RESULTS / (prefix + "_plan_r01/setup.state.json"),
                     before=RESULTS / (prefix + "_guard_before_r01/stdout.json"))
    if args.before:
        paths["before"] = args.before / "stdout.json"
    if args.diagnostic60:
        paths["diagnostic_plan"] = RESULTS / "diagnosis30_diag60_plan_r01/launch.json"
    if args.after:
        paths["after"] = args.after / "stdout.json"
    data = {k: json.loads(p.read_text()) for k, p in paths.items()}
    spec, setup, before = data["spec"], data["setup"], data["before"]
    expected_catalogs = spec.get("expected_keeper_catalogs", {r["endpoint"]: r["catalog_sha256"] for r in before["keepers"]})
    if args.hostipc or args.sysnice:
        setup_dir = RESULTS / (prefix + "_plan_r01")
        paths["setup_stdout"] = setup_dir / "setup.stdout.txt"
        paths["setup_returncode"] = setup_dir / "setup.returncode.txt"
        setup = {"run": {"returncode": int(paths["setup_returncode"].read_text())},
                 "container": {"id": paths["setup_stdout"].read_text().strip(), "State": setup}}
    checks = {"setup_docker_success": setup["run"]["returncode"] == 0}
    for label in ("before", "after"):
        if label not in data:
            continue
        guard = data[label]
        ci = guard["container"]
        hc = ci["HostConfig"]
        checks[label + "/identity"] = all(guard["container_identity_checks"].values()) and guard["boot_id"] == spec["boot_id"] and guard["hostname"] == spec["hostname"] and ci["id"] == setup["container"]["id"] and ci["State"]["StartedAt"] == setup["container"]["State"]["StartedAt"] and ci["image_id"] == spec["image_id"] and ci["State"]["Running"] is True
        checks[label + "/runtime_config"] = hc["Memory"] == 8 << 30 and hc["IpcMode"] == ("host" if args.hostipc else "private") and hc["ShmSize"] == 1 << 30 and not hc["Privileged"] and hc["PidsLimit"] == 512 and set(hc["CapAdd"]) == ({"IPC_LOCK", "SYS_NICE"} if args.sysnice else {"IPC_LOCK"})
        checks[label + "/headroom"] = memory_headroom(guard) >= (2 << 30) + (512 << 20)
        checks[label + "/assets"] = all(hashlib.sha256(base64.b64decode(a["base64"], validate=True)).hexdigest() == a["sha256"] == guard["source_assets"][a["relative_path"]]["sha256"] for a in spec["assets"])
        discoveries = {r["pid"]: r for r in guard["keeper_process_discovery"]}
        checks[label + "/declared_old_processes"] = (args.node != "node28" or len(spec["known_live_workers"]) == 13) and all(discoveries.get(r["pid"], {}).get("owner_matches") is True and discoveries[r["pid"]]["start_time_ticks"] == r["start_time_ticks"] for r in spec["known_live_workers"])
        protected = {c["id"]: c for c in guard["protected_containers"]}
        old_expectations = spec.get("protected_container_expectations", [{"id": r["id"], "started": r["State"]["StartedAt"]} for r in before["protected_containers"]])
        checks[label + "/old_containers"] = all(r["id"] in protected and protected[r["id"]]["State"]["StartedAt"] == r["started"] and protected[r["id"]]["State"]["Running"] for r in old_expectations)
        keepers = {r["endpoint"]: r for r in guard["keepers"]}
        checks[label + "/catalogs_idle_preserved"] = set(keepers) == set(expected_catalogs) and all(r["passed"] and r["catalog_sha256"] == keeper_signature(r["body"]) == expected_catalogs[ep] and not r["body"]["transactions"] and all(x["leases"] == 0 for x in r["body"]["replicas"]) for ep, r in keepers.items())
    free_observations = []
    if "after" in data:
        after_keepers = {r["endpoint"]: r for r in data["after"]["keepers"]}
        for row in before["keepers"]:
            if row["body"]["gpu"] != spec["gpu"]:
                continue
            old_free = row["body"]["headroom_bytes"]["hbm"]
            new_free = after_keepers[row["endpoint"]]["body"]["headroom_bytes"]["hbm"]
            checks["after/selected_keeper_hip_free"] = old_free > 2 << 30 and max(0, old_free-new_free) <= 128 << 20
            free_observations.append({"endpoint": row["endpoint"], "before_bytes": old_free, "after_bytes": new_free, "scope": "existing initialized keeper HIP free, not fresh child or sysfs"})
    runtime = None
    stage = None
    source_hash = next(a["sha256"] for a in spec["assets"] if a["relative_path"].endswith("/init_gate.py"))
    if args.diagnostic60:
        checks["diagnostic_plan/original_source_preserved"] = data["diagnostic_plan"]["original_source_sha256"] == source_hash
        source_hash = data["diagnostic_plan"]["diagnostic_source_sha256"]
    if args.run:
        for name in ("registration.json", "child.owner.json", "child.process.json", "summary.json", "source.py", "child.events.jsonl", "child.log"):
            paths["run/" + name] = args.run / name
        reg = json.loads(paths["run/registration.json"].read_text())
        summary = json.loads(paths["run/summary.json"].read_text())
        process = json.loads(paths["run/child.process.json"].read_text())
        owner = json.loads(paths["run/child.owner.json"].read_text())
        events = [json.loads(line) for line in paths["run/child.events.jsonl"].read_text().splitlines()]
        stage = reg["stage"]
        checks["run/identity_source"] = reg["container_id"] == before["container"]["id"] and reg["container_started_at"] == before["container"]["State"]["StartedAt"] and reg["bdf"] == spec["bdf"] and reg["host_boot_id"] == spec["boot_id"] and sha(paths["run/source.py"]) == reg["source_sha256"] == source_hash
        checks["run/process_summary"] = summary["process"] == process and process["hard_deadline_seconds"] == reg["hard_deadline_seconds"] == (60 if args.diagnostic60 else 15)
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
    result = {"evidence_consistent": all(checks.values()), "checks": checks, "failed": [k for k, v in checks.items() if not v], "runtime_stage": stage, "runtime_passed": runtime, "original_15s_admission_passed": False if args.diagnostic60 else runtime if stage == "init" else None, "diagnostic60_runtime_passed": runtime if args.diagnostic60 else None, "copy_passed": runtime if stage == "copy" else None, "xml_runtime_accepted": None, "performance_ready": False, "postflight_present": bool(args.after), "selected_keeper_hip_free": free_observations, "scope": "Current node28 metadata, exact old process identities and keeper catalog; no full weight content reread or cluster-wide claim. A 60s diagnostic does not certify original 15s admission.", "input_refs": [{"path": str(p.resolve()), "sha256": sha(p)} for p in paths.values()]}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("evidence_consistent", "failed", "runtime_stage", "runtime_passed", "original_15s_admission_passed", "diagnostic60_runtime_passed", "postflight_present")}))
    if not result["evidence_consistent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
