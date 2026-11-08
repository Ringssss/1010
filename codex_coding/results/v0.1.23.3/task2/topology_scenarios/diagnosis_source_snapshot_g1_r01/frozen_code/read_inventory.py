"""Read only NIC/PCIe/CPU evidence, or reuse the retained inventory CLI."""
import argparse
import json
import os
from pathlib import Path
import resource
import sys

CALIBRATION = Path(__file__).resolve().parent.parent / "calibration"
sys.path.insert(0, str(CALIBRATION))
import inventory as base


def pci(path):
    target = path.resolve()
    chain = []
    for item in [target, *target.parents]:
        if (item / "vendor").exists():
            chain.append({"bdf": item.name, "sysfs": str(item), **{key: base.read_text(item / key) for key in
                ("vendor", "device", "class", "numa_node", "local_cpulist", "current_link_speed",
                 "current_link_width", "max_link_speed", "max_link_width")}})
    return chain


def facts(session, node):
    state = base.load_session(session)
    hcas = []
    for path in sorted(Path("/sys/class/infiniband").glob("*")):
        ports = []
        for port in sorted((path / "ports").glob("*")):
            ports.append({"port": int(port.name), **{key: base.read_text(port / key) for key in
                ("rate", "state", "phys_state", "link_layer", "lid", "sm_lid")},
                "gids": {p.name: base.read_text(p) for p in sorted((port / "gids").glob("*"))},
                "counters": {p.name: base.read_text(p) for p in sorted((port / "counters").glob("*"))},
                "counter_units": "port_xmit_data/port_rcv_data: 4 B per raw unit"})
        hcas.append({"hca": path.name, "pci_chain": pci(path / "device"), "ports": ports,
            "driver": str((path / "device/driver").resolve()),
            "netdevs": [p.name for p in sorted((path / "device/net").glob("*"))]})
    processes = []
    for entry in base.protected_specs(state, node):
        pid = entry.get("registered", {}).get("pid")
        if pid is None:
            processes.append(entry)
            continue
        entry["cpu_affinity"] = sorted(os.sched_getaffinity(pid))
        entry["status_binding"] = [line for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                                   if line.startswith(("Cpus_allowed", "Mems_allowed"))]
        processes.append(entry)
    return {"schema_version": "contention_inventory.v1", "node": node, "read_only": True,
        "clock_domain": base.clock_domain(), "hcas": hcas, "protected_processes": processes,
        "numa": {p.name: {key: base.read_text(p / key) for key in ("cpulist", "distance")}
                 for p in sorted(Path("/sys/devices/system/node").glob("node[0-9]*"))},
        "network_devices": {p.name: {"pci_chain": pci(p / "device"),
            "speed": base.read_text(p / "speed"), "mtu": base.read_text(p / "mtu")}
            for p in sorted(Path("/sys/class/net").glob("ib*"))}}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--node", choices=("node27", "node29"))
    parser.add_argument("--node-snapshot", choices=("node27", "node29"))
    parser.add_argument("--hca", default="shca_0")
    parser.add_argument("--ifname", default="ib0")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--owned-state", type=Path)
    args = parser.parse_args()
    if args.owned_state:
        directory = args.owned_state.resolve()
        if base.RESULT_ROOT not in directory.parents or not directory.name.startswith("diagnosis_"):
            raise ValueError("owned-state must be an isolated diagnosis run")
        rows = []
        for path in sorted(directory.glob("rank_*.process.pid.json")):
            registered = json.loads(path.read_text())
            rows.append({"path": str(path), "registered": registered,
                         "actual": base.proc_state(registered["pid"])})
        print(json.dumps(rows))
    elif args.node_snapshot:
        value = base.node_snapshot(args.session, args.node_snapshot, args.hca, args.ifname)
        value["diagnosis_nic_cpu_facts"] = facts(args.session, args.node_snapshot)
        print(json.dumps(value))
    elif args.node:
        print(json.dumps(facts(args.session, args.node)))
    else:
        if args.out is None:
            parser.error("--out is required for remote collection")
        out = args.out.resolve()
        if base.RESULT_ROOT not in out.parents or not out.name.startswith("diagnosis_inventory_"):
            raise ValueError("use a new topology_scenarios/diagnosis_inventory_* directory")
        out.mkdir(parents=True, exist_ok=False)
        state = base.load_session(args.session)
        nodes = {}
        for node in state["nodes"]:
            nodes[node] = json.loads(base.remote(state, node, ["python3", str(Path(__file__).resolve()),
                "--session", str(args.session.resolve()), "--node", node]))
            base.write(out / f"{node}.json", nodes[node])
        base.write(out / "summary.json", {"status": "passed", "read_only": True,
            "session_sha256": base.digest(args.session), "script_sha256": base.digest(__file__),
            "nodes": list(nodes)})
        print(json.dumps({"status": "passed", "out": str(out)}))


if __name__ == "__main__":
    main()
