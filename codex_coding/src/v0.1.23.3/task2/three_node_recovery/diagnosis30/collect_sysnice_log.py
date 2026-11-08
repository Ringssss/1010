"""Read one new SYS_NICE container's child13 log and CPU security metadata once."""
import hashlib
import inspect
import json
from pathlib import Path

import collect_storage_env as cpu

CID = "bcee43e68fc88a6bcea76cc58a11241646bd5b2d5605aaab7a7cda156a675d99"
STARTED = "2026-10-09T06:36:37.844554212Z"
BOOT = "5313e6f8-1712-45dc-9855-4c47358b6c4b"
PAYLOAD = r'''
import datetime, hashlib, json, os, resource, signal, stat
from pathlib import Path
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
signal.alarm(5)
assert os.uname().nodename == "m03r2n08"
assert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == "5313e6f8-1712-45dc-9855-4c47358b6c4b"
assert os.readlink("/proc/self/ns/pid") == "pid:[4026541416]"
status = {line.partition(":")[0]: line.partition(":")[2].strip() for line in Path("/proc/self/status").read_text().splitlines() if line.partition(":")[0] in ("CapEff", "CapBnd", "Seccomp", "Seccomp_filters", "NoNewPrivs", "Uid", "Gid")}
row = {"sampled_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": status, "sys_nice_effective": bool(int(status["CapEff"], 16)&(1<<23)), "sys_nice_bounding": bool(int(status["CapBnd"],16)&(1<<23)), "logs": [], "gpu_operations": 0, "remote_file_writes": 0, "signals_to_processes": 0}
since = datetime.datetime.fromisoformat("2026-10-09T06:36:37.844554+00:00").timestamp()
try:
    names = sorted(p for p in Path("/var/log/hylog").iterdir() if p.name.endswith("_13.log"))
    row["matching_names_total"] = len(names)
    for path in names[:32]:
        item = {"path": str(path)}
        row["logs"].append(item)
        try:
            meta = path.lstat()
            item.update(size=meta.st_size, mtime_ns=meta.st_mtime_ns)
            if not stat.S_ISREG(meta.st_mode) or meta.st_mtime < since:
                item["skipped"] = "not regular or predates exact container StartedAt"
                continue
            fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
            try:
                raw = os.read(fd, 65537)
            finally:
                os.close(fd)
            item.update(text=raw[:65536].decode(errors="replace"), truncated=len(raw)>65536, captured_sha256=hashlib.sha256(raw[:65536]).hexdigest())
        except OSError as exc:
            item.update(error=type(exc).__name__, errno=exc.errno, message=str(exc))
except OSError as exc:
    row["directory_error"] = {"error": type(exc).__name__, "errno": exc.errno, "message": str(exc)}
row["mbind_blocked_text_present"] = any("mbind is blocked by seccomp" in item.get("text", "") for item in row["logs"])
print(json.dumps(row))
'''


def main():
    base = Path(__file__).resolve().parents[6]/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = base/"diagnosis30_sysnice_log_r01"
    out.mkdir(exist_ok=False)
    code = "import os,json,resource,signal,subprocess,time\nfrom pathlib import Path\n"
    code += "KEYS="+repr(cpu.KEYS)+"\nCID="+repr(CID)+"\nSTARTED="+repr(STARTED)+"\nBOOT="+repr(BOOT)+"\nPAYLOAD="+repr(PAYLOAD)+"\n"
    code += inspect.getsource(cpu.proc)+"\n"+inspect.getsource(cpu.bounded)+"\n"
    code += '''
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
'''
    compile(code, "readonly_host.py", "exec")
    (out/"readonly_host.py").write_text(code)
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    argv = ["ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1","-o","Hostname=12.11.3.28","node28","python3 /dev/stdin"]
    (out/"command.json").write_text(json.dumps({"argv":argv,"stdin_source_sha256":hashlib.sha256(code.encode()).hexdigest(),"deadline_seconds":20,"gpu_operations":0},indent=2)+"\n")
    result = cpu.bounded(argv,20,code.encode())
    stdout = result.pop("stdout")
    (out/"stdout.json").write_text(stdout)
    (out/"stderr.txt").write_text(result.pop("stderr"))
    (out/"result.json").write_text(json.dumps(result,indent=2)+"\n")
    assert result["returncode"]==0 and not result["timed_out"]
    data = json.loads(stdout)["container_result"]
    print(json.dumps({"out":str(out),"sys_nice_effective":data["sys_nice_effective"],"seccomp":data["status"]["Seccomp"],"mbind_blocked_text_present":data["mbind_blocked_text_present"],"captured_logs":len(data["logs"]),"gpu_operations":0}))


if __name__ == "__main__":
    main()
