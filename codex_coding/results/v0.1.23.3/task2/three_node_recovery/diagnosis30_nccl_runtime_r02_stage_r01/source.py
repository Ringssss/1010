"""Stage exactly the new reviewed r02 runtime in node30's existing owned shm tree."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path

import collect_storage_env as cpu


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--expected-sha",required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[6]
    base=root/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out=base/"diagnosis30_nccl_runtime_r02_stage_r01"
    out.mkdir(exist_ok=False)
    raw=args.bundle.read_bytes()
    bundle=json.loads(raw)
    assert bundle["node"]=="node30" and bundle["root"]=="/dev/shm/fluidckpt_diag30_r01/project" and len(bundle["assets"])==1
    assert bundle["assets"][0]["sha256"]==args.expected_sha
    payload="BUNDLE="+repr(bundle)+"\nEXPECTED_SHA="+repr(args.expected_sha)+"\n"+r'''
import base64,hashlib,json,os,resource,signal
from pathlib import Path
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
signal.alarm(8)
assert os.uname().nodename=="m03r2n10" and Path("/proc/sys/kernel/random/boot_id").read_text().strip()=="d9d6a6fb-013f-49ac-98f9-e77eca67952e"
assert os.readlink("/proc/self/ns/pid")=="pid:[4026541131]"
root=Path("/dev/shm/fluidckpt_diag30_r01/project")
assert root.is_dir() and root.resolve()==root and BUNDLE["root"]==str(root)
runtime_rel=Path("codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r02.py")
fs=os.statvfs("/dev/shm")
assert fs.f_bavail*fs.f_frsize>=64*1024*1024
decoded=[]
seen=set()
for asset in BUNDLE["assets"]:
    rel=Path(asset["relative_path"])
    assert not rel.is_absolute() and ".." not in rel.parts and str(rel) not in seen
    assert rel==runtime_rel
    seen.add(str(rel))
    data=base64.b64decode(asset["base64"],validate=True)
    assert len(data)<=1048576 and hashlib.sha256(data).hexdigest()==asset["sha256"]
    if rel==runtime_rel:
        assert asset["sha256"]==EXPECTED_SHA
        compile(data,str(root/rel),"exec")
    decoded.append((rel,data,asset["sha256"]))
assert str(runtime_rel) in seen
rows=[]
for rel,data,digest in decoded:
    target=root/rel
    assert not target.exists() and not target.is_symlink()
    for parent in target.parents:
        if parent==root:break
        assert not parent.is_symlink()
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("xb") as handle:handle.write(data)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
    rows.append({"path":str(target),"bytes":len(data),"sha256":digest})
after=os.statvfs("/dev/shm")
print(json.dumps({"staged_assets":rows,"shm_available_after":after.f_bavail*after.f_frsize,"gpu_operations":0,"write_scope":"exactly one new runtime_gate_r02.py under existing owned shm project","signals_to_processes":0}))
'''
    code="import os,json,resource,signal,subprocess,time\nfrom pathlib import Path\nKEYS="+repr(cpu.KEYS)+"\nPAYLOAD="+repr(payload)+"\n"
    code+=inspect.getsource(cpu.proc)+"\n"+inspect.getsource(cpu.bounded)+"\n"
    code+='''
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
assert os.uname().nodename=="m03r2n10" and Path("/proc/sys/kernel/random/boot_id").read_text().strip()=="d9d6a6fb-013f-49ac-98f9-e77eca67952e"
cid="7d3143da42c4c16a9f48e61189035e48a2dc3d9f177cd6207cb9b7f447120695"
fmt='{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}}}'
def identity():
    row=bounded(["docker","inspect","--format",fmt,cid],3)
    assert row["returncode"]==0 and not row["timed_out"]
    ci=json.loads(row["stdout"])
    assert ci["id"]==cid and ci["started"]=="2026-10-08T08:19:06.916290051Z" and ci["running"] is True and ci["image"]=="sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588" and ci["task_label"]=="v0.1.23.3/task2/three_node_recovery"
    return row
before=identity()
run=bounded(["docker","exec","-i","-e","PYTHONDONTWRITEBYTECODE=1",cid,"python3","/dev/stdin"],10,PAYLOAD.encode())
after=identity()
print(json.dumps({"identity_before":before,"identity_after":after,"run":run,"stage_result":json.loads(run["stdout"]) if run["returncode"]==0 else None,"gpu_operations":0}))
'''
    compile(code,"nccl_stage_host.py","exec")
    (out/"nccl_stage_host.py").write_text(code)
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    (out/"bundle_ref.json").write_text(json.dumps({"path":str(args.bundle.resolve()),"sha256":hashlib.sha256(raw).hexdigest(),"node":"node30","gpu_operations":0},indent=2)+"\n")
    argv=["ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1","-o","Hostname=12.11.3.30","node30","python3 /dev/stdin"]
    row=cpu.bounded(argv,22,code.encode())
    stdout=row.pop("stdout")
    (out/"stdout.json").write_text(stdout)
    (out/"stderr.txt").write_text(row.pop("stderr"))
    (out/"result.json").write_text(json.dumps(row,indent=2)+"\n")
    data=json.loads(stdout)
    assert row["returncode"]==0 and data["stage_result"] is not None and data["run"]["returncode"]==0
    print(json.dumps({"out":str(out),"stage_passed":True,"assets":len(data["stage_result"]["staged_assets"]),"shm_available_after":data["stage_result"]["shm_available_after"],"gpu_operations":0}))


if __name__=="__main__":main()
