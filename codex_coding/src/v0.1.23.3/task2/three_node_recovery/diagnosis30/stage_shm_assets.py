"""Copy three frozen CPU namespace assets into the already-owned node30 shm stage."""
import inspect
import json
from pathlib import Path
import shlex

import collect_storage_env as cpu


def main():
    root = Path(__file__).resolve().parents[6]
    base = root/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = base/"diagnosis30_shm_assets_r01"
    out.mkdir(exist_ok=False)
    spec = json.loads((base/"inventory_setup_plan_r06/node30.spec.json").read_text())
    assets = [{"relative_path":row["relative_path"],"sha256":row["sha256"]} for row in spec["assets"]]
    assert len(assets)==3
    payload = "ASSETS="+repr(assets)+"\n"+r'''
import hashlib,json,os,resource,signal
from pathlib import Path
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
signal.alarm(8)
assert os.uname().nodename=="m03r2n10"
assert Path("/proc/sys/kernel/random/boot_id").read_text().strip()=="d9d6a6fb-013f-49ac-98f9-e77eca67952e"
assert os.readlink("/proc/self/ns/pid")=="pid:[4026541131]"
source=Path("/home/zhujianian/zhujianian/fluidckpt_zjn")
destination=Path("/dev/shm/fluidckpt_diag30_r01/project")
assert destination.is_dir() and destination.resolve()==destination
assert hashlib.sha256((destination/"codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py").read_bytes()).hexdigest()=="d5331fa21f89df2428bce8faad3130bc41dbaa2a9b17b05fb0e5e042d217c90d"
rows=[]
for asset in ASSETS:
    rel=Path(asset["relative_path"])
    assert not rel.is_absolute() and ".." not in rel.parts
    raw=(source/rel).read_bytes()
    assert len(raw)<=1048576 and hashlib.sha256(raw).hexdigest()==asset["sha256"]
    target=destination/rel
    assert not target.exists() and not target.is_symlink()
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("xb") as handle:handle.write(raw)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==asset["sha256"]
    assert hashlib.sha256((source/rel).read_bytes()).hexdigest()==asset["sha256"]
    rows.append({"source":str(source/rel),"destination":str(target),"bytes":len(raw),"sha256":asset["sha256"]})
fs=os.statvfs("/dev/shm")
print(json.dumps({"copied_assets":rows,"shm_available_after":fs.f_bavail*fs.f_frsize,"file_write_scope":"new exclusively created files under own /dev/shm/fluidckpt_diag30_r01","gpu_operations":0,"signals_to_processes":0}))
'''
    code="import os,json,resource,signal,subprocess,time\nfrom pathlib import Path\nKEYS="+repr(cpu.KEYS)+"\nPAYLOAD="+repr(payload)+"\n"
    code+=inspect.getsource(cpu.proc)+"\n"+inspect.getsource(cpu.bounded)+"\n"
    code+='''
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
'''
    compile(code,"assets_host.py","exec")
    (out/"assets_host.py").write_text(code)
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    opts=["-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1","-o","Hostname=12.11.3.30"]
    argv=["ssh","-T",*opts,"node30","python3 /dev/stdin"]
    row=cpu.bounded(argv,22,code.encode())
    stdout=row.pop("stdout")
    (out/"stdout.json").write_text(stdout)
    (out/"stderr.txt").write_text(row.pop("stderr"))
    (out/"result.json").write_text(json.dumps(row,indent=2)+"\n")
    data=json.loads(stdout)
    assert row["returncode"]==0 and data["assets_result"] is not None and data["run"]["returncode"]==0
    shm="/dev/shm/fluidckpt_diag30_r01/project"
    remote=["docker","exec","-e","PYTHONDONTWRITEBYTECODE=1",cpu.CID,"python3",shm+"/codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/container_namespace.py","--host",shm+"/codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_frozen_isolated_r01/node30.json","--out",shm+"/codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_namespace_node30_nccl_r01"]
    command=["timeout","--signal=TERM","12s","ssh","-T",*opts,"node30",shlex.join(remote)]
    (out/"namespace_command.sh").write_text("#!/bin/bash\nset -u\nulimit -c 0\n"+shlex.join(command)+"\n")
    (out/"namespace_launch.json").write_text(json.dumps({"prepared_not_executed":True,"argv":command,"remote_argv":remote,"gpu_operations":0,"preserved_frozen_assets":assets,"parent_exact_guard_required":True},indent=2)+"\n")
    print(json.dumps({"out":str(out),"assets_copied":len(assets),"shm_available_after":data["assets_result"]["shm_available_after"],"namespace_prepared":True,"gpu_operations":0}))


if __name__=="__main__":main()
