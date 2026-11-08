"""Audit fixed-layout comparisons, full content checks, timing and retained replica costs."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]


def main(args):
    directory = Path(args.run_dir)
    subprocess.run([sys.executable, str(Path(__file__).with_name("analyze_loading_windows.py")),
                    "--run-dir", str(directory)], check=True, stdout=subprocess.DEVNULL)
    raw = (directory / "summary.json").read_bytes()
    data = json.loads(raw)
    windows = {s["phase"]: s for s in json.loads((directory / "loading_windows.json").read_text())["samples"]}
    replicas = {r["id"]: r for r in data["comparison_layout"]["replicas"]}
    report = {"run": data["run"], "status": data["status"], "development": data["development"],
        "summary_sha256": hashlib.sha256(raw).hexdigest(), "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "order": data["variant_order"], "handoff_poll_s": data["handoff_poll_s"], "rows": [],
        "retained_GiB": {tier: sum(r["allocated_bytes"] for r in replicas.values() if r["tier"] == tier)/2**30
                         for tier in ("dram", "hbm")},
        "source_hash_mismatches": [name for name, sha in data["code_sha256"].items()
                                   if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha],
        "limitations": ["one observation per policy in fixed order; startup variation is retained",
                        "all policies retain the same physical replicas, including sources they do not read",
                        "the LP capacity profile remains provisional, not calibrated from these samples",
                        "source utilization is not proof of replica benefit; this is an initial screening"]}
    for s in data["restores"]:
        w, flow = windows[s["phase"]], s["flow"]
        rank_events = {rank: {e["event"]: e["monotonic_ns"] for e in r["events"]}
                       for rank, r in s["timing"]["ranks"].items()}
        exports = [r["target_exported"] for r in rank_events.values()]
        critical_events = rank_events[w["critical_rank"]]
        peer_wait = (max(exports)-critical_events["target_exported"])/1e9
        post_exports = (critical_events["target_accepted"]-max(exports))/1e9
        if post_exports < 0: raise ValueError("keeper accepted before all exports were published")
        expected, actual = {}, {}
        for o in flow["plan"]["ops"]:
            rid = o["replica_id"]; expected[rid] = expected.get(rid, 0)+o["n"]
        for worker in flow["execution"]:
            for op in worker["records"]:
                if op["role"] in ("source", "both"):
                    rid = op["replica_id"]; actual[rid] = actual.get(rid, 0)+op["bytes"]
        passed = (s["passed"] and s["verification"]["passed"] and expected == actual == flow["source_bytes"]
                  and s["verification"]["checked_bytes"] == sum(t["n"] for t in flow["plan"]["targets"])
                  and s["layout_fingerprint"] == data["comparison_layout"]["fingerprint"]
                  and w["wait_details_s"] is not None)
        report["rows"].append({"policy": s["policy"], "load_s": w["load_s"], "passed": passed,
            "target_segments_s": w["segments_s"], "target_wait_details_s": w["wait_details_s"],
            "pre_mapping_detail_s": {"peer_export_wait_s": peer_wait, "after_all_exports_to_accept_s": post_exports},
            "all_rank_arrival_skew_s": s["timing"]["arrival_skew_s"],
            "all_rank_export_skew_s": (max(exports)-min(exports))/1e9,
            "plan_wall_s": s["plan_wall_s"], "planner_rpc_breakdown_s": flow["plan"]["control_durations_s"],
            "coordinator_s": {k: flow[k] for k in ("inventory_rpc_s", "prepare_rpc_s", "execute_wall_s", "verify_and_publish_s")},
            "source_GiB": {rid: n/2**30 for rid, n in actual.items()},
            "extra_source_GiB": sum(n for rid, n in actual.items() if "/remote/" not in rid)/2**30,
            "verified_bytes": s["verification"]["checked_bytes"], "generation_checks": len(s["replies"]),
            "model_transfer_estimate_s": flow["plan"]["estimated_transfer_s"]})
    builds = {"hbm": data["model_gpu_placement"]["transfer"], "local_dram": data["model_joint_placement"]["transfer"]}
    report["extra_replica_build_transactions_s"] = {
        kind: {k: flow[k] for k in ("prepare_s", "execute_wall_s", "verify_and_publish_s", "coordinator_wall_s")}
        for kind, flow in builds.items()}
    baseline = next(r for r in report["rows"] if r["policy"] == "primary_only")
    extra_cost = sum(f["coordinator_wall_s"] for f in builds.values())
    for r in report["rows"]:
        saving = baseline["load_s"]-r["load_s"]
        r["observed_load_saving_vs_primary_s"] = saving
        r["illustrative_build_amortization_reuses"] = math.ceil(extra_cost/saving) if saving > 0 else None
    report["amortization_scope"] = "illustrative algebra only; one-sample load differences include startup noise; build transactions exclude placement planning and memory opportunity cost"
    def signature(sample):
        fields = ("src_w", "dst_w", "tp", "off", "n", "replica_id", "generation", "source_offset", "kind")
        return sorted(tuple(o[k] for k in fields) for o in sample["flow"]["plan"]["ops"])
    report["identical_byte_plans"] = [[a["policy"], b["policy"]] for i, a in enumerate(data["restores"])
                                      for b in data["restores"][i+1:] if signature(a) == signature(b)]
    report["passed"] = (data["status"] == "passed" and len(report["rows"]) == 4
                        and {r["policy"] for r in report["rows"]} == set(data["variant_order"])
                        and all(r["passed"] for r in report["rows"])
                        and not report["source_hash_mismatches"] and data.get("cleanup_errors") == [])
    (directory / "policy_analysis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", required=True)
    main(parser.parse_args())
