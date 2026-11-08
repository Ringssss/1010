"""Aggregate fixed evidence and freeze final independent scope qualification."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
BASE = ROOT / "codex_coding/results/v0.1.23.3/task2/multitier_scenarios"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, refs = {}, {}
    directories = {"minimum": "execution_review_minimum_r02", "mapping": "execution_review_setup_r02",
                   "adapter": "execution_review_adapter_r01", "runtime": "execution_review_runtime_r01"}
    for namespace, dirname in directories.items():
        path = BASE / dirname / "qualification.json"
        value = json.loads(path.read_text())
        checks.update({namespace + "/" + k: v for k, v in value["checks"].items()})
        refs[str(path)] = sha(path)
        for ref in value.get("evidence_refs", []):
            p = Path(ref["path"])
            p = p if p.is_absolute() else ROOT / p
            checks["provenance/" + str(p.relative_to(ROOT))] = p.is_file() and sha(p) == ref["sha256"]
            refs[str(p)] = ref["sha256"]
    binding_path = BASE / "mapping_binding_r03/machine_binding.json"
    binding = json.loads(binding_path.read_text())
    refs[str(binding_path)] = sha(binding_path)
    hosts = {h["node"]: h for h in binding["hosts"]}
    for ref in binding["evidence_refs"]:
        p = ROOT / ref["path"]
        checks["binding_provenance/" + ref["path"]] = p.is_file() and sha(p) == ref["sha256"]
        refs[str(p)] = ref["sha256"]
    for node, ordinals in (("node28", [0, 1]), ("node30", [4, 5])):
        raw_path = BASE / "mapping_owner_refresh_r01" / (node + ".stdout.json")
        raw = json.loads(raw_path.read_text())
        refs[str(raw_path)] = sha(raw_path)
        checks["owners/" + node + "/same_boot"] = raw["boot_id"] == hosts[node]["boot_id"]
        checks["owners/" + node + "/exact_selected_ordinals"] = [g["historical_ordinal"] for g in raw["gpus"]] == ordinals
        checks["owners/" + node + "/still_metadata_not_runtime"] = raw["runtime_admission"] is False and raw["gpu_calls"] == 0
        for role in hosts[node]["roles"]:
            source = next(g for g in raw["gpus"] if g["bdf"] == role["bdf"])
            checks["owners/" + node + "/" + role["logical_gpu"] + "/exact_raw_free_metadata"] = source == role["focused_owner_free_metadata"] and source["sysfs_total_minus_used_bytes"] == int(source["sysfs_total_bytes"]) - int(source["sysfs_used_bytes"])
    checks["scope/three_logical_nodes_six_selected_GPU_pairs"] = len(hosts) == 3 and sum(len(h["roles"]) for h in hosts.values()) == 6
    checks["scope/new_DRAM6GiB_HBM2GiB_caps_separate_from_legacy"] = binding["task_budget"]["next_proposed_dram_cap_per_node_bytes"] == 6 << 30 and binding["task_budget"]["next_proposed_hbm_cap_per_node_bytes"] == 2 << 30
    checks["scope/physical_mapping_unadmitted"] = binding["physical_ready"] is False
    checks["scope/node27_single_card_actual_subset"] = hosts["node27"]["roles"][0]["runtime_qualified"] is True and hosts["node27"]["roles"][1]["runtime_qualified"] is False
    checks["scope/no_node29_access_or_old_exit_invention"] = binding["node29_accesses"] == 0 and binding["node29_pending"]["new_exit_evidence"] is False
    plan_path = BASE / "execution_review_minimum_r02/minimum_plan.json"
    plan = json.loads(plan_path.read_text())
    refs[str(plan_path)] = sha(plan_path)
    for ref in plan["evidence_refs"]:
        p = Path(ref["path"])
        checks["minimum_snapshot_provenance/" + ref["source_path_at_build"]] = p.is_file() and sha(p) == ref["sha256"]
        refs[str(p)] = ref["sha256"]
    for p in sorted((BASE / "adapter_delivery_r02").rglob("*.py")):
        refs[str(p)] = sha(p)
    for p in sorted(Path(__file__).parent.glob("*.py")):
        refs[str(p)] = sha(p)
    snapshots = []
    for name, expected in sorted(refs.items()):
        path = Path(name)
        target = args.out / "evidence_snapshot" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        if sha(target) != expected:
            raise ValueError("evidence changed while finalizing: " + name)
        snapshots.append({"source_path_at_review": name, "path": str(target.resolve()), "sha256": expected})
    result = {"development": "v0.1.23.3/task2", "qualification": "independent_mapping_API_and_node27_single_GPU_passed" if all(checks.values()) else "failed",
              "passed": all(checks.values()), "checks": checks, "check_count": len(checks),
              "base_independent_check_count": 119,
              "certified_scope": ["three-node six-role static map with real local block-device parent mounts",
                                  "native CPU API serial save/restore plus precise failure cleanup",
                                  "new node27 GPU0/BDF09 actual 1MiB init/copy"],
              "six_worker_execution_ready": False, "SSD_restore_on_current_three_nodes_verified": False,
              "NUMA_touch_registration_current_task_verified": False,
              "physical_trusted_admission_gate_implemented": False,
              "full_physical_peak_verified": False, "performance_ready": False, "actual_model_T_ready_measured": False,
              "review_GPU_calls": 0, "review_SSH_calls": 0,
              "minimal_restore_sequence": ["SSD_only", "C0", "C1", "C3"],
              "new_budget": {"DRAM_incremental_bytes_per_node": 6 << 30, "HBM_incremental_bytes_per_node": 2 << 30,
                             "policy": "aggregate runtime and application; cap contracted independently of old 2GiB experiments"},
              "next_required_inputs": ["fresh second-card/current six-worker BDF/session mesh and necessary path evidence",
                  "NUMA mbind actual touch/register proof in task environment with validated SYS_NICE/seccomp settings",
                  "three run-owned writable local SSD services plus complete 16/8/8MiB base manifest and active Catalog owners",
                  "trusted coordinator gate independently auditing actual path semantics and fresh per-node aggregate runtime/application caps",
                  "once-per-configuration SSD_only/C0/C1/C3 full-SHA recovery and exact protected cleanup"],
              "evidence_refs": snapshots}
    (args.out / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.out / "finalize.source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"passed": result["passed"], "checks": len(checks), "out": str(args.out)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
