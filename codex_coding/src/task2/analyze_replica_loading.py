"""Report actual source bytes, loading latency and extra replica costs without hiding failures."""
import argparse
import json
from pathlib import Path
import statistics

parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", required=True)
args = parser.parse_args()
directory = Path(args.run_dir)
data = json.loads((directory / "summary.json").read_text())
groups = {}
report = {"run": data["run"], "status": data["status"], "error": data.get("error"),
          "failed_sample": data.get("failed_sample"), "groups": {},
          "scope": "node27 TP2, fixed 8GiB extra replicas; no cross-node claim"}
for sample in data.get("samples", []):
    if sample["mode"] != "restore": continue
    key = sample["planner"]+"/"+sample["executor"]
    groups.setdefault(key, []).append(sample)
for key, samples in groups.items():
    rows = []
    for s in samples:
        expected, actual = {}, {}
        for op in s["plan"]["ops"]:
            rid = op["replica_id"]; expected[rid] = expected.get(rid, 0)+op["n"]
        for flow in s["flow"]:
            for path in flow["paths"].values():
                for rid, count in path["bytes_by_source"].items(): actual[rid] = actual.get(rid, 0)+count
        rows.append({"phase": s["phase"], "load_s": s["timing"]["load_s"], "passed": s.get("passed", False),
                     "source_bytes": actual, "plan_source_bytes_match": expected == actual,
                     "catalog_plan_s": s["plan"]["catalog_and_plan_s"],
                     "flow_s": max(f["flow_total_s"] for f in s["flow"]),
                     "map_prepare_s": max(f["prep_s"] for f in s["flow"]),
                     "verified_bytes": sum(v["checked_bytes"] for v in s.get("verification", []))})
    times = [r["load_s"] for r in rows if r["passed"]]
    result = {"samples": rows, "count": len(rows), "all_passed": all(r["passed"] and r["plan_source_bytes_match"] for r in rows)}
    if times:
        result.update(median_s=statistics.median(times), min_s=min(times), max_s=max(times))
        if len(times) >= 4:
            q = statistics.quantiles(times, n=4, method="inclusive")
            result["iqr_over_median"] = (q[2]-q[0])/statistics.median(times)
    report["groups"][key] = result
    print(key, {k: v for k, v in result.items() if k != "samples"}, flush=True)
report["replica_builds"] = data.get("replica_builds")
report["pss_with_replicas_GiB"] = sum(r["memory"]["Pss"] for r in data.get("memory_with_replicas", []))/2**30
(directory / "analysis.json").write_text(json.dumps(report, indent=2))
print("status:", report["status"], "PSS GiB:", report["pss_with_replicas_GiB"], "error:", report["error"])
