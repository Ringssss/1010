"""One read-only node28 admission snapshot; no GPU API or payload access."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import urllib.request

BOOT = "5313e6f8-1712-45dc-9855-4c47358b6c4b"
CONTAINERS = (
    ("zjn-fluid-glm-full_distributed02", "0deafa1b72721d0ca8d27634f9f2bf71021d5900e631680baa41c88ff0f1fd8c", "2026-10-04T01:14:43.800160031Z"),
    ("zjn-keep-v02-n28", "177035641ac22e59854da0edd9af0941d7599c1820cf686491fea5a2a06c962c", "2026-10-02T03:16:20.975758112Z"),
)
WORKERS = ((62788, "440237850", 0), (83571, "441210241", 0),
           (83577, "441210242", 0), (83579, "441210242", 0),
           (83580, "441210242", 0), (83581, "441210246", 0),
           (83582, "441210247", 0), (4062669, "427790780", 1),
           (4062708, "427790789", 1), (4062748, "427790795", 1),
           (4062792, "427790800", 1), (4062830, "427790805", 1),
           (4062873, "427790810", 1))


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": repr(exc)}


def command(argv):
    done = subprocess.run(argv, capture_output=True, timeout=4)
    return {"argv": argv, "returncode": done.returncode,
            "stdout": done.stdout.decode(errors="replace"),
            "stderr": done.stderr.decode(errors="replace")}


def process(pid):
    stat = read(f"/proc/{pid}/stat")
    tail = stat.rsplit(") ", 1)[1].split() if isinstance(stat, str) else []
    return {"pid": pid, "state": tail[0] if tail else None,
            "start_time_ticks": tail[19] if tail else None,
            "cgroup": read(f"/proc/{pid}/cgroup"), "comm": read(f"/proc/{pid}/comm")}


def keeper(gpu):
    endpoint = f"http://12.11.3.28:2970{gpu}"
    row = {"endpoint": endpoint, "path": "/inventory?compact=1", "timeout_seconds": 2}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(endpoint + row["path"], timeout=2) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("inventory exceeds 1MiB")
        body = json.loads(raw)
        keys = ("worker", "node", "gpu", "numa", "epoch", "session", "pid", "budget_bytes", "used_bytes", "replicas", "transactions", "transaction_phases", "staging_bytes", "hbm_allocated_bytes", "hbm_reserved_bytes")
        canonical = {key: body.get(key) for key in keys}
        row.update(passed=True, body=body, raw_sha256=hashlib.sha256(raw).hexdigest(),
                   catalog_sha256=hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    except Exception as exc:
        row.update(passed=False, error=repr(exc))
    return row


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("readonly 25s deadline")))
    signal.alarm(25)
    result = {"started_utc": datetime.now(timezone.utc).isoformat(), "hostname": os.uname().nodename,
              "boot_id": read("/proc/sys/kernel/random/boot_id"), "gpu_api_calls": 0,
              "docker_creations": 0, "signals": 0, "node27_accesses": 0, "node29_accesses": 0}
    if result["hostname"] != "m03r2n08" or result["boot_id"] != BOOT:
        raise ValueError("node28 identity differs; stop before resource/keeper reads")
    result["df"] = command(["df", "-l", "-B1", "-T", "/", "/tmp", "/var/lib/docker", "/data/node28"])
    result["docker_root"] = command(["docker", "info", "--format", "{{json .DockerRootDir}}"])
    fmt = '{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},"State":{{json .State}}}'
    containers = []
    for name, cid, started in CONTAINERS:
        row = command(["docker", "inspect", "--format", fmt, cid])
        body = json.loads(row["stdout"]) if row["returncode"] == 0 else {}
        checks = {"id": body.get("id") == cid, "name": body.get("name") == "/" + name,
                  "started": body.get("State", {}).get("StartedAt") == started,
                  "running": body.get("State", {}).get("Running") is True}
        containers.append({"expected_id": cid, "expected_started": started, "body": body,
                           "checks": checks, "command": row})
    result["protected_containers"] = containers
    if not all(all(row["checks"].values()) for row in containers):
        raise ValueError("old keeper container identity differs; no inventory requests")
    processes = []
    for pid, start, index in WORKERS:
        row = process(pid)
        row["owner_matches"] = (row["start_time_ticks"] == start and row["state"] not in (None, "Z")
                                and CONTAINERS[index][1] in str(row["cgroup"]))
        row["expected_start_time_ticks"] = start
        processes.append(row)
    result["protected_processes"] = processes
    gpu = Path("/sys/bus/pci/devices/0000:55:00.0")
    result["selected_gpu"] = {"physical_gpu": 2, "bdf": gpu.name,
                              "sysfs": {key: read(gpu / key) for key in ("mem_info_vram_total", "mem_info_vram_used", "numa_node")},
                              "hip_free_bytes": None}
    owners = []
    for path in sorted(Path("/sys/class/kfd/kfd/proc").iterdir(), key=lambda p: p.name):
        if not path.name.isdigit():
            continue
        row = process(int(path.name))
        row["kfd_fields"] = {p.name: read(p) for p in path.iterdir() if re.fullmatch(r"vram_[0-9]+|pasid", p.name)}
        owners.append(row)
    result["owners"] = owners
    with ThreadPoolExecutor(max_workers=3) as pool:
        result["keepers"] = list(pool.map(keeper, range(2, 8)))
    result["keeper_rpc_calls"] = 6
    result["ended_utc"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
