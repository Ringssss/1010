"""Collect node30 storage and DTK static metadata through stdin without remote writes."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

CID = "7d3143da42c4c16a9f48e61189035e48a2dc3d9f177cd6207cb9b7f447120695"
STARTED = "2026-10-08T08:19:06.916290051Z"
BOOT = "d9d6a6fb-013f-49ac-98f9-e77eca67952e"
IMAGE = "sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588"
NAME = "zjn-fluid-three-node-isolated_r01-n30"
KEYS = ("pid", "start_time", "pgid", "sid", "argv")
CONTAINER_SOURCE = r'''
import hashlib, json, os, resource, signal
from pathlib import Path
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
signal.alarm(4)
assert os.uname().nodename == "m03r2n10"
assert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == "d9d6a6fb-013f-49ac-98f9-e77eca67952e"
path = Path("/opt/dtk/env.sh")
assert path.stat().st_size <= 65536
raw = path.read_bytes()
paths = ("/opt/hyhal/lib", "/opt/hyhal/lib/cmake/rocm_smi", "/opt/hyhal/lib/librocm_smi64.so", "/opt/hyhal/lib/librocm_smi64.so.1", "/opt/hyhal/bin/hy-smi", "/opt/dtk/.hyhal/rocm_smi/lib", "/usr/local/lib", "/usr/local/lib64")
row = {"hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "pid_namespace": os.readlink("/proc/self/ns/pid"), "env_sh": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode(), "sourced": False}, "branch_paths": {p: {"exists": Path(p).exists(), "is_dir": Path(p).is_dir(), "is_file": Path(p).is_file()} for p in paths}, "env_whitelist": {k: os.environ.get(k) for k in ("PATH", "LD_LIBRARY_PATH", "ROCM_PATH", "DTKROOT", "HYHAL_PATH", "HIP_PATH", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}, "signals": [], "gpu_operations": 0, "device_open_calls": 0, "library_loads": 0}
print(json.dumps(row))
'''


def proc(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        argv = [x.decode() for x in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if x]
        return {"pid": pid, "start_time": fields[19], "state": fields[0],
                "pgid": int(fields[2]), "sid": int(fields[3]), "argv": argv}
    except FileNotFoundError:
        return {"pid": pid, "state": "absent"}


def bounded(argv, seconds, stdin=None):
    start = time.monotonic_ns()
    child = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    owner = proc(child.pid)
    boot_before = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    result = {"argv": argv, "owner": owner, "deadline_seconds": seconds, "signals": [], "timed_out": False}
    try:
        stdout, stderr = child.communicate(stdin, timeout=seconds)
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            actual = proc(child.pid)
            if actual.get("state") in ("absent", "Z"):
                break
            if (Path("/proc/sys/kernel/random/boot_id").read_text().strip() != boot_before
                    or not all(actual.get(k) == owner.get(k) for k in KEYS)
                    or owner.get("pgid") != child.pid or owner.get("sid") != child.pid):
                result["cleanup_error"] = "exact newly-created CPU CLI ownership changed; no signal"
                break
            os.killpg(child.pid, sig)
            result["signals"].append(sig.name)
            try:
                stdout, stderr = child.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                continue
    result.update(returncode=child.poll(), actual_after=proc(child.pid), elapsed_ns=time.monotonic_ns()-start,
                  stdout=stdout.decode(errors="replace"), stderr=stderr.decode(errors="replace"))
    result["cleanup_proven"] = result["actual_after"].get("state") in ("absent", "Z")
    return result


def statvfs(path):
    try:
        fs = os.statvfs(path)
        stat = os.stat(path)
        return {"path": path, "st_dev": stat.st_dev, "realpath": os.path.realpath(path),
                "total_bytes": fs.f_blocks * fs.f_frsize, "available_bytes": fs.f_bavail * fs.f_frsize,
                "free_bytes": fs.f_bfree * fs.f_frsize, "available_inodes": fs.f_favail,
                "write_access": os.access(path, os.W_OK)}
    except OSError as exc:
        return {"path": path, "error": repr(exc)}


def remote():
    row = {"sampled_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
           "gpu_operations": 0, "device_open_calls": 0, "remote_file_writes": 0, "commands": {}}
    if row["hostname"] != "m03r2n10" or row["boot_id"] != BOOT:
        row["identity_passed"] = False
        print(json.dumps(row))
        return
    commands = row["commands"]
    commands["df_bytes"] = bounded(["df", "-l", "-B1", "-P"], 3)
    commands["df_inodes"] = bounded(["df", "-l", "-i", "-P"], 3)
    commands["docker_root"] = bounded(["docker", "info", "--format", "{{.DockerRootDir}}"], 3)
    fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"name":{{json .Name}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}},"mounts":{{json .Mounts}},"oom_killed":{{json .State.OOMKilled}}}'
    commands["container_identity"] = bounded(["docker", "inspect", "--format", fmt, CID], 3)
    inspected = json.loads(commands["container_identity"]["stdout"]) if commands["container_identity"]["returncode"] == 0 else {}
    row["container_identity"] = inspected
    row["identity_passed"] = (inspected.get("id") == CID and inspected.get("started") == STARTED
                              and inspected.get("running") is True and inspected.get("image") == IMAGE
                              and inspected.get("name") == "/"+NAME
                              and inspected.get("task_label") == "v0.1.23.3/task2/three_node_recovery")
    commands["container_size"] = bounded(["docker", "inspect", "--size", "--format", '{"size_rw":{{json .SizeRw}},"size_root_fs":{{json .SizeRootFs}}}', CID], 3) if row["identity_passed"] else {"skipped": "exact container identity rejected"}
    paths = ["/", "/tmp", "/var/lib/docker", "/dev/shm", "/data", "/data/node30", "/data/node30/shared", "/data/node30/shared/zhujianian", "/tmp/fluidckpt_three_node/isolated_r01_n30/project"]
    root_command = commands["docker_root"]
    if root_command["returncode"] == 0 and root_command["stdout"].strip().startswith("/"):
        paths.append(root_command["stdout"].strip())
    row["filesystem_paths"] = [statvfs(p) for p in dict.fromkeys(paths)]
    if row["identity_passed"]:
        commands["container_env_static"] = bounded(["docker", "exec", "-i", CID, "python3", "/dev/stdin"], 5, CONTAINER_SOURCE.encode())
        value = commands["container_env_static"]
        row["container_env_static"] = json.loads(value["stdout"]) if value["returncode"] == 0 else None
    print(json.dumps(row, ensure_ascii=False))


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.remote:
        remote()
        return
    if args.out is None or not args.out.name.startswith("diagnosis30_"):
        parser.error("require a new diagnosis30_* result directory")
    args.out.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).read_bytes()
    (args.out / "source.py").write_bytes(source)
    command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
               "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3",
               "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3.30", "node30", "python3 /dev/stdin --remote"]
    (args.out / "command.json").write_text(json.dumps({"argv": command, "stdin_source_sha256": hashlib.sha256(source).hexdigest(), "execute": args.execute, "deadline_seconds": 30}, indent=2)+"\n")
    if not args.execute:
        print(json.dumps({"prepared": True, "gpu_operations": 0}))
        return
    value = bounded(command, 30, source)
    (args.out / "stdout.json").write_text(value.pop("stdout"))
    (args.out / "stderr.txt").write_text(value.pop("stderr"))
    (args.out / "result.json").write_text(json.dumps(value, indent=2)+"\n")
    print(json.dumps({"out": str(args.out), "returncode": value["returncode"], "timed_out": value["timed_out"], "cleanup_proven": value["cleanup_proven"]}))


if __name__ == "__main__":
    main()
