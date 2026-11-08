"""Verify parent-produced node27 copy evidence without rerunning hardware."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    setup = json.loads(args.setup.read_text())
    launch = json.loads((args.run / "prepared_launch.json").read_text())
    result = json.loads((args.run / "result.json").read_text())
    art = args.run / "artifacts"
    registration = json.loads((art / "registration.json").read_text())
    summary = json.loads((art / "summary.json").read_text())
    plan = json.loads((args.plan / "plan.json").read_text())
    process, payload = summary["process"], summary["child_result"]
    setup_container = setup["container"]
    checks = {
        "actual_setup_rc0": setup["run"]["returncode"] == 0,
        "actual_container_private_core0_not_privileged": setup_container["HostConfig"]["IpcMode"] == "private" and setup_container["HostConfig"]["Privileged"] is False and
            {"Name": "core", "Hard": 0, "Soft": 0} in setup_container["HostConfig"]["Ulimits"],
        "single_copy_command_scope": registration["node"] == "node27" and registration["stage"] == "copy" and registration["gpu"] == 0 and registration["visibility_policy"] == "selected",
        "registered_container_matches_setup": registration["container_id"] == setup_container["id"] and registration["container_started_at"] == setup_container["State"]["StartedAt"],
        "registered_boot_namespace_matches_owned_process": registration["host_boot_id"] == process["owner"]["boot_id"] and registration["pid_namespace"] == process["owner"]["pid_namespace"],
        "registered_core0": registration["core_dump_limit"] == [0, 0],
        "runtime_source_SHA_exact": sha(art / "source.py") == registration["source_sha256"] == plan["copy_runtime_source_sha256"],
        "actual_selected_GPU_BDF09": payload["actual_bdf"] == payload["requested_bdf"] == "0000:09:00.0",
        "actual_1MiB_verified": payload["payload_bytes"] == payload["correctness"]["verified_bytes"] == 1 << 20,
        "SHA_independently_reconstructed": hashlib.sha256(bytes(i % 251 for i in range(1 << 20))).hexdigest() == payload["correctness"]["sha256"] == payload["correctness"]["expected_sha256"],
        "natural_rc0_no_timeout_registration_error": process["returncode"] == 0 and process["timed_out"] is False and process["registration_error"] is None,
        "bounded_60s_observation": process["hard_deadline_seconds"] == 60 and process["elapsed_ns"] < 60 * 10**9,
        "cleanup_exact_no_signal_empty_group": process["cleanup"]["passed"] is True and process["cleanup"]["signals"] == [] and process["cleanup"]["pending_group_members"] == [],
        "owned_child_absent_after": process["actual_after"]["state"] == "absent" and process["actual_after"]["pid"] == process["owner"]["pid"],
        "docker_cli_rc0": result["returncode"] == 0,
        "fresh_CID_started_preserved": launch["before"]["id"] == result["after"]["id"] == setup_container["id"] and launch["before"]["State"]["StartedAt"] == result["after"]["State"]["StartedAt"],
        "cgroup_quiet_after": result["cgroup_after"]["pids.current"] == "1",
        "cgroup_memory_below_new6GiB_cap": int(result["cgroup_after"]["memory.max"]) == 6 << 30 and int(result["cgroup_after"]["memory.current"]) < 6 << 30,
        "old_stopped_containers_exactly_preserved": launch["protected_before"] == result["protected_after"] and all(c["State"]["Running"] is False for c in result["protected_after"]),
        "new_functional_scope_not_NCCL_or_performance": summary["xml_runtime_accepted"] is False and summary["performance_ready"] is False and payload["performance_sample"] is False,
        "observed_total_used_plus_payload_within2GiB": payload["hip_total_bytes"] - payload["hip_free_bytes"] + payload["payload_bytes"] <= 2 << 30,
        "actual_HIP_free_over_guard_plus_payload": payload["hip_free_bytes"] > registration["guard_bytes"] + registration["payload_bytes"],
        "reported_pass_conjunction": summary["passed"] and result["passed"],
    }
    def events(raw):
        return {k: int(v) for k, v in (line.split() for line in raw.splitlines())}
    before, after = events(launch["cgroup_before"]["memory.events"]), events(result["cgroup_after"]["memory.events"])
    checks["OOM_events_independently_equal"] = all(before[k] == after[k] for k in ("oom", "oom_kill"))
    refs = [{"path": str(p.resolve()), "sha256": sha(p)} for p in [args.setup, args.plan / "plan.json", *sorted(args.run.glob("*.json")), *sorted(art.glob("*.json")), art / "source.py"]]
    out = {"qualification": "node27_GPU0_fresh_copy_independently_passed" if all(checks.values()) else "failed",
           "passed": all(checks.values()), "checks": checks, "check_count": len(checks), "evidence_refs": refs,
           "actual_functional_qualification": {"node": "node27", "gpu": 0, "bdf": "0000:09:00.0", "payload_bytes": 1 << 20},
           "observed_HIP_total_used_bytes": payload["hip_total_bytes"] - payload["hip_free_bytes"],
           "full_physical_peak_verified": False, "performance_ready": False, "six_worker_ready": False,
           "observation_elapsed_includes_initialization": True, "review_GPU_calls": 0, "review_SSH_calls": 0}
    (args.out / "qualification.json").write_text(json.dumps(out, indent=2) + "\n")
    (args.out / "audit.source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"passed": out["passed"], "checks": len(checks), "out": str(args.out)}))
    if not out["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
