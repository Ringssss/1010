"""Read only node30's authorized failed init identity and exact eight file hashes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import resource
import shlex
import subprocess

from review_outer_guard import RESULTS, sha

CID = "7d3143da42c4c16a9f48e61189035e48a2dc3d9f177cd6207cb9b7f447120695"
STARTED = "2026-10-08T08:19:06.916290051Z"
BOOT = "d9d6a6fb-013f-49ac-98f9-e77eca67952e"
RUN = "/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_node30_init_r01"
NAMES = ("registration.json", "source.py", "child.owner.json", "child.process.json", "child.log", "child.events.jsonl", "child.result.json", "summary.json")


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULTS or not args.out.name.startswith("audit_"):
        parser.error("require a fresh versioned audit directory")
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    known = Path.home() / ".ssh/known_hosts"
    known_before = sha(known)
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3.30", "node30"]
    commands = []

    def remote(label, argv):
        command = ssh + [shlex.join(argv)]
        started = datetime.now(timezone.utc).isoformat()
        value = subprocess.run(command, capture_output=True, text=True, timeout=7)
        row = {"label": label, "argv": command, "started_utc": started, "returncode": value.returncode, "stdout": value.stdout, "stderr": value.stderr}
        commands.append(row)
        (args.out / f"{label}.json").write_text(json.dumps(row, indent=2) + "\n")
        return row

    fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}}}'
    identity = remote("container_identity", ["docker", "inspect", "--format", fmt, CID])
    ci = json.loads(identity["stdout"]) if identity["returncode"] == 0 else {}
    identity_passed = ci.get("id") == CID and ci.get("started") == STARTED and ci.get("running") is True
    host_boot = remote("host_boot", ["cat", "/proc/sys/kernel/random/boot_id"])
    identity_passed = identity_passed and host_boot["returncode"] == 0 and host_boot["stdout"].strip() == BOOT
    if not identity_passed:
        raise RuntimeError("current node30/container lifecycle differs; no old PID lookup")
    hashes = remote("exact_artifact_hashes", ["docker", "exec", CID, "sha256sum", *[RUN + "/" + name for name in NAMES]])
    pid = remote("exact_pid13", ["docker", "exec", CID, "cat", "/proc/13/stat"])
    parsed = {}
    for line in hashes["stdout"].splitlines():
        digest, name = line.split(maxsplit=1)
        parsed[Path(name).name] = digest
    current_pid_absent = pid["returncode"] != 0 and "No such file or directory" in pid["stderr"]
    result = {"identity_passed": identity_passed, "exact_pid13_current_absent": current_pid_absent, "remote_artifact_sha256": parsed, "missing_artifacts": [name for name in NAMES if name not in parsed], "known_hosts_unchanged": known_before == sha(known), "source_sha256": sha(args.out / "source.py"), "commands": commands, "gpu_operations": 0, "signals": 0, "scope": "authorized one failed node30 init lifecycle, exact PID13 and eight artifact hashes; no HIP, SMI, model or other PID access"}
    (args.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "commands"}))


if __name__ == "__main__":
    main()
