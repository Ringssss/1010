"""Bounded read-only node28 identity, capacity and owned container metadata."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

OWN = ["0deafa1b72721d0ca8d27634f9f2bf71021d5900e631680baa41c88ff0f1fd8c", "177035641ac22e59854da0edd9af0941d7599c1820cf686491fea5a2a06c962c"]

def command(argv, seconds=4):
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
        return {"path": path, "apparent_bytes": s.st_size, "allocated_bytes": s.st_blocks * 512, "device": s.st_dev, "inode": s.st_ino, "mode": oct(s.st_mode), "uid": s.st_uid, "mtime_ns": s.st_mtime_ns}
    except OSError as exc:
        return {"path": path, "error": str(exc)}

if os.uname().nodename != "m03r2n08":
    raise SystemExit("node28 actual hostname mismatch")
out = {"hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "uid": os.getuid(), "utc": datetime.now(timezone.utc).isoformat(), "remote_writes": 0, "gpu_operations": 0, "signals_to_services": 0}
out["local_df_bytes"] = command(["df", "-l", "-B1", "-T"])
out["local_df_inodes"] = command(["df", "-l", "-i", "-T"])
out["mountinfo"] = Path("/proc/self/mountinfo").read_text()
out["docker_info"] = command(["docker", "info", "--format", '{{json .DockerRootDir}} {{json .Driver}}'])
out["containers"] = command(["docker", "ps", "-a", "--no-trunc", "--format", '{{json .ID}} {{json .Names}} {{json .Image}} {{json .Status}}'])
template = '{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},"Created":{{json .Created}},"State":{{json .State}},"LogPath":{{json .LogPath}},"GraphDriver":{{json .GraphDriver}},"Mounts":{{json .Mounts}},"ShmSize":{{json .HostConfig.ShmSize}},"IpcMode":{{json .HostConfig.IpcMode}}}'
out["owned"] = []
for cid in OWN:
    row = command(["docker", "inspect", "--format", template, cid])
    if row.get("returncode") == 0:
        row["parsed"] = json.loads(row["stdout"])
        row["log_metadata"] = metadata(row["parsed"]["LogPath"])
    out["owned"].append(row)
print(json.dumps(out, indent=2))
