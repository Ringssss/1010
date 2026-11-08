"""Authenticate the failed node30 init boundary using frozen local and read-only refs."""
import argparse
import hashlib
import json
from pathlib import Path

from review_outer_guard import RESULTS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULTS or not args.out.name.startswith("audit_"):
        parser.error("require a fresh versioned audit directory")
    run = RESULTS / "probe_node30_init_r01"
    registration = json.loads((run / "registration.json").read_text())
    owner = json.loads((run / "child.owner.json").read_text())
    process = json.loads((run / "child.process.json").read_text())
    summary = json.loads((run / "summary.json").read_text())
    events = [json.loads(line) for line in (run / "child.events.jsonl").read_text().splitlines()]
    stages = [row["stage"] for row in events]
    remote_path = RESULTS / "audit_remote_node30_init_r01/summary.json"
    remote = json.loads(remote_path.read_text())
    guard_path = RESULTS / "audit_guard_node30_init_r01/summary.json"
    guard = json.loads(guard_path.read_text())
    checks = {
        "remote_current_lifecycle_and_absence": remote["identity_passed"] and remote["exact_pid13_current_absent"] and remote["known_hosts_unchanged"],
        "frozen_source": sha(run / "source.py") == registration["source_sha256"] == "927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b",
        "local_artifact_hashes_match_remote": all(sha(run / name) == digest for name, digest in remote["remote_artifact_sha256"].items() if (run / name).exists()),
        "only_child_result_missing_remotely": remote["missing_artifacts"] == ["child.result.json"],
        "declared_init_core0_zero_payload": registration["stage"] == "init" and registration["payload_bytes"] == 0 and registration["core_dump_limit"] == [0, 0],
        "failed_summary_preserved": summary["passed"] is False and summary["child_result"] is None and summary["xml_runtime_accepted"] is False and summary["performance_ready"] is False,
        "owner_registration_matches_summary": summary["process"] == process and process["owner"] == owner and owner["pid"] == 13 and owner["start_time"] == "477410700",
        "deadline_15s_plus_bounded_cleanup": process["timed_out"] is True and process["hard_deadline_seconds"] == 15 and 15e9 <= process["elapsed_ns"] <= 16.5e9,
        "exact_term_identity": process["cleanup"]["signals"] == ["TERM"] and all(process["cleanup"]["before"][k] == owner[k] for k in ("pid", "start_time", "pgid", "sid", "argv")),
        "child_reaped_and_group_empty": process["returncode"] == -15 and process["cleanup"]["passed"] is True and process["cleanup"]["after"]["state"] == "absent" and process["cleanup"]["pending_group_members"] == [] and process["actual_after"]["live"] is False,
        "phase_boundary": stages == ["child_enter", "torch_import_enter", "torch_import_exit", "set_device_enter"] and process["last_stage"] == "set_device_enter",
        "child_preimport_identity_core0": events[0]["core_limit"] == [0, 0] and events[0]["pid"] == owner["pid"] and events[0]["boot_id"] == registration["host_boot_id"] == owner["boot_id"] and events[0]["pid_namespace"] == registration["pid_namespace"] == owner["pid_namespace"],
        "outer_metadata_guards_passed": guard["passed"] is True and not guard["failed"],
        "launcher_failed_returncode": (RESULTS / "probe_launch_node30_init_r01/returncode.txt").read_text().strip() == "1",
    }
    import_ns = events[2]["monotonic_ns"] - events[1]["monotonic_ns"]
    raw_before = guard["selected_gpu_sysfs_observations"]["before"]
    raw_after = guard["selected_gpu_sysfs_observations"]["after"]
    sysfs = {"before_total_minus_used_bytes": int(raw_before["mem_info_vram_total"]) - int(raw_before["mem_info_vram_used"]), "after_total_minus_used_bytes": int(raw_after["mem_info_vram_total"]) - int(raw_after["mem_info_vram_used"]), "metric": "sysfs total-used only; not HIP free or budget"}
    sysfs["delta_bytes"] = sysfs["after_total_minus_used_bytes"] - sysfs["before_total_minus_used_bytes"]
    result = {"evidence_consistent": all(checks.values()), "checks": checks, "failed_checks": [k for k, v in checks.items() if not v], "runtime_passed": False, "runtime_failure": "fresh Torch set_device did not return before hard 15s deadline", "torch_import_ns": import_ns, "requested_node": registration["node"], "requested_bdf": registration["bdf"], "actual_hip_bdf_verified": False, "hip_free_bytes": None, "payload_bytes": 0, "communicators_started": 0, "exact_cleanup_verified": True, "outer_metadata_postflight_verified": True, "sysfs": sysfs, "visible_owner_delta": guard["visible_kfd_owner_delta"], "gpu_operations_by_audit": 0, "signals_by_audit": 0, "new_probe_cleanup_signals": ["TERM"], "scope": "one failed node30 masked GPU0 init and its own process/container metadata; no permanent GPU failure, XML rejection, full-cluster protection or HIP physical-free recovery certification", "node27_D_pending_preserved": {"pid": 4037472, "start_time": "477235183"}, "node29_old_pending_preserved": [{"pid": 21883, "start_time": "475875814"}, {"pid": 21901, "start_time": "475875846"}]}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    refs = [run / name for name in ("registration.json", "source.py", "child.owner.json", "child.process.json", "child.events.jsonl", "summary.json")]
    refs += [remote_path, guard_path, RESULTS / "probe_launch_node30_init_r01/command.sh", RESULTS / "probe_launch_node30_init_r01/returncode.txt", RESULTS / "probe_launch_node30_init_r01/stdout.txt"]
    result["input_refs"] = [{"path": str(p), "sha256": sha(p)} for p in refs]
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("checks", "input_refs")}, ensure_ascii=False))
    if not result["evidence_consistent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
