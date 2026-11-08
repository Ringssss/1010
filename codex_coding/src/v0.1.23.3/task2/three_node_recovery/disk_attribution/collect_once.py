"""Run one saved read-only host script over strict known node28 SSH."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--timeout", type=int, default=45)
parser.add_argument("--sudo", action="store_true")
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=False)
source = args.source.read_bytes()
if args.source.name == "phase5_host.py":
    payload = args.source.with_name("swap_container.py").read_bytes()
    (args.out / "swap_container.source.py").write_bytes(payload)
    source = ("SWAP_PAYLOAD = " + repr(payload) + "\n").encode() + source
argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3.28", "node28", "sudo -n python3 -" if args.sudo else "python3 -"]
record = {"argv": argv, "source_sha256": hashlib.sha256(source).hexdigest(), "started_utc": datetime.now(timezone.utc).isoformat(), "timeout_s": args.timeout}
(args.out / "host.source.py").write_bytes(source)
try:
    done = subprocess.run(argv, input=source, capture_output=True, timeout=args.timeout)
    stdout, stderr = done.stdout, done.stderr
    record["returncode"] = done.returncode
except subprocess.TimeoutExpired as exc:
    stdout, stderr = exc.stdout or b"", exc.stderr or b""
    record.update(returncode=None, timed_out=True, automatic_own_ssh_timeout_kill_attempted=True)
(args.out / "stdout.json").write_bytes(stdout)
(args.out / "stderr.txt").write_bytes(stderr)
record["ended_utc"] = datetime.now(timezone.utc).isoformat()
(args.out / "command.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record))
