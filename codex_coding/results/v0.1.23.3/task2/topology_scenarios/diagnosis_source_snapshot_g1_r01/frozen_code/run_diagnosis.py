"""Narrow adapters around the frozen calibration runner; GPU launch is opt-in."""
import argparse
import json
from pathlib import Path
import resource
import sys

CALIBRATION = Path(__file__).resolve().parent.parent / "calibration"
sys.path.insert(0, str(CALIBRATION))
import inventory
import run_calibration as original
from fluidckpt.benchmarks.topology_calibration import parse_binding

BASE_SPECS = original.process_specs
BASE_COLLECT = original.collect
BASE_CODE = original.frozen_code
BASE_INSPECT = original.inspect


def new_out(path):
    path = Path(path).resolve()
    if inventory.RESULT_ROOT not in path.parents or not path.name.startswith("diagnosis_probe_"):
        raise ValueError("use a new topology_scenarios/diagnosis_probe_* directory")
    path.mkdir(parents=True, exist_ok=False)
    path.chmod(0o777)
    return path


def frozen_code():
    result = BASE_CODE()
    for path in Path(__file__).parent.glob("*.py"):
        result[str(path.relative_to(inventory.ROOT))] = inventory.digest(path)
    return result


def process_specs(state, workers, out, args):
    rows = BASE_SPECS(state, workers, out, args)
    for row in rows:
        path = Path(row["spec"])
        value = json.loads(path.read_text())
        flow = row["rank"] % 2
        hca = args.hca if flow == 0 else args.flow_b_hca
        value["command"][value["command"].index("--hca") + 1] = hca
        value["env"]["NCCL_IB_HCA"] = hca
        value["diagnosis_control"] = {"flow_id": flow, "hca": hca,
            "gdr_level_override": None, "baseline_rank_script": str(original.RANK_SCRIPT),
            "cpu_affinity_override": None,
            "scope": "per-process HCA/GDR environment only; original buffer/iterations/barriers unchanged"}
        inventory.write(path, value)
        row["spec_sha256"] = inventory.digest(path)
    return rows


def binding(out, rank, hca, ifname):
    paths = sorted(out.glob(f"rank_{rank}.nccl.*.log"))
    logs = "\n".join(p.read_text(errors="replace") for p in paths)
    if not logs:
        logs = (out / f"rank_{rank}.log").read_text(errors="replace")
    result = dict(parse_binding(logs, hca, ifname), rank=rank)
    result["gdr_decision_lines"] = [line for line in logs.splitlines()
                                    if "GPU Direct RDMA" in line]
    result["gdr_enabled_observed"] = any("GPU Direct RDMA Enabled" in line
                                          for line in result["gdr_decision_lines"])
    result["gdr_disabled_observed"] = any("GPU Direct RDMA Disabled" in line
                                           for line in result["gdr_decision_lines"])
    result["gdr_channel_lines"] = [line for line in logs.splitlines()
                                   if "via NET/" in line and "GDRDMA" in line]
    result["all_channel_transport_lines"] = [line for line in logs.splitlines() if "via NET/" in line]
    result["affinity_lines"] = [line for line in logs.splitlines() if "affinity" in line.lower()]
    result["topology_file_lines"] = [line for line in logs.splitlines() if "topo" in line.lower()]
    result["initialization_core_lines"] = [line for line in logs.splitlines() if " on core " in line]
    return result


def inspect(state, session, hca="shca_0", ifname="ib0", port=None, protect_sessions=()):
    result = BASE_INSPECT(state, session, hca, ifname, port, protect_sessions)
    for node, data in result["nodes"].items():
        available = {r["hca"]: r for r in data["diagnosis_nic_cpu_facts"]["hcas"]}
        for name in ("shca_0", "shca_1"):
            selected = available.get(name)
            if selected is None or not any(p["port"] == 1 and p["state"] == "4: ACTIVE"
                                            for p in selected["ports"]):
                result["resource_admission"]["errors"].append(f"{node}: {name}:1 is not ACTIVE")
    errors = result["resource_admission"]["errors"]
    result["resource_admission"].update(passed=not errors, status="blocked" if errors else "passed")
    return result


def per_port_payload(flows, bindings):
    groups = {}
    for flow in flows:
        selected = bindings[flow["dst"]]["actual_hca_ports"]
        if len(selected) != 1:
            return {"passed": False, "reason": "flow receiver port absent or ambiguous"}
        key = (selected[0]["hca"], selected[0]["port"])
        groups.setdefault(key, []).append(flow)
    evidence = []
    for (hca, port), rows in sorted(groups.items()):
        item = original.payload_evidence(rows, {"actual_hca_ports": [{"hca": hca, "port": port}]})
        item["flow_ids"] = [r["flow_id"] for r in rows]
        evidence.append(item)
    return {"passed": all(e["passed"] for e in evidence), "per_port": evidence,
        "expected_payload_bytes": sum(e["expected_payload_bytes"] for e in evidence),
        "actual_counter_delta_bytes": sum(e.get("actual_counter_delta_bytes") or 0 for e in evidence),
        "background_fraction_estimate": max(e["background_fraction_estimate"] for e in evidence),
        "background_excluded": False,
        "scope": "union of per-flow snapshots within each actual receiver port; each distinct port counted once"}


def collect(out, prereg, hca, ifname):
    observations, _, ranks = BASE_COLLECT(out, prereg, hca, ifname)
    bindings = []
    for rank in range(4):
        spec = json.loads((out / f"rank_{rank}.process.json").read_text())
        bindings.append(binding(out, rank, spec["env"]["NCCL_IB_HCA"], ifname))
    for row in observations:
        row["actual_nic_payload_evidence"] = per_port_payload(row["flows"], bindings)
    return observations, bindings, ranks


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--flow-b-hca", choices=("shca_0", "shca_1"), default="shca_1")
    parser.add_argument("--ifname", default="ib0")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--protect-session", type=Path, action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 1800:
        parser.error("port or deadline outside the bounded protocol")
    args.matrix = "network"
    args.hca = "shca_0"
    original.new_out = new_out
    original.frozen_code = frozen_code
    original.process_specs = process_specs
    original.collect = collect
    original.inspect = inspect
    original.SCRIPT = Path(__file__).with_name("read_inventory.py")
    inventory.SCRIPT = original.SCRIPT
    result = original.driver(args)
    result["diagnosis_control"] = {"flow_a_hca": args.hca, "flow_b_hca": args.flow_b_hca,
        "flow_a_gdr_level_override": None, "flow_b_gdr_level_override": None,
        "gdr_intervention_requires_runtime_confirmation": True,
        "baseline_comparison": "use matched singles in this run; HCA change also changes PCIe/NUMA route"}
    if result.get("observations") and any(not r["actual_nic_payload_evidence"]["passed"]
                                           for r in result["observations"]):
        result.update(status="failed", error="actual receiving port payload evidence failed")
    inventory.write(Path(result["out"]) / "summary.json", result)
    print(json.dumps({"status": result["status"], "out": result["out"]}))
    if result["status"] not in ("passed", "registered_only"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
