import os,json,resource,signal,subprocess,time
from pathlib import Path
KEYS=('pid', 'start_time', 'pgid', 'sid', 'argv')
SPEC={'hostname': 'm03r2n08', 'boot': '5313e6f8-1712-45dc-9855-4c47358b6c4b', 'cid': 'e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba', 'started': '2026-10-09T06:19:44.292587393Z', 'pidns': 'pid:[4026541407]'}
PAYLOAD='SPEC={\'hostname\': \'m03r2n08\', \'boot\': \'5313e6f8-1712-45dc-9855-4c47358b6c4b\', \'cid\': \'e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba\', \'started\': \'2026-10-09T06:19:44.292587393Z\', \'pidns\': \'pid:[4026541407]\'}\n\nimport datetime,hashlib,json,os,resource,signal,stat\nfrom pathlib import Path\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\nsignal.alarm(6)\nassert os.uname().nodename==SPEC["hostname"] and Path("/proc/sys/kernel/random/boot_id").read_text().strip()==SPEC["boot"]\nassert os.readlink("/proc/self/ns/pid")==SPEC["pidns"]\nrows=[]\nfor name in ("/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so","/opt/rccl-rdma-sharp-plugins/lib/librccl-net.so","/usr/lib/x86_64-linux-gnu/libshca.so","/usr/lib/x86_64-linux-gnu/libibverbs.so.1"):\n    path=Path(name)\n    row={"path":name}\n    rows.append(row)\n    try:\n        resolved=path.resolve(strict=True)\n        meta=resolved.stat()\n        assert stat.S_ISREG(meta.st_mode) and meta.st_size<=8*1024*1024\n        digest=hashlib.sha256()\n        with resolved.open("rb") as handle:\n            while True:\n                chunk=handle.read(65536)\n                if not chunk:break\n                digest.update(chunk)\n        row.update(exists=True,resolved=str(resolved),bytes=meta.st_size,mode=oct(stat.S_IMODE(meta.st_mode)),sha256=digest.hexdigest())\n    except OSError as exc:\n        row.update(exists=False,error=type(exc).__name__,errno=exc.errno,message=str(exc))\nenv={key:os.environ.get(key) for key in ("LD_LIBRARY_PATH","NCCL_NET_PLUGIN","NCCL_NET","NCCL_IB_HCA","HSA_FORCE_FINE_GRAIN_PCIE","NCCL_IB_DISABLE","NCCL_SOCKET_IFNAME")}\nprint(json.dumps({"sampled_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"files":rows,"env_whitelist":env,"hostname":os.uname().nodename,"boot_id":SPEC["boot"],"pid_namespace":SPEC["pidns"],"gpu_operations":0,"library_loads":0,"device_open_calls":0,"communication_operations":0,"file_writes":0}))\n'
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


resource.setrlimit(resource.RLIMIT_CORE,(0,0))
assert os.uname().nodename==SPEC["hostname"] and Path("/proc/sys/kernel/random/boot_id").read_text().strip()==SPEC["boot"]
fmt='{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}}}'
def identity():
    row=bounded(["docker","inspect","--format",fmt,SPEC["cid"]],3)
    assert row["returncode"]==0 and not row["timed_out"]
    ci=json.loads(row["stdout"])
    assert ci["id"]==SPEC["cid"] and ci["started"]==SPEC["started"] and ci["running"] is True and ci["image"]=="sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588" and ci["task_label"]=="v0.1.23.3/task2/three_node_recovery"
    return row
before=identity()
run=bounded(["docker","exec","-i","-e","PYTHONDONTWRITEBYTECODE=1",SPEC["cid"],"python3","/dev/stdin"],8,PAYLOAD.encode())
after=identity()
print(json.dumps({"identity_before":before,"identity_after":after,"command":run,"container_metadata":json.loads(run["stdout"]) if run["returncode"]==0 else None,"gpu_operations":0}))
