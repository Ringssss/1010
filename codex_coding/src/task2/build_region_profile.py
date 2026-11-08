"""Convert explicitly measured local/remote and contention observations into a scoped model."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
data = json.loads(Path(args.input).read_text())
paths, resources = {}, {}
for name in ("local0", "local1", "remote0", "remote1"):
    row = data["scenarios"][name][0]["rows"][-1]
    key = f"{row['numa']}:{row['gpu']}"
    resource = f"gpu_in:{row['gpu']}"
    resources[resource] = max(resources.get(resource, 0), row["median_GBps"])
    paths[key] = {"GBps": row["median_GBps"], "resources": [resource]}
mem0 = sum(r["rows"][-1]["median_GBps"] for r in data["scenarios"]["shared_numa0"])
mem3 = sum(r["rows"][-1]["median_GBps"] for r in data["scenarios"]["shared_numa3"])
resources["shared_numa0_measured"] = mem0
resources["shared_numa3_measured"] = mem3
for key, path in paths.items():
    if key.startswith("0:"): path["resources"].append("shared_numa0_measured")
    if key.startswith("3:"): path["resources"].append("shared_numa3_measured")
out = {"paths": paths, "resources": resources, "source": args.input,
       "topology_fingerprint": data["topology_fingerprint"],
       "scope": "GPU0/1 and NUMA0/3 only; NUMA0 dual-reader cap is empirical, not universal",
       "unmodeled": ["arbitrary cross-NUMA mixes", "IPC/setup"]}
Path(args.out).parent.mkdir(parents=True, exist_ok=True)
Path(args.out).write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
