"""Read bounded host metadata without driver properties, children, or writes."""
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time

HOSTS = {"node27": "m03r2n07", "node28": "m03r2n08", "node30": "m03r2n10"}
PENDING = {"node27": [(4037472, "477235183")], "node28": [], "node30": []}
PCI_RE = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": repr(exc)}


def chain(path):
    actual = Path(path).resolve()
    return [{"bdf": p.name, "sysfs": str(p), **{k: read(p / k) for k in
            ("numa_node", "local_cpulist", "vendor", "device", "class")}}
            for p in (actual, *actual.parents) if PCI_RE.fullmatch(p.name)]


def identity(pid):
    path = Path("/proc") / str(pid)
    row = {"pid": pid, "present": path.exists()}
    stat = read(path / "stat")
    if isinstance(stat, str):
        fields = stat.rsplit(") ", 1)[1].split()
        row.update(state=fields[0], ppid=int(fields[1]), pgrp=int(fields[2]),
                   start_ticks=fields[19], rss_pages=int(fields[21]),
                   comm=read(path / "comm"), cgroup=read(path / "cgroup"))
        row["status_selected"] = [s for s in str(read(path / "status")).splitlines()
                                  if s.startswith(("Name:", "Uid:", "NSpid:", "Mems_allowed_list:", "Cpus_allowed_list:"))]
    else:
        row["stat_error"] = stat
    return row


def cgroup(pid):
    raw = read(f"/proc/{pid}/cgroup")
    row = {"proc_cgroup": raw}
    if isinstance(raw, str):
        v2 = next((s.split(":", 2)[2] for s in raw.splitlines() if s.startswith("0::")), None)
        if v2:
            root = Path("/sys/fs/cgroup") / v2.lstrip("/")
            row["files"] = {k: read(root / k) for k in
                            ("memory.current", "memory.max", "memory.events", "pids.current", "pids.max",
                             "cpuset.cpus.effective", "cpuset.mems.effective")}
    return row


