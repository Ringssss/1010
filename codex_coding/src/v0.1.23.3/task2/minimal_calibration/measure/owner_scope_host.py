"""Read exact zero-VRAM owners; expose only device-visibility environment keys."""
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess

OWNERS = ((3152797, "486284133"), (3153233, "486286927"))
VISIBILITY = ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("owner metadata 15s deadline")))
    signal.alarm(15)
    if os.uname().nodename != "m03r2n10" or Path("/proc/sys/kernel/random/boot_id").read_text().strip() != "d9d6a6fb-013f-49ac-98f9-e77eca67952e":
        raise ValueError("node30 identity differs")
    rows = []
    for pid, stamp in OWNERS:
        row = {"pid": pid, "expected_start_ticks": stamp}
        try:
            tail = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
            row.update(actual_start_ticks=tail[19], state=tail[0], owner_matches=tail[19] == stamp and tail[0] != "Z")
            if not row["owner_matches"]:
                rows.append(row)
                continue
            row["cgroup"] = Path(f"/proc/{pid}/cgroup").read_text().strip()
            row["argv"] = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").strip("\0").split("\0")
            try:
                data = Path(f"/proc/{pid}/environ").read_bytes()
                row["visibility"] = {k: v for entry in data.decode(errors="replace").split("\0") if "=" in entry
                                     for k, v in (entry.split("=", 1),) if k in VISIBILITY}
            except OSError as exc:
                row["visibility"] = {"unknown": repr(exc)}
            match = re.search(r"docker-([a-f0-9]{64})\.scope", row["cgroup"])
            if match:
                fmt = '{"id":{{json .Id}},"name":{{json .Name}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}}}'
                done = subprocess.run(["docker", "inspect", "--format", fmt, match[1]], capture_output=True, timeout=3)
                row["container_metadata"] = json.loads(done.stdout) if done.returncode == 0 else {"unknown": done.stderr.decode(errors="replace")}
        except OSError as exc:
            row["unknown"] = repr(exc)
        rows.append(row)
    print(json.dumps({"node": "node30", "owners": rows, "GPU_calls": 0,
                      "scope": "exact process attribution only; unset visibility cannot prove an unused GPU"}, indent=2))


if __name__ == "__main__":
    main()
