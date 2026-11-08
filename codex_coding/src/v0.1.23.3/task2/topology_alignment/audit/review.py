"""Audit saved topology probes using local files only; no Torch or network."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[6]


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def graph(path):
    rows = []

    def walk(element, cpu=None, pci=()):
        if element.tag == "cpu":
            cpu = dict(element.attrib)
        if element.tag == "pci":
            pci = (*pci, dict(element.attrib))
        if element.tag in ("gpu", "net"):
            rows.append({"kind": element.tag, "attributes": dict(element.attrib),
                         "cpu": cpu, "pci": list(pci)})
        for child in element:
            walk(child, cpu, pci)

    walk(ET.parse(path).getroot())
    return rows


def cpus(text):
    result = set()
    for part in text.split(","):
        low, _, high = part.partition("-")
        result.update(range(int(low), int(high or low) + 1))
    return result


def physical_comparison(nodes, host):
    devices = {}
    for row in host["drm"]:
        chain = row["pci_chain"]
        if chain:
            devices[chain[0]["bdf"]] = chain
    hcas = {r["name"]: r for r in host["hca"]}
    result = []
    for row in nodes:
        buses = [p["busid"].lower() for p in row["pci"] if p.get("busid")]
        if row["kind"] == "gpu":
            expected = devices.get(buses[-1] if buses else "")
            label = buses[-1] if buses else "GPU without BDF"
        else:
            label = row["attributes"].get("name")
            expected = hcas.get(label, {}).get("pci_chain")
        detail = {"kind": row["kind"], "label": label, "graph_bdf_chain": buses,
                  "graph_cpu": row["cpu"], "sysfs_present": bool(expected)}
        if expected:
            physical = expected[0]
            ancestors = [r["bdf"] for r in reversed(expected)]
            graph_cpu = row["cpu"] or {}
            affinity = graph_cpu.get("affinity")
            mask_cpus = None
            if affinity:
                mask = int(affinity.replace(",", ""), 16)
                mask_cpus = {bit for bit in range(mask.bit_length()) if mask & (1 << bit)}
            detail.update(sysfs_bdf_chain=ancestors, sysfs_numa=physical["numa_node"],
                sysfs_local_cpus=physical["local_cpulist"], graph_affinity_cpus=sorted(mask_cpus) if mask_cpus is not None else None,
                endpoint_bdf_matches=bool(buses) and buses[-1] == physical["bdf"],
                ancestry_members_match=bool(buses) and all(bus in ancestors for bus in buses),
                numa_matches=graph_cpu.get("numaid") == physical["numa_node"],
                affinity_matches=mask_cpus == cpus(physical["local_cpulist"]))
            # RCCL may suppress transparent bridge levels; preserve both chains.
            detail["exact_chain_matches"] = buses == ancestors
            detail["placement_matches"] = all(detail[k] for k in
                ("endpoint_bdf_matches", "ancestry_members_match", "numa_matches", "affinity_matches"))
        result.append(detail)
    return result


def review(path, provenance=None):
    path = Path(path).resolve()
    summary = read(path)
    raw = path.parent.parent if path.parent.name == "postexit" else path.parent
    original = read(raw / "summary.json")
    protocol = read(raw / "protocol.json")
    ranks = [read(raw / f"rank_{rank}.json") for rank in range(4)]
    checks = []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})

    check("postexit_passed", summary["status"] == "passed")
    check("source_summary_sha", digest(raw / "summary.json") == summary["source_summary_sha256"])
    check("all_raw_ranks_passed", all(r["status"] == "passed" for r in ranks))
    check("input_sha", all(digest(p) == sha for p, sha in original["input_sha256"].items()))
    check("code_snapshot_unchanged_during_run", original["code_sha256"] == original["code_sha256_after"])
    before, after = [read(raw / f"inventory_{part}.json") for part in ("before", "after")]
    fields = ("worker", "epoch", "session", "transactions", "transaction_phases", "used_bytes",
              "hbm_allocated_bytes", "hbm_reserved_bytes", "staging_bytes", "replicas")
    check("four_keeper_catalogs_and_allocations", len(before["workers"]) == len(after["workers"]) == 4
          and all({k: a.get(k) for k in fields} == {k: b.get(k) for k in fields}
                  for a, b in zip(before["workers"], after["workers"])))
    for initial, final in zip(before["workers"], after["workers"]):
        check(f"worker{initial['worker']}_free_hbm", initial["headroom_bytes"]["hbm"]
              - final["headroom_bytes"]["hbm"] <= 128 << 20)
    for node, initial in before["nodes"].items():
        final = after["nodes"][node]
        check(node + "_parked_commands", initial["parked_commands"] == final["parked_commands"])
        stable = ("spec", "spec_sha256", "registered")
        check(node + "_protected_processes", all(p["passed"] for p in final["protected_processes"])
              and [{k: p[k] for k in stable} for p in initial["protected_processes"]]
              == [{k: p[k] for k in stable} for p in final["protected_processes"]])
    legacy = {r["endpoint"]: r for r in after["protected_legacy_endpoints"]}
    check("accessible_legacy_preserved", all(legacy.get(r["endpoint"], {}).get("accessible")
          and legacy[r["endpoint"]]["signature"] == r["signature"]
          for r in before["protected_legacy_endpoints"] if r["accessible"]))
    check("raw_cleanup_passed", original["cleanup"]["passed"])
    for node, states in original["cleanup"]["process_states"].items():
        for state in states:
            registered, actual = state["registered"], state["actual"]
            started = next(p["start_reply"] for p in original["processes"] if p["spec"] == registered["spec"])
            check(f"{node}_pid{registered['pid']}_owned_exit", started == registered
                  and actual["pid"] == registered["pid"] and not actual["live"]
                  and actual.get("start_time") in (None, registered["start_time"]))
    for item in summary["postexit"]["artifacts"]:
        check(f"rank{item['rank']}_{Path(item['path']).name}_artifact_exists", item["exists"])
        if item["exists"]:
            check(Path(item["path"]).name + "_four_sha", digest(item["path"]) == digest(item["saved_path"])
                  == item["sha256"] == item["local_sha256"] == item["saved_sha256"])
    environments = {r["rank"]: r for r in summary["environment"]}
    modes, graphs, libraries = [], [], []
    for rank in range(4):
        entry = next(p for p in original["processes"] if p["rank"] == rank)
        spec = read(entry["spec"])
        env = environments[rank]
        actual_env = env["effective_environment"]
        expected_topo = summary["topology_control"]["xml_requested"]
        check(f"rank{rank}_pre_torch_effective_environment", env["pid"] == entry["start_reply"]["pid"]
              and env["pre_torch_import"] and not env["torch_imported"]
              and actual_env.get("NCCL_TOPO_FILE") == expected_topo)
        check(f"rank{rank}_core_zero", ranks[rank]["core_dump_limit"] == [0, 0])
        loaded = read(path.parent / f"rank_{rank}.libraries.json")
        relevant = [r for r in loaded["libraries"] if any(name in r["path"].lower()
                    for name in ("librccl.so", "libnccl.so", "librccl-net-shca"))]
        check(f"rank{rank}_loaded_rccl_identity", loaded["pid"] == env["pid"]
              and bool(relevant) and all(r.get("sha256") for r in relevant))
        libraries.append({"rank": rank, "files": relevant})
        log_path = path.parent / f"rank_{rank}.nccl.{env['pid']}.log"
        log = log_path.read_text()
        lines = log.splitlines()
        plugin_paths = sorted(set(re.findall(r"Plugin Path\s*:\s*(\S+)", log)))
        if provenance:
            container = read(provenance / entry["node"] / "container.json")
            plugin_files = [r for r in container["files"] if r["path"] in plugin_paths]
            check(f"rank{rank}_plugin_logged_path_fingerprinted", len(plugin_paths) == len(plugin_files) == 1
                  and bool(plugin_files[0].get("sha256")))
            libraries[-1]["plugin_static_snapshot"] = plugin_files
            libraries[-1]["plugin_snapshot_scope"] = "logged loaded path matched to read-only provenance snapshot; plugin may unload before final maps"
        transport = [line for line in lines if "via NET/" in line]
        ports = sorted(set(re.findall(r"Using \[\d+\]([^ :]+):(\d+)/IB", log)))
        check(f"rank{rank}_owned_log", any(f":{env['pid']}:" in line and "NET/IB : Using" in line for line in lines))
        check(f"rank{rank}_actual_requested_port", ports == [(spec["env"]["NCCL_IB_HCA"], "1")])
        mode = "GDR" if transport and all("GDRDMA" in s for s in transport) else (
            "non-GDR" if transport and all("GDRDMA" not in s for s in transport) else "unknown_or_mixed")
        check(f"rank{rank}_transport_known", mode != "unknown_or_mixed")
        gpu_decision = [s for s in lines if "GPU Direct RDMA" in s and "for GPU" in s]
        dump = path.parent / f"rank_{rank}.topology.xml"
        check(f"rank{rank}_actual_dump", dump.exists())
        graph_gdr_zero = False
        if dump.exists():
            nodes = graph(dump)
            graph_gdr_zero = bool(nodes) and all(n["attributes"].get("gdr") == "0" for n in nodes)
            item = {"rank": rank, "sha256": digest(dump), "nodes": nodes}
            if provenance:
                host = read(provenance / entry["node"] / "host.json")
                item["physical_comparison"] = physical_comparison(nodes, host)
                item["observed_node_placement_matches"] = all(
                    r.get("placement_matches", False) for r in item["physical_comparison"])
            graphs.append(item)
        capability_lines = [s for s in lines if "GPU Direct RDMA" in s and "for HCA" in s]
        capability_skip = graph_gdr_zero and mode == "non-GDR" and any("Disabled" in s for s in capability_lines)
        check(f"rank{rank}_GDR_decision_or_capability_skip", bool(gpu_decision) or capability_skip)
        modes.append({"rank": rank, "actual_ports": ports, "mode": mode,
                      "transport_connection_count": len(transport), "gdr_decisions": gpu_decision,
                      "hca_capability_lines": capability_lines, "graph_all_gdr_zero": graph_gdr_zero,
                      "affinity_lines": [s for s in lines if "affinity" in s.lower()],
                      "topology_lines": [s for s in lines if "topo" in s.lower()],
                      "effective_environment": actual_env, "initial_cpu_affinity": env["cpu_affinity"]})
    observations = []
    for case in protocol["cases"]:
        rows = [c["flow"] for rank in ranks for c in rank["cases"] if c["case_id"] == case["case_id"] and c["flow"]]
        targets = sorted((r for r in rows if r["role"] == "target"), key=lambda r: r["flow_id"])
        check(case["case_id"] + "_receiver_clock", len({f["clock_domain"] for f in targets}) == 1)
        check(case["case_id"] + "_flow_ids", [f["flow_id"] for f in targets] == case["flow_ids"])
        details, groups = [], {}
        for flow in targets:
            source = next(f for f in rows if f["role"] == "source" and f["flow_id"] == flow["flow_id"])
            check(f"{case['case_id']}_flow{flow['flow_id']}_bytes", flow["bytes"] == case["bytes_per_flow"]
                  == flow["buffer_bytes"] * flow["iterations"] and len(flow["chunk_intervals"]) == flow["iterations"])
            check(f"{case['case_id']}_flow{flow['flow_id']}_sha", flow["correctness"]["passed"]
                  and source["correctness"]["passed"] and flow["correctness"]["sha256"]
                  == source["correctness"]["sha256"] == flow["correctness"]["expected_sha256"])
            check(f"{case['case_id']}_flow{flow['flow_id']}_warmup_reset", flow["receiver_reset_before_timing"]
                  and flow["warmup"]["excluded_from_timing"] and flow["warmup"]["end_ns"] < flow["receiver_start_ns"])
            elapsed = flow["receiver_end_ns"] - flow["receiver_start_ns"]
            details.append({"flow_id": flow["flow_id"], "elapsed_ns": elapsed, "GBps": flow["bytes"] / elapsed})
            for port in modes[flow["dst"]]["actual_ports"]:
                groups.setdefault(port, []).append(flow)
        counters = []
        for (hca, port), members in groups.items():
            first = min((f["nic_snapshot_before"] for f in members), key=lambda s: s["monotonic_ns"])
            last = max((f["nic_snapshot_after"] for f in members), key=lambda s: s["monotonic_ns"])
            key = f"/sys/class/infiniband/{hca}/ports/{port}/counters/port_rcv_data"
            a, b = first["values"][key], last["values"][key]
            payload = sum(f["bytes"] for f in members)
            delta = (b["raw"] - a["raw"]) * 4
            check(f"{case['case_id']}_{hca}_counter", a["unit_bytes"] == b["unit_bytes"] == 4
                  and first["clock_domain"] == last["clock_domain"] and delta >= payload)
            counters.append({"hca": hca, "port": port, "payload_bytes": payload, "receive_bytes": delta})
        start = min(f["receiver_start_ns"] for f in targets)
        end = max(f["receiver_end_ns"] for f in targets)
        observations.append({"case_id": case["case_id"], "makespan_ns": end - start, "flows": details,
            "start_skew_ns": max(f["receiver_start_ns"] for f in targets) - start, "ports": counters,
            "overlap_fraction": max(0, min(f["receiver_end_ns"] for f in targets)
                 - max(f["receiver_start_ns"] for f in targets)) / min(f["elapsed_ns"] for f in details)})
    if not protocol.get("startup_only"):
        by_case = {r["case_id"]: r for r in observations}
        singles = [by_case[f"network_single_{s}"]["flows"][0] for s in "ab"]
        pair = by_case["network_pair"]
        pair.update(independent_expected_ns=max(s["elapsed_ns"] for s in singles),
            independence_ratio=max(s["elapsed_ns"] for s in singles) / pair["makespan_ns"],
            skew_fraction_shorter_single=pair["start_skew_ns"] / min(s["elapsed_ns"] for s in singles))
        check("pair_overlap_gate", pair["overlap_fraction"] >= .8)
        check("pair_start_skew_gate", pair["skew_fraction_shorter_single"] <= .05)
        check("one_observation_per_config", all(r["samples"] == 1 for r in summary["observations"]))
    return {"summary": str(path), "summary_sha256": digest(path), "raw": str(raw),
            "status": "passed" if all(c["passed"] for c in checks) else "failed", "checks": checks,
            "stage": summary["topology_control"]["stage"], "topology_mode": summary["topology_control"]["mode"],
            "observations": observations, "modes": modes, "graphs": graphs, "libraries": libraries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--failed-run", action="append", type=Path, default=[])
    parser.add_argument("--runtime-evidence", action="append", type=Path, default=[])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results = [review(p, args.provenance) for p in args.run]
    controls = []
    provenance_checks = []
    if args.provenance:
        manifest = read(args.provenance / "manifest.json")
        provenance_checks = [{"name": "provenance_" + r["path"], "passed": digest(ROOT / r["path"]) == r["sha256"]}
                             for r in manifest["files"]]
    failures = []
    for failed in args.failed_run:
        source = failed / "summary.json"
        saved = read(source)
        failures.append({"run": str(failed), "summary_sha256": digest(source), "status": saved["status"],
                         "measurement_status": saved["measurement_status"], "error": saved.get("error"),
                         "classification": "resource_admission_failed_before_GPU_launch" if not saved["processes"]
                         and not saved["observations"] else "runtime_failure_or_incomplete_evidence"})
    initial_by_stage = {}
    for result in results:
        initial = initial_by_stage.setdefault(result["stage"], result)
        if result is not initial:
            for rank in range(4):
                excluded = {"NCCL_TOPO_FILE", "NCCL_DEBUG_FILE", "NCCL_TOPO_DUMP_FILE"}
                a, b = [r["modes"][rank] for r in (initial, result)]
                controls.append({"name": f"rank{rank}_effective_environment_only_topology_and_evidence_paths",
                    "passed": {k: v for k, v in a["effective_environment"].items() if k not in excluded}
                    == {k: v for k, v in b["effective_environment"].items() if k not in excluded}})
                controls.append({"name": f"rank{rank}_initial_allowed_cpus_and_ports_same",
                    "passed": a["initial_cpu_affinity"] == b["initial_cpu_affinity"] and a["actual_ports"] == b["actual_ports"]})
                controls.append({"name": f"rank{rank}_actual_loaded_rccl_plugin_same",
                    "passed": initial["libraries"][rank] == result["libraries"][rank]})
            protocols = [read(Path(r["raw"]) / "protocol.json") for r in (initial, result)]
            controls.append({"name": "protocol_workers_cases_resources_same", "passed": all(
                protocols[0][k] == protocols[1][k] for k in ("workers", "cases", "resource_limits"))})
    runtime_evidence = [{"path": str(p), "sha256": digest(p)} for p in args.runtime_evidence]
    if args.provenance:
        after_path = args.provenance / "runtime_after.json"
        if after_path.exists():
            runtime_evidence.append({"path": str(after_path), "sha256": digest(after_path)})
            for node, row in read(after_path)["nodes"].items():
                files = read(args.provenance / node / "container.json")["files"]
                for line in row["data"].splitlines():
                    sha, filename = line.split(maxsplit=1)
                    before_file = next(r for r in files if r["path"] == filename)
                    provenance_checks.append({"name": node + "_binary_xml_before_after_" + filename,
                        "passed": row["returncode"] == 0 and before_file["sha256"] == sha})
    all_checks = [c for r in results for c in r["checks"]]
    aggregate_hbm = all(c["passed"] for c in all_checks if c["name"].endswith("_free_hbm"))
    own_cleanup = all(c["passed"] for c in all_checks if c["name"].endswith("_owned_exit"))
    factual_checks = [c for c in all_checks if c["name"] not in ("postexit_passed", "raw_cleanup_passed")
                      and not c["name"].endswith("_free_hbm")]
    result = {"status": "passed" if all(r["status"] == "passed" for r in results)
              and all(c["passed"] for c in controls + provenance_checks) else "failed", "runs": results,
              "control_checks": controls, "provenance_checks": provenance_checks, "preserved_failures": failures,
              "runtime_evidence": runtime_evidence,
              "facts_verified": all(c["passed"] for c in factual_checks + controls + provenance_checks),
              "own_process_cleanup": own_cleanup, "aggregate_HBM_gate": aggregate_hbm,
              "performance_comparison_ready": False,
              "performance_readiness_reason": "Shared GPU activity and changing free HBM prevent an isolated performance comparison; empty-seed formal run was withheld.",
              "gpu_operations": 0, "network_operations": 0}
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "checks": sum(len(r["checks"]) for r in results) + len(controls) + len(provenance_checks),
                      "out": str(args.out)}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
