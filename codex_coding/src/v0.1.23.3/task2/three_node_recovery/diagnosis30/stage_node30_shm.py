"""Stage an unchanged 60s gate in node30's existing private shm using stdin only."""
import argparse
import base64
import hashlib
import inspect
import json
from pathlib import Path
import shlex

import collect_storage_env as cpu

CID = cpu.CID
STARTED = cpu.STARTED
BOOT = cpu.BOOT
SHM_ROOT = "/dev/shm/fluidckpt_diag30_r01/project"
REL = "codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py"
CONTAINER = r'''
import base64, datetime, hashlib, json, os, resource, signal, stat
from pathlib import Path
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
signal.alarm(8)
assert os.uname().nodename == "m03r2n10"
assert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == "d9d6a6fb-013f-49ac-98f9-e77eca67952e"
assert os.readlink("/proc/self/ns/pid") == "pid:[4026541131]"
shm = Path("/dev/shm")
assert shm.resolve() == shm and not shm.is_symlink()
fs = os.statvfs(shm)
assert fs.f_bavail*fs.f_frsize >= 64*1024*1024
assert fs.f_blocks*fs.f_frsize == 1073741824
root = Path("/dev/shm/fluidckpt_diag30_r01/project")
stage = root.parent
assert not stage.exists() and not stage.is_symlink()
original = Path("/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/init_gate.py")
assert hashlib.sha256(original.read_bytes()).hexdigest() == "927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b"
raw = base64.b64decode(SOURCE_BASE64, validate=True)
assert hashlib.sha256(raw).hexdigest() == "d5331fa21f89df2428bce8faad3130bc41dbaa2a9b17b05fb0e5e042d217c90d"
destination = root/"codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py"
assert destination.parents[6] == root
compile(raw, str(destination), "exec")
stage.mkdir(mode=0o700, exist_ok=False)
destination.parent.mkdir(parents=True, exist_ok=False)
with destination.open("xb") as handle:
    handle.write(raw)
assert hashlib.sha256(destination.read_bytes()).hexdigest() == "d5331fa21f89df2428bce8faad3130bc41dbaa2a9b17b05fb0e5e042d217c90d"
original_files = []
for name in ("/usr/local/built-in-508-topo-input-tj-default.xml", "/opt/dtk/rccl/lib/topo_mapping_default.xml"):
    path = Path(name)
    if path.exists():
        assert path.stat().st_size <= 16*1024*1024
        original_files.append({"path":name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    else:
        original_files.append({"path":name,"status":"missing"})
status = {line.partition(":")[0]:line.partition(":")[2].strip() for line in Path("/proc/self/status").read_text().splitlines() if line.partition(":")[0] in ("CapEff","CapBnd","Seccomp","Seccomp_filters","NoNewPrivs","Uid","Gid")}
cgroup = {}
for name in ("memory.current","memory.max","memory.events","pids.current","pids.max"):
    path = Path("/sys/fs/cgroup")/name
    try:
        cgroup[name] = path.read_text().strip()
    except OSError as exc:
        cgroup[name] = {"error":repr(exc)}
after = os.statvfs(shm)
row = {"sampled_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"hostname":os.uname().nodename,"boot_id":Path("/proc/sys/kernel/random/boot_id").read_text().strip(),"pid_namespace":os.readlink("/proc/self/ns/pid"),"shm_total_bytes":fs.f_blocks*fs.f_frsize,"shm_available_before":fs.f_bavail*fs.f_frsize,"shm_available_after":after.f_bavail*after.f_frsize,"staged_source":str(destination),"source_bytes":len(raw),"source_sha256":hashlib.sha256(raw).hexdigest(),"original_source_preserved":hashlib.sha256(original.read_bytes()).hexdigest()=="927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b","future_result":str(root/"codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_node30_diag60_r01"),"original_files":original_files,"security_status":status,"cgroup":cgroup,"file_write_scope":"new exclusively created /dev/shm/fluidckpt_diag30_r01 only","gpu_operations":0,"signals_to_processes":0}
print(json.dumps(row))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-stage", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[6]
    base = root/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = base/"diagnosis30_shm60_stage_r01"
    out.mkdir(exist_ok=False)
    source = Path(__file__).with_name("init_gate_diag60.py").read_bytes()
    assert hashlib.sha256(source).hexdigest() == "d5331fa21f89df2428bce8faad3130bc41dbaa2a9b17b05fb0e5e042d217c90d"
    payload = "SOURCE_BASE64="+repr(base64.b64encode(source).decode())+"\n"+CONTAINER
    code = "import os,json,resource,signal,subprocess,time\nfrom pathlib import Path\n"
    code += "KEYS="+repr(cpu.KEYS)+"\nCID="+repr(CID)+"\nSTARTED="+repr(STARTED)+"\nBOOT="+repr(BOOT)+"\nPAYLOAD="+repr(payload)+"\n"
    code += inspect.getsource(cpu.proc)+"\n"+inspect.getsource(cpu.bounded)+"\n"
    code += '''
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
assert os.uname().nodename == "m03r2n10" and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == BOOT
fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}},"ipc":{{json .HostConfig.IpcMode}},"shm_size":{{json .HostConfig.ShmSize}},"memory":{{json .HostConfig.Memory}},"pids_limit":{{json .HostConfig.PidsLimit}},"caps":{{json .HostConfig.CapAdd}},"privileged":{{json .HostConfig.Privileged}}}'
def identity():
    result = bounded(["docker","inspect","--format",fmt,CID],3)
    assert result["returncode"]==0 and not result["timed_out"]
    ci = json.loads(result["stdout"])
    assert ci["id"]==CID and ci["started"]==STARTED and ci["running"] is True and ci["image"]=="sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588" and ci["task_label"]=="v0.1.23.3/task2/three_node_recovery"
    assert ci["ipc"]=="private" and ci["shm_size"]==1073741824 and ci["memory"]==8589934592 and ci["pids_limit"]==512 and ci["caps"]==["IPC_LOCK"] and ci["privileged"] is False
    return result
