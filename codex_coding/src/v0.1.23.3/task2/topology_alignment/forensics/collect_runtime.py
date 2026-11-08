"""Collect read-only load or installed file hashes on both nodes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("load", "hashes", "identities"), required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--container", default="zjn-fluid-basic-1bce3897a2075b")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = {"utc": datetime.now(timezone.utc).isoformat(), "read_only": True, "mode": args.mode, "nodes": {}}
    source = Path(__file__).with_name("runtime_pid_identities.py" if args.mode == "identities" else "runtime_snapshot.py")
    snapshot = json.loads(args.snapshot.read_text()) if args.mode == "identities" else None
    def collect(node):
        if args.mode == "load":
            argv, raw = ["python3", "-", args.container], source.read_bytes()
        elif args.mode == "identities":
            pids = re.findall(r"PID:\s+(\d+)", snapshot["nodes"][node]["data"]["smi"]["pids"]["stdout"])
            argv, raw = ["python3", "-", *pids], source.read_bytes()
        else:
            argv, raw = ["docker", "exec", args.container, "sha256sum", "/opt/dtk/lib/librccl.so.1", "/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so", "/usr/local/built-in-508-topo-input-tj-default.xml", "/opt/dtk/rccl/lib/topo_mapping_default.xml"], None
        if node == "node29":
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "12.11.3.29", shlex.join(argv)]
        begin = datetime.now(timezone.utc).isoformat()
        p = subprocess.run(argv, input=raw, capture_output=True, timeout=200)
        record = {"utc_begin": begin, "utc_end": datetime.now(timezone.utc).isoformat(), "argv": argv, "returncode": p.returncode, "stderr": p.stderr.decode(errors="replace"), "stdout_sha256": hashlib.sha256(p.stdout).hexdigest()}
        if raw:
            record["stdin_file"], record["stdin_sha256"] = str(source), hashlib.sha256(raw).hexdigest()
        record["data"] = json.loads(p.stdout) if args.mode in ("load", "identities") and p.returncode == 0 else p.stdout.decode(errors="replace")
        return node, record
    with ThreadPoolExecutor(max_workers=2) as pool:
        for node, record in pool.map(collect, ("node27", "node29")):
            result["nodes"][node] = record
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "mode": args.mode, "returncodes": {n: r["returncode"] for n, r in result["nodes"].items()}}))

if __name__ == "__main__":
    main()
