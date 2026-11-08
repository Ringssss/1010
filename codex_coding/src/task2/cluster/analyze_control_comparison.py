"""Attribute matched model restores to control-plane bytes and target-host timing stages."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]


def main(args):
    directory = Path(args.run_dir)
    subprocess.run([sys.executable, str(Path(__file__).with_name("analyze_loading_windows.py")),
                    "--run-dir", str(directory)], check=True, stdout=subprocess.DEVNULL)
    raw = (directory / "summary.json").read_bytes(); data = json.loads(raw)
    windows = {r["phase"]: r for r in json.loads((directory / "loading_windows.json").read_text())["samples"]}
    report = {"run": data["run"], "development": data["development"], "status": data["status"],
              "summary_sha256": hashlib.sha256(raw).hexdigest(),
              "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "rows": [], "planner_probes": data.get("control_planner_probes", []),
              "source_hash_mismatches": [name for name, sha in data["code_sha256"].items()
                                         if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha],
              "limitations": ["one observation per mode, legacy first; no statistical speedup claim",
                              "control byte counts are JSON bodies, excluding HTTP/TCP headers",
                              "per-request timings overlap; use coordinator wall durations for totals",
                              "target events share one host clock; no cross-host timestamp subtraction"]}
    manifests = []
    for s in data["restores"]:
        flow, plan = s["flow"], s["flow"]["plan"]
        w = windows[s["phase"]]
        events = {rank: {e["event"]: e["monotonic_ns"] for e in r["events"]}
                  for rank, r in s["timing"]["ranks"].items()}
        exports = [r["target_exported"] for r in events.values()]
        critical = events[w["critical_rank"]]
        rpc = plan["inventory_rpc"] + [plan["planner_rpc"]] + [r for phase in flow["rpc"].values() for r in phase]
        expected, actual = {}, {}
        for o in plan["ops"]:
            rid = o["replica_id"]; expected[rid] = expected.get(rid, 0)+o["n"]
        for worker in flow["execution"]:
            for o in worker["records"]:
                if o["role"] in ("source", "both"):
                    rid = o["replica_id"]; actual[rid] = actual.get(rid, 0)+o["bytes"]
        layout = [{k: t[k] for k in ("identity", "meta", "index", "worker", "tp", "off", "n", "mode")}
                  for t in plan["targets"]]
        manifests.append(layout)
        passed = (s["passed"] and s["verification"]["passed"] and expected == actual == flow["source_bytes"]
                  and s["verification"]["checked_bytes"] == sum(t["n"] for t in plan["targets"])
                  and s["layout_fingerprint"] == data["comparison_layout"]["fingerprint"]
                  and w["wait_details_s"] is not None)
        report["rows"].append({"control_mode": s["control_mode"], "passed": passed, "load_s": w["load_s"],
            "plan_wall_s": s["plan_wall_s"], "plan_control_s": plan["control_durations_s"],
            "coordinator_s": {k: flow[k] for k in ("inventory_rpc_s", "prepare_rpc_s", "execute_wall_s", "verify_and_publish_s")},
            "target_segments_s": w["segments_s"], "target_wait_details_s": w["wait_details_s"],
            "peer_export_wait_s": (max(exports)-critical["target_exported"])/1e9,
            "after_all_exports_to_accept_s": (critical["target_accepted"]-max(exports))/1e9,
            "planner_request_bytes": plan["planner_rpc"]["request_bytes"],
            "planner_response_bytes": plan["planner_rpc"]["response_bytes"],
            "inventory_response_bytes": sum(r["response_bytes"] for r in plan["inventory_rpc"]+flow["rpc"]["inventory"]),
            "prepare_request_bytes": sum(r["request_bytes"] for r in flow["rpc"]["prepare"]),
            "control_json_bytes": sum(r["request_bytes"]+r["response_bytes"] for r in rpc),
            "control_rpc_calls": len(rpc), "epoch_snapshot_reused": flow["epoch_snapshot_reused"],
            "source_bytes": actual, "verified_bytes": s["verification"]["checked_bytes"],
            "generation_checks": len(s["replies"]), "byte_plan_sha256": s["byte_plan_sha256"]})
    modes = {r["control_mode"]: r for r in report["rows"]}
    if set(modes) == {"legacy", "compact"}:
        old, new = modes["legacy"], modes["compact"]
        report["improvement"] = {"load_seconds_saved": old["load_s"]-new["load_s"],
            "load_fraction_reduction": 1-new["load_s"]/old["load_s"],
            "control_json_fraction_reduction": 1-new["control_json_bytes"]/old["control_json_bytes"],
            "plan_seconds_saved": old["plan_wall_s"]-new["plan_wall_s"],
            "byte_plans_equal": old["byte_plan_sha256"] == new["byte_plan_sha256"],
            "target_manifests_equal": manifests[0] == manifests[1]}
    report["passed"] = (data["status"] == "passed" and len(report["rows"]) == 2 and set(modes) == {"legacy", "compact"}
                        and all(r["passed"] for r in report["rows"])
                        and report.get("improvement", {}).get("byte_plans_equal", False)
                        and report.get("improvement", {}).get("target_manifests_equal", False)
                        and all(p["ops_and_manifests_equal"] for p in report["planner_probes"])
                        and not report["source_hash_mismatches"] and data.get("cleanup_errors") == [])
    (directory / "control_analysis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "planner_probes"}, indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", required=True)
    main(parser.parse_args())