before = identity()
command = bounded(["docker","exec","-i","-e","PYTHONDONTWRITEBYTECODE=1",CID,"python3","/dev/stdin"],10,PAYLOAD.encode())
after = identity()
print(json.dumps({"identity_before":before,"identity_after":after,"command":command,"stage_result":json.loads(command["stdout"]) if command["returncode"]==0 else None,"identity_passed":True,"gpu_operations":0}))
'''
    compile(code, "stage_host.py", "exec")
    (out/"stage_host.py").write_text(code)
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    options = ["-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1","-o","Hostname=12.11.3.30"]
    ssh = ["ssh","-T",*options,"node30","python3 /dev/stdin"]
    (out/"stage_command.json").write_text(json.dumps({"argv":ssh,"stdin_sha256":hashlib.sha256(code.encode()).hexdigest(),"execute_stage":args.execute_stage,"deadline_seconds":22,"gpu_operations":0},indent=2)+"\n")
    remote = ["docker","exec","-e","OMP_NUM_THREADS=1","-e","MKL_NUM_THREADS=1","-e","PYTHONDONTWRITEBYTECODE=1",CID,"python3",SHM_ROOT+"/"+REL,"--node","node30","--container-id",CID,"--container-started-at",STARTED,"--host-boot-id",BOOT,"--expected-container-hostname","m03r2n10","--gpu","0","--bdf","0000:09:00.0","--visibility","selected","--stage","init","--out",SHM_ROOT+"/codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_node30_diag60_r01"]
    launch = ["timeout","--signal=TERM","75s","ssh","-T",*options,"node30",shlex.join(remote)]
    (out/"command.sh").write_text("#!/bin/bash\nset -u\nulimit -c 0\n"+shlex.join(launch)+"\n")
    (out/"launch.json").write_text(json.dumps({"executed_gpu":False,"argv":launch,"remote_argv":remote,"source_sha256":hashlib.sha256(source).hexdigest(),"out_in_container":remote[-1],"deadline_seconds":60,"outer_timeout_seconds":75,"parent_fresh_owner_and_resource_guard_required":True,"identity_context":"same original node30 private container; source/results relocated to own private shm plus 60s source; not a strict comparison to yesterday"},indent=2)+"\n")
    if args.execute_stage:
        result = cpu.bounded(ssh,22,code.encode())
        stdout = result.pop("stdout")
        (out/"stdout.json").write_text(stdout)
        (out/"stderr.txt").write_text(result.pop("stderr"))
        (out/"result.json").write_text(json.dumps(result,indent=2)+"\n")
        assert result["returncode"]==0 and not result["timed_out"]
        data = json.loads(stdout)
        assert data["stage_result"] is not None and data["command"]["returncode"]==0
        print(json.dumps({"out":str(out),"stage_passed":True,"shm_available_after":data["stage_result"]["shm_available_after"],"source_sha256":data["stage_result"]["source_sha256"],"gpu_operations":0}))


if __name__ == "__main__":
    main()
