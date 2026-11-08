"""Audit actual planner selection, bounded pipeline correctness and matched transfer timings."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.control import Cluster


def overlap(events):
    points = sorted([(e["begin_ms"], 1, e["kind"]) for e in events]
                    + [(e["end_ms"], -1, e["kind"]) for e in events])
    active, last, total = {}, 0.0, 0.0
    for stamp, direction, kind in points:
        if active.get("copy", 0) > 0 and active.get("network", 0) > 0: total += stamp-last
        active[kind] = active.get(kind, 0)+direction
        last = stamp
    return total


def checked(flow, verification=None):
    expected, actual = {}, {}
    for o in flow["plan"]["ops"]:
        key = o["replica_id"]; expected[key] = expected.get(key, 0)+o["n"]
    for worker in flow["execution"]:
        for o in worker["records"]:
            if o["role"] in ("target", "both"):
                key = o["replica_id"]; actual[key] = actual.get(key, 0)+o["bytes"]
    verification = verification or flow["verification"]
    return (expected == actual == flow["source_bytes"] and verification["passed"]
            and verification["checked_bytes"] == sum(t["n"] for t in flow["plan"]["targets"]))


def main(args):
    directory = Path(args.run_dir)
    raw = (directory / "summary.json").read_bytes(); data = json.loads(raw)
    subprocess.run([sys.executable, str(Path(__file__).with_name("analyze_loading_windows.py")),
                    "--run-dir", str(directory)], check=True, stdout=subprocess.DEVNULL)
    windows = {r["phase"]: r for r in json.loads((directory / "loading_windows.json").read_text())["samples"]}
    report = {"run": data["run"], "status": data["status"], "development": data["development"],
        "summary_sha256": hashlib.sha256(raw).hexdigest(), "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "restore_default": inspect.signature(Cluster.plan_restore).parameters["strategy"].default,
        "executor_default": inspect.signature(Cluster.__init__).parameters["executor"].default,
        "placement_default": inspect.signature(Cluster.place).parameters["policy"].default,
        "placement_evaluation": {k: data.get("model_joint_placement", {}).get("placement", {}).get(k) for k in
            ("policy", "evaluation_requested_strategy", "evaluation_actual_strategy_counts", "evaluation_fallback_reasons")},
        "fixtures": [], "models": [], "diagnostic_overlap": [],
        "source_hash_mismatches": [name for name, sha in data["code_sha256"].items()
                                   if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha],
        "limitations": ["one observation per executor; no statistical speedup claim",
                        "microbenchmark has a preallocated target and is not a full model load",
                        "GPU event overlap includes waits and is not proof of simultaneous DMA-engine utilization",
                        "LP profile remains provisional; these results do not establish a planning speedup"]}
    for row in data["pipeline_fixture"]["rows"]:
        f = row["result"]
        workers = [w for w in f["execution"] if w["records"]]
        report["fixtures"].append({"executor": row["executor"], "passed": checked(f),
            "execute_wall_s": f["execute_wall_s"], "planner": f["plan"]["strategy"],
            "workers": [{"worker": w["worker"], "execute_s": w["execute_s"],
                         "operation_s": sum(o["elapsed_s"] for o in w["records"]),
                         "setup_and_other_s": w["execute_s"]-sum(o["elapsed_s"] for o in w["records"])} for w in workers]})
    retry = data["pipeline_failure_check"]["retry"]
    report["failure_and_retry_passed"] = data["pipeline_failure_check"]["passed"] and checked(retry)
    for w in retry["execution"]:
        for op in w["records"]:
            report["diagnostic_overlap"].append({"worker": w["worker"], "role": op["role"],
                "copy_collective_event_overlap_ms": overlap(op["pipeline"]["trace"]), "max_slots": op["pipeline"]["max_slots"]})
    for s in data.get("restores", []):
        f, w = s["flow"], windows[s["phase"]]
        p = f["plan"]
        copy_ends = [e["monotonic_ns"] for r in s["timing"]["ranks"].values()
                     for e in r["events"] if e["event"] == "target_copy_complete"]
        passed = (s["passed"] and checked(f, s["verification"])
                  and p["strategy"] == data["requested_strategy"] and not p["fallback_reason"]
                  and s["layout_fingerprint"] == data["comparison_layout"]["fingerprint"]
                  and f["executor"] == s["executor"])
        report["models"].append({"executor": s["executor"], "passed": passed, "load_s": w["load_s"],
            "execute_wall_s": f["execute_wall_s"], "requested_planner": p["requested_strategy"],
            "actual_planner": p["strategy"], "fallback_reason": p["fallback_reason"],
            "byte_plan_sha256": s["byte_plan_sha256"], "source_bytes": f["source_bytes"],
            "verified_bytes": s["verification"]["checked_bytes"], "generation_checks": len(s["replies"]),
            "target_segments_s": w["segments_s"], "target_wait_details_s": w["wait_details_s"],
            "target_copy_finish_skew_s": (max(copy_ends)-min(copy_ends))/1e9,
            "staging_bytes_per_worker": f["execution"][0]["staging_bytes"]})
    if len(report["models"]) == 2:
        old, new = report["models"]
        report["comparison"] = {"byte_plans_equal": old["byte_plan_sha256"] == new["byte_plan_sha256"],
            "load_s_saved": old["load_s"]-new["load_s"], "execution_s_saved": old["execute_wall_s"]-new["execute_wall_s"]}
    report["passed"] = (data["status"] == "passed" and not report["source_hash_mismatches"]
        and data.get("cleanup_errors") == [] and report["failure_and_retry_passed"]
        and all(r["passed"] for r in report["fixtures"])
        and (args.fixture_only or (len(report["models"]) == 2 and all(r["passed"] for r in report["models"])
                                  and report["comparison"]["byte_plans_equal"])))
    (directory / "pipeline_analysis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", required=True)
    parser.add_argument("--fixture-only", action="store_true")
    main(parser.parse_args())
