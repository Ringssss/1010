"""Read-only evidence extraction followed by a new managed-placement report."""
import argparse
import hashlib
import json
from pathlib import Path

from analyze_restore_profile import loading


def analyze(directory):
    raw = (directory/"summary.json").read_bytes(); data = json.loads(raw)
    p = data["joint_plan"]
    samples = []
    for row in data["restores"]:
        flow = row["flow"]; plan = flow["plan"]
        expected = sum(t["n"] for t in plan["targets"])
        assert row["verification"]["passed"] and row["verification"]["checked_bytes"] == expected
        assert plan["strategy"] == "resource_lp" and plan["fallback_reason"] is None
        samples.append(dict(loading(row), predicted_transfer_s=plan["estimated_transfer_s"],
            execute_wall_s=flow["execute_wall_s"], verified_bytes=expected,
            prediction_relative_error=(plan["estimated_transfer_s"]-flow["execute_wall_s"])/flow["execute_wall_s"],
            source_bytes=flow["source_bytes"], generation_checks=len(row["replies"])))
    passed = (data["status"] == "passed" and len(samples) == 2 and not data["cleanup_errors"]
              and not data["source_hash_mismatches"] and data["transition"]["journal"]["status"] == "applied")
    report = {"passed": passed, "scope": data["scope"], "summary_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_audit": p["candidates"], "decision": p["decision"], "solver": p["solver"],
        "selected": p["selected"], "transition_peak_usage": p["transition"]["peak_usage"],
        "final_usage": data["transition"]["journal"]["final_usage"],
        "transition_wall_s": data["transition"]["wall_s"], "measured": samples,
        "limitations": ["one observation per layout, existing first; no stable performance ranking",
            "single target, fixed complete base and finite planning blocks; no multi-target claim",
            "contiguous calibration excludes real model scatter and per-op overhead",
            "budget accounting uses page-rounded logical allocations; device allocator slack is covered by headroom reserve",
            "checkpoint interruption is after a committed action, not a node/network crash",
            "load-window and transport changes are reported separately; no SGLang keepalive comparison"]}
    if len(samples) == 2:
        a, b = samples
        report["observed_change"] = {"load_reduction_fraction": 1-b["load_s"]/a["load_s"],
                                     "transfer_reduction_fraction": 1-b["execute_wall_s"]/a["execute_wall_s"]}
    (directory/"managed_analysis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": passed, "solver": p["solver"],
        "samples": [{k: s[k] for k in ("phase", "load_s", "execute_wall_s", "predicted_transfer_s", "verified_bytes")} for s in samples],
        "observed_change": report.get("observed_change")}, indent=2))
    if not passed: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", type=Path, required=True)
    analyze(parser.parse_args().run_dir)
