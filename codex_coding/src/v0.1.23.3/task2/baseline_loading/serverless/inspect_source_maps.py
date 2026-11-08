"""Read source worker map metadata without attaching or modifying the process."""
import argparse
import json
from pathlib import Path
import resource

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
p = argparse.ArgumentParser()
p.add_argument("--pid", type=int, required=True)
p.add_argument("--logical-bytes", type=int, required=True)
a = p.parse_args()
rows = []
for line in Path(f"/proc/{a.pid}/maps").read_text().splitlines():
    parts = line.split()
    begin, end = (int(value, 16) for value in parts[0].split("-"))
    if end-begin >= a.logical_bytes and parts[1].startswith("rw"):
        rows.append({"line": line, "begin": begin, "end": end, "mapped_bytes": end-begin,
                     "anonymous": len(parts) == 5})
print(json.dumps({"pid": a.pid, "logical_bytes": a.logical_bytes, "candidate_maps": rows}, indent=2))
