"""Read small frozen files and owned-worker state after exact container identity checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--worker-out")
    args = parser.parse_args()
    row = json.loads(args.binding)
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != row["boot_id"]:
        raise ValueError("boot changed")
    fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}}}'
    done = subprocess.run(["docker", "inspect", "--format", fmt, row["container_id"]], capture_output=True, timeout=4)
    identity = json.loads(done.stdout) if done.returncode == 0 else {}
    if identity != {"id": row["container_id"], "started": row["container_started_at"], "running": True}:
        raise ValueError("container identity changed")
    paths = ["/usr/local/built-in-508-topo-input-tj-default.xml", "/opt/dtk/rccl/lib/topo_mapping_default.xml",
             row["env"]["NCCL_TOPO_FILE"], args.plan]
    command = ["docker", "exec", row["container_id"], "sha256sum", *paths]
    readback = subprocess.run(command, capture_output=True, timeout=8)
    result = {"identity": identity, "boot_id": row["boot_id"], "hash_argv": command,
              "hash_returncode": readback.returncode, "hash_stdout": readback.stdout.decode(),
              "hash_stderr": readback.stderr.decode(), "GPU_calls": 0, "signals": 0}
    if args.worker_out:
        # The owned stage is a host bind mount, so exact proc evidence stays in the container PID namespace.
        command = ["docker", "exec", row["container_id"], "cat", args.worker_out + "/supervisor.summary.json"]
        owned = subprocess.run(command, capture_output=True, timeout=4)
        result["supervisor_summary_returncode"] = owned.returncode
        result["supervisor_summary_stderr"] = owned.stderr.decode()
        if owned.returncode == 0:
            result["supervisor_summary_sha256"] = hashlib.sha256(owned.stdout).hexdigest()
            result["supervisor_summary"] = json.loads(owned.stdout)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
