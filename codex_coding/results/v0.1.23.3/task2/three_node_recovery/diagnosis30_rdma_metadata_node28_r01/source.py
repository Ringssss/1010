"""Read current vendor network plugin metadata once per exact container, without loading it."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import collect_storage_env as cpu

NODES={"node28":{"hostname":"m03r2n08","boot":"5313e6f8-1712-45dc-9855-4c47358b6c4b","cid":"e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba","started":"2026-10-09T06:19:44.292587393Z","pidns":"pid:[4026541407]"},
       "node30":{"hostname":"m03r2n10","boot":cpu.BOOT,"cid":cpu.CID,"started":cpu.STARTED,"pidns":"pid:[4026541131]"}}
CONTAINER=r'''
import datetime,hashlib,json,os,resource,signal,stat
from pathlib import Path
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
signal.alarm(6)
assert os.uname().nodename==SPEC["hostname"] and Path("/proc/sys/kernel/random/boot_id").read_text().strip()==SPEC["boot"]
assert os.readlink("/proc/self/ns/pid")==SPEC["pidns"]
rows=[]
for name in ("/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so","/opt/rccl-rdma-sharp-plugins/lib/librccl-net.so","/usr/lib/x86_64-linux-gnu/libshca.so","/usr/lib/x86_64-linux-gnu/libibverbs.so.1"):
    path=Path(name)
    row={"path":name}
    rows.append(row)
    try:
        resolved=path.resolve(strict=True)
        meta=resolved.stat()
        assert stat.S_ISREG(meta.st_mode) and meta.st_size<=8*1024*1024
        digest=hashlib.sha256()
        with resolved.open("rb") as handle:
            while True:
                chunk=handle.read(65536)
                if not chunk:break
                digest.update(chunk)
        row.update(exists=True,resolved=str(resolved),bytes=meta.st_size,mode=oct(stat.S_IMODE(meta.st_mode)),sha256=digest.hexdigest())
    except OSError as exc:
        row.update(exists=False,error=type(exc).__name__,errno=exc.errno,message=str(exc))
env={key:os.environ.get(key) for key in ("LD_LIBRARY_PATH","NCCL_NET_PLUGIN","NCCL_NET","NCCL_IB_HCA","HSA_FORCE_FINE_GRAIN_PCIE","NCCL_IB_DISABLE","NCCL_SOCKET_IFNAME")}
print(json.dumps({"sampled_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"files":rows,"env_whitelist":env,"hostname":os.uname().nodename,"boot_id":SPEC["boot"],"pid_namespace":SPEC["pidns"],"gpu_operations":0,"library_loads":0,"device_open_calls":0,"communication_operations":0,"file_writes":0}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node",choices=tuple(NODES),required=True)
    args=parser.parse_args()
    spec=NODES[args.node]
    base=Path(__file__).resolve().parents[7]/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out=base/("diagnosis30_rdma_metadata_"+args.node+"_r01")
    out.mkdir(exist_ok=False)
    payload="SPEC="+repr(spec)+"\n"+CONTAINER
    code="import os,json,resource,signal,subprocess,time\nfrom pathlib import Path\nKEYS="+repr(cpu.KEYS)+"\nSPEC="+repr(spec)+"\nPAYLOAD="+repr(payload)+"\n"
    code+=inspect.getsource(cpu.proc)+"\n"+inspect.getsource(cpu.bounded)+"\n"
    code+='''
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
'''
    compile(code,"readonly_host.py","exec")
    (out/"readonly_host.py").write_text(code)
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    argv=["ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1","-o","Hostname=12.11.3."+args.node[4:],args.node,"python3 /dev/stdin"]
    (out/"command.json").write_text(json.dumps({"argv":argv,"stdin_source_sha256":hashlib.sha256(code.encode()).hexdigest(),"gpu_operations":0,"deadline_seconds":20},indent=2)+"\n")
    row=cpu.bounded(argv,20,code.encode())
    stdout=row.pop("stdout")
    (out/"stdout.json").write_text(stdout)
    (out/"stderr.txt").write_text(row.pop("stderr"))
    (out/"result.json").write_text(json.dumps(row,indent=2)+"\n")
    data=json.loads(stdout)
    assert row["returncode"]==0 and data["container_metadata"] is not None and data["command"]["returncode"]==0
    plugin=next(item for item in data["container_metadata"]["files"] if item["path"].endswith("librccl-net-shca.so"))
    print(json.dumps({"out":str(out),"plugin":plugin,"gpu_operations":0}))


if __name__=="__main__":main()
