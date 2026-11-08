import os,json,resource,signal,subprocess,time
from pathlib import Path
KEYS=('pid', 'start_time', 'pgid', 'sid', 'argv')
PAYLOAD='ASSETS=[{\'relative_path\': \'codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/init_gate.py\', \'sha256\': \'927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b\'}, {\'relative_path\': \'codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/container_namespace.py\', \'sha256\': \'0c30726b7a6c8bc36949084c1df7543c8ea5821eef6ccb75796b439ec008cf2a\'}, {\'relative_path\': \'codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_frozen_isolated_r01/node30.json\', \'sha256\': \'8da563c2a306f3d3cb8c6287f2c620b8ce76802eb6a6a6d1dd1014f8260c871a\'}]\n\nimport hashlib,json,os,resource,signal\nfrom pathlib import Path\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\nsignal.alarm(8)\nassert os.uname().nodename=="m03r2n10"\nassert Path("/proc/sys/kernel/random/boot_id").read_text().strip()=="d9d6a6fb-013f-49ac-98f9-e77eca67952e"\nassert os.readlink("/proc/self/ns/pid")=="pid:[4026541131]"\nsource=Path("/home/zhujianian/zhujianian/fluidckpt_zjn")\ndestination=Path("/dev/shm/fluidckpt_diag30_r01/project")\nassert destination.is_dir() and destination.resolve()==destination\nassert hashlib.sha256((destination/"codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py").read_bytes()).hexdigest()=="d5331fa21f89df2428bce8faad3130bc41dbaa2a9b17b05fb0e5e042d217c90d"\nrows=[]\nfor asset in ASSETS:\n    rel=Path(asset["relative_path"])\n    assert not rel.is_absolute() and ".." not in rel.parts\n    raw=(source/rel).read_bytes()\n    assert len(raw)<=1048576 and hashlib.sha256(raw).hexdigest()==asset["sha256"]\n    target=destination/rel\n    assert not target.exists() and not target.is_symlink()\n    target.parent.mkdir(parents=True,exist_ok=True)\n    with target.open("xb") as handle:handle.write(raw)\n    assert hashlib.sha256(target.read_bytes()).hexdigest()==asset["sha256"]\n    assert hashlib.sha256((source/rel).read_bytes()).hexdigest()==asset["sha256"]\n    rows.append({"source":str(source/rel),"destination":str(target),"bytes":len(raw),"sha256":asset["sha256"]})\nfs=os.statvfs("/dev/shm")\nprint(json.dumps({"copied_assets":rows,"shm_available_after":fs.f_bavail*fs.f_frsize,"file_write_scope":"new exclusively created files under own /dev/shm/fluidckpt_diag30_r01","gpu_operations":0,"signals_to_processes":0}))\n'
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
assert os.uname().nodename=="m03r2n10" and Path("/proc/sys/kernel/random/boot_id").read_text().strip()=="d9d6a6fb-013f-49ac-98f9-e77eca67952e"
cid="7d3143da42c4c16a9f48e61189035e48a2dc3d9f177cd6207cb9b7f447120695"
fmt='{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}}}'
def identity():
    row=bounded(["docker","inspect","--format",fmt,cid],3)
    assert row["returncode"]==0 and not row["timed_out"]
    ci=json.loads(row["stdout"])
    assert ci["id"]==cid and ci["started"]=="2026-10-08T08:19:06.916290051Z" and ci["running"] is True and ci["image"]=="sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588"
    return row
before=identity()
run=bounded(["docker","exec","-i","-e","PYTHONDONTWRITEBYTECODE=1",cid,"python3","/dev/stdin"],10,PAYLOAD.encode())
after=identity()
print(json.dumps({"identity_before":before,"identity_after":after,"run":run,"assets_result":json.loads(run["stdout"]) if run["returncode"]==0 else None,"gpu_operations":0}))
