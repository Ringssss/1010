"""Execute one saved Docker root metadata collector through strict node28 SSH."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--out",type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).with_name("dockerroot_readonly_host.py").read_bytes()
    argv=["ssh","-T","-o","BatchMode=yes","-o","ConnectTimeout=5","-o","ConnectionAttempts=1","-o","StrictHostKeyChecking=yes","-o","UpdateHostKeys=no","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1","-o","Hostname=12.11.3.28","node28","python3 -"]
    row={"argv":argv,"source_sha256":hashlib.sha256(source).hexdigest(),"started_utc":datetime.now(timezone.utc).isoformat(),"timeout_s":12}
    (args.out/"host.source.py").write_bytes(source)
    try:
        done=subprocess.run(argv,input=source,capture_output=True,timeout=12)
        stdout,stderr=done.stdout,done.stderr;row["returncode"]=done.returncode
    except subprocess.TimeoutExpired as exc:
        stdout,stderr=exc.stdout or b"",exc.stderr or b"";row.update(returncode=None,timed_out=True,automatic_own_ssh_timeout_kill_attempted=True)
    (args.out/"stdout.json").write_bytes(stdout);(args.out/"stderr.txt").write_bytes(stderr)
    row["ended_utc"]=datetime.now(timezone.utc).isoformat();(args.out/"command.json").write_text(json.dumps(row,indent=2)+"\n")
    print(json.dumps(row))

if __name__=="__main__":main()
