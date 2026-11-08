"""Independently recompute saved contention runs; no network or GPU access."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(path):
    summary_path = path if path.is_file() else path / "summary.json"
    summary = read(summary_path)
    directory = Path(summary.get("source_run", summary["out"])).resolve()
    protocol = read(directory / "protocol.json")
    ranks = [read(directory / f"rank_{rank}.json") for rank in range(4)]
    before, after = [read(directory / f"inventory_{part}.json") for part in ("before", "after")]
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    check("run_passed", summary["status"] == "passed")
    manifest_path = summary_path.parent / "manifest.json"
    if summary.get("source_run") and manifest_path.exists():
        manifest = read(manifest_path)
        check("derived_manifest_source_files", all(sha(Path(p)) == h
              for p, h in manifest["source_files_sha256"].items()))
        check("derived_manifest_output_files", all(sha(Path(p)) == h
              for p, h in manifest["derived_files_sha256"].items()))
        source_summary = read(directory / "summary.json")
        check("derived_raw_flow_observations_unchanged", [r["flows"] for r in summary["observations"]]
              == [r["flows"] for r in source_summary["observations"]])
    check("all_raw_ranks_passed", all(r["status"] == "passed" for r in ranks))
    check("input_hashes", all(sha(Path(p)) == h for p, h in summary["input_sha256"].items()))
    check("frozen_code_preserved_during_run", summary["code_sha256"] == summary["code_sha256_after"])
    catalog_fields = ("worker", "epoch", "session", "transactions", "transaction_phases", "used_bytes",
                      "hbm_allocated_bytes", "hbm_reserved_bytes", "staging_bytes", "replicas")
    check("raw_keeper_catalog_and_allocation_preserved", all(
        {k: a.get(k) for k in catalog_fields} == {k: b.get(k) for k in catalog_fields}
        for a, b in zip(before["workers"], after["workers"])))
    check("all_four_worker_ids", [w["worker"] for w in before["workers"]] == list(range(4))
          and [w["worker"] for w in after["workers"]] == list(range(4)))
    for node, initial in before["nodes"].items():
        final = after["nodes"][node]
        stable_fields = ("spec", "spec_sha256", "registered")
        check(f"{node}_protected_process_identity", all(p["passed"] for p in final["protected_processes"])
              and [{k: p[k] for k in stable_fields} for p in initial["protected_processes"]]
              == [{k: p[k] for k in stable_fields} for p in final["protected_processes"]])
        check(f"{node}_parked_commands", initial["parked_commands"] == final["parked_commands"])
    legacy_after = {r["endpoint"]: r for r in after["protected_legacy_endpoints"]}
    check("accessible_legacy_catalogs_preserved", all(
        legacy_after.get(r["endpoint"], {}).get("accessible")
        and legacy_after[r["endpoint"]]["signature"] == r["signature"]
        for r in before["protected_legacy_endpoints"] if r["accessible"]))
    headroom = []
    for initial, final in zip(before["workers"], after["workers"]):
        delta = initial["headroom_bytes"]["hbm"] - final["headroom_bytes"]["hbm"]
        headroom.append({"worker": initial["worker"], "free_hbm_loss_bytes": delta})
        check(f"worker{initial['worker']}_physical_reclaimed", delta <= 128 << 20)
    for node, states in summary["cleanup"]["process_states"].items():
        for state in states:
            actual, registered = state["actual"], state["registered"]
            spec = next(p for p in summary["processes"] if p["spec"] == registered["spec"])
            check(f"{node}_{registered['pid']}_owned_exit", spec["start_reply"] == registered
                  and actual["pid"] == registered["pid"] and not actual["live"]
                  and actual.get("start_time") in (None, registered["start_time"]))
    binding = {r["rank"]: r for r in summary["binding"]}
    modes = []
    for rank in range(4):
        spec = read(directory / f"rank_{rank}.process.json")
        row = binding[rank]
        channels = row.get("all_channel_transport_lines", row["transport_evidence"])
        affinity_masks = sorted(set(m.group(1) for line in row.get("affinity_lines", [])
                                    if (m := re.search(r" to ([0-9a-f]+)", line))))
        mode = ("GDR" if channels and all("GDRDMA" in line for line in channels)
                else "non-GDR" if channels and all("GDRDMA" not in line for line in channels) else "unknown_or_mixed")
        modes.append({"rank": rank, "requested_hca": spec["env"]["NCCL_IB_HCA"],
                      "actual_ports": row["actual_hca_ports"], "channels": len(channels),
                      "gdr_channels": sum("GDRDMA" in line for line in channels),
                      "transport_mode": mode, "affinity_masks": affinity_masks,
                      "gdr_decision_lines": row.get("gdr_decision_lines", []),
                      "affinity_lines": row.get("affinity_lines", []),
                      "cpu_affinity_override": spec.get("diagnosis_control", {}).get("cpu_affinity_override"),
                      "gdr_level_env": spec["env"].get("NCCL_NET_GDR_LEVEL")})
        check(f"rank{rank}_bound_requested_hca", len(row["actual_hca_ports"]) == 1
              and row["actual_hca_ports"][0]["hca"] == spec["env"]["NCCL_IB_HCA"])
        check(f"rank{rank}_core_zero", spec["core_dump_limit"] == 0 and ranks[rank]["core_dump_limit"] == [0, 0])
        if summary.get("diagnosis_control", {}).get("flow_b_gdr_level_override") == "LOC":
            is_b = rank in (1, 3)
            check(f"rank{rank}_B_only_LOC_env", spec["env"].get("NCCL_NET_GDR_LEVEL") == ("LOC" if is_b else None))
            check(f"rank{rank}_actual_intervention_mode", mode == ("non-GDR" if is_b else "GDR"))
            check(f"rank{rank}_GPU_specific_decision", any(
                ("GPU Direct RDMA Disabled for GPU" if is_b else "GPU Direct RDMA Enabled for GPU") in line
                for line in row.get("gdr_decision_lines", [])))
    observations = []
    saved = {r["case_id"]: r for r in summary["observations"]}
    for case in protocol["cases"]:
        if case["kind"] != "network":
            continue
        rows = [c["flow"] for r in ranks for c in r["cases"]
                if c["case_id"] == case["case_id"] and c["flow"]]
        flows = sorted((r for r in rows if r["role"] == "target"), key=lambda f: f["flow_id"])
        check(case["case_id"] + "_one_receiver_clock", len({r["clock_domain"] for r in flows}) == 1)
        check(case["case_id"] + "_flow_set", [f["flow_id"] for f in flows] == case["flow_ids"])
        start = min(r["receiver_start_ns"] for r in flows)
        end = max(r["receiver_end_ns"] for r in flows)
        details, grouped = [], {}
        for flow in flows:
            fid = flow["flow_id"]
            source = next(r for r in rows if r["role"] == "source" and r["flow_id"] == fid)
            check(f"{case['case_id']}_flow{fid}_bytes", flow["bytes"] == case["bytes_per_flow"]
                  == flow["buffer_bytes"] * flow["iterations"] and len(flow["chunk_intervals"]) == flow["iterations"])
            check(f"{case['case_id']}_flow{fid}_final_sha", flow["correctness"]["passed"]
                  and source["correctness"]["passed"] and flow["correctness"]["sha256"]
                  == source["correctness"]["sha256"] == flow["correctness"]["expected_sha256"])
            check(f"{case['case_id']}_flow{fid}_reset_and_warmup", flow["receiver_reset_before_timing"]
                  and flow["warmup"]["excluded_from_timing"] and flow["warmup"]["end_ns"] < flow["receiver_start_ns"])
            duration = flow["receiver_end_ns"] - flow["receiver_start_ns"]
            details.append({"flow_id": fid, "bytes": flow["bytes"], "elapsed_ns": duration,
                            "GBps": flow["bytes"] / duration,
                            "warmup_ns": flow["warmup"]["end_ns"] - flow["warmup"]["start_ns"],
                            "final_sha256": flow["correctness"]["sha256"]})
            selected_ports = binding[flow["dst"]]["actual_hca_ports"]
            check(f"{case['case_id']}_flow{fid}_receiver_port_known", len(selected_ports) == 1)
            if len(selected_ports) == 1:
                port = selected_ports[0]
                grouped.setdefault((port["hca"], port["port"]), []).append(flow)
        ports = []
        for (hca, port), members in sorted(grouped.items()):
            first = min((f["nic_snapshot_before"] for f in members), key=lambda s: s["monotonic_ns"])
            last = max((f["nic_snapshot_after"] for f in members), key=lambda s: s["monotonic_ns"])
            key = f"/sys/class/infiniband/{hca}/ports/{port}/counters/port_rcv_data"
            a, b = first["values"][key], last["values"][key]
            check(f"{case['case_id']}_{hca}_counter_units", a["unit_bytes"] == b["unit_bytes"] == 4
                  and a["raw"] <= b["raw"] and first["clock_domain"] == last["clock_domain"])
            payload = sum(f["bytes"] for f in members)
            received = (b["raw"] - a["raw"]) * 4
            ports.append({"hca": hca, "port": port, "flow_ids": [f["flow_id"] for f in members],
                          "payload_bytes": payload, "receive_bytes": received,
                          "counter_window_ns": last["monotonic_ns"] - first["monotonic_ns"]})
            check(f"{case['case_id']}_{hca}_payload_covered", received >= payload)
        check(case["case_id"] + "_saved_port_count_matches", bool(ports) and sum(p["receive_bytes"] for p in ports)
              == saved[case["case_id"]]["actual_nic_payload_evidence"].get("actual_counter_delta_bytes"))
        observations.append({"case_id": case["case_id"], "flows": details, "ports": ports,
                             "makespan_ns": end - start, "aggregate_GBps": sum(r["bytes"] for r in flows) / (end - start),
                             "start_skew_ns": max(r["receiver_start_ns"] for r in flows) - start,
                             "overlap_fraction": (max(0, min(r["receiver_end_ns"] for r in flows)
                                - max(r["receiver_start_ns"] for r in flows)) / min(d["elapsed_ns"] for d in details))})
    by_case = {r["case_id"]: r for r in observations}
    singles = [by_case[f"network_single_{letter}"]["flows"][0] for letter in "ab"]
    pair = by_case["network_pair"]
    pair["independent_expected_ns"] = max(f["elapsed_ns"] for f in singles)
    pair["independence_ratio"] = pair["independent_expected_ns"] / pair["makespan_ns"]
    pair["slowdowns"] = [f["elapsed_ns"] / s["elapsed_ns"] for f, s in zip(pair["flows"], singles)]
    pair["skew_fraction_shorter_single"] = pair["start_skew_ns"] / min(f["elapsed_ns"] for f in singles)
    return {"run": str(directory), "summary_path": str(summary_path),
            "summary_sha256": sha(summary_path),
            "status": "passed" if all(c["passed"] for c in checks) else "failed",
            "checks": checks, "observations": observations, "binding_modes": modes,
            "headroom": headroom, "gpu_samples_added": 0,
            "limits": ["Final SHA covers one reused buffer, not distinct payloads for every iteration.",
                       "Host completed-call overlap does not measure GPU kernel occupancy.",
                       "Main PID or RCCL affinity lines do not constitute an all-thread runtime trace.",
                       "NIC counts include all users and protocol overhead; idle baseline does not exclude burst traffic."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--labels", nargs="+")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results = [review(p.resolve()) for p in args.run]
    if args.labels and len(args.labels) != len(results):
        parser.error("--labels must name each --run in order")
    for label, result in zip(args.labels or [p.name for p in args.run], results):
        result["label"] = label
    payload = {"status": "passed" if all(r["status"] == "passed" for r in results) else "failed", "runs": results}
    payload["contrast_schema"] = {
        "G1_vs_G0": {"question": "B uses a distinct HCA/PCIe/NUMA route and may change transport mode",
                     "supported_claim": "path-selection package changes the shared-path slowdown",
                     "unique_NIC_causality": False},
        "G2_vs_G1": {"question": "same split HCA paths; only B GDR threshold changes",
                     "required": "actual B transport changes, A mode and observed affinity/channel configuration remain stable",
                     "supported_claim": "effect of B GDR/host-backed mode under split paths"},
        "G2_vs_G0": {"question": "same A GDR/B non-GDR modes; B HCA/PCIe/NUMA route changes",
                     "required": "both B ends are actually non-GDR",
                     "supported_claim": "path-splitting effect does not require B GDR enablement",
                     "unique_NIC_causality": False}}
    labeled = {r["label"]: r for r in results}
    comparisons = []
    for baseline, intervention in (("G0", "G1"), ("G1", "G2"), ("G0", "G2")):
        if baseline not in labeled or intervention not in labeled:
            continue
        a, b = labeled[baseline], labeled[intervention]
        ac, bc = [{r["case_id"]: r for r in item["observations"]} for item in (a, b)]
        comparisons.append({"baseline": baseline, "intervention": intervention,
            "pair_makespan_intervention_over_baseline": bc["network_pair"]["makespan_ns"] / ac["network_pair"]["makespan_ns"],
            "pair_independence_ratios": [ac["network_pair"]["independence_ratio"], bc["network_pair"]["independence_ratio"]],
            "single_makespan_intervention_over_baseline": {letter: bc[f"network_single_{letter}"]["makespan_ns"]
                / ac[f"network_single_{letter}"]["makespan_ns"] for letter in "ab"},
            "actual_transport_modes": [[m["transport_mode"] for m in r["binding_modes"]] for r in (a, b)],
            "actual_hca_ports": [[m["actual_ports"] for m in r["binding_modes"]] for r in (a, b)],
            "observed_affinity_masks": [[m["affinity_masks"] for m in r["binding_modes"]] for r in (a, b)],
            "unique_hardware_component_identified": False})
    payload["comparisons"] = comparisons
    if "G1" in labeled and "G2" in labeled:
        a, b = labeled["G1"], labeled["G2"]
        controls = []
        for rank in range(4):
            initial, final = [read(Path(item["run"]) / f"rank_{rank}.process.json") for item in (a, b)]
            ignored = {"NCCL_DEBUG_FILE", "NCCL_NET_GDR_LEVEL"}
            controls.append({"name": f"rank{rank}_env_except_B_GDR_and_run_log_same",
                "passed": {k: v for k, v in initial["env"].items() if k not in ignored}
                    == {k: v for k, v in final["env"].items() if k not in ignored}})
            initial_mode, final_mode = a["binding_modes"][rank], b["binding_modes"][rank]
            controls.append({"name": f"rank{rank}_actual_port_affinity_and_connections_same",
                "passed": all(initial_mode[k] == final_mode[k] for k in ("actual_ports", "affinity_masks", "channels"))})
        initial_protocol, final_protocol = [read(Path(item["run"]) / "protocol.json") for item in (a, b)]
        controls.append({"name": "matched_protocol_workers_cases_and_resource_limits",
            "passed": all(initial_protocol[k] == final_protocol[k] for k in ("workers", "cases", "resource_limits"))})
        payload["G1_G2_control_checks"] = controls
        if not all(c["passed"] for c in controls):
            payload["status"] = "failed"
    (args.out / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": payload["status"], "checks": sum(len(r["checks"]) for r in results)
                      + len(payload.get("G1_G2_control_checks", [])),
                      "out": str(args.out)}))
    if payload["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
