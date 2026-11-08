"""Read only node28 DockerRootDir and its filesystem capacity, once."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess

def command(argv):
    row={"argv":argv,"timeout_s":3,"started_utc":datetime.now(timezone.utc).isoformat()}
    try:
        done=subprocess.run(argv,capture_output=True,timeout=3)
        row.update(returncode=done.returncode,stdout=done.stdout.decode(errors="replace"),stderr=done.stderr.decode(errors="replace"))
    except subprocess.TimeoutExpired as exc:
        decode=lambda v:v.decode(errors="replace") if isinstance(v,bytes) else v or ""
        row.update(returncode=None,timed_out=True,stdout=decode(exc.stdout),stderr=decode(exc.stderr),automatic_own_cli_timeout_kill_attempted=True)
    row["ended_utc"]=datetime.now(timezone.utc).isoformat();return row

def main():
    if os.uname().nodename!="m03r2n08":raise SystemExit("node28 actual hostname mismatch")
    info=command(["docker","info","--format","{{.DockerRootDir}}"])
    root=info.get("stdout","").strip()
    out={"hostname":os.uname().nodename,"boot_id":Path("/proc/sys/kernel/random/boot_id").read_text().strip(),"docker_root_command":info,"docker_root_dir":root if info["returncode"]==0 else None,"gpu_operations":0,"remote_writes":0}
    if info["returncode"]==0 and root.startswith("/") and "\n" not in root:
        out["actual_root_df_bytes"]=command(["df","-B1","--",root])
    print(json.dumps(out,indent=2))

if __name__=="__main__":main()
