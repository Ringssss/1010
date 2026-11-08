"""Read swap accounting only; do not touch swap payloads."""
import json
from pathlib import Path
out = {}
for path in ["/proc/swaps", "/sys/fs/cgroup/memory.swap.current", "/sys/fs/cgroup/memory.swap.max"]:
    try:
        out[path] = Path(path).read_text()
    except OSError as exc:
        out[path] = {"error": str(exc)}
print(json.dumps(out))
