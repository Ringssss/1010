"""Build reviewed isolated-stage specs or execute parent-selected host setup/guard."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import ast

ROOT=Path(__file__).resolve().parents[6]
IMAGE="sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588"

def sha(raw):return hashlib.sha256(raw).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory",type=Path)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--run",default="isolated_r01")
    p.add_argument("--spec",type=Path)
    p.add_argument("--mode",choices=("setup","guard"),default="setup")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--expected-container-id")
    p.add_argument("--expected-container-started")
    a=p.parse_args()
    if not re.fullmatch(r"[a-z0-9_]+",a.run):p.error("safe run ID required")
    a.out.mkdir(parents=True,exist_ok=False)
    host=Path(__file__).with_name("fresh_host.py").read_bytes()
    if a.spec:
        spec=json.loads(a.spec.read_text())
        if spec["host_wrapper_sha256"]!=sha(host):p.error("host wrapper changed; generate new plan without overwriting")
        if bool(a.expected_container_id)!=bool(a.expected_container_started):p.error("full container ID/StartedAt must be paired")
        if a.expected_container_id:
            spec.update(expected_container_id=a.expected_container_id,expected_container_started=a.expected_container_started)
        (a.out/"effective.spec.json").write_text(json.dumps(spec,indent=2)+"\n")
        (a.out/"fresh_host.source.py").write_bytes(host)
        (a.out/"fresh_container.source.py").write_bytes(Path(__file__).read_bytes())
        options=["-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1","-o","Hostname="+spec["ip"]]
        command_record={"host_source_sha256":sha(host),"original_spec_sha256":sha(a.spec.read_bytes()),"effective_spec_sha256":sha((a.out/"effective.spec.json").read_bytes()),"execute":a.execute,"commands":[]}
        (a.out/"command.json").write_text(json.dumps(command_record,indent=2)+"\n")
        if not a.execute:return
        bootstrap_cmd=["ssh","-T",*options,spec["node"],"mktemp -d /tmp/fluidckpt_three_node_bootstrap."+spec["node"]+".XXXXXX"]
        bootstrap=subprocess.run(bootstrap_cmd,capture_output=True,timeout=8)
        bootstrap_path=bootstrap.stdout.decode().strip()
        command_record["commands"].append({"argv":bootstrap_cmd,"returncode":bootstrap.returncode,"stdout":bootstrap_path,"stderr":bootstrap.stderr.decode(errors="replace")})
        if bootstrap.returncode!=0 or not re.fullmatch(r"/tmp/fluidckpt_three_node_bootstrap\.node(28|30)\.[a-zA-Z0-9]+",bootstrap_path):raise RuntimeError("bootstrap directory failed")
        upload_cmd=["scp",*options,str(a.out/"fresh_host.source.py"),str(a.out/"effective.spec.json"),spec["node"]+":"+bootstrap_path+"/"]
        upload=subprocess.run(upload_cmd,capture_output=True,timeout=10)
        command_record["commands"].append({"argv":upload_cmd,"returncode":upload.returncode,"stdout":upload.stdout.decode(errors="replace"),"stderr":upload.stderr.decode(errors="replace")})
        argv=["ssh","-T",*options,spec["node"],shlex.join(["python3",bootstrap_path+"/fresh_host.source.py",a.mode,bootstrap_path+"/effective.spec.json"])]
        command_record["commands"].append({"argv":argv})
        (a.out/"command.json").write_text(json.dumps(command_record,indent=2)+"\n")
        if upload.returncode!=0:raise RuntimeError("bootstrap source/spec upload failed")
        proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        start=datetime.now(timezone.utc).isoformat()
        try:
            stdout,stderr=proc.communicate(timeout=35)
            result={"returncode":proc.returncode,"started_utc":start,"ended_utc":datetime.now(timezone.utc).isoformat(),"signals":0}
            if proc.returncode==0:
                body=json.loads(stdout)
                result["identity_checks_passed"]=all(body.get("container_identity_checks",{}).values()) if spec.get("expected_container_id") else None
        except subprocess.TimeoutExpired as exc:
            stdout,stderr=exc.stdout or b"",exc.stderr or b""
            # Only terminate this new SSH client; remote status remains pending.
            proc.terminate()
            try:proc.wait(timeout=1)
            except subprocess.TimeoutExpired:pass
            result={"returncode":proc.poll(),"timed_out":True,"new_ssh_pid":proc.pid,"signals":1,"remote_cleanup":"pending; no remote signal"}
        (a.out/"stdout.json").write_bytes(stdout);(a.out/"stderr.txt").write_bytes(stderr)
        (a.out/"result.json").write_text(json.dumps(result,indent=2)+"\n");print(json.dumps(result));return
    if a.inventory is None:p.error("--inventory required for plan")
    loader=ROOT/"codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration/inventory.py"
    tree=ast.parse(loader.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="load_session")
    namespace={"json":json,"Path":Path};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(loader),"exec"),namespace)
    state=namespace["load_session"](ROOT/"codex_coding/results/motivation/fig4_lanes11/session.json")
    assets=[]
    for name in ("init_gate.py","container_namespace.py"):
        rel=Path("codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe")/name
        raw=(ROOT/rel).read_bytes();assets.append({"relative_path":str(rel),"sha256":sha(raw),"base64":base64.b64encode(raw).decode()})
    for n in (28,30):
        inv=a.inventory/f"node{n}.stdout.json";d=json.loads(inv.read_text());hostrel=f"codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_frozen_{a.run}/node{n}.json"
        raw=inv.read_bytes()
        spec={"node":f"node{n}","ip":f"12.11.3.{n}","hostname":d["hostname"],"boot_id":d["boot_id"],"image_id":IMAGE,"container_name":f"zjn-fluid-three-node-{a.run}-n{n}","stage":f"/tmp/fluidckpt_three_node/{a.run}_n{n}","project_container_path":str(ROOT),"gpu":2 if n==28 else 0,"bdf":"0000:55:00.0" if n==28 else "0000:09:00.0","protected_containers":[c["name"] for c in d["containers"] if c["name"].startswith("zjn")],"assets":assets+[{"relative_path":hostrel,"sha256":sha(raw),"base64":base64.b64encode(raw).decode()}],"host_wrapper_sha256":sha(host),"authorization":"parent must independently review then explicitly --execute setup; no GPU operation here"}
        spec["keeper_endpoints"]=[ep for member,ep in zip(state["members"],state["endpoints"]) if member[0]==f"node{n}"]
        spec["keeper_session_loader_source_sha256"]=sha(loader.read_bytes())
        own_ids=[c["id"] for c in d["containers"] if c["name"].startswith("zjn")]
        spec["known_live_workers"]=[{"pid":r["pid"],"start_time_ticks":r["start_time_ticks"],"cgroup":r["cgroup"]["proc_cgroup"],"gpu":r["worker_args"]["--gpu"]} for r in d["process_summary"] if r.get("worker_args",{}).get("--node")==f"node{n}" and "--gpu" in r["worker_args"] and isinstance(r["cgroup"]["proc_cgroup"],str) and any(i in r["cgroup"]["proc_cgroup"] for i in own_ids)]
        (a.out/f"node{n}.spec.json").write_text(json.dumps(spec,indent=2)+"\n")
    (a.out/"host.source.py").write_bytes(host)
    print(json.dumps({"out":str(a.out),"gpu_operations":0,"docker_creations":0,"stage_mode":"new /tmp minimal assets mapped to container project root"}))

if __name__=="__main__":main()
