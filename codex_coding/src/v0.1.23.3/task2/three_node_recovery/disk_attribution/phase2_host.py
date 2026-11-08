"""Read only owned upper layers and keeper process memory, with bounded commands."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

OWN = ["0deafa1b72721d0ca8d27634f9f2bf71021d5900e631680baa41c88ff0f1fd8c", "177035641ac22e59854da0edd9af0941d7599c1820cf686491fea5a2a06c962c"]

def command(argv, seconds=5):
    row = {"argv": argv, "timeout_s": seconds, "started_utc": datetime.now(timezone.utc).isoformat()}
    try:
        result = subprocess.run(argv, capture_output=True, timeout=seconds)
        row.update(returncode=result.returncode, stdout=result.stdout.decode(errors="replace"), stderr=result.stderr.decode(errors="replace"))
    except subprocess.TimeoutExpired as exc:
        decode = lambda value: value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        row.update(returncode=None, timed_out=True, stdout=decode(exc.stdout), stderr=decode(exc.stderr), automatic_own_cli_timeout_kill_attempted=True)
    row["ended_utc"] = datetime.now(timezone.utc).isoformat()
    return row

def metadata(path):
    try:
        s = os.stat(path)
        return {"path": str(path), "apparent_bytes": s.st_size, "allocated_bytes": s.st_blocks * 512, "device": s.st_dev, "inode": s.st_ino, "uid": s.st_uid, "mtime_ns": s.st_mtime_ns}
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}

def read(path):
    try:
        return Path(path).read_text()
    except OSError as exc:
        return {"error": str(exc)}

if os.uname().nodename != "m03r2n08":
    raise SystemExit("node28 actual hostname mismatch")
out = {"hostname": os.uname().nodename, "boot_id": read("/proc/sys/kernel/random/boot_id"), "uid": os.getuid(), "utc": datetime.now(timezone.utc).isoformat(), "remote_writes": 0, "gpu_operations": 0, "signals_to_services": 0, "owned": []}
out["meminfo"] = read("/proc/meminfo")
for cid in OWN:
    row = {"id": cid}
    inspect = command(["docker", "inspect", "--format", '{"Id":{{json .Id}},"Name":{{json .Name}},"Pid":{{json .State.Pid}},"StartedAt":{{json .State.StartedAt}},"Running":{{json .State.Running}},"LogPath":{{json .LogPath}},"GraphDriver":{{json .GraphDriver}}}', cid])
    row["inspect"] = inspect
    if inspect.get("returncode") != 0:
        out["owned"].append(row)
        continue
    data = json.loads(inspect["stdout"])
    if data["Id"] != cid or not data["Running"]:
        raise SystemExit("owned container identity mismatch")
    upper = data["GraphDriver"]["Data"]["UpperDir"]
    row["log_metadata"] = metadata(data["LogPath"])
    row["upper_du_allocated"] = command(["du", "-x", "-B1", "--max-depth=2", "--", upper], 15)
    row["upper_du_apparent"] = command(["du", "-x", "--apparent-size", "-B1", "--max-depth=1", "--", upper], 15)
    top = command(["docker", "top", cid, "-eo", "pid,ppid,stat,rss,args"])
    row["top"] = top
    row["keeper_processes"] = []
    if top.get("returncode") == 0:
        for line in top["stdout"].splitlines()[1:]:
            if "keeper" not in line.lower():
                continue
            pid = int(line.split()[0])
            base = Path("/proc") / str(pid)
            cgroup = read(base / "cgroup")
            proc = {"pid": pid, "stat_before": read(base / "stat"), "status": read(base / "status"), "cmdline": (base / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace"), "cgroup": cgroup, "fd_memory_metadata": []}
            for fd in (base / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                    if "memfd:" in target or target.startswith("/dev/shm/"):
                        proc["fd_memory_metadata"].append({"fd": fd.name, "target": target, "metadata": metadata(fd)})
                except OSError as exc:
                    proc["fd_memory_metadata"].append({"fd": fd.name, "error": str(exc)})
            proc["stat_after"] = read(base / "stat")
            row["keeper_processes"].append(proc)
    init_cgroup = read(Path("/proc") / str(data["Pid"]) / "cgroup")
    row["init_cgroup"] = init_cgroup
    row["memory_cgroup"] = {}
    if isinstance(init_cgroup, str):
        for line in init_cgroup.splitlines():
            parts = line.split(":", 2)
            if parts[1] == "":
                cg = Path("/sys/fs/cgroup") / parts[2].lstrip("/")
                names = ["memory.current", "memory.max", "memory.stat", "memory.events"]
            elif "memory" in parts[1].split(","):
                cg = Path("/sys/fs/cgroup/memory") / parts[2].lstrip("/")
                names = ["memory.usage_in_bytes", "memory.limit_in_bytes", "memory.stat", "memory.failcnt"]
            else:
                continue
            row["memory_cgroup"] = {"path": str(cg), **{name: read(cg / name) for name in names}}
    out["owned"].append(row)
    print(json.dumps({"completed": cid, "upper_allocated": row["upper_du_allocated"], "log": row["log_metadata"]}), flush=True)
out["shm_df"] = command(["df", "-B1", "--", "/dev/shm"])
print("FINAL_JSON")
print(json.dumps(out, indent=2))
