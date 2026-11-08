"""Separate thread/stream setup, path execution and measured GPU-event overlap."""
import argparse
import json
from pathlib import Path
import statistics

parser = argparse.ArgumentParser()
parser.add_argument("--run-dir", required=True)
args = parser.parse_args()
directory = Path(args.run_dir)
data = json.loads((directory / "summary.json").read_text())
groups = {}
for sample in data["samples"]:
    if sample["warmup"]: continue
    key = f"{sample['scenario']}/{sample['strategy']}/parallel={sample['parallel']}"
    groups.setdefault(key, []).append(sample)
report = {"status": data["status"], "scope": data["timing_scope"], "groups": {}}
for key, rows in groups.items():
    times = [r["transfer_s"] for r in rows]
    path_times = [max(p["copy_and_wait_s"] for w in r["workers"] if w["execution"]
                      for p in w["execution"]["paths"].values()) for r in rows]
    overlaps = [max(w["execution"]["gpu_path_overlap_ms"] for w in r["workers"] if w["execution"]) for r in rows]
    paths = [{"rank": op["tp"], "source": op["replica_id"], "off": op["off"], "n": op["n"]}
             for op in rows[0]["plan"]["ops"]]
    device_spans = [max([(max(e["gpu_end_ms"] for e in p["trace"])-min(e["gpu_begin_ms"] for e in p["trace"]))
                       for w in r["workers"] if w["execution"] for p in w["execution"]["paths"].values() if p["trace"]] or [0]) for r in rows]
    report["groups"][key] = {"times_s": times, "median_s": statistics.median(times),
                             "median_path_execution_s": statistics.median(path_times),
                             "overlap_ms": overlaps, "ops": paths,
                             "device_path_span_ms": device_spans,
                             "all_bytes_equal": all(w["mismatched_bytes"] == 0 for r in rows for w in r["workers"])}
    print(key, "total", round(statistics.median(times), 5), "path", round(statistics.median(path_times), 5),
          "overlap_ms", round(statistics.median(overlaps), 4), "device_ms", round(statistics.median(device_spans), 4), "ops", paths)
(directory / "analysis.json").write_text(json.dumps(report, indent=2))
