"""Read-only retained inventory and node evidence; no forward or catalog mutation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios"))
from runtime_support import select_base
from fluidckpt.benchmarks.topology_calibration import signature

RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/topology_scenarios"
SCRIPT = Path(__file__).resolve()


def write(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    tmp.replace(path)
    os.chmod(path, 0o666)


def new_out(path):
    path = Path(path).resolve()
    if RESULT_ROOT not in path.parents or not path.name.startswith("calibration_"):
        raise ValueError("use a new topology_scenarios/calibration_* run directory")
    path.mkdir(parents=True, exist_ok=False)
    os.chmod(path, 0o777)
    return path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_session(path):
    state = json.loads(Path(path).read_text())
    if "root_session" in state:
        root = json.loads(Path(state["root_session"]).read_text())
        for key in ("nodes", "members", "endpoints", "env"):
            state.setdefault(key, root[key])
    return state


def get_inventory(endpoint, timeout=5):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint + "/inventory?compact=1", timeout=timeout) as response:
        return json.loads(response.read())


def remote(state, node, command, timeout=40):
    script = "ulimit -c 0\nsource /opt/dtk/env.sh >/dev/null 2>&1\ncd " + shlex.quote(str(ROOT)) + "\nexec " + shlex.join(command)
    argv = ["docker", "exec", state["nodes"][node]["container"], "bash", "-lc", script]
    if node != "node27":
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", node, shlex.join(argv)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{node}: command failed {result.returncode}: {result.stderr[-2000:]} {result.stdout[-2000:]}")
    return result.stdout


def read_text(path, default=None):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return default


def clock_domain():
    return f"{socket.gethostname()}/{read_text('/proc/sys/kernel/random/boot_id')}/monotonic_ns"


def counters(hca, ifname):
    values = {}
    for port in Path("/sys/class/infiniband", hca, "ports").glob("*"):
        for name in ("port_xmit_data", "port_rcv_data"):
            path = port / "counters" / name
            raw = read_text(path)
            if raw is not None:
                values[str(path)] = {"raw": int(raw), "unit_bytes": 4}
    for name in ("tx_bytes", "rx_bytes"):
        path = Path("/sys/class/net", ifname, "statistics", name)
        raw = read_text(path)
        if raw is not None:
            values[str(path)] = {"raw": int(raw), "unit_bytes": 1}
    return {"values": values, "monotonic_ns": time.monotonic_ns(), "clock_domain": clock_domain()}


def proc_state(pid):
    raw = read_text(f"/proc/{pid}/stat")
    if raw is None:
        return {"live": False, "pid": pid}
    fields = raw.split(") ", 1)[1].split()
    return {"live": fields[0] != "Z", "pid": pid, "state": fields[0], "start_time": fields[19]}


def protected_specs(state, node):
    paths = {path for n, path in state.get("basic_owned_processes", []) if n == node}
    paths.update(path for n, path in state.get("recovery_owned_processes", []) if n == node)
    paths.update(path for n, path in state.get("new_engine_processes", []) if n == node)
    rows = []
    for spec in sorted(paths):
        pid_path = Path(spec).with_suffix(".pid.json")
        try:
            registered = json.loads(pid_path.read_text())
            actual = proc_state(registered["pid"])
            rows.append({"spec": spec, "spec_sha256": digest(spec), "registered": registered,
                         "actual": actual, "passed": actual["live"] and actual.get("start_time") == registered["start_time"]})
        except Exception as exc:
            rows.append({"spec": spec, "passed": False, "error": repr(exc)})
    return rows


def node_snapshot(session, node, hca, ifname):
    from fluidckpt.topology.discover import discover, read
    state = load_session(session)
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit():
            mem[fields[0].rstrip(":")] = int(fields[1]) * 1024
    occupied = set()
    for name in ("tcp", "tcp6"):
        for line in Path("/proc/net", name).read_text().splitlines()[1:]:
            occupied.add(int(line.split()[1].split(":")[1], 16))
    cg = {}
    for key, paths in {"current": ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
                       "max": ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")}.items():
        cg[key] = next((value for path in paths if (value := read(path)) is not None), None)
    commands = {}
    for name, slot in state.get("slots", {}).items():
        if state["members"][slot["workers"][0]][0] == node and slot.get("command"):
            commands[name] = {"path": slot["command"], "sha256": digest(slot["command"]), "parked": slot.get("parked")}
    return {"node": node, "clock_domain": clock_domain(), "topology": discover(),
            "host_memory_bytes": mem, "cgroup_memory": cg, "occupied_ports": sorted(occupied),
            "protected_processes": protected_specs(state, node), "parked_commands": commands,
            "nic_counters": counters(hca, ifname),
            "nic_binding": {"hca": hca, "ifname": ifname,
                "hca_sysfs_exists": Path("/sys/class/infiniband", hca).exists(),
                "net_sysfs_exists": Path("/sys/class/net", ifname).exists()},
            "read_only": True}


def inspect(state, session, hca="shca_0", ifname="ib0", port=None, protect_sessions=()):
    with ThreadPoolExecutor(len(state["endpoints"])) as pool:
        workers = list(pool.map(get_inventory, state["endpoints"]))
    if [w["worker"] for w in workers] != list(range(4)) or any(w["transactions"] for w in workers):
        raise ValueError("keeper identity or idle transaction admission failed")
    if len({w["session"] for w in workers}) != 1:
        raise ValueError("mixed keeper sessions")
    replicas = [r for w in workers for r in w["replicas"]]
    base = select_base(state["models"]["B"], replicas)
    def snapshot(node):
        raw = remote(state, node, ["python3", str(SCRIPT), "--node-snapshot", node,
                     "--session", str(session), "--hca", hca, "--ifname", ifname])
        return node, json.loads(raw)
    with ThreadPoolExecutor(len(state["nodes"])) as pool:
        nodes = dict(pool.map(snapshot, state["nodes"]))
    errors = []
    for worker in workers:
        if worker["headroom_bytes"]["hbm"] < 2 << 30:
            errors.append(f"GPU {worker['worker']} has less than 2 GiB physical headroom")
    for node, data in nodes.items():
        if not all(p["passed"] for p in data["protected_processes"]) or not data["protected_processes"]:
            errors.append(f"{node}: protected process identity failed")
        if any(not row["parked"] for row in data["parked_commands"].values()):
            errors.append(f"{node}: engine was not declared parked")
        limit = data["cgroup_memory"]["max"]
        current = data["cgroup_memory"]["current"]
        host_free = data["host_memory_bytes"].get("MemAvailable", 0)
        if limit is not None and limit != "max" and current is not None:
            host_free = min(host_free, int(limit)-int(current))
        if host_free < (576 << 20)+(2 << 30):
            errors.append(f"{node}: host/cgroup headroom inadequate")
        if port is not None and port in data["occupied_ports"]:
            errors.append(f"{node}: rendezvous port occupied")
        if not all(data["nic_binding"][key] for key in ("hca_sysfs_exists", "net_sysfs_exists")):
            errors.append(f"{node}: requested NIC sysfs absent")
    legacy = []
    endpoints = sorted({ep for path in protect_sessions for ep in load_session(path)["endpoints"]} - set(state["endpoints"]))
    def legacy_probe(endpoint):
        try:
            data = get_inventory(endpoint)
            return {"endpoint": endpoint, "accessible": True, "signature": signature([data]), "inventory": data}
        except Exception as exc:
            return {"endpoint": endpoint, "accessible": False, "error": repr(exc)}
    if endpoints:
        with ThreadPoolExecutor(len(endpoints)) as pool:
            legacy = list(pool.map(legacy_probe, endpoints))
    return {"workers": workers, "nodes": nodes, "protected_legacy_endpoints": legacy,
            "protected_catalog_signature": signature(workers), "retained_base_ids": [r["id"] for r in base],
            "retained_base_bytes": sum(r["n"] for r in base),
            "resource_admission": {"status": "passed" if not errors else "blocked", "passed": not errors, "errors": errors,
                "gpu_required_headroom_bytes": 2 << 30,
                "host_required_headroom_bytes_per_node": (576 << 20)+(2 << 30)}, "read_only": True}


def preservation(before, after):
    checks = {"catalog_unchanged": before["protected_catalog_signature"] == after["protected_catalog_signature"],
              "protected_processes_unchanged": True, "parked_commands_unchanged": True,
              "legacy_accessible_catalogs_unchanged": True}
    for node in before["nodes"]:
        old, new = before["nodes"][node], after["nodes"][node]
        checks["protected_processes_unchanged"] &= all(p["passed"] for p in new["protected_processes"]) and (
            [{k: p[k] for k in ("spec", "spec_sha256", "registered")} for p in old["protected_processes"]]
            == [{k: p[k] for k in ("spec", "spec_sha256", "registered")} for p in new["protected_processes"]])
        checks["parked_commands_unchanged"] &= old["parked_commands"] == new["parked_commands"]
    current = {r["endpoint"]: r for r in after["protected_legacy_endpoints"]}
    for row in before["protected_legacy_endpoints"]:
        if row["accessible"]:
            new = current.get(row["endpoint"], {})
            checks["legacy_accessible_catalogs_unchanged"] &= new.get("accessible", False) and row["signature"] == new.get("signature")
    return {"status": "passed" if all(checks.values()) else "failed", "passed": all(checks.values()), "checks": checks,
            "scope": "catalog identities/generations, registered processes and parked command files; no GPU content reread"}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--node-snapshot", choices=("node27", "node29"))
    parser.add_argument("--owned-state", type=Path)
    parser.add_argument("--hca", default="shca_0")
    parser.add_argument("--ifname", default="ib0")
    parser.add_argument("--protect-session", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.owned_state:
        directory = args.owned_state.resolve()
        if RESULT_ROOT not in directory.parents or not directory.name.startswith("calibration_"):
            raise ValueError("owned-state must be an isolated calibration run")
        rows = []
        for path in sorted(directory.glob("rank_*.process.pid.json")):
            recorded = json.loads(path.read_text())
            rows.append({"path": str(path), "registered": recorded, "actual": proc_state(recorded["pid"])})
        print(json.dumps(rows))
        return
    if args.node_snapshot:
        print(json.dumps(node_snapshot(args.session, args.node_snapshot, args.hca, args.ifname)))
        return
    if args.out is None:
        parser.error("--out is required outside node-snapshot mode")
    out = new_out(args.out)
    result = {"status": "running", "read_only": True, "session": str(args.session.resolve()),
              "session_overlay_sha256": digest(args.session)}
    write(out / "summary.json", result)
    try:
        result.update(inspect(load_session(args.session), args.session.resolve(), args.hca, args.ifname,
                              protect_sessions=args.protect_session))
        result["status"] = "passed" if result["resource_admission"]["passed"] else "blocked"
    except Exception as exc:
        result.update(status="failed", error=repr(exc))
    write(out / "summary.json", result)
    print(json.dumps({"status": result["status"], "out": str(out)}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
