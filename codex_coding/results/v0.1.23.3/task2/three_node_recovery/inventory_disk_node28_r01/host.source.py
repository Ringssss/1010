"""One bounded filesystem capacity observation on node28, without writes."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess

def main():
    if os.uname().nodename!="m03r2n08":raise SystemExit("node28 actual hostname mismatch")
    paths=["/tmp","/dev/shm","/var/lib/docker","/data/node28","/home"]
    out={"started_utc":datetime.now(timezone.utc).isoformat(),"hostname":os.uname().nodename,"boot_id":Path("/proc/sys/kernel/random/boot_id").read_text().strip(),"commands":[],"writes":0,"gpu_operations":0}
    for argv in (["df","-B1","--",*paths],["df","-i","--",*paths],*[["findmnt","-J","-T",path,"-o","TARGET,SOURCE,FSTYPE"] for path in paths]):
        row={"argv":argv,"started_utc":datetime.now(timezone.utc).isoformat(),"timeout_s":2}
        try:
            p=subprocess.run(argv,capture_output=True,timeout=2)
            row.update(returncode=p.returncode,stdout=p.stdout.decode(errors="replace"),stderr=p.stderr.decode(errors="replace"))
        except subprocess.TimeoutExpired as exc:
            decode=lambda v:v.decode(errors="replace") if isinstance(v,bytes) else v or ""
            row.update(returncode=None,timed_out=True,stdout=decode(exc.stdout),stderr=decode(exc.stderr),automatic_own_cli_timeout_kill_attempted=True)
        row["ended_utc"]=datetime.now(timezone.utc).isoformat();out["commands"].append(row)
    out["ended_utc"]=datetime.now(timezone.utc).isoformat();print(json.dumps(out,indent=2))

if __name__=="__main__":main()
