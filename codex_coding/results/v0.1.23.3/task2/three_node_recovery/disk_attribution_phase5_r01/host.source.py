SWAP_PAYLOAD = b'"""Read swap accounting only; do not touch swap payloads."""\nimport json\nfrom pathlib import Path\nout = {}\nfor path in ["/proc/swaps", "/sys/fs/cgroup/memory.swap.current", "/sys/fs/cgroup/memory.swap.max"]:\n    try:\n        out[path] = Path(path).read_text()\n    except OSError as exc:\n        out[path] = {"error": str(exc)}\nprint(json.dumps(out))\n'
"""Read exact owned swap accounting from saved stdin payload."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

if os.uname().nodename != "m03r2n08":
    raise SystemExit("hostname mismatch")
out = {"hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "utc": datetime.now(timezone.utc).isoformat(), "remote_writes": 0, "gpu_operations": 0, "signals_to_services": 0, "owned": []}
for cid in ["0deafa1b72721d0ca8d27634f9f2bf71021d5900e631680baa41c88ff0f1fd8c", "177035641ac22e59854da0edd9af0941d7599c1820cf686491fea5a2a06c962c"]:
    argv = ["docker", "exec", "-i", "-u", "0", cid, "timeout", "3s", "python3", "-"]
    row = {"id": cid, "argv": argv, "stdin_source_sha256": hashlib.sha256(SWAP_PAYLOAD).hexdigest()}
    try:
        done = subprocess.run(argv, input=SWAP_PAYLOAD, capture_output=True, timeout=5)
        row.update(returncode=done.returncode, stdout=done.stdout.decode(errors="replace"), stderr=done.stderr.decode(errors="replace"))
    except subprocess.TimeoutExpired as exc:
        row.update(returncode=None, timed_out=True, stdout=(exc.stdout or b"").decode(errors="replace"), stderr=(exc.stderr or b"").decode(errors="replace"), automatic_own_cli_timeout_kill_attempted=True)
    out["owned"].append(row)
print(json.dumps(out, indent=2))
