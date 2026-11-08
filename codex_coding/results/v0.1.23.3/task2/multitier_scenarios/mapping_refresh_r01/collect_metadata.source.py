"""Collect one traceable read-only snapshot of node27/28/30 into a new run."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.parent.name != "multitier_scenarios" or not args.out.name.startswith("mapping_"):
        parser.error("fresh multitier_scenarios/mapping_* result required")
    args.out.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).with_name("host_metadata.py")
    body, source_sha = source.read_bytes(), sha(source)
    (args.out / "host_metadata.source.py").write_bytes(body)
    (args.out / "collect_metadata.source.py").write_bytes(Path(__file__).read_bytes())
    known = Path.home() / ".ssh/known_hosts"
    manifest = {"started_utc": now(), "source_host": socket.gethostname(),
                "host_source_sha256": source_sha, "known_hosts_before": sha(known),
                "commands": [], "remote_writes": 0, "signals": [], "gpu_calls": 0,
                "node29_accesses": 0, "remote_deadline_s": 35, "controller_command_timeout_s": 45,
                "reuse": ["three_node_recovery/inventory/collect.py strict SSH flags",
                          "three_node_recovery/inventory/host_readonly.py PCI chain/proc/cgroup/mount concepts",
                          "tp4_scenarios/feasibility/metadata_refresh.py metadata-only scope"],
                "exclusions": ["node29", "driver VRAM/KFD properties", "container exec", "keeper RPC", "GPU runtime"]}
    save(args.out / "manifest.json", manifest)
    def one(node):
        remote = ["python3", "-", node, source_sha]
        if node == "node27" and socket.gethostname() == "m03r2n07":
            argv = remote
        else:
            argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
                    "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3",
                    "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3." + node[4:], node, shlex.join(remote)]
        row = {"node": node, "argv": argv, "started_utc": now(), "stdin_sha256": source_sha}
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        row["controller_child_pid"] = child.pid
        try:
            stdout, stderr = child.communicate(input=body, timeout=45)
            row["returncode"] = child.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or b"", exc.stderr or b""
            row.update(returncode=None, timed_out=True, pending_local_ssh_pid=child.pid,
                       remote_deadline_exits_interpreter_without_signal=True)
        (args.out / (node + ".stdout.json")).write_bytes(stdout)
        (args.out / (node + ".stderr.txt")).write_bytes(stderr)
        row.update(ended_utc=now(), stdout_bytes=len(stdout), stderr_bytes=len(stderr))
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(one, node) for node in ("node27", "node28", "node30")]):
            row = future.result()
            manifest["commands"].append(row)
            save(args.out / "manifest.json", manifest)
            print(json.dumps(row), flush=True)
    manifest.update(ended_utc=now(), known_hosts_after=sha(known))
    manifest["known_hosts_unchanged"] = manifest["known_hosts_before"] == manifest["known_hosts_after"]
    manifest["files"] = {p.name: sha(p) for p in sorted(args.out.iterdir()) if p.name != "manifest.json"}
    save(args.out / "manifest.json", manifest)


if __name__ == "__main__":
    main()
