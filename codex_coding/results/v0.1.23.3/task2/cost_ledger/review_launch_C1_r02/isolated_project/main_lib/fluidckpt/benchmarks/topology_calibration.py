"""CPU-only contracts for disposable topology probes, not a capacity optimizer."""
import hashlib
import json
import math
import re

from fluidckpt.cluster.path_catalog import topology_id

SCHEMA = "topology_calibration.v1"
BUFFER_BYTES = 256 << 20
ITERATIONS = 16
TOTAL_BYTES = BUFFER_BYTES * ITERATIONS


def protocol(workers, session_sha256, matrix="all", reverse=False):
    if [(w["node"], w["gpu"]) for w in workers] != [
            ("node27", 0), ("node27", 1), ("node29", 0), ("node29", 1)]:
        raise ValueError("the bounded probe requires the retained four-GPU topology")
    if matrix not in ("all", "network", "h2d"):
        raise ValueError("unsupported matrix")
    cases = []
    for kind, node in (("network", None), ("h2d", "node27"), ("h2d", "node29")):
        if matrix != "all" and kind != matrix:
            continue
        for mode, ids in (("single_a", [0]), ("single_b", [1]), ("pair", [0, 1])):
            prefix = kind if node is None else f"{kind}_{node}"
            cases.append({"case_id": f"{prefix}_{mode}", "kind": kind, "node": node,
                          "mode": "concurrent" if len(ids) == 2 else "isolated",
                          "flow_ids": ids, "buffer_bytes": BUFFER_BYTES,
                          "iterations": ITERATIONS, "bytes_per_flow": TOTAL_BYTES,
                          "warmup_iterations": 1})
    if reverse:
        cases.reverse()
    return {"schema_version": SCHEMA, "workers": [{k: w[k] for k in
            ("worker", "node", "gpu", "numa")} for w in workers],
            "topology_id": topology_id(workers), "session_overlay_sha256": session_sha256,
            "cases": cases, "samples_per_configuration": 1,
            "calibration_executor": "isolated_pair_rccl_broadcast",
            "h2d_calibration_executor": "isolated_numa_registered_copy",
            "application_executor": "keeper_serial",
            "capacity_is_physical_upper_bound": False,
            "resource_limits": {"synthetic_hbm_bytes_per_gpu": BUFFER_BYTES,
                "verification_temporary_bytes_per_rank": 32 << 20,
                "pinned_host_bytes_per_h2d_rank": BUFFER_BYTES,
                "minimum_physical_hbm_headroom_bytes": 2 << 30,
                "host_bytes_per_node_admission": 576 << 20,
                "host_guard_bytes": 2 << 30,
                "runtime_overhead": "separate physical sampling; not part of synthetic buffer"}}


def signature(workers):
    fields = ("id", "identity", "worker", "tp", "off", "n", "tier", "epoch", "generation", "state")
    rows = [{"worker": w["worker"], "epoch": w["epoch"], "session": w["session"],
             "transactions": w["transactions"],
             "transaction_phases": w.get("transaction_phases"),
             "used_bytes": w.get("used_bytes"), "hbm_allocated_bytes": w.get("hbm_allocated_bytes"),
             "hbm_reserved_bytes": w.get("hbm_reserved_bytes"), "staging_bytes": w.get("staging_bytes"),
             "replicas": sorted([{k: r.get(k) for k in fields} for r in w["replicas"]],
                                key=lambda r: r["id"])} for w in workers]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def validate_process_exit(expected, registered, actual):
    if expected is None or expected != registered:
        raise ValueError("owned PID identity differs from launch registration")
    if actual.get("pid") != expected["pid"] or actual.get("live", True):
        raise ValueError("the recorded owned process has not exited")
    if actual.get("start_time") not in (None, expected["start_time"]):
        raise ValueError("PID was reused; exit evidence belongs to another process")
    return True


def overlap(flows):
    if len(flows) != 2:
        return {"status": "not_applicable", "overlap_ns": 0, "overlap_fraction": 0.0}
    domains = {f["clock_domain"] for f in flows}
    if len(domains) != 1:
        raise ValueError("cross-host clocks cannot prove overlap")
    starts = [f["receiver_start_ns"] for f in flows]
    ends = [f["receiver_end_ns"] for f in flows]
    durations = [e-s for s, e in zip(starts, ends)]
    if min(durations) <= 0:
        raise ValueError("invalid transfer interval")
    shared = max(0, min(ends)-max(starts))
    return {"status": "observed" if shared else "no_overlap", "clock_domain": next(iter(domains)),
            "overlap_ns": shared, "overlap_fraction": shared / min(durations),
            "scope": "synchronous operation windows; not individual hardware kernel occupancy"}


