"""Summarize weight loading only; preserve the individual observations and failures."""
import argparse
import json
from pathlib import Path
import statistics


def analyze(path):
    data = json.loads((path / "summary.json").read_text())
    groups = {}
    for sample in data.get("samples", []):
        if sample["phase"] == "warmup" or sample["mode"] == "capture":
            continue
        key = "baseline" if sample["mode"] == "baseline" else "/".join(
            [sample["backend"], sample["executor"], sample["planner"]])
        groups.setdefault(key, []).append(sample)
    report = {"run": data["run"], "status": data["status"], "error": data.get("error"),
              "failed_sample": data.get("failed_sample"),
              "metric": "max weights_ready - min load_begin, same-host monotonic clock",
              "groups": {}, "captures": []}
    for key, samples in groups.items():
        passed = [s for s in samples if s.get("passed")]
        times = [s["timing"]["load_s"] for s in passed]
        row = {"attempts_recorded": len(samples), "passed": len(passed), "load_s": times}
        if times:
            row.update(median_s=statistics.median(times), min_s=min(times), max_s=max(times))
            if len(times) >= 4:
                q = statistics.quantiles(times, n=4, method="inclusive")
                row["iqr_over_median"] = (q[2] - q[0]) / statistics.median(times)
            row["cache_resident_ratios"] = [s["cache_before"]["resident_bytes"] / s["cache_before"]["file_bytes"] for s in passed]
            if passed[0].get("flow"):
                row["flow_s"] = [max(f["total_s"] for f in s["flow"]) for s in passed]
                row["map_prepare_s"] = [max(f["prep_s"] for f in s["flow"]) for s in passed]
                row["copy_sync_s"] = [max(f["local"]["copy_and_sync_s"] for f in s["flow"]) for s in passed]
                row["planning_s"] = [s["plan"]["planning_s"] for s in passed]
                row["verified_bytes"] = [sum(v["checked_bytes"] for v in s["verification"]) for s in passed]
        report["groups"][key] = row
    baseline = report["groups"].get("baseline", {}).get("median_s")
    for key, row in report["groups"].items():
        if baseline and key != "baseline" and "median_s" in row:
            row["speedup_vs_baseline_median"] = baseline / row["median_s"]
            row["reduction_vs_baseline_median"] = 1 - row["median_s"] / baseline
    for sample in data.get("samples", []):
        if sample["mode"] == "capture":
            report["captures"].append({"backend": sample["backend"], "save_s": sample["capture_s"],
                "pss_GiB": sum(r["memory"]["Pss"] for r in sample.get("keeper_memory", [])) / 2**30,
                "allocations": [r[0]["memory"] for r in sample["capture"]],
                "verified": all(r["passed"] for r in sample.get("verification", []))})
    (path / "analysis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    analyze(Path(args.run_dir))
