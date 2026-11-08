"""Collect exactly one read-only inventory per authorized node into a fresh run."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")

def now():
    return datetime.now(timezone.utc).isoformat()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent.name != "three_node_recovery" or not out.name.startswith("inventory_"):
        parser.error("fresh three_node_recovery/inventory_* result required")
    out.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).with_name("host_readonly.py")
    body, source_sha = source.read_bytes(), sha(source)
    (out/"host_readonly.source.py").write_bytes(body)
    (out/"collect.source.py").write_bytes(Path(__file__).read_bytes())
    provenance = Path(__file__).resolve().parents[2]/"topology_alignment/forensics"
    reused = {str(provenance/name): sha(provenance/name) for name in ("host_snapshot.py", "container_snapshot.py", "runtime_snapshot.py")}
    known = Path.home()/".ssh/known_hosts"
    manifest = {"started_utc":now(), "source_host":socket.gethostname(), "source_sha256":source_sha, "reused_source_functions":reused, "reuse_scope":"adapted PCI chain/read/env whitelist/cgroup/proc identity primitives; omitted all HIP/SMI/ELF loading", "known_hosts_before":sha(known) if known.exists() else None, "commands":[], "gpu_api_calls":0, "signals":0, "node29_accesses":0}
    prior = Path(__file__).resolve().parents[6]/"results/v0.1.23.3/task2/resource_map/connectivity_nodes_r01"
    manifest["hostname_evidence"] = {str(prior/f"ssh_12_11_3_{n}.json"):sha(prior/f"ssh_12_11_3_{n}.json") for n in (27,28,30)}
    save(out/"manifest.json",manifest)
    def one(node):
        argv = ["python3", "-", node, source_sha]
        if node != "node27":
            ip = {"node28":"12.11.3.28", "node30":"12.11.3.30"}[node]
            argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=1", "-o", "Hostname="+ip, node, shlex.join(argv)]
        row = {"node":node,"argv":argv,"stdin_sha256":source_sha,"started_utc":now(),"timeout_s":60}
        start = time.monotonic()
        try:
            p = subprocess.run(argv,input=body,capture_output=True,timeout=60)
            (out/(node+".stdout.json")).write_bytes(p.stdout)
            (out/(node+".stderr.txt")).write_bytes(p.stderr)
            row.update(returncode=p.returncode,stdout_bytes=len(p.stdout),stderr_bytes=len(p.stderr))
            if p.returncode == 0:
                payload = json.loads(p.stdout)
                row["snapshot"] = node+".stdout.json"
                free = [(c["card"],round(c.get("total_minus_used_bytes",0)/2**30,3)) for c in payload["drm"]]
                print(json.dumps({"node":node,"sysfs_total_minus_used_GiB":free,"kfd_owner_count":len(payload["kfd_processes"]),"containers":[{"name":c["name"],"running":c["running"],"image_id":c["image_id"],"python_metadata_ok":c.get("python_metadata_command",{}).get("returncode")} for c in payload["containers"]]},ensure_ascii=False),flush=True)
        except subprocess.TimeoutExpired as exc:
            row.update(returncode=None,timed_out=True)
            (out/(node+".stdout.partial.txt")).write_bytes(exc.stdout or b"")
            (out/(node+".stderr.txt")).write_bytes(exc.stderr or b"")
        except (OSError, ValueError) as exc:
            row.update(error=repr(exc))
        row.update(ended_utc=now(),elapsed_s=time.monotonic()-start)
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(one,node) for node in ("node27","node28","node30")]):
            manifest["commands"].append(future.result())
            save(out/"manifest.json",manifest)
    manifest.update(ended_utc=now(),known_hosts_after=sha(known) if known.exists() else None)
    manifest["known_hosts_unchanged"] = manifest["known_hosts_before"] == manifest["known_hosts_after"]
    manifest["files"] = {p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "manifest.json"}
    save(out/"manifest.json",manifest)

if __name__ == "__main__":
    main()