def counter_deltas(before, after):
    result = []
    boundary_valid = (before["clock_domain"] == after["clock_domain"] and
                      after["monotonic_ns"] > before["monotonic_ns"])
    for name, initial in before["values"].items():
        if name not in after["values"]:
            continue
        final = after["values"][name]
        valid = (boundary_valid and type(initial["unit_bytes"]) is int and initial["unit_bytes"] in (1, 4)
                 and initial["unit_bytes"] == final["unit_bytes"] and type(initial["raw"]) is int
                 and type(final["raw"]) is int and 0 <= initial["raw"] <= final["raw"])
        result.append({"counter": name, "unit_bytes": initial["unit_bytes"], "valid": valid,
                       "delta_bytes": (final["raw"]-initial["raw"])*initial["unit_bytes"] if valid else None,
                       "scope": "shared port/device counter; background traffic not attributed"})
    return {"snapshot_start_ns": before["monotonic_ns"], "snapshot_end_ns": after["monotonic_ns"],
            "clock_domain": before["clock_domain"], "deltas": result,
            "background_excluded": False, "boundary_valid": boundary_valid}


def parse_binding(log, hca, ifname):
    selected = []
    for line in log.splitlines():
        if "NET/IB : Using" in line:
            selected.append(line)
    ports = sorted(set(re.findall(r"\[\d+\]([\w.-]+):(\d+)/IB", "\n".join(selected))))
    oob = sorted(set(re.findall(r"OOB ([\w.-]+):", "\n".join(selected))))
    transport_lines = [line for line in log.splitlines() if "via NET/" in line]
    matches = bool(ports) and all(name == hca for name, _ in ports) and ifname in oob
    actual_rdma = any("NET/IB" in line for line in transport_lines)
    return {"requested_hca": hca, "requested_ifname": ifname,
            "actual_hca_ports": [{"hca": h, "port": int(p)} for h, p in ports],
            "actual_oob_ifnames": oob, "selected_lines": selected,
            "transport_evidence": transport_lines[:8],
            "passed": matches and actual_rdma,
            "scope": "communicator-selected NIC and channel transport; per-flow NIC usage not independently instrumented"}


def validate_flow(flow, case):
    if flow["flow_id"] not in case["flow_ids"]:
        raise ValueError("flow identity is outside the registered case")
    if case["kind"] == "network" and (flow["src"], flow["dst"]) != (flow["flow_id"], flow["flow_id"]+2):
        raise ValueError("network path differs from the registered pair")
    if case["kind"] == "h2d":
        rank = flow["flow_id"] + (2 if case["node"] == "node29" else 0)
        if flow["src"] != rank or flow["dst"] != rank or flow["node"] != case["node"]:
            raise ValueError("H2D location differs from the registered node")
    if flow["bytes"] != case["bytes_per_flow"] or flow["iterations"] != case["iterations"]:
        raise ValueError("actual traffic differs from the preregistered case")
    check = flow["correctness"]
    if (not check["passed"] or check["sha256"] != check["expected_sha256"]
            or check["verified_bytes"] != case["buffer_bytes"]):
        raise ValueError("the full synthetic allocation failed verification")
    if flow["completion"] != "cuda_stream_synchronize":
        raise ValueError("completion lacks a CUDA synchronization boundary")
    if not flow.get("receiver_reset_before_timing", False):
        raise ValueError("receiver buffer was not reset after warmup")
    if flow["receiver_end_ns"] <= flow["receiver_start_ns"]:
        raise ValueError("invalid completed transfer window")
    if len(flow["chunk_intervals"]) != case["iterations"]:
        raise ValueError("missing iteration intervals")
    if any(e <= s for s, e in flow["chunk_intervals"]):
        raise ValueError("invalid iteration interval")
    last = flow["receiver_start_ns"]
    for start, end in flow["chunk_intervals"]:
        if start < last or end > flow["receiver_end_ns"]:
            raise ValueError("iteration intervals are outside the completed window or unordered")
        last = end
    service_s = (flow["receiver_end_ns"]-flow["receiver_start_ns"]) / 1e9
    if not math.isfinite(service_s) or service_s <= 0:
        raise ValueError("invalid service duration")
    return dict(flow, service_s=service_s, empirical_effective_GBps=flow["bytes"]/service_s/1e9,
                rate_kind="empirical_effective", capacity_is_physical_upper_bound=False)


