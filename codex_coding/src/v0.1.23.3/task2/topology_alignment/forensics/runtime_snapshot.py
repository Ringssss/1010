"""Read runtime load, process identities, cgroups and SMI display queries."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": str(exc)}

def processes():
    result = {}
    for p in Path("/proc").glob("[0-9]*"):
        try:
            raw = (p / "stat").read_text()
            data = raw.rsplit(") ", 1)[1].split()
            result[p.name] = {"pid": int(p.name), "comm": (p / "comm").read_text().strip(), "state": data[0], "ppid": int(data[1]), "utime_ticks": int(data[11]), "stime_ticks": int(data[12]), "threads": int(data[17]), "start_time": data[19], "rss_pages": int(data[21])}
        except (OSError, ValueError):
            pass
    return result

def cgroups(pid):
    proc = Path(f"/proc/{pid}")
    raw = read(proc / "cgroup")
    data = {"proc_cgroup": raw, "status_selected": [line for line in (proc / "status").read_text().splitlines() if line.startswith(("Name:", "NSpid:", "Cpus_allowed_list:", "Mems_allowed_list:"))]}
    if isinstance(raw, str):
        v2 = next((l.split(":", 2)[2] for l in raw.splitlines() if l.startswith("0::")), None)
        if v2:
            root = Path("/sys/fs/cgroup") / v2.lstrip("/")
            data["files"] = {name: read(root / name) for name in ("cpu.stat", "cpu.max", "cpu.weight", "cpu.pressure", "cpuset.cpus.effective", "cpuset.mems.effective", "memory.current", "memory.max", "memory.events", "memory.pressure")}
    return data

def cmd(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=45)
    return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

out = {"utc_begin": datetime.now(timezone.utc).isoformat(), "hostname": os.uname().nodename, "boot_id": read("/proc/sys/kernel/random/boot_id"), "cpu_count": os.cpu_count(), "clock_ticks": os.sysconf("SC_CLK_TCK"), "page_size": os.sysconf("SC_PAGE_SIZE"), "loadavg": read("/proc/loadavg"), "memory": read("/proc/meminfo"), "numa_memory": {p.name: read(p / "meminfo") for p in Path("/sys/devices/system/node").glob("node[0-9]*")}, "pressure": {name: read(f"/proc/pressure/{name}") for name in ("cpu", "memory", "io")}}
before, cpu_before, begin = processes(), read("/proc/stat"), time.monotonic()
time.sleep(2)
after, cpu_after, end = processes(), read("/proc/stat"), time.monotonic()
rows = []
for pid, item in after.items():
    old = before.get(pid)
    if old and old["start_time"] == item["start_time"]:
        item["cpu_seconds_in_window"] = ((item["utime_ticks"] + item["stime_ticks"]) - (old["utime_ticks"] + old["stime_ticks"])) / out["clock_ticks"]
        item["cpu_percent_one_core"] = 100 * item["cpu_seconds_in_window"] / (end - begin)
        rows.append(item)
out["cpu_sample"] = {"duration_seconds": end - begin, "proc_stat_before": cpu_before, "proc_stat_after": cpu_after, "top_processes": sorted(rows, key=lambda x: x["cpu_seconds_in_window"], reverse=True)[:40]}
container = sys.argv[1]
ci = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
out["owned_container"] = {"id": ci["Id"], "pid": ci["State"]["Pid"], "cgroup": cgroups(ci["State"]["Pid"])}
out["smi"] = {}
out["smi"]["pids"] = cmd(["/opt/hyhal/bin/hy-smi", "--showpids", "--json"])
out["smi"]["memory_use"] = cmd(["/opt/hyhal/bin/hy-smi", "--showmeminfo", "vram", "--showmemavailable", "--showuse", "--json"])
# Device fd inspection supplies host identities even if SMI exposes only PIDs.
gpu_processes = []
for pid, item in after.items():
    p = Path(f"/proc/{pid}")
    try:
        devices = []
        for fd in (p / "fd").glob("*"):
            try:
                target = os.readlink(fd)
                if target == "/dev/kfd" or target.startswith("/dev/dri/renderD"):
                    devices.append(target)
            except OSError:
                pass
        if devices:
            gpu_processes.append({**item, "gpu_fds": sorted(set(devices)), "cgroup": cgroups(pid)})
    except OSError:
        pass
out["gpu_fd_processes"] = gpu_processes
docker = cmd(["docker", "ps", "--format", "{{.ID}} {{.Names}}"])
out["container_id_name_map"] = docker
pids = sorted({p["pid"] for p in gpu_processes})
if pids:
    out["smi"]["pid_hcus"] = cmd(["/opt/hyhal/bin/hy-smi", "--showpidhcus", *map(str, pids), "--json"])
out["utc_end"] = datetime.now(timezone.utc).isoformat()
print(json.dumps(out, indent=2))
