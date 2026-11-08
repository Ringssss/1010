"""Decompose recorded load windows without changing or re-running measured code."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


BOUNDARIES = (
    "load_begin", "model_layout_begin", "model_layout_end",
    "target_exported", "target_filled", "weights_ready",
)
SEGMENTS = (
    "loader_entry_s", "model_layout_s", "target_export_s",
    "target_wait_s", "finalize_s",
)


def analyze(directory):
    raw = (directory / "summary.json").read_bytes()
    data = json.loads(raw)
    groups = {}
    report = {
        "run": data["run"], "status": data["status"],
        "summary_sha256": hashlib.sha256(raw).hexdigest(),
        "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "metric": "max(weights_ready) - min(load_begin), same-host monotonic clock",
        "notes": [
            "Per-sample critical-rank segments plus arrival skew exactly sum to the load window.",
            "Critical rank is the rank with the latest weights_ready timestamp.",
            "Target wait includes controller polling, planning, mapping, copying and notification.",
            "Keeper and planner durations overlap rank segments; do not add them again.",
            "Medians of individual segments need not sum to the median load window.",
            "First solver invocation remains in the main statistics; no slow samples removed.",
        ],
        "groups": {},
    }
    for sample in data["samples"]:
        if sample["mode"] != "restore":
            continue
        ranks = sample["timing"]["ranks"]
        earliest = min(r["begin_ns"] for r in ranks.values())
        critical = max(ranks, key=lambda rank: ranks[rank]["end_ns"])
        rank_rows = {}
        for rank, record in ranks.items():
            stamps = []
            for boundary in BOUNDARIES:
                found = [e["monotonic_ns"] for e in record["events"] if e["event"] == boundary]
                if len(found) != 1:
                    raise ValueError(f"{sample['phase']}/{rank}: ambiguous or missing {boundary}")
                stamps.append(found[0])
            if stamps != sorted(stamps):
                raise ValueError("non-monotonic phase boundaries")
            rank_rows[rank] = dict(zip(SEGMENTS, [(b-a)/1e9 for a, b in zip(stamps, stamps[1:])]))
        pieces = {"arrival_skew_s": (ranks[critical]["begin_ns"]-earliest)/1e9, **rank_rows[critical]}
        if abs(sum(pieces.values()) - sample["timing"]["load_s"]) > 1e-8:
            raise ValueError("critical-rank decomposition does not sum to total")
        row = {
            "phase": sample["phase"], "passed": sample.get("passed", False),
            "load_s": sample["timing"]["load_s"], "critical_rank": critical,
            "critical_rank_segments_s": pieces, "rank_segments_s": rank_rows,
            "catalog_and_plan_s": sample["plan"]["catalog_and_plan_s"],
            "actual_strategy": sample["plan"]["strategy"],
            "fallback_reason": sample["plan"].get("fallback_reason"),
            "keeper_max_prepare_s": max(f["prep_s"] for f in sample["flow"]),
            "keeper_max_execute_s": max(f["total_s"] for f in sample["flow"]),
            "keeper_max_flow_s": max(f["flow_total_s"] for f in sample["flow"]),
        }
        groups.setdefault(sample["planner"]+"/"+sample["executor"], []).append(row)
    for name, rows in groups.items():
        good = [row for row in rows if row["passed"]]
        result = {"samples": rows, "attempts": len(rows), "passed": len(good)}
        if good:
            fields = ("load_s", "catalog_and_plan_s", "keeper_max_prepare_s",
                      "keeper_max_execute_s", "keeper_max_flow_s")
            result["medians_s"] = {k: statistics.median(r[k] for r in good) for k in fields}
            result["critical_rank_segment_medians_s"] = {
                k: statistics.median(r["critical_rank_segments_s"][k] for r in good)
                for k in good[0]["critical_rank_segments_s"]
            }
        report["groups"][name] = result
        print(name, json.dumps({k: v for k, v in result.items() if k != "samples"}))
    report["replicas"] = [{
        "replica_id": r["replica_id"], "numa": r["numa"],
        "physical_bytes": r["owned_allocation_bytes"],
        "build_s": sum(r["build"][k] for k in ("allocate_s", "copy_s", "verify_s")),
        "allocation_s": r["build"]["allocate_s"], "copy_s": r["build"]["copy_s"],
        "verify_s": r["build"]["verify_s"],
    } for r in data.get("replica_builds", [])]
    (directory / "breakdown.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    analyze(Path(args.run_dir))