def docker_get(endpoint):
    # A socket timeout and a response-size bound replace subprocess timeouts.
    connection = http.client.HTTPConnection("localhost", timeout=3)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect("/var/run/docker.sock")
        connection.sock = sock
        connection.request("GET", endpoint, headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("Docker response exceeds 2MiB")
        if response.status != 200:
            return {"endpoint": endpoint, "status": response.status, "error": body.decode(errors="replace")[:500]}
        return json.loads(body)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {"endpoint": endpoint, "error": repr(exc)}
    finally:
        connection.close()
        sock.close()


def mounts():
    rows = []
    raw = read("/proc/self/mountinfo")
    if not isinstance(raw, str):
        return [], raw
    for line in raw.splitlines():
        left, right = line.split(" - ", 1)
        a, b = left.split(), right.split()
        rows.append({"mount_id": a[0], "parent_id": a[1], "major_minor": a[2],
                     "root": a[3], "target": a[4], "options": a[5],
                     "fstype": b[0], "source": b[1], "super_options": b[2]})
    return rows, None


def main():
    node, source_sha = sys.argv[1:3]
    if node not in HOSTS or os.uname().nodename != HOSTS[node]:
        raise SystemExit("host identity mismatch")
    # Exit our own interpreter even if a read blocks; no child or signal exists.
    def deadline():
        time.sleep(35)
        os._exit(124)
    threading.Thread(target=deadline, daemon=True).start()
    start = now()
    out = {"started_utc": start, "node": node, "hostname": os.uname().nodename,
           "boot_id": read("/proc/sys/kernel/random/boot_id"), "collector_pid": os.getpid(),
           "collector_source_sha256": source_sha, "uid": os.getuid(),
           "host_memory": read("/proc/meminfo"), "loadavg": read("/proc/loadavg"),
           "numa": {}, "gpus": [], "hca": [], "network": [], "block_devices": [],
           "pending_exact": [], "containers": [], "process_summary": [],
           "gpu_calls": 0, "driver_property_reads": 0, "keeper_rpc_calls": 0,
           "remote_writes": 0, "signals": [], "children_created": 0,
           "node29_accesses": 0, "runtime_admission": False}
    for pid, ticks in PENDING[node]:
        row = identity(pid)
        row["expected_start_ticks"] = ticks
        row["exact_old_identity_present"] = row.get("start_ticks") == ticks
        row["old_process_absent_at_sample"] = not row["present"] or (row.get("start_ticks") is not None and row["start_ticks"] != ticks)
        row["exit_status_recovered"] = False
        out["pending_exact"].append(row)
    for p in sorted(Path("/sys/devices/system/node").glob("node[0-9]*")):
        out["numa"][p.name] = {k: read(p / k) for k in ("cpulist", "distance", "meminfo")}
    for p in sorted(Path("/sys/bus/pci/devices").iterdir()):
        cls = read(p / "class")
        if isinstance(cls, str) and cls.startswith("0x03"):
            out["gpus"].append({"bdf": p.name, "pci_chain": chain(p),
                                "free_hbm_bytes": None, "free_hbm_reason": "driver properties deliberately excluded"})
    for p in sorted(Path("/sys/class/infiniband").glob("*")):
        out["hca"].append({"name": p.name, "pci_chain": chain(p / "device"),
                           "ports": {q.name: {k: read(q / k) for k in ("state", "phys_state", "rate", "link_layer")}
                                     for q in sorted((p / "ports").glob("*"))}})
    for p in sorted(Path("/sys/class/net").glob("*")):
        if (p / "device").exists():
            out["network"].append({"name": p.name, "pci_chain": chain(p / "device"),
                                   "operstate": read(p / "operstate")})
    for p in sorted(Path("/sys/class/block").glob("*"))[:64]:
        out["block_devices"].append({"name": p.name, "resolved": str(p.resolve()),
                                     "dev": read(p / "dev"), "size_512b_sectors": read(p / "size"),
                                     "rotational": read(p / "queue/rotational"),
                                     "logical_block_size": read(p / "queue/logical_block_size"),
                                     "model": read(p / "device/model"), "pci_chain": chain(p / "device")})
    mount_rows, mount_error = mounts()
    out["mounts"] = mount_rows
    out["mount_error"] = mount_error
    out["storage_paths"] = []
    paths = ["/", "/tmp", "/var/lib/docker", "/home", "/data", f"/data/{node}", "/data/node28/shared", "/dev/shm"]
    for path in paths:
        matching = [m for m in mount_rows if path == m["target"] or path.startswith(m["target"].rstrip("/") + "/")]
        mount = max(matching, key=lambda m: len(m["target"])) if matching else None
        row = {"path": path, "mount": mount}
        if mount and mount["fstype"] in ("nfs", "nfs4", "autofs", "cifs", "fuse", "fuse.sshfs"):
            row.update(exists=None, statvfs=None, reason="network/autofs path not touched")
        else:
            row["exists"] = Path(path).exists()
            if row["exists"]:
                try:
                    v = os.statvfs(path)
                    row["statvfs"] = {"capacity_bytes": v.f_blocks * v.f_frsize,
                                       "available_bytes": v.f_bavail * v.f_frsize,
                                       "free_bytes": v.f_bfree * v.f_frsize,
                                       "inodes": v.f_files, "available_inodes": v.f_favail}
                except OSError as exc:
                    row["statvfs_error"] = repr(exc)
        out["storage_paths"].append(row)
    summary = docker_get("/containers/json?all=1")
    if isinstance(summary, list):
        out["docker_container_count"] = len(summary)
        selected = [c for c in summary if any("zjn" in n or "fluid" in n for n in c.get("Names", []))][:12]
        for entry in selected:
            ci = docker_get("/containers/" + entry["Id"] + "/json")
            if "Id" not in ci:
                out["containers"].append(ci)
                continue
            state = ci["State"]
            out["containers"].append({"id": ci["Id"], "name": ci["Name"], "image_id": ci["Image"],
                                       "state": state, "created": ci["Created"],
                                       "mounts": ci.get("Mounts", []),
                                       "host_config": {k: ci["HostConfig"].get(k) for k in
                                                       ("Memory", "ShmSize", "NetworkMode", "IpcMode", "CpusetCpus", "CpusetMems", "Devices")},
                                       "cgroup": cgroup(state["Pid"]) if state["Running"] else None})
    else:
        out["docker_error"] = summary
    proc = sorted((p for p in Path("/proc").iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
    out["process_scan_truncated"] = len(proc) > 4096
    for p in proc[:4096]:
        comm = read(p / "comm")
        if isinstance(comm, str) and any(s in comm.lower() for s in ("python", "vllm", "sglang")):
            row = identity(int(p.name))
            try:
                argv = (p / "cmdline").read_bytes().decode(errors="replace").split("\0")
                row["is_fluid_worker"] = any("fluidckpt.cluster.worker" in s for s in argv)
                row["is_vllm"] = any("vllm" in s.lower() for s in argv)
                row["is_sglang"] = any("sglang" in s.lower() for s in argv)
                row["selected_args"] = {a: argv[i + 1] for i, a in enumerate(argv[:-1])
                                        if a in ("--gpu", "--gpu-id", "--port-base", "--node", "--tp-rank")}
            except OSError as exc:
                row["argv_error"] = repr(exc)
            out["process_summary"].append(row)
    out["ended_utc"] = now()
    print(json.dumps(out, indent=2), flush=True)


if __name__ == "__main__":
    main()
