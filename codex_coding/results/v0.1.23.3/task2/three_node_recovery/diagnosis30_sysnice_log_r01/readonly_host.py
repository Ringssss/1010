import os,json,resource,signal,subprocess,time
from pathlib import Path
KEYS=('pid', 'start_time', 'pgid', 'sid', 'argv')
CID='bcee43e68fc88a6bcea76cc58a11241646bd5b2d5605aaab7a7cda156a675d99'
STARTED='2026-10-09T06:36:37.844554212Z'
BOOT='5313e6f8-1712-45dc-9855-4c47358b6c4b'
PAYLOAD='\nimport datetime, hashlib, json, os, resource, signal, stat\nfrom pathlib import Path\nresource.setrlimit(resource.RLIMIT_CORE, (0, 0))\nsignal.alarm(5)\nassert os.uname().nodename == "m03r2n08"\nassert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == "5313e6f8-1712-45dc-9855-4c47358b6c4b"\nassert os.readlink("/proc/self/ns/pid") == "pid:[4026541416]"\nstatus = {line.partition(":")[0]: line.partition(":")[2].strip() for line in Path("/proc/self/status").read_text().splitlines() if line.partition(":")[0] in ("CapEff", "CapBnd", "Seccomp", "Seccomp_filters", "NoNewPrivs", "Uid", "Gid")}\nrow = {"sampled_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": status, "sys_nice_effective": bool(int(status["CapEff"], 16)&(1<<23)), "sys_nice_bounding": bool(int(status["CapBnd"],16)&(1<<23)), "logs": [], "gpu_operations": 0, "remote_file_writes": 0, "signals_to_processes": 0}\nsince = datetime.datetime.fromisoformat("2026-10-09T06:36:37.844554+00:00").timestamp()\ntry:\n    names = sorted(p for p in Path("/var/log/hylog").iterdir() if p.name.endswith("_13.log"))\n    row["matching_names_total"] = len(names)\n    for path in names[:32]:\n        item = {"path": str(path)}\n        row["logs"].append(item)\n        try:\n            meta = path.lstat()\n            item.update(size=meta.st_size, mtime_ns=meta.st_mtime_ns)\n            if not stat.S_ISREG(meta.st_mode) or meta.st_mtime < since:\n                item["skipped"] = "not regular or predates exact container StartedAt"\n                continue\n            fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW)\n            try:\n                raw = os.read(fd, 65537)\n            finally:\n                os.close(fd)\n            item.update(text=raw[:65536].decode(errors="replace"), truncated=len(raw)>65536, captured_sha256=hashlib.sha256(raw[:65536]).hexdigest())\n        except OSError as exc:\n            item.update(error=type(exc).__name__, errno=exc.errno, message=str(exc))\nexcept OSError as exc:\n    row["directory_error"] = {"error": type(exc).__name__, "errno": exc.errno, "message": str(exc)}\nrow["mbind_blocked_text_present"] = any("mbind is blocked by seccomp" in item.get("text", "") for item in row["logs"])\nprint(json.dumps(row))\n'
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
assert os.uname().nodename == "m03r2n08" and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == BOOT
fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}}}'
def identity():
    result = bounded(["docker", "inspect", "--format", fmt, CID], 3)
    assert result["returncode"] == 0 and not result["timed_out"]
    ci = json.loads(result["stdout"])
    assert ci["id"] == CID and ci["started"] == STARTED and ci["running"] is True and ci["image"] == "sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588" and ci["task_label"] == "v0.1.23.3/task2/three_node_recovery"
    return result
before = identity()
command = bounded(["docker", "exec", "-i", CID, "python3", "/dev/stdin"], 7, PAYLOAD.encode())
after = identity()
print(json.dumps({"identity_before":before,"identity_after":after,"command":command,"container_result":json.loads(command["stdout"]) if command["returncode"]==0 else None,"identity_passed":True,"gpu_operations":0,"remote_file_writes":0}))
