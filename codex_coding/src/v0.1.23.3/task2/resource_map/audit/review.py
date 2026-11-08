"""Independent CPU review against raw evidence; never launch remote/GPU work."""

import argparse
import hashlib
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def load(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cpus(raw):
    out = []
    for group in raw.split(","):
        bounds = list(map(int, group.split("-")))
        out.extend(range(bounds[0], bounds[-1] + 1))
    return out


def total(raw):
    return int(re.search(r"MemTotal:\s+(\d+) kB", raw).group(1)) * 1024


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def review(mapping, provenance, runtime):
    checks = {}
    registry = mapping["physical"]["resources"]
    counts = Counter(r["kind"] for r in registry.values())
    for kind, number in (("gpu", 16), ("numa", 16), ("hca", 8), ("hca_port", 8)):
        checks[f"count/{kind}"] = counts[kind] == number
    checks["no_unmeasured_effective_resource_capacity"] = all(r.get("capacity_Bps") is None for r in registry.values())
    fabric = mapping["physical"]["fabric"]
    checks["fabric_unknown_preserved"] = all(fabric[k] is None for k in ("internal_topology", "capacity_Bps", "sharing_interference"))
    refs = mapping["source_refs"]
    for ref in refs:
        checks[f"sha/{ref['path']}"] = digest(ref["path"]) == ref["sha256"]
    for node in ("node27", "node29"):
        host = load(provenance / node / "host.json")
        namespace = load(provenance / node / "container.json")["namespace_sysfs"]
        ns_devices = {r["resolved"]: r for r in namespace["devices"]}
        dynamic = runtime["nodes"][node]["data"]
        old = load(ROOT / f"codex_coding/results/v0.1.23.3/task2/discovery01/{node}_topology.json")["topology"]
        ordinal = {r["bdf"]: r["logical_gpu"] for r in old["gpus"]}
        smi = json.loads(dynamic["smi"]["memory_use"]["stdout"])
        for key, row in host["numa"].items():
            rid = f"{node}/numa/{key[4:]}"
            actual = registry[rid]
            checks[rid + "/cpus"] = actual["cpus"] == cpus(row["cpulist"])
            checks[rid + "/dram_total"] = actual["capacity_bytes"] == total(dynamic["numa_memory"][key])
            checks[rid + "/distance_is_relative"] = actual["distance"] == list(map(int, row["distance"].split())) and "not time" in actual["distance_basis"]
        for kind, rows in (("gpu", host["kfd"]), ("hca", host["hca"])):
            for raw in rows:
                chain = raw["pci_chain"]
                endpoint = chain[0]
                rid = f"{node}/pci/{endpoint['bdf']}"
                actual = registry[rid]
                checks[rid + "/kind"] = actual["kind"] == kind
                checks[rid + "/numa"] = actual["numa_id"] == f"{node}/numa/{endpoint['numa_node']}"
                checks[rid + "/cpu"] = actual["local_cpulist"] == endpoint["local_cpulist"]
                checks[rid + "/chain"] = actual["pci_chain_leaf_to_root"] == [f"{node}/pci/{p['bdf']}" for p in chain]
                checks[rid + "/namespace"] = ns_devices[endpoint["sysfs"]]["numa_node"] == endpoint["numa_node"] and ns_devices[endpoint["sysfs"]]["local_cpulist"] == endpoint["local_cpulist"]
                for ancestor in chain:
                    bridge_id = f"{node}/pci/{ancestor['bdf']}"
                    bridge = registry[bridge_id]
                    checks[bridge_id + "/physical_sysfs"] = bridge["sysfs"] == ancestor["sysfs"]
                    checks[bridge_id + "/pcie_current"] = bridge["pcie_current_link_speed"] == ancestor["current_link_speed"] and bridge["pcie_current_link_width"] == ancestor["current_link_width"]
                if kind == "gpu":
                    logical = ordinal[endpoint["bdf"]]
                    checks[rid + "/historical_ordinal_binding"] = actual["logical_gpu"] == logical and actual.get("logical_mapping_sampled_at") == old["timestamp"]
                    checks[rid + "/hbm_total"] = actual["capacity_bytes"] == int(smi[f"card{logical}"]["vram Total Memory (MiB)"]) * 2**20
                    checks[rid + "/kfd_identity"] = actual["kfd_node"] == int(raw["node"]) and actual["kfd_gpu_id"] == raw["gpu_id"]
                    availability = mapping["availability"]["resources"][rid]
                    checks[rid + "/free_independent"] = availability["free_bytes"] == int(smi[f"card{logical}"]["Available memory size (MiB)"]) * 2**20
                    checks[rid + "/no_inferred_budget"] = availability["authorized_budget_bytes"] is None and not availability.get("budget_authorization_ref")
                    checks[rid + "/external_owner_blocks"] = bool(availability["external_active_owners"]) and availability["exclusive"] is False
                else:
                    for port, state in raw["ports"].items():
                        pid = f"{node}/hca/{raw['name']}/port/{port}"
                        resource = registry[pid]
                        checks[pid + "/nominal_only"] = resource["nominal_line_rate_bps"] == 400_000_000_000 and resource["capacity_Bps"] is None
                        checks[pid + "/device_chain"] = resource["device_id"] == rid and resource["pci_chain_leaf_to_root"] == actual["pci_chain_leaf_to_root"]
        checks[node + "/node_total_not_additional_resource"] = mapping["physical"]["nodes"][node]["dram_total_is_summary_not_additional_resource"] is True
    checks["historical_profiles_not_current"] = all(r["admission"]["current_profile_eligible"] is False for r in mapping["measurements"])
    checks["owned_xml_cpu_validation_not_runtime"] = all(not r.get("runtime_observed") for key, r in mapping["runtime_configs"].items() if key.startswith("owned_xml/"))
    api = module(ROOT / "main_lib/fluidckpt/topology/resource_map.py", "audited_map")
    for node in ("node27", "node29"):
        gpu, hca = f"{node}/pci/0000:09:00.0", f"{node}/pci/0000:03:00.0"
        shared = api.shared_ancestors(mapping, gpu, hca)
        checks[node + "/shared_bridge_once"] = {r["id"] for r in shared} == {f"{node}/pci/0000:01:00.0", f"{node}/pci/0000:00:01.1"} and len(shared) == 2
    checks["api_structure_valid"] = api.validate_resource_map(mapping)["passed"]
    readiness = api.planner_readiness(mapping)
    checks["default_planning_fail_closed"] = readiness["structure_ready"] and not readiness["ready"]
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "counts": dict(counts), "failed": [k for k, v in checks.items() if not v],
            "planner_readiness": readiness, "gpu_operations": 0, "remote_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = review(load(args.map), args.provenance, load(args.runtime))
    result["map_sha256"] = digest(args.map)
    (args.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()),
                      "total": len(result["checks"]), "failed": result["failed"]}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
