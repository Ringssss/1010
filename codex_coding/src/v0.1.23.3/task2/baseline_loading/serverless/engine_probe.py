"""Validate one recorded engine PID/starttime and read its actual handoff timeout."""
import argparse
import hashlib
import json
from pathlib import Path
import resource

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
p = argparse.ArgumentParser()
p.add_argument("--spec", type=Path, required=True)
a = p.parse_args()
record = json.loads(a.spec.with_suffix(".pid.json").read_text())
pid = record["pid"]
stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
if stat[0] == "Z" or stat[19] != record["start_time"]:
    raise RuntimeError("recorded target engine exited or PID was reused")
environment = dict(item.split(b"=", 1) for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if b"=" in item)
timeout = float(environment.get(b"FLUIDCKPT_TIMEOUT", b"600"))
print(json.dumps({"pid": pid, "start_time": stat[19], "process_state": stat[0],
    "handoff_timeout_s": timeout, "source": "actual /proc/PID/environ; recorded PID/starttime checked",
    "process_spec": str(a.spec), "spec_sha256": hashlib.sha256(a.spec.read_bytes()).hexdigest()}, indent=2))
