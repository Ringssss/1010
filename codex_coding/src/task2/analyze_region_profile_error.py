"""Compare fixture timings with the bandwidth bound, preserving model limitations."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def analyze(directory):
    raw = (directory / "summary.json").read_bytes()
    data = json.loads(raw)
    groups = {}
    for sample in data["samples"]:
        if sample["warmup"]:
            continue
        expected, actual = {}, {}
        for op in sample["plan"]["ops"]:
            key = op["replica_id"]
            expected[key] = expected.get(key, 0) + op["n"]
        checked, mismatches = 0, 0
        for worker in sample["workers"]:
            checked += worker["verified_bytes"]
            mismatches += worker["mismatched_bytes"]
            if worker["execution"]:
                for path in worker["execution"]["paths"].values():
                    for key, count in path["bytes_by_source"].items():
                        actual[key] = actual.get(key, 0) + count
        bound = sample["plan"]["rounded_resource_bound_s"]
        elapsed = sample["transfer_s"]
        key = f"{sample['scenario']}/{sample['strategy']}/parallel={sample['parallel']}"
        groups.setdefault(key, []).append({
            "repeat": sample["repeat"], "transfer_s": elapsed,
            "bandwidth_bound_s": bound,
            "gap_relative_to_actual": (elapsed-bound)/elapsed,
            "source_bytes_match": actual == expected,
            "verified_bytes": checked, "mismatched_bytes": mismatches,
            "passed": actual == expected and checked == sum(expected.values()) and mismatches == 0,
        })
    report = {
        "status": data["status"],
        "summary_sha256": hashlib.sha256(raw).hexdigest(),
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run": data["run"], "rank_bytes": data["rank_bytes"],
        "scope": "preallocated transfer fixture; excludes planning and full model preparation",
        "notes": [
            "A bandwidth bound is not a full-load prediction or a schedule feasibility proof.",
            "The gap includes setup and scheduling effects; it is not pure bandwidth measurement error.",
            "A gap above 20% flags a combination that needs calibration before time prediction.",
            "When GPU tracing is disabled, zero overlap fields mean unmeasured, not no concurrency.",
        ], "groups": {},
    }
    for name, rows in groups.items():
        gaps = [abs(row["gap_relative_to_actual"]) for row in rows]
        result = {
            "samples": rows, "all_passed": all(row["passed"] for row in rows),
            "median_transfer_s": statistics.median(row["transfer_s"] for row in rows),
            "median_absolute_gap": statistics.median(gaps), "max_absolute_gap": max(gaps),
            "requires_calibration_for_time_prediction": any(gap > 0.20 for gap in gaps),
        }
        report["groups"][name] = result
        print(name, json.dumps({k: v for k, v in result.items() if k != "samples"}))
    (directory / "profile_validation.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    analyze(Path(args.run_dir))
