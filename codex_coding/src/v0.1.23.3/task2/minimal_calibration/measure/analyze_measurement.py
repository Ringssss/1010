"""Bind raw evidence and check observed whole-process/container resource peaks."""
import argparse
import hashlib
import json
from pathlib import Path


def reference(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def integer(value):
    return int(value) if isinstance(value, (str, int)) and str(value).isdigit() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--guard-before", type=Path, required=True)
    parser.add_argument("--guard-after", type=Path, required=True)
    parser.add_argument("--first-container-guard", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    summary = json.loads((args.run / "summary.json").read_text())
    result = summary.get("child_result")
    before, after, first = (json.loads(p.read_text()) for p in
                            (args.guard_before, args.guard_after, args.first_container_guard))
    source_refs = [reference(p) for p in (args.run / "summary.json", args.run / "registration.json",
                   args.run / "child.process.json", args.guard_before, args.guard_after, args.first_container_guard)]
    checks = {"run_passed": summary.get("passed") is True, "child_result_present": result is not None}
    report = {"schema_version": "fluidckpt.minimal_calibration.measurement_review.v1", "checks": checks,
              "evidence_refs": source_refs, "formal_performance_ready": False, "planner_profile_eligible": False,
              "resource_peak_scope": "20ms sampled physical VRAM and kernel cgroup memory.peak; instantaneous GPU peak can be missed"}
    if result:
        samples = [result.get("before_resources", {}), *result.get("resource_samples", []),
                   result.get("before_transfer_resources", {}), result.get("after_transfer_resources", {})]
        gpu = [integer(s.get("sysfs_gpu_used_bytes")) for s in samples]
        gpu = [v for v in gpu if v is not None]
        current = [integer(s.get("cgroup_memory", {}).get("memory.current")) for s in samples]
        current = [v for v in current if v is not None]
        peak = [integer(s.get("cgroup_memory", {}).get("memory.peak")) for s in samples]
        peak = [v for v in peak if v is not None]
        gpu_base = integer(before.get("selected_gpu_sysfs", {}).get("mem_info_vram_used"))
        mem_base = integer(before.get("container_cgroup", {}).get("files", {}).get("memory.current"))
        observed_gpu = max(gpu) - gpu_base if gpu and gpu_base is not None else None
        container_peak = max(peak + current) if peak or current else None
        observed_dram = container_peak - mem_base if container_peak is not None and mem_base is not None else None
        report["resources"] = {"before_run_guard_gpu_used_bytes": gpu_base, "observed_gpu_used_peak_bytes": max(gpu) if gpu else None,
            "observed_incremental_GPU_bytes": observed_gpu, "before_run_guard_cgroup_current_bytes": mem_base,
            "first_container_guard_gpu_used_bytes": integer(first.get("selected_gpu_sysfs", {}).get("mem_info_vram_used")),
            "first_container_guard_cgroup_current_bytes": integer(first.get("container_cgroup", {}).get("files", {}).get("memory.current")),
            "observed_container_current_peak_bytes": max(current) if current else None,
            "kernel_cgroup_memory_peak_bytes": max(peak) if peak else None, "observed_incremental_container_bytes": observed_dram,
            "HBM_limit_bytes": 2 << 30, "DRAM_limit_bytes": 2 << 30, "background_usage_not_attributed": True,
            "GPU_peak_is_sampled_lower_bound": True, "container_peak_is_cumulative_since_container_creation": True}
        checks.update(correct_full_SHA=result.get("correctness", {}).get("passed") is True,
                      completed_transfer_samples_present=bool(result.get("samples")) or result["config"].get("rank") == 0,
                      sampled_HBM_within_limit=observed_gpu is not None and 0 <= observed_gpu <= 2 << 30,
                      sampled_DRAM_within_limit=observed_dram is not None and 0 <= observed_dram <= 2 << 30,
                      cgroup_DRAM_peak_within_limit=bool(peak) and observed_dram is not None and 0 <= observed_dram <= 2 << 30,
                      original_XML_preserved=result.get("original_xml_preserved") is True,
                      cleanup_errors_empty=not result.get("cleanup_errors"))
        for label, guard in (("before", before), ("after", after)):
            container = guard.get("container", {})
            checks[label + "_container_identity"] = (container.get("id") == result["config"]["container_id"] and
                container.get("State", {}).get("StartedAt") == result["config"]["container_started_at"] and
                container.get("State", {}).get("Running") is True)
            events = guard.get("container_cgroup", {}).get("files", {}).get("memory.events", "")
            checks[label + "_no_oom"] = isinstance(events, str) and "oom 0" in events and "oom_kill 0" in events
            checks[label + "_staged_SHA"] = all(row.get("sha256") == row.get("expected_sha256")
                                                 for row in guard.get("source_assets", {}).values())
            checks[label + "_known_owner_identities"] = all(row.get("owner_matches") is True
                                                             for row in guard.get("keeper_process_discovery", []))
        checks["retained_catalogs_unchanged"] = {r["endpoint"]: r.get("catalog_sha256") for r in before.get("keepers", [])} == {
            r["endpoint"]: r.get("catalog_sha256") for r in after.get("keepers", [])}
        def old_containers(guard):
            return [(c.get("id"), c.get("State", {}).get("StartedAt"), c.get("State", {}).get("Running"))
                    for c in guard.get("protected_containers", [])]
        checks["retained_containers_unchanged"] = old_containers(before) == old_containers(after)
        processes = summary.get("process", {})
        checks["own_process_cleanup"] = processes.get("cleanup", {}).get("passed") is True and not processes.get("actual_after", {}).get("live", False)
        if "host_allocation" in result:
            host = result["host_allocation"]
            checks["actual_NUMA_pages"] = host.get("placement_samples") == 256 and host.get("sampled_page_distribution") == {str(result["config"]["source_numa"]): 1.0}
        if result["config"]["primitive"].startswith("remote"):
            path = result.get("runtime_path", {})
            checks["actual_IB_XML_path"] = all(path.get(k, {}).get("passed") is True for k in ("communication", "gpu_mapping", "net_mapping"))
        for path in (args.run / "source.py", args.run / "config.json", args.run / "child.result.json"):
            if path.exists():
                source_refs.append(reference(path))
    report["observed_checks_passed"] = all(checks.values())
    if result:
        observed_failure = not checks.get("sampled_HBM_within_limit") or not checks.get("sampled_DRAM_within_limit")
        report["resource_budget_status"] = ("failed_observed_budget" if observed_failure else
            "observed_within_limit_full_peak_unverified" if not checks.get("cgroup_DRAM_peak_within_limit") else "kernel_DRAM_peak_within_limit_GPU_peak_sampled")
        report["full_physical_peak_verified"] = False
        report["functional_checks_passed"] = all(value for key, value in checks.items() if key not in
            ("sampled_HBM_within_limit", "sampled_DRAM_within_limit", "cgroup_DRAM_peak_within_limit"))
    report["hardware_capacity_or_exclusive_admission_established"] = False
    (args.out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "observed_checks_passed": report["observed_checks_passed"],
                      "formal_performance_ready": False}))


if __name__ == "__main__":
    main()
