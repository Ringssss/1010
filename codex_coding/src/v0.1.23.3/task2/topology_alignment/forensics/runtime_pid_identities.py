"""Read known SMI PIDs' identity and cgroup metadata without command arguments."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": str(exc)}

now = datetime.now(timezone.utc)
uptime = float(Path("/proc/uptime").read_text().split()[0])
boot = now - timedelta(seconds=uptime)
ticks = os.sysconf("SC_CLK_TCK")
result = {"utc": now.isoformat(), "boot_utc_from_uptime": boot.isoformat(), "clock_ticks": ticks, "processes": [], "container_names": subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}}"], text=True)}
for pid in sys.argv[1:]:
    p = Path("/proc") / pid
    stat = read(p / "stat")
    item = {"host_pid": int(pid), "comm": read(p / "comm"), "stat": stat, "cgroup": read(p / "cgroup")}
    if isinstance(stat, str):
        fields = stat.rsplit(") ", 1)[1].split()
        start_seconds = int(fields[19]) / ticks
        item.update(start_time_ticks=fields[19], start_utc=(boot + timedelta(seconds=start_seconds)).isoformat(), age_seconds=uptime - start_seconds, state=fields[0], threads=int(fields[17]))
    status = read(p / "status")
    item["status_selected"] = [l for l in status.splitlines() if l.startswith(("Name:", "Uid:", "Gid:", "NSpid:", "Cpus_allowed_list:", "Mems_allowed_list:"))] if isinstance(status, str) else status
    if isinstance(item["cgroup"], str):
        v2 = next((l.split(":", 2)[2] for l in item["cgroup"].splitlines() if l.startswith("0::")), None)
        if v2:
            root = Path("/sys/fs/cgroup") / v2.lstrip("/")
            item["cgroup_files"] = {n: read(root / n) for n in ("cpu.stat", "cpu.max", "cpuset.cpus.effective", "cpuset.mems.effective", "memory.current", "memory.max")}
    result["processes"].append(item)
print(json.dumps(result, indent=2))
