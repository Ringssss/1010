"""Replay recorded real-model planner inputs through full and projected schemas once each."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.planning import restore_plan
from fluidckpt.cluster.wire import planning_payload, bind_manifests, worker_plan

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); parser.add_argument("--out", required=True)
args = parser.parse_args()
raw = Path(args.input).read_bytes(); data = json.loads(raw)
workers = data["final_inventory"]
replicas = [r for w in workers for r in w["replicas"] if r["state"] == "VALID"]
rows = []
size = lambda value: len(json.dumps(value).encode())
for sample in data["restores"]:
    old = sample["flow"]["plan"]
    sources = [r for r in replicas if "/remote/" in r["id"]] if sample["policy"] == "primary_only" else replicas
    full = {"replicas": sources, "targets": old["targets"], "workers": workers,
            "profile": data["profile"], "strategy": sample["strategy"]}
    lean = planning_payload(sources, old["targets"], workers, data["profile"], sample["strategy"])
    def solve(body):
        return restore_plan(body["replicas"], body["targets"], body["workers"], body["profile"], body["strategy"])
    original = solve(full); projected = solve(json.loads(json.dumps(lean)))
    restored = bind_manifests(projected, old["targets"])
    row = {"policy": sample["policy"], "ops_equal": original["ops"] == restored["ops"] == old["ops"],
           "manifests_equal": original["targets"] == restored["targets"],
           "planner_request_bytes": {"full": size(full), "compact": size(lean)},
           "planner_response_bytes": {"full": size(original), "compact": size(projected)},
           "prepare_plan_bytes_all_workers": {"full": size(original)*len(workers),
               "compact": sum(size(worker_plan(restored, w["worker"])) for w in workers)}}
    rows.append(row)
report = {"input_sha256": hashlib.sha256(raw).hexdigest(), "rows": rows,
          "scope": "recorded real layout, CPU planner replay; no live GPU transfer or performance speedup claim",
          "passed": all(r["ops_equal"] and r["manifests_equal"] for r in rows)}
out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
if not report["passed"]: raise SystemExit(1)
