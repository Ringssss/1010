"""Independently review a local XML run from its frozen local artifacts."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
PAYLOAD = 1 << 20


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(run):
    raw = load(run / "summary.json")
    protocol = load(run / "protocol.json")
    if all(not row.get("start_reply") for row in raw["processes"]):
        checks = {"raw_prelaunch_failure": raw["status"] == "failed" and not raw.get("functional_stage_complete"),
                  "zero_registered_ranks": all(not Path(r["spec"]).with_suffix(".pid.json").exists() for r in raw["processes"]),
                  "no_rank_results": all(not (run / f"rank_{rank}.json").exists() for rank in (0, 1)),
                  "cleanup_only_not_started": raw["cleanup"]["passed"] and len(raw["cleanup"]["checks"]) == 2 and all(c["not_started"] for c in raw["cleanup"]["checks"]),
                  "no_acceptance_upgrade": raw["runtime_accepted"] is False and raw["whole_machine_runtime_accepted"] is False and raw["nic_gdr_accepted"] is False,
                  "zero_performance_samples": raw["samples_performance"] == 0 and raw["performance_ready"] is False,
                  "protection_not_upgraded": raw["protection"]["passed"] is False,
                  "code_preserved_claim": raw["code_preserved"] is True,
                  "inputs_preserved_claim": raw["inputs_preserved"] is True,
                  "before_read_failure_saved": (run / "before.read_failures.json").exists(),
                  "after_read_failure_saved": (run / "after.read_failures.json").exists()}
        for name, digest in raw["code_sha256"].items():
            checks["code_sha/" + name] = sha(name) == digest == protocol["code_sha256"][name]
        for name, digest in raw["input_sha256"].items():
            checks["input_sha/" + name] = sha(name) == digest == protocol["input_sha256"][name]
        return {"status": "verified_prelaunch_failure" if all(checks.values()) else "failed",
                "checks": checks, "failed": [k for k, v in checks.items() if not v],
                "communication_rank_processes_launched": 0, "runtime_accepted": False,
                "performance_ready": False, "protected_postflight_complete": False,
                "protected_state_corrupted": None, "gpu_operations": 0, "remote_operations": 0,
                "actual_original_run_scope": "zero communication ranks; preflight discovery attempted HIP initialization",
                "interpretation": "preflight read failed; no evidence of XML acceptance or rejection",
                "raw_error": raw["error"], "raw_ref": {"path": str(run / "summary.json"), "sha256": sha(run / "summary.json")}}
    before, after = load(run / "before.json"), load(run / "after.json")
    checks = {"raw_success": raw["status"] == "passed",
              "zero_performance_samples": raw["samples_performance"] == 0 and raw["performance_measurement"] is False,
              "one_warmup_one_functional": raw["warmup_iterations"] == 1 and raw["functional_iterations"] == 1,
              "two_local_ranks": len(raw["processes"]) == 2 and all(r["node"] == "node27" for r in raw["processes"]),
              "payload_1MiB": raw["payload_bytes_per_gpu"] == PAYLOAD,
              "no_whole_machine_upgrade": raw["whole_machine_runtime_accepted"] is False,
              "no_nic_gdr_upgrade": raw["nic_gdr_accepted"] is False,
              "no_performance_upgrade": raw["performance_ready"] is False,
              "cleanup_passed": raw["cleanup"]["passed"] is True,
              "protection_passed": raw["protection"]["passed"] is True,
              "original_free_gate": raw["free_recovery"]["tolerance_bytes"] == 128 << 20,
              "free_recovery_passed": raw["free_recovery"]["passed"] is True,
              "original_xml_unchanged": before["node"]["original_xml"] == after["node"]["original_xml"],
              "same_boot": before["host"]["boot_id"] == after["host"]["boot_id"],
              "same_container": before["host"]["container_identity"] == after["host"]["container_identity"],
              "full_base_bytes": before["base_bytes"] == after["base_bytes"] == 70329718112,
              "base_ids_unchanged": before["base_ids"] == after["base_ids"],
              "full_catalog_unchanged": before["catalog_sha256"] == after["catalog_sha256"],
              "parked_commands_unchanged": before["node"]["parked_commands"] == after["node"]["parked_commands"],
              "both_resource_admissions": before["resource_admission"]["passed"] and after["resource_admission"]["passed"]}
    for name, digest in raw["code_sha256"].items():
        checks["code_sha/" + name] = sha(name) == digest == protocol["code_sha256"][name]
    for name, digest in raw["input_sha256"].items():
        checks["input_sha/" + name] = sha(name) == digest == protocol["input_sha256"][name]
    for stage, snapshot in (("before", before), ("after", after)):
        for worker in snapshot["workers"]:
            checks[f"{stage}/worker{worker['gpu']}/identity"] = worker["node"] == "node27" and worker["worker"] == worker["gpu"] and worker["gpu"] in (0, 1)
            checks[f"{stage}/worker{worker['gpu']}/transactions"] = not worker["transactions"]
            checks[f"{stage}/worker{worker['gpu']}/replicas_unleased"] = all(r["leases"] == 0 for r in worker["replicas"])
            checks[f"{stage}/worker{worker['gpu']}/full_base_valid"] = all(r["state"] == "VALID" for r in worker["replicas"] if r["id"] in snapshot["base_ids"])
    old_protected, new_protected = before["node"]["protected_processes"], after["node"]["protected_processes"]
    checks["three_protected_local_owners"] = len(old_protected) == len(new_protected) == 3
    keys = ("spec", "spec_sha256", "registered", "live_command_sha256", "pgid", "passed")
    checks["protected_owner_identity_unchanged"] = [{k: p[k] for k in keys} for p in old_protected] == [{k: p[k] for k in keys} for p in new_protected]
    checks["protected_all_live_exact"] = all(p["passed"] and p["actual"]["live"] and p["actual"]["start_time"] == p["registered"]["start_time"] for p in old_protected + new_protected)
    graph_path = ROOT / "codex_coding/src/v0.1.23.3/task2/topology_alignment/audit/review.py"
    spec = importlib.util.spec_from_file_location("frozen_graph_reader", graph_path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    host = load(RESULTS / "provenance_r01/node27/host.json")
    coverage, transports = [], []
    for row in raw["processes"]:
        rank, launched = row["rank"], row["start_reply"]
        process_spec = load(row["spec"])
        pid = launched["pid"]
        registered = load(Path(row["spec"]).with_suffix(".pid.json"))
        checks[f"rank{rank}/registration"] = registered == launched
        bundle = load(run / "postexit" / f"rank_{rank}.evidence.json")
        actual = bundle["actual"]
        checks[f"rank{rank}/exact_exit"] = bundle["registered"] == launched and actual["pid"] == pid and not actual["live"] and actual.get("start_time") in (None, launched["start_time"])
        for artifact in bundle["artifacts"]:
            checks[f"rank{rank}/artifact/{Path(artifact['path']).name}"] = artifact["exists"] and sha(artifact["path"]) == sha(artifact["saved_path"]) == artifact["sha256"]
        result = load(run / "postexit" / f"rank_{rank}.json")
        environment = load(run / "postexit" / f"rank_{rank}.environment.json")
        checks[f"rank{rank}/result_identity"] = result["pid"] == pid and result["process"]["start_time"] == launched["start_time"] and result["node"] == "node27" and result["rank"] == result["gpu"] == rank
        checks[f"rank{rank}/preimport_core"] = environment["pid"] == pid and environment["pre_torch_import"] and not environment["torch_imported"] and environment["core_dump_limit"] == [0, 0]
        checks[f"rank{rank}/input_and_distinct_dump"] = environment["effective_environment"]["NCCL_TOPO_FILE"] == process_spec["env"]["NCCL_TOPO_FILE"] and process_spec["env"]["NCCL_TOPO_FILE"] != process_spec["env"]["NCCL_TOPO_DUMP_FILE"]
        checks[f"rank{rank}/selected_visibility"] = all(environment["effective_environment"].get(k) == "0,1" for k in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES"))
        expected_pre = hashlib.sha256(bytes([31 if rank == 0 else 0]) * PAYLOAD).hexdigest()
        expected_post = hashlib.sha256(bytes([31]) * PAYLOAD).hexdigest()
        checks[f"rank{rank}/reset_content_sha"] = result["reset_after_warmup"] and result["pre_transfer_sha"]["sha256"] == expected_pre and result["pre_transfer_sha"]["verified_bytes"] == PAYLOAD
        checks[f"rank{rank}/full_content_sha"] = result["status"] == "passed" and result["correctness"]["sha256"] == expected_post and result["correctness"]["verified_bytes"] == PAYLOAD
        graph = reader.graph(run / "postexit" / f"rank_{rank}.topology.xml")
        comparison = reader.physical_comparison(graph, host)
        checks[f"rank{rank}/retained_physical_placements"] = bool(comparison) and all(r["placement_matches"] for r in comparison)
        bdf = "0000:09:00.0" if rank == 0 else "0000:36:00.0"
        gpu = [g for g in graph if g["kind"] == "gpu" and g["pci"][-1]["busid"] == bdf]
        checks[f"rank{rank}/selected_device"] = result["actual_gpu_bdf"] == bdf and len(gpu) == 1 and gpu[0]["attributes"].get("rank") == str(rank) and gpu[0]["attributes"].get("dev") == str(rank)
        log = (run / "postexit" / f"rank_{rank}.nccl.{pid}.log").read_text()
        own = [line for line in log.splitlines() if re.search(rf":{pid}:\d+\s+\[{rank}\]", line)]
        loading = [line for line in own if "Loading topology file " + process_spec["env"]["NCCL_TOPO_FILE"] in line]
        channels = [line for line in own if "Channel" in line and re.search(r"via (P2P|SHM|NET)", line)]
        modes = sorted(set(re.findall(r"via (P2P|SHM|NET)", "\n".join(channels))))
        checks[f"rank{rank}/input_loading_owned_log"] = bool(loading) and not any("Loading topology mapping file" in line for line in own)
        checks[f"rank{rank}/actual_local_channel"] = bool(channels) and any(re.search(rf"rank {rank} nRanks 2 nNodes 1 localRanks 2 localRank {rank}\b", line) for line in own)
        libs = load(run / "postexit" / f"rank_{rank}.libraries.json")
        checks[f"rank{rank}/loaded_library_fingerprints"] = libs["pid"] == pid and bool(libs["libraries"]) and all("sha256" in x for x in libs["libraries"])
        coverage.append({"rank": rank, "gpu_bdf": bdf, "physical_comparison": comparison,
                         "graph_ref": {"path": str(run / "postexit" / f"rank_{rank}.topology.xml"), "sha256": sha(run / "postexit" / f"rank_{rank}.topology.xml")},
                         "graph_only_hca_names": sorted({g["attributes"]["name"] for g in graph if g["kind"] == "net"})})
        transports.append({"rank": rank, "actual_channel_modes": modes, "channel_lines": channels,
                           "scope": "owned node27 two-rank communicator only"})
    passed = all(checks.values())
    return {"status": "passed" if passed else "failed", "checks": checks,
            "failed": [k for k, v in checks.items() if not v], "coverage": coverage, "transport": transports,
            "runtime_accepted": passed, "runtime_scope": "node27 GPU0/1 local communicator only",
            "observed_resource_ids": ["node27/pci/0000:09:00.0", "node27/pci/0000:36:00.0"] if passed else [],
            "transport_validated_hca_ports": [], "whole_machine_runtime_accepted": False,
            "nic_gdr_accepted": False, "performance_ready": False,
            "protected_scope": "node27 full metadata/index/catalog and two keepers/parked engine; no new source weight reread or node29 postflight",
            "raw_ref": {"path": str(run / "summary.json"), "sha256": sha(run / "summary.json")},
            "gpu_operations": 0, "remote_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = review(args.run.resolve())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (args.out / "review_source.py").write_bytes(Path(__file__).read_bytes())
    refs = [Path(__file__).resolve(), args.out / "summary.json", args.out / "review_source.py",
            args.run / "summary.json", args.run / "protocol.json",
            ROOT / "codex_coding/src/v0.1.23.3/task2/topology_alignment/audit/review.py"]
    (args.out / "manifest.json").write_text(json.dumps({"source_refs": [
        {"path": str(p.resolve()), "sha256": sha(p)} for p in refs],
        "gpu_operations": 0, "remote_operations": 0}, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()),
                      "total": len(result["checks"]), "failed": result["failed"]}))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
