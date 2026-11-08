"""Fit single-path time on 16/256 MiB, check 1 GiB without fitting it."""
import argparse
import json
from pathlib import Path
import statistics

parser = argparse.ArgumentParser()
parser.add_argument("--profile", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
data = json.loads(Path(args.profile).read_text())
models = []
for name in ("local0", "local1", "remote0", "remote1"):
    rows = sorted(data["scenarios"][name][0]["rows"], key=lambda r: r["bytes"])
    a, b, heldout = rows
    ta, tb = statistics.median(a["seconds"]), statistics.median(b["seconds"])
    beta_inverse = (tb - ta) / (b["bytes"] - a["bytes"])
    if beta_inverse <= 0:
        raise ValueError("nonpositive bandwidth fit")
    alpha = max(0, ta - a["bytes"] * beta_inverse)
    predicted = alpha + heldout["bytes"] * beta_inverse
    observed = statistics.median(heldout["seconds"])
    models.append({"name": name, "gpu": a["gpu"], "numa": a["numa"],
                   "alpha_s": alpha, "bandwidth_GBps": 1 / beta_inverse / 1e9,
                   "heldout_bytes": heldout["bytes"], "predicted_s": predicted,
                   "observed_s": observed, "relative_error": abs(predicted - observed) / observed})
result = {"topology_fingerprint": data["topology_fingerprint"], "paths": models,
          "median_heldout_relative_error": statistics.median(m["relative_error"] for m in models),
          "scope": "microbenchmark only, not a validated model of complete weight loading",
          "shared_numa0_aggregate_GBps": sum(g["rows"][-1]["median_GBps"] for g in data["scenarios"]["shared_numa0"])}
Path(args.out).write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
