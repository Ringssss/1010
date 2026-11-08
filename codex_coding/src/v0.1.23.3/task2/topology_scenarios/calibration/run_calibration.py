"""Register or run disposable probes while preserving the retained application mesh."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import resource
import sys
import time

from inventory import (ROOT, SCRIPT, digest, inspect, load_session, new_out, preservation,
                       remote, write)
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.topology_calibration import (SCHEMA, counter_deltas, overlap,
    parse_binding, protocol, shared_gate, validate_flow, validate_process_exit)

PROCESS_SCRIPT = ROOT / "codex_coding/src/task2/cluster/process.py"
RANK_SCRIPT = Path(__file__).with_name("probe_rank.py")


def frozen_code():
    paths = [*Path(__file__).parent.glob("*.py"),
        ROOT / "main_lib/fluidckpt/benchmarks/topology_calibration.py",
        ROOT / "main_lib/fluidckpt/cluster/path_catalog.py",
        ROOT / "main_lib/fluidckpt/topology/discover.py",
        ROOT / "main_lib/fluidckpt/profiler/numa.py",
        ROOT / "main_lib/fluidckpt/memory/numa.py",
        ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py", PROCESS_SCRIPT]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def process_specs(state, workers, out, args):
    protected = {path for _, path in state.get("basic_owned_processes", [])}
    result = []
    for worker in workers:
        rank, node = worker["worker"], worker["node"]
        command = ["python3", str(RANK_SCRIPT), "--protocol", str(out / "protocol.json"),
            "--out", str(out), "--rank", str(rank), "--node", node,
            "--gpu", str(worker["gpu"]), "--numa", str(worker["numa"]),
            "--master", f"tcp://{state['nodes']['node27']['ip']}:{args.port}",
            "--hca", args.hca, "--ifname", args.ifname, "--deadline", str(args.timeout),
            "--collective-timeout", "90"]
        env = {key: value for key, value in state.get("env", {}).items() if not key.startswith("FLUIDCKPT_")}
        env.update(PYTHONPATH=str(ROOT / "main_lib"), HIP_VISIBLE_DEVICES="0,1", CUDA_VISIBLE_DEVICES="0,1",
            AMD_SERIALIZE_KERNEL="0", NCCL_NET_PLUGIN="librccl-net-shca.so", NCCL_DEBUG="INFO",
            NCCL_DEBUG_SUBSYS="INIT,NET,GRAPH", NCCL_SOCKET_IFNAME=args.ifname,
            GLOO_SOCKET_IFNAME=args.ifname, NCCL_IB_HCA=args.hca,
            NCCL_DEBUG_FILE=str(out / f"rank_{rank}.nccl.%p.log"),
            OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        path = out / f"rank_{rank}.process.json"
        if str(path) in protected or path.exists():
            raise ValueError("process spec collision")
        value = {"command": command, "env": env, "cwd": str(ROOT), "log": str(out / f"rank_{rank}.log"),
                 "ownership_namespace": out.name, "owner_node": node, "owner_rank": rank,
                 "rendezvous": {"node": "node27", "port": args.port}, "core_dump_limit": 0}
        write(path, value)
        result.append({"node": node, "rank": rank, "spec": str(path), "spec_sha256": digest(path)})
    return result


def payload_evidence(flows, binding):
    ports = binding["actual_hca_ports"]
    if len(ports) != 1:
        return {"passed": False, "reason": "actual NIC selection is absent or ambiguous"}
    name = f"/sys/class/infiniband/{ports[0]['hca']}/ports/{ports[0]['port']}/counters/port_rcv_data"
    domains = {f["clock_domain"] for f in flows}
    if len(domains) != 1:
        return {"passed": False, "reason": "receiver NIC clock domain differs"}
    first = min((f["nic_snapshot_before"] for f in flows), key=lambda s: s["monotonic_ns"])
    last = max((f["nic_snapshot_after"] for f in flows), key=lambda s: s["monotonic_ns"])
    rows = {d["counter"]: d for d in counter_deltas(first, last)["deltas"]}
    row = rows.get(name, {})
    expected = sum(f["bytes"] for f in flows)
    actual = row.get("delta_bytes")
    idle = flows[0]["idle_background_counter_delta"]
    bg = next((d for d in idle["deltas"] if d["counter"] == name and d["valid"]), None)
    background_fraction = 1.0
    if bg is not None and actual and idle["snapshot_end_ns"] > idle["snapshot_start_ns"]:
        background_fraction = (bg["delta_bytes"] / (idle["snapshot_end_ns"]-idle["snapshot_start_ns"]) *
                               (last["monotonic_ns"]-first["monotonic_ns"]) / actual)
    return {"passed": bool(row.get("valid") and actual is not None and actual >= expected),
            "actual_hca_port": ports[0], "counter": name, "direction": "receive",
            "expected_payload_bytes": expected, "actual_counter_delta_bytes": actual,
            "background_fraction_estimate": background_fraction,
            "background_excluded": False,
            "scope": "one shared receiving port, union of measured per-flow snapshot windows; counted once",
            "limitation": "idle baseline estimates background; unrelated traffic cannot be individually subtracted"}


def collect(out, prereg, hca, ifname):
    ranks = [json.loads((out / f"rank_{rank}.json").read_text()) for rank in range(4)]
    if any(r["status"] != "passed" for r in ranks):
        raise ValueError("not all disposable ranks passed")
    bindings = []
    for rank in range(4):
        logs = "\n".join(p.read_text(errors="replace") for p in sorted(out.glob(f"rank_{rank}.nccl.*.log")))
        if not logs:
            logs = (out / f"rank_{rank}.log").read_text(errors="replace")
        bindings.append(dict(parse_binding(logs, hca, ifname), rank=rank))
    observations = []
    for case in prereg["cases"]:
        rows = [r["flow"] for rank in ranks for r in rank["cases"] if r["case_id"] == case["case_id"] and r["flow"]]
        flows = [validate_flow(row, case) for row in rows if row["role"] == "target"]
        if sorted(f["flow_id"] for f in flows) != case["flow_ids"]:
            raise ValueError("missing, duplicate or incorrect target flow IDs")
        for flow in flows:
            if case["kind"] == "network":
                source = next((r for r in rows if r["role"] == "source" and r["flow_id"] == flow["flow_id"]), None)
                if source is None or source["correctness"]["sha256"] != flow["correctness"]["sha256"]:
                    raise ValueError("source/target synthetic SHA mismatch")
                flow["source_sha256"] = source["correctness"]["sha256"]
                flow["source_timing"] = {k: source[k] for k in
                    ("clock_domain", "receiver_start_ns", "receiver_end_ns", "chunk_intervals")}
            else:
                expected = {str(prereg["workers"][flow["dst"]]["numa"]): 1.0}
                if flow["numa_host_placement"]["sampled_placement"] != expected:
                    raise ValueError("H2D sampled NUMA placement differs from the protocol")
        row = {"case_id": case["case_id"], "kind": case["kind"], "node": case["node"], "mode": case["mode"],
               "flows": flows, "overlap": overlap(flows), "samples": 1,
               "rate_kind": "empirical_effective", "capacity_is_physical_upper_bound": False,
               "timing_scope": "receiver host synchronous completed operation window; not device kernel occupancy"}
        if case["kind"] == "network":
            receiver_binding = bindings[2]
            if bindings[2]["actual_hca_ports"] != bindings[3]["actual_hca_ports"]:
                receiver_binding = {"actual_hca_ports": []}
            row["actual_nic_payload_evidence"] = payload_evidence(flows, receiver_binding)
        observations.append(row)
    return observations, bindings, ranks


def driver(args):
    out = new_out(args.out)
    state = load_session(args.session)
    session = args.session.resolve()
    result = {"schema_version": SCHEMA, "status": "preflight", "session": str(session),
        "session_overlay_sha256": digest(session), "out": str(out), "observations": [],
        "measurement_status": "not_measured", "processes": [], "cleanup": {"passed": False, "errors": []},
        "capacity_is_physical_upper_bound": False,
        "command": sys.argv, "code_sha256": frozen_code()}
    write(out / "summary.json", result)
    handles = []
    before = None
    try:
        before = inspect(state, session, args.hca, args.ifname, args.port, args.protect_session)
        write(out / "inventory_before.json", before)
        result["resource_admission"] = before["resource_admission"]
        if not before["resource_admission"]["passed"]:
            raise RuntimeError("resource admission failed: " + str(before["resource_admission"]["errors"]))
        prereg = protocol(before["workers"], digest(session), args.matrix, args.reverse)
        write(out / "protocol.json", prereg)
        result.update({k: prereg[k] for k in ("workers", "topology_id", "calibration_executor", "application_executor")})
        result["protocol"] = str(out / "protocol.json")
        result["processes"] = process_specs(state, before["workers"], out, args)
        result["input_sha256"] = {str(path): digest(path) for path in
            [session, out / "protocol.json", *(Path(entry["spec"]) for entry in result["processes"])]}
        result["status"] = "registered_only"
        write(out / "summary.json", result)
        if not args.execute:
            return result
        if digest(session) != result["session_overlay_sha256"]:
            raise ValueError("session overlay changed after registration")
        if frozen_code() != result["code_sha256"]:
            raise ValueError("calibration code changed after registration")
        result.update(status="running", measurement_status="attempted")
        write(out / "summary.json", result)
        for entry in result["processes"]:
            # Register the cleanup scope before launch so a lost SSH reply is recoverable.
            handles.append(entry)
            entry["start_reply"] = json.loads(remote(state, entry["node"],
                ["python3", str(PROCESS_SCRIPT), "start", "--spec", entry["spec"]]))
            write(out / "summary.json", result)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            statuses = []
            for rank in range(4):
                path = out / f"rank_{rank}.json"
                statuses.append(json.loads(path.read_text()).get("status") if path.exists() else None)
            if "failed" in statuses:
                raise RuntimeError("a disposable rank failed; see rank output and raw logs")
            if all(s == "passed" for s in statuses):
                break
            time.sleep(.5)
        else:
            raise TimeoutError("calibration driver deadline expired")
        observations, bindings, ranks = collect(out, prereg, args.hca, args.ifname)
        result.update(observations=observations, binding=bindings, rank_resource_sampling=[
            {"rank": r["rank"], "sampling": r["resource_sampling"], "host_buffer": r.get("host_buffer")} for r in ranks],
            status="measured", measurement_status="measured")
    except BaseException as exc:
        result.update(status="failed", error=repr(exc))
    finally:
        for entry in reversed(handles):
            try:
                entry["stop_reply"] = remote(state, entry["node"],
                    ["python3", str(PROCESS_SCRIPT), "stop", "--spec", entry["spec"]], timeout=45)
            except Exception as exc:
                result["cleanup"]["errors"].append({"node": entry["node"], "spec": entry["spec"], "error": repr(exc)})
        if handles:
            try:
                states = {}
                for node in state["nodes"]:
                    rows = json.loads(remote(state, node, ["python3", str(SCRIPT), "--session", str(session),
                        "--owned-state", str(out)]))
                    owned = {r["registered"]["spec"]: r for r in rows}
                    entries = [entry for entry in handles if entry["node"] == node]
                    if any(entry["spec"] not in owned for entry in entries):
                        raise RuntimeError(f"{node}: missing owned PID registration evidence")
                    states[node] = [owned[entry["spec"]] for entry in entries]
                    for entry in entries:
                        evidence = owned[entry["spec"]]
                        validate_process_exit(entry.get("start_reply"), evidence["registered"], evidence["actual"])
                    if any(r["actual"]["live"] for r in states[node]):
                        raise RuntimeError(f"{node}: an owned rank process is still live")
                result["cleanup"]["process_states"] = states
                time.sleep(1)
            except Exception as exc:
                result["cleanup"]["errors"].append({"error": repr(exc)})
        if before is not None:
            try:
                after = inspect(state, session, args.hca, args.ifname, protect_sessions=args.protect_session)
                write(out / "inventory_after.json", after)
                result["before_after_preservation"] = preservation(before, after)
                deltas = [{"worker": a["worker"], "headroom_before_bytes": b["headroom_bytes"]["hbm"],
                    "headroom_after_bytes": a["headroom_bytes"]["hbm"],
                    "unreclaimed_bytes": max(0, b["headroom_bytes"]["hbm"]-a["headroom_bytes"]["hbm"])}
                    for b, a in zip(before["workers"], after["workers"])]
                result["cleanup"]["physical_hbm_reclamation"] = {"rows": deltas,
                    "tolerance_bytes": 128 << 20, "passed": all(d["unreclaimed_bytes"] <= 128 << 20 for d in deltas),
                    "scope": "total free-HBM before/after; concurrent unrelated changes remain confounded"}
                if not result["before_after_preservation"]["passed"]:
                    result["cleanup"]["errors"].append({"error": "protected state preservation failed"})
            except Exception as exc:
                result["cleanup"]["errors"].append({"error": "postflight: " + repr(exc)})
        result["cleanup"]["passed"] = (not result["cleanup"]["errors"] and
            result["cleanup"].get("physical_hbm_reclamation", {}).get("passed", not handles))
        result["cleanup"]["status"] = "passed" if result["cleanup"]["passed"] else "failed"
        result["code_sha256_after"] = frozen_code()
        result["code_preservation"] = {"changed": sorted(path for path in
            set(result["code_sha256"]) | set(result["code_sha256_after"])
            if result["code_sha256"].get(path) != result["code_sha256_after"].get(path))}
        result["code_preservation"]["passed"] = not result["code_preservation"]["changed"]
        result["code_preservation"]["status"] = "passed" if result["code_preservation"]["passed"] else "failed"
        if result.get("input_sha256"):
            changed = [path for path, sha in result["input_sha256"].items() if digest(path) != sha]
            result["input_preservation"] = {"passed": not changed, "status": "failed" if changed else "passed", "changed": changed}
        else:
            result["input_preservation"] = {"passed": False, "status": "not_registered", "changed": []}
        result["shared_resource"] = shared_gate(result["observations"],
            all(b["passed"] for b in result.get("binding", [])) and bool(result.get("binding")),
            result.get("before_after_preservation", {}).get("passed", False) and result["code_preservation"]["passed"]
                and result["input_preservation"]["passed"],
            result["cleanup"]["passed"])
        result["profile"] = {"status": "empirical_effective_partial" if result["observations"] else "not_measured",
            "capacity_is_physical_upper_bound": False, "application_executor_compatible": False,
            "rates": {}, "directed_path_rates": {}, "unmeasured_paths": "peer, SSD, reverse network, DRAM-staged network",
            "provenance": "isolated broadcast/H2D microprobes; no serial keeper service-capacity extrapolation"}
        if result["status"] == "measured":
            result["status"] = "passed" if (result["cleanup"]["passed"] and
                result["before_after_preservation"]["passed"] and all(b["passed"] for b in result["binding"])) else "failed"
        if not result["code_preservation"]["passed"] or (result.get("input_sha256") and not result["input_preservation"]["passed"]):
            result["status"] = "failed"
        write(out / "summary.json", result)
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=33960)
    parser.add_argument("--hca", default="shca_0")
    parser.add_argument("--ifname", default="ib0")
    parser.add_argument("--matrix", choices=("all", "network", "h2d"), default="all")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--protect-session", type=Path, action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 1800:
        parser.error("port or deadline outside the bounded protocol")
    result = driver(args)
    print(json.dumps({"status": result["status"], "out": result["out"],
                      "shared_resource": result["shared_resource"]}))
    if result["status"] not in ("passed", "registered_only"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
