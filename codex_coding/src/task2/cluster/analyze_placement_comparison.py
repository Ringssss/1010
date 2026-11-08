"""Audit budgets, predicted placement costs, and the two observed model load windows."""
import argparse
import hashlib
import json
from pathlib import Path

from analyze_restore_profile import loading


def analyze(directory):
    raw = (directory/"summary.json").read_bytes(); data = json.loads(raw)
    solutions = data["placement_solutions"]
    report = {"development": data["development"], "status": data["status"],
              "scope": data["scope"], "selected_policy": solutions["selected_policy"],
              "summary_sha256": hashlib.sha256(raw).hexdigest(),
              "calibration": [{k: r[k] for k in ("path", "rank_bytes", "target_seconds", "GBps")} for r in data["calibration"]],
              "predicted_policies": [], "measured": [], "layouts": {},
              "limitations": ["one observation per layout, existing first then optimized",
                              "calibration uses contiguous targets; model scatter and scheduling can add cost",
                              "MILP optimum applies only to the finite 1GiB candidates and approximate single-scenario model",
                              "HBM reservations compete with future inference capacity; target-worker allowance was limited to 4GiB each",
                              "base DRAM replicas and verification/build work are outside the live weight-loading timer"]}
    for name, row in solutions["policies"].items():
        report["predicted_policies"].append({"policy": name, "predicted_transfer_s": row["objective_s"],
            "tier_bytes": row["tier_bytes"], "solve_wall_s": row.get("solve_wall_s"),
            "mip_gap": row.get("mip_gap"), "dual_bound_s": row.get("dual_bound_s"),
            "selected": [{k: c[k] for k in ("id", "worker", "tier", "tp", "off", "n")} for c in row["selected"]]})
    for name, layout in data["layouts"].items():
        base = [r for r in layout["replicas"] if r["id"].startswith("model/remote/")]
        extra = [r for r in layout["replicas"] if r not in base]
        by_tier = {t: sum(r["allocated_bytes"] for r in extra if r["tier"] == t) for t in ("dram", "hbm")}
        report["layouts"][name] = {"base_bytes": sum(r["allocated_bytes"] for r in base),
                                   "extra_tier_bytes": by_tier, "replicas": layout["replicas"]}
        if by_tier["dram"] > 2 << 30 or by_tier["hbm"] > 8 << 30: raise AssertionError("physical budget exceeded")
    for sample in data["restores"]:
        f = sample["flow"]; p = f["plan"]
        window = loading(sample)
        report["measured"].append(dict(window, predicted_transfer_s=p["estimated_transfer_s"],
            execute_wall_s=f["execute_wall_s"], actual_planner=p["strategy"], fallback_reason=p["fallback_reason"],
            verified_bytes=sample["verification"]["checked_bytes"], generation_checks=len(sample["replies"]),
            source_bytes=f["source_bytes"]))
        if not sample["verification"]["passed"] or sample["verification"]["checked_bytes"] != sum(t["n"] for t in p["targets"]):
            raise AssertionError("incomplete weight verification")
        if p["strategy"] != "resource_lp" or p["fallback_reason"]: raise AssertionError("LP not active")
    if len(report["measured"]) == 2:
        old, new = report["measured"]
        report["observed_difference"] = {"load_s_saved": old["load_s"]-new["load_s"],
            "load_reduction_fraction": 1-new["load_s"]/old["load_s"],
            "execute_s_saved": old["execute_wall_s"]-new["execute_wall_s"],
            "execute_reduction_fraction": 1-new["execute_wall_s"]/old["execute_wall_s"]}
    report["optimized_build_wall_s"] = data["optimized_build"]["wall_s"]
    report["cleanup_errors"] = data["cleanup_errors"]
    report["source_hash_mismatches"] = data["source_hash_mismatches"]
    report["passed"] = (data["status"] == "passed" and len(report["measured"]) == 2
        and data["cleanup_errors"] == [] and data["source_hash_mismatches"] == []
        and report["layouts"]["existing"]["extra_tier_bytes"] == report["layouts"]["optimized"]["extra_tier_bytes"])
    (directory/"placement_analysis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": report["passed"], "selected_policy": report["selected_policy"],
                      "policies": [{k: r[k] for k in ("policy", "predicted_transfer_s", "solve_wall_s", "mip_gap")}
                                   for r in report["predicted_policies"]],
                      "measured": [{k: r[k] for k in ("phase", "load_s", "execute_wall_s", "predicted_transfer_s")}
                                   for r in report["measured"]],
                      "difference": report.get("observed_difference")}, indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", type=Path, required=True)
    analyze(parser.parse_args().run_dir)
