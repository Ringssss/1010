"""Review an owned XML startup from local frozen artifacts only."""

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(run, provenance):
    raw = load(run / "summary.json")
    checks = {"no_performance_samples": raw.get("measurement_status") != "performance_sample"}
    if not raw.get("processes"):
        checks["failed_before_communication_launch"] = raw["status"] == "failed" and raw["measurement_status"] == "not_measured"
        return {"status": "verified_prelaunch_failure" if all(checks.values()) else "failed", "checks": checks,
                "runtime_accepted": False, "performance_ready": False, "communication_processes_launched": 0,
                "raw_error": raw.get("error")}
    if raw.get("status") == "failed" and not (run / "postexit/summary.json").exists():
        checks["raw_failure_preserved"] = True
        checks["no_completed_observations"] = not raw.get("observations")
        checks["registered_four_processes"] = len(raw["processes"]) == 4
        checks["cleanup_passed"] = raw.get("cleanup", {}).get("passed") is True
        checks["original_128MiB_gate"] = raw.get("cleanup", {}).get("physical_hbm_reclamation", {}).get("tolerance_bytes") == 128 * 2**20
        checks["keeper_preserved"] = raw.get("before_after_preservation", {}).get("passed") is True
        checks["code_unchanged_during_run"] = raw.get("code_sha256") == raw.get("code_sha256_after")
        exits = []
        for node, states in raw.get("cleanup", {}).get("process_states", {}).items():
            for state in states:
                registered, actual = state["registered"], state["actual"]
                started = next(p["start_reply"] for p in raw["processes"] if p["spec"] == registered["spec"])
                exits.append(started == registered and actual["pid"] == registered["pid"] and not actual["live"] and actual.get("start_time") in (None, registered["start_time"]))
        checks["all_four_exact_processes_exited"] = len(exits) == 4 and all(exits)
        environment = []
        for entry in raw["processes"]:
            rank = entry["rank"]
            spec = load(entry["spec"])
            path = run / f"rank_{rank}.environment.json"
            row = load(path)
            checks[f"rank{rank}/preimport_input"] = row["pid"] == entry["start_reply"]["pid"] and row["pre_torch_import"] and not row["torch_imported"] and row["effective_environment"]["NCCL_TOPO_FILE"] == spec["env"]["NCCL_TOPO_FILE"]
            rank_path = run / f"rank_{rank}.json"
            environment.append({"rank": rank, "preimport_environment_sha256": sha(path),
                                "rank_result_exists": rank_path.exists(),
                                "rank_result": load(rank_path) if rank_path.exists() else None})
        return {"status": "verified_failed_startup" if all(checks.values()) else "failed", "checks": checks,
                "failed": [k for k,v in checks.items() if not v], "runtime_accepted": False, "performance_ready": False,
                "communication_processes_registered": 4, "completed_gpu_observations": 0,
                "environment": environment, "raw_error": raw.get("error"),
                "interpretation": "pre-Torch XML environment reached all ranks; incomplete rendezvous does not prove RCCL accepted or rejected XML"}
    summary = load(run / "postexit/summary.json")
    protocol = load(run / "protocol.json")
    checks.update(postexit_passed=summary["status"] == "passed",
                  raw_summary_sha=sha(run / "summary.json") == summary["source_summary_sha256"],
                  code_unchanged_during_run=raw["code_sha256"] == raw["code_sha256_after"],
                  startup_only=protocol["startup_only"] and protocol["samples_per_configuration"] == 0,
                  single_1MiB_case=len(protocol["cases"]) == 1 and protocol["cases"][0]["buffer_bytes"] == 2**20 and protocol["cases"][0]["iterations"] == 1,
                  cleanup_gate=raw["cleanup"]["passed"],
                  original_128MiB_gate=raw["cleanup"]["physical_hbm_reclamation"]["tolerance_bytes"] == 128 * 2**20,
                  keeper_preserved=raw["before_after_preservation"]["passed"],
                  inputs_unchanged=raw["input_preservation"]["passed"])
    graph_spec = importlib.util.spec_from_file_location("old_graph_reader", ROOT / "codex_coding/src/v0.1.23.3/task2/topology_alignment/audit/review.py")
    graph_reader = importlib.util.module_from_spec(graph_spec)
    graph_spec.loader.exec_module(graph_reader)
    coverage, transport = [], []
    for entry in raw["processes"]:
        rank, node = entry["rank"], entry["node"]
        spec = load(entry["spec"])
        result = load(run / f"rank_{rank}.json")
        environment = load(run / "postexit" / f"rank_{rank}.environment.json")
        pid = entry["start_reply"]["pid"]
        selected_gpu = rank % 2
        expected_bdf = "0000:09:00.0" if selected_gpu == 0 else "0000:36:00.0"
        checks[f"rank{rank}/content"] = result["status"] == "passed" and result["gpu"] == selected_gpu and result["pid"] == pid
        checks[f"rank{rank}/pernode_input"] = environment["effective_environment"]["NCCL_TOPO_FILE"] == summary["topology_control"]["xml_requested_by_node"][node] == spec["env"]["NCCL_TOPO_FILE"]
        checks[f"rank{rank}/preimport"] = environment["pre_torch_import"] and not environment["torch_imported"] and environment["pid"] == pid
        checks[f"rank{rank}/visibility"] = environment["effective_environment"]["HIP_VISIBLE_DEVICES"] == "0,1" and environment["effective_environment"]["CUDA_VISIBLE_DEVICES"] == "0,1"
        dump = run / "postexit" / f"rank_{rank}.topology.xml"
        graph = graph_reader.graph(dump)
        comparison = graph_reader.physical_comparison(graph, load(provenance / node / "host.json"))
        checks[f"rank{rank}/all_retained_graph_placements"] = all(r.get("placement_matches", False) for r in comparison)
        selected = [r for r in graph if r["kind"] == "gpu" and r["pci"][-1]["busid"] == expected_bdf]
        checks[f"rank{rank}/selected_gpu_bdf"] = len(selected) == 1
        checks[f"rank{rank}/selected_gpu_dev_rank"] = bool(selected) and selected[0]["attributes"].get("dev") == str(selected_gpu) and selected[0]["attributes"].get("rank") == str(0 if rank < 2 else 1)
        nets = [r for r in graph if r["kind"] == "net" and r["attributes"].get("name") == "shca_0"]
        checks[f"rank{rank}/selected_net_bdf_speed"] = len(nets) == 1 and nets[0]["pci"][-1]["busid"] == "0000:03:00.0" and nets[0]["attributes"].get("port") == "1" and nets[0]["attributes"].get("speed") == "400000"
        guid_snapshot = run.parent / "gdr_readonly_r01" / node / "snapshot.json"
        hca = next(r for r in load(guid_snapshot)["hca"] if r["name"] == "shca_0")
        expected_guid = int.from_bytes(bytes.fromhex(hca["node_guid"].replace(":", "")), "little")
        checks[f"rank{rank}/pernode_net_guid_dev"] = len(nets) == 1 and nets[0]["attributes"].get("dev") == "0" and int(nets[0]["attributes"].get("guid", "0"), 0) == expected_guid
        checks[f"rank{rank}/conservative_gdr"] = all(r["attributes"].get("gdr") == "0" for r in graph)
        log = (run / "postexit" / f"rank_{rank}.nccl.{pid}.log").read_text()
        lines = log.splitlines()
        connections = [line for line in lines if "via NET/" in line]
        checks[f"rank{rank}/all_64_nonGDR"] = len(connections) == 64 and all("GDRDMA" not in line for line in connections)
        checks[f"rank{rank}/owned_log"] = any(f":{pid}:" in line and "NET/IB : Using" in line for line in lines)
        checks[f"rank{rank}/requested_port"] = sorted(set(re.findall(r"Using \[\d+\]([^ :]+):(\d+)/IB", log))) == [("shca_0", "1")]
        checks[f"rank{rank}/input_loaded"] = any("Loading topology file " + spec["env"]["NCCL_TOPO_FILE"] in line for line in lines)
        coverage.append({"rank": rank, "node": node, "selected_gpu": selected_gpu, "bdf": expected_bdf,
                         "graph_rows": len(graph), "selected_net_attributes": [r["attributes"] for r in nets],
                         "node_guid_evidence": {"path": str(guid_snapshot), "sha256": sha(guid_snapshot), "node_guid": hca["node_guid"], "runtime_guid_byteorder": "little-endian ibv node_guid in deployed runtime"},
                         "physical_comparison": comparison})
        transport.append({"rank": rank, "mode": "non-GDR" if checks[f"rank{rank}/all_64_nonGDR"] else "unverified"})
    for artifact in summary["postexit"]["artifacts"]:
        checks[f"artifact/{Path(artifact['path']).name}"] = artifact["exists"] and sha(artifact["path"]) == sha(artifact["saved_path"]) == artifact["sha256"] == artifact["local_sha256"] == artifact["saved_sha256"]
    checks["four_selected_graphs"] = len(coverage) == 4
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "failed": [k for k, v in checks.items() if not v], "coverage": coverage, "transport": transport,
            "runtime_accepted": all(checks.values()), "performance_ready": False,
            "runtime_scope": "GPU0/1 and shca_0:1 on node27/node29 only; retained unused XML leaves are not runtime device certification",
            "gpu_operations": 0, "remote_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = review(args.run, args.provenance)
    (args.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()), "total": len(result["checks"]), "failed": result.get("failed", [])}))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
