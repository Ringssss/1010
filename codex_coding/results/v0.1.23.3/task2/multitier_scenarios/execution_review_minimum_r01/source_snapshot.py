"""Freeze the smallest physical qualification sequence without remote execution."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
MIB = 1 << 20
GIB = 1 << 30


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root=ROOT):
    rel = "codex_coding/results/v0.1.23.3/task2/multitier_scenarios/contract_cpu_r02/contract.json"
    contract = json.loads((root / rel).read_text())
    workers = {x["id"]: x for x in contract["workers"]}
    locations = {x["id"]: x for x in contract["locations"]}
    phases = {x["id"]: x for x in contract["phases"]}
    blocks = {x["id"]: x for x in contract["blocks"]}
    scenarios = []
    selections = {"SSD_only": [{"block": b["id"], "candidate": b["id"] + "@" + b["authoritative_location"], "nbytes": b["nbytes"]} for b in blocks.values()]}
    selections.update({name: phases[name]["witness"] for name in ("C0", "C1", "C3")})
    for name, witness in selections.items():
        ledger = {node: {"staging_hbm_bytes": 128 * MIB, "target_hbm_bytes": 0,
                         "retained_hbm_bytes": 0, "retained_dram_bytes": 0,
                         "fixed_ssd_bytes": 0, "runtime_hbm_bytes": None,
                         "runtime_dram_bytes": None, "http_and_pagecache_peak_bytes": None,
                         "checksum_and_registration_peak_bytes": None} for node in ("n0", "n1", "n2")}
        routes = []
        for b in blocks.values():
            ledger[workers[b["target_worker"]]["node"]]["target_hbm_bytes"] += b["nbytes"]
            ledger[locations[b["authoritative_location"]]["node"]]["fixed_ssd_bytes"] += b["nbytes"]
        for item in witness:
            loc = locations[item["candidate"].split("@", 1)[1]]
            if loc["tier"] != "ssd":
                ledger[loc["node"]]["retained_" + loc["tier"] + "_bytes"] += item["nbytes"]
            b = blocks[item["block"]]
            source = loc["worker"]
            target = b["target_worker"]
            routes.append({"block": b["id"], "candidate": item["candidate"],
                           "tier": loc["tier"], "source_worker": source,
                           "target_worker": target, "source_node": loc["node"],
                           "target_node": workers[target]["node"], "nbytes": b["nbytes"],
                           "source_numa": loc["numa"], "target_numa": workers[target]["numa"],
                           "physical_route_proof_required": True})
        for entry in ledger.values():
            entry["known_allocated_hbm_bytes_before_runtime"] = sum(entry[k] for k in ("staging_hbm_bytes", "target_hbm_bytes", "retained_hbm_bytes"))
            entry["staging_already_in_measured_runtime_must_not_be_added_twice"] = True
        scenarios.append({"id": name, "repetitions": 1, "payload_bytes": contract["payload_bytes"],
                          "routes": routes, "ledger_by_logical_node": ledger,
                          "mode": "functional_observation_original_serial_Cluster_transfer",
                          "time_windows": ["save_build_and_publish", "prepare", "execute_filled", "verify_publish", "retirement"],
                          "expected_sha_blocks": list(blocks), "expected_target_ranks": [0, 1, 2, 3],
                          "expected_ssd_logical_source_bytes": sum(x["nbytes"] for x in routes if x["tier"] == "ssd"),
                          "physical_disk_read_bytes_claim": None})
    refs = [rel, "docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md",
            "codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure/build_setup_plan.py",
            "codex_coding/results/v0.1.23.3/task2/multitier_scenarios/review_hardware_r02/qualification.json",
            "main_lib/fluidckpt/memory/ssd.py", "main_lib/fluidckpt/cluster/worker.py",
            "main_lib/fluidckpt/cluster/control.py"]
    return {"development": "v0.1.23.3/task2", "schema": "minimum_multitier_execution_v1",
            "contract_internal_sha256": contract["contract_hash"],
            "allowed_physical_nodes": ["node27", "node28", "node30"],
            "required_physical_gpu_nodes": 3, "required_physical_gpu_count": 6,
            "new_scenario_budget": {"dram_bytes_per_node": 6 * GIB, "hbm_bytes_per_node": 2 * GIB,
                "container_ceiling_bytes": 8 * GIB, "postwarm_application_margin_bytes": 256 * MIB,
                "physical_free_reserve_dram_bytes_per_node": 2 * GIB,
                "physical_free_reserve_hbm_bytes_per_selected_gpu": 2 * GIB,
                "scope": "aggregate incremental runtime, HTTP/pagecache, buffers, checksum and retained/target bytes",
                "authorization": "parent task coordination explicitly accepted a new finite cap for current user-authorized three-node six-GPU scenario",
                "fresh_gate": "min(task cap, measured available minus physical reserve, cgroup available); selected NUMA must also admit touching/registering allocation",
                "historical_2GiB_result_policy": "preserve old failed_observed_budget and old contracts; new scenario has separate run IDs and cap"},
            "budget_origin": {"historical": "temporary incremental budget for old two-worker small experiment",
                "user_hard_limit_original_quote_found": False,
                "first_documented_statement": "docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md:660",
                "exact_excerpt": "暂定新增物理实验配额每node HBM≤2GiB、DRAM≤2GiB，容器8GiB总量并单列Torch/驱动与临时峰值"},
            "sequence": ["read_only_binding_provenance_and_old_owner_protection", "fresh_selected_GPU_runtime_BDF_NUMA_mesh_qualification",
                         "create_fsync_verify_protect_complete_SSD_4_2_2_manifest", "SSD_only_once", "C0_once", "C1_once", "C3_once", "exact_owned_cleanup_and_final_protection"],
            "scenarios": scenarios,
            "third_node_runtime_rule": "node27 unchanged boot with still-D owned pending process blocks blind HIP retry; obtain driver/process recovery evidence or explicitly use a different separately approved execution graph",
            "ssd_rule": "three actual local block-device mounts bound to run-owned directories; shared NFS is a different storage graph",
            "persistent_base_rule": "keep authoritative extents live throughout all phases; temporary retirement excludes base IDs; close invokes delete, so shutdown needs explicit base-safe lifecycle evidence",
            "shared_workpoint_rule": "only after original executor proves two disjoint concurrent pairs and measured shared physical incidence; C2a remains logical reference",
            "physical_ready": False, "gpu_command": None,
            "claim_scope": "CPU frozen admission design; actual three-node runtime, performance and model T_ready require measured evidence",
            "evidence_refs": [{"path": p, "sha256": digest(root / p)} for p in refs]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    plan = build()
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "minimum_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    checks = {"exact_minimal_scenarios": [x["id"] for x in plan["scenarios"]] == ["SSD_only", "C0", "C1", "C3"],
              "every_scenario_32MiB": all(x["payload_bytes"] == 32 * MIB for x in plan["scenarios"]),
              "every_scenario_once": all(x["repetitions"] == 1 for x in plan["scenarios"]),
              "SSD_only_all_3_storage_nodes": {x["source_node"] for x in plan["scenarios"][0]["routes"]} == {"n0", "n1", "n2"},
              "C3_both_helper_sources": {"w1", "w3"} <= {x["source_worker"] for x in plan["scenarios"][-1]["routes"]},
              "C3_SSD_gap_4MiB": plan["scenarios"][-1]["expected_ssd_logical_source_bytes"] == 4 * MIB,
              "task_caps_new_independent": plan["new_scenario_budget"]["dram_bytes_per_node"] == 6 * GIB,
              "no_physical_or_GPU_claim": plan["physical_ready"] is False and plan["gpu_command"] is None}
    (args.out / "qualification.json").write_text(json.dumps({"qualification": "minimum_execution_design_passed" if all(checks.values()) else "failed",
        "checks": checks, "passed": all(checks.values()), "physical_ready": False,
        "new_budget_is_not_physical_peak_evidence": True}, indent=2) + "\n")
    (args.out / "source_snapshot.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"out": str(args.out), "checks": len(checks), "passed": all(checks.values())}))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
