"""One strict node28 hostname-only SSH observation; no setup or GPU calls."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--out",type=Path,required=True);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    argv=["ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=2","-o","ServerAliveCountMax=1","-o","Hostname=12.11.3.28","node28","hostname"]
    row={"argv":argv,"started_utc":datetime.now(timezone.utc).isoformat(),"timeout_s":8,"source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"gpu_operations":0,"remote_writes":0}
    try:
        done=subprocess.run(argv,capture_output=True,timeout=8)
        row.update(returncode=done.returncode,stdout=done.stdout.decode(errors="replace"),stderr=done.stderr.decode(errors="replace"))
    except subprocess.TimeoutExpired as exc:
        decode=lambda value:value.decode(errors="replace") if isinstance(value,bytes) else value or ""
        row.update(returncode=None,timed_out=True,stdout=decode(exc.stdout),stderr=decode(exc.stderr),automatic_ssh_client_kill_attempted=True)
    row["ended_utc"]=datetime.now(timezone.utc).isoformat()
    (a.out/"result.json").write_text(json.dumps(row,indent=2)+"\n");(a.out/"source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps(row))

if __name__=="__main__":main()