def shared_gate(observations, binding_passed, preservation_passed, cleanup_passed,
                aggregate_threshold=0.85):
    network = {r["case_id"]: r for r in observations if r["case_id"].startswith("network_")}
    required = {"network_single_a", "network_single_b", "network_pair"}
    base = {"resources": [], "application_attribution": "not_established",
            "capacity_is_physical_upper_bound": False, "independence_completion_threshold": aggregate_threshold,
            "application_executor_contention_verified": False}
    if not required.issubset(network):
        return dict(base, gate_status="blocked", reason="network matrix incomplete")
    pair = network["network_pair"]
    if not (binding_passed and preservation_passed and cleanup_passed):
        return dict(base, gate_status="blocked", reason="binding, preservation or cleanup failed")
    if pair["overlap"]["overlap_fraction"] < .8:
        return dict(base, gate_status="blocked", reason="insufficient measured overlap")
    singles = {i: network[f"network_single_{letter}"]["flows"][0] for i, letter in enumerate("ab")}
    ratios = {str(f["flow_id"]): f["empirical_effective_GBps"] /
              singles[f["flow_id"]]["empirical_effective_GBps"] for f in pair["flows"]}
    pair_window_ns = max(f["receiver_end_ns"] for f in pair["flows"])-min(
        f["receiver_start_ns"] for f in pair["flows"])
    pair_common_GBps = sum(f["bytes"] for f in pair["flows"]) / pair_window_ns
    isolated_rate_sum = sum(f["empirical_effective_GBps"] for f in singles.values())
    aggregate_ratio = pair_common_GBps / isolated_rate_sum
    isolated_makespan_ns = max(f["receiver_end_ns"]-f["receiver_start_ns"] for f in singles.values())
    independence_completion_ratio = isolated_makespan_ns / pair_window_ns
    pressure = independence_completion_ratio <= aggregate_threshold
    counters_observed = pair.get("actual_nic_payload_evidence", {}).get("passed", False)
    quiet_background = pair.get("actual_nic_payload_evidence", {}).get("background_fraction_estimate", 1) <= .05
    start_skew_ns = max(f["receiver_start_ns"] for f in pair["flows"])-min(
        f["receiver_start_ns"] for f in pair["flows"])
    matched_shorter_single_ns = min(f["receiver_end_ns"]-f["receiver_start_ns"] for f in singles.values())
    start_skew_fraction = start_skew_ns / matched_shorter_single_ns
    qualified = pressure and counters_observed and quiet_background and start_skew_fraction <= .05
    return dict(base, gate_status="qualified" if qualified else "conditional", pair_to_single_rate_ratio=ratios,
                aggregate_pair_to_isolated_rate_ratio=aggregate_ratio,
                aggregate_rate_ratio_scope="descriptive only; unequal isolated rates invalidate it as a shared-resource gate",
                independence_completion_ratio=independence_completion_ratio,
                isolated_expected_makespan_ns=isolated_makespan_ns, pair_makespan_ns=pair_window_ns,
                receiver_start_skew_ns=start_skew_ns,
                receiver_start_skew_fraction_of_shorter_single=start_skew_fraction,
                receiver_start_skew_threshold=.05,
                per_flow_slowdown={str(f["flow_id"]): (f["receiver_end_ns"]-f["receiver_start_ns"])/
                    (singles[f["flow_id"]]["receiver_end_ns"]-singles[f["flow_id"]]["receiver_start_ns"])
                    for f in pair["flows"]},
                pair_common_window_GBps=pair_common_GBps,
                isolated_rate_sum_GBps=isolated_rate_sum,
                isolated_rate_sum_scope="sum of separately observed rates; comparison reference, not simultaneous hardware capacity",
                resources=["observed_end_to_end_shared_path"] if qualified else [],
                reason="overlapping isolated flows slow down on the observed shared path; NIC/PCIe/DRAM root cause remains unresolved"
                if qualified else "measured overlap exists but slowdown, NIC payload, background or start-skew evidence is insufficient",
                background_traffic_attribution="unresolved; counters include all port users")
