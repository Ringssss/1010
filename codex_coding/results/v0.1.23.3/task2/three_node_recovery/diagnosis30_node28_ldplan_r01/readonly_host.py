import os, json, resource, signal, subprocess, time
from pathlib import Path
KEYS = ('pid', 'start_time', 'pgid', 'sid', 'argv')
SPEC = {'hostname': 'm03r2n08', 'boot_id': '5313e6f8-1712-45dc-9855-4c47358b6c4b', 'container_id': 'e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba', 'container_started': '2026-10-09T06:19:44.292587393Z', 'image_id': 'sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588'}
CONTAINER_SOURCE = '\nimport hashlib, json, os, resource, signal\nfrom pathlib import Path\nresource.setrlimit(resource.RLIMIT_CORE, (0, 0))\nsignal.alarm(4)\nassert os.uname().nodename == "m03r2n08"\nassert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == "5313e6f8-1712-45dc-9855-4c47358b6c4b"\npath = Path("/opt/dtk/env.sh")\nassert path.stat().st_size <= 65536\nraw = path.read_bytes()\npaths = ("/opt/hyhal/lib", "/opt/hyhal/lib/cmake/rocm_smi", "/opt/hyhal/lib/librocm_smi64.so", "/opt/hyhal/lib/librocm_smi64.so.1", "/opt/hyhal/bin/hy-smi", "/opt/dtk/.hyhal/rocm_smi/lib", "/usr/local/lib", "/usr/local/lib64")\nrow = {"hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "pid_namespace": os.readlink("/proc/self/ns/pid"), "env_sh": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode(), "sourced": False}, "branch_paths": {p: {"exists": Path(p).exists(), "is_dir": Path(p).is_dir(), "is_file": Path(p).is_file()} for p in paths}, "env_whitelist": {k: os.environ.get(k) for k in ("PATH", "LD_LIBRARY_PATH", "ROCM_PATH", "DTKROOT", "HYHAL_PATH", "HIP_PATH", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}, "signals": [], "gpu_operations": 0, "device_open_calls": 0, "library_loads": 0}\nprint(json.dumps(row))\n'
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


resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
row = {"hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "remote_file_writes": 0, "gpu_operations": 0}
assert row["hostname"] == SPEC["hostname"] and row["boot_id"] == SPEC["boot_id"]
fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}}}'
def ident():
    command = bounded(["docker", "inspect", "--format", fmt, SPEC["container_id"]], 3)
    assert command["returncode"] == 0 and not command["timed_out"]
    ci = json.loads(command["stdout"])
    assert ci["id"] == SPEC["container_id"] and ci["started"] == SPEC["container_started"] and ci["running"] is True and ci["image"] == SPEC["image_id"] and ci["task_label"] == "v0.1.23.3/task2/three_node_recovery"
    return command
row["identity_before"] = ident()
row["static_command"] = bounded(["docker", "exec", "-i", SPEC["container_id"], "python3", "/dev/stdin"], 5, CONTAINER_SOURCE.encode())
assert row["static_command"]["returncode"] == 0 and not row["static_command"]["timed_out"]
row["static_env"] = json.loads(row["static_command"]["stdout"])
row["identity_after"] = ident()
row["identity_passed"] = True
print(json.dumps(row))
