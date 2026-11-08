"""Fresh isolated container setup and metadata guard, invoked only by parent."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

IMAGE = "sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588"
PROJECT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
DEVICES = ("/dev/kfd", "/dev/mkfd", "/dev/dri", "/dev/infiniband")

def now():
    return datetime.now(timezone.utc).isoformat()

def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error":repr(exc)}

def command(argv, timeout=5):
    p = subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        stdout,stderr = p.communicate(timeout=timeout)
        return {"argv":argv,"returncode":p.returncode,"stdout":stdout.decode(errors="replace"),"stderr":stderr.decode(errors="replace")}
    except subprocess.TimeoutExpired:
        # Kill only this newly created CLI, then use a bounded reap.
        p.kill()
        try:
            stdout,stderr = p.communicate(timeout=1)
            return {"argv":argv,"returncode":p.returncode,"timed_out":True,"cleanup_signal":"KILL own new CLI","stdout":stdout.decode(errors="replace"),"stderr":stderr.decode(errors="replace")}
        except subprocess.TimeoutExpired:
            return {"argv":argv,"returncode":None,"timed_out":True,"pending_cli_pid":p.pid,"stdout":"","stderr":""}

def inspected(name):
    row = command(["docker","inspect",name])
    if row["returncode"] != 0:
        return {"command":row}
    ci=json.loads(row.pop("stdout"))[0]
    return {"id":ci["Id"],"name":ci["Name"],"image_id":ci["Image"],"created":ci["Created"],"State":ci["State"],"HostConfig":{k:ci["HostConfig"].get(k) for k in ("Privileged","NetworkMode","IpcMode","Memory","PidsLimit","ShmSize","CapAdd","Devices","Ulimits")},"mounts":[{k:m.get(k) for k in ("Type","Source","Destination","RW")} for m in ci.get("Mounts",[])],"command":row}

def guard(spec):
    rows=[];errors=[]
    root=Path("/sys/class/kfd/kfd/proc")
    try:
        pids=sorted(root.iterdir(),key=lambda p:p.name)
    except OSError as exc:
        pids=[];errors.append(repr(exc))
    for p in pids:
        if not p.name.isdigit():continue
        stat=read(f"/proc/{p.name}/stat")
        fields=stat.rsplit(") ",1)[1].split() if isinstance(stat,str) else []
        try:
            values={q.name:read(q) for q in p.iterdir() if re.fullmatch(r"vram_[0-9]+|pasid",q.name)}
        except OSError as exc:
            values={"error":repr(exc)}
        rows.append({"pid":int(p.name),"start_time_ticks":fields[19] if fields else None,"state":fields[0] if fields else None,"comm":read(f"/proc/{p.name}/comm"),"cgroup":read(f"/proc/{p.name}/cgroup"),"kfd_vram":values})
    files={}
    for asset in spec["assets"]:
        p=Path(spec["stage"])/"project"/asset["relative_path"]
        files[asset["relative_path"]]={"sha256":hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None,"expected_sha256":asset["sha256"]}
    keeper_endpoints=set(spec.get("keeper_endpoints",[]));discovery=[]
    for expected in spec.get("known_live_workers",[]):
        pid=expected["pid"];stat=read(f"/proc/{pid}/stat")
        fields=stat.rsplit(") ",1)[1].split() if isinstance(stat,str) else []
        cg=read(f"/proc/{pid}/cgroup")
        row={"pid":pid,"start_time_ticks":fields[19] if fields else None,"owner_matches":bool(fields and fields[19]==expected["start_time_ticks"] and cg==expected["cgroup"])}
        if row["owner_matches"]:
            try:
                argv=Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
                args={arg:argv[i+1] for i,arg in enumerate(argv[:-1]) if arg in ("--node","--gpu","--port-base")}
                row["args"]=args
                if args.get("--node")=="node28" and int(args.get("--gpu","-1")) in range(2,8) and args.get("--port-base") in ("29700","39700"):
                    endpoint=f"http://12.11.3.28:{int(args['--port-base'])+int(args['--gpu'])}"
                    row["endpoint"]=endpoint
                else:row["discovery_error"]="known keeper args missing/unsupported; no inferred RPC"
            except (OSError,ValueError) as exc:row["discovery_error"]=repr(exc)
        discovery.append(row)
    def keeper(endpoint):
        if spec["node"]!="node28" or not re.fullmatch(r"http://12\.11\.3\.28:[23]970[2-7]",endpoint):
            raise ValueError("keeper endpoint outside node28 retained set")
        row={"endpoint":endpoint,"started_utc":now(),"timeout_s":2}
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(endpoint+"/inventory?compact=1",timeout=2) as response:
                raw=response.read(1048577)
                if len(raw)>1048576:raise ValueError("keeper response exceeds bound")
            body=json.loads(raw)
            canonical={k:body.get(k) for k in ("worker","node","gpu","numa","epoch","session","pid","budget_bytes","used_bytes","replicas","transactions","transaction_phases","staging_bytes","hbm_allocated_bytes","hbm_reserved_bytes")}
            row.update(passed=True,body=body,raw_sha256=hashlib.sha256(raw).hexdigest(),catalog_sha256=hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(",",":")).encode()).hexdigest())
        except Exception as exc:row.update(passed=False,error=repr(exc))
        row["ended_utc"]=now();return row
    with ThreadPoolExecutor(max_workers=3) as pool:
        keepers=list(pool.map(keeper,sorted(keeper_endpoints)))
    container=inspected(spec["container_name"])
    cg=read(f"/proc/{container.get('State',{}).get('Pid',0)}/cgroup")
    cgroup_memory={"proc_cgroup":cg}
    if isinstance(cg,str):
        v2=next((line.split(":",2)[2] for line in cg.splitlines() if line.startswith("0::")),None)
        if v2:
            root=Path("/sys/fs/cgroup")/v2.lstrip("/")
            cgroup_memory["files"]={key:read(root/key) for key in ("memory.current","memory.max","memory.events","pids.current","pids.max")}
    gpu=Path("/sys/bus/pci/devices")/spec["bdf"]
    return {"started_utc":now(),"hostname":os.uname().nodename,"boot_id":read("/proc/sys/kernel/random/boot_id"),"host_memory":read("/proc/meminfo"),"selected_gpu_sysfs":{key:read(gpu/key) for key in ("mem_info_vram_total","mem_info_vram_used","numa_node")},"container_cgroup":cgroup_memory,"container":container,"protected_containers":[inspected(name) for name in spec.get("protected_containers",[])],"owners":rows,"visibility_errors":errors,"source_assets":files,"gpu_api_calls":0,"keeper_rpc_calls":len(keepers),"keepers":keepers,"keeper_process_discovery":discovery,"scope":"metadata/catalog only; canonical catalog excludes dynamic free/sample fields; no model-content reread; additional keep-v02 six identities have no catalog RPC"}

def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    spec=json.loads(Path(sys.argv[2]).read_text())
    mode=sys.argv[1]
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=spec["host_wrapper_sha256"]:
        raise SystemExit("uploaded host wrapper SHA mismatch")
    if spec["node"] not in ("node28","node30") or os.uname().nodename != spec["hostname"] or read("/proc/sys/kernel/random/boot_id") != spec["boot_id"]:
        raise SystemExit("fresh host identity mismatch")
    if spec["image_id"] != IMAGE or not re.fullmatch(r"zjn-fluid-three-node-[a-z0-9_]+-n(28|30)",spec["container_name"]):
        raise SystemExit("unexpected image/name")
    stage=Path(spec["stage"])
    if stage.parent != Path("/tmp/fluidckpt_three_node") or not re.fullmatch(r"[a-z0-9_]+_n(28|30)",stage.name):
        raise SystemExit("invalid isolated stage")
    signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(TimeoutError("host wrapper deadline")))
    signal.alarm(25)
    if mode=="guard":
        result=guard(spec)
        expected_id=spec.get("expected_container_id")
        expected_started=spec.get("expected_container_started")
        if expected_id:
            result["container_identity_checks"]={"id":result["container"].get("id")==expected_id,"started":result["container"].get("State",{}).get("StartedAt")==expected_started,"image":result["container"].get("image_id")==IMAGE,"running":result["container"].get("State",{}).get("Running") is True}
        print(json.dumps(result,indent=2));return
    if mode!="setup":raise SystemExit("setup or guard only")
    if stage.exists():raise SystemExit("stage already exists; no overwrite")
    existing=command(["docker","ps","-a","--format","{{.Names}}"])
    if existing["returncode"]!=0 or spec["container_name"] in existing["stdout"].splitlines():
        raise SystemExit("container inventory failed/name already exists")
    image=command(["docker","image","inspect",IMAGE,"--format","{{.Id}}"])
    if image["returncode"]!=0 or image["stdout"].strip()!=IMAGE:
        raise SystemExit("exact cached image absent; no pull")
    device_evidence={p:{"exists":Path(p).exists(),"readable":os.access(p,os.R_OK),"writable":os.access(p,os.W_OK)} for p in DEVICES}
    if not all(v["exists"] for v in device_evidence.values()) or not Path("/opt/hyhal").is_dir():
        raise SystemExit("required local devices/hyhal missing")
    stage.mkdir(parents=True,exist_ok=False)
    for asset in spec["assets"]:
        rel=Path(asset["relative_path"])
        if rel.is_absolute() or ".." in rel.parts or not str(rel).startswith("codex_coding/"):
            raise SystemExit("asset outside isolated project")
        data=base64.b64decode(asset["base64"],validate=True)
        if hashlib.sha256(data).hexdigest()!=asset["sha256"]:
            raise SystemExit("asset source SHA mismatch")
        dest=stage/"project"/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
    argv=["docker","run","-d","--pull=never","--no-healthcheck","--entrypoint","/bin/sleep","--name",spec["container_name"],"--hostname",spec["hostname"],"--label","fluidckpt.task=v0.1.23.3/task2/three_node_recovery","--memory","8g","--pids-limit","512","--network","host","--ipc","private","--shm-size","1g","--cap-add","IPC_LOCK","--ulimit","memlock=-1:-1","--ulimit","core=0:0"]
    for device in DEVICES:argv += ["--device",device+":"+device+":rwm"]
    argv += ["--mount","type=bind,src=/opt/hyhal,dst=/opt/hyhal,readonly","--mount",f"type=bind,src={stage}/project,dst={PROJECT}","--workdir",PROJECT,IMAGE,"infinity"]
    run=command(argv,timeout=8)
    result={"run":run,"device_evidence":device_evidence,"stage":str(stage),"cached_image":image,"gpu_api_calls":0,"pulls":0,"result_utc":now(),"container":inspected(spec["container_name"])}
    (stage/"setup.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
