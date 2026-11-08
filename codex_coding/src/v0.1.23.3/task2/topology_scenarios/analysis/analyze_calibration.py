"""Offline derived tables for a completed topology run; never imports Torch or runs probes."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/topology_scenarios"


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compute(summary):
    cases = []
    for observation in summary["observations"]:
        flows = []
        for flow in observation["flows"]:
            duration_ns = flow["receiver_end_ns"]-flow["receiver_start_ns"]
            rate = flow["bytes"] / duration_ns
            assert abs(rate-flow["empirical_effective_GBps"]) < 1e-9
            intervals = flow["chunk_intervals"]
            call_ns = sum(end-start for start, end in intervals)
            window_gap_ns = duration_ns-call_ns
            warmup_ns = flow["warmup"]["end_ns"]-flow["warmup"]["start_ns"]
            flows.append({"flow_id": flow["flow_id"], "src": flow["src"], "dst": flow["dst"],
                "node": flow["node"], "gpu": flow["gpu"], "numa": flow["numa"], "bytes": flow["bytes"],
                "duration_ns": duration_ns, "duration_ms": duration_ns/1e6, "GBps": rate,
                "warmup_ms": warmup_ns/1e6, "synchronous_call_ms": call_ns/1e6,
                "window_gap_ms": window_gap_ns/1e6, "clock_domain": flow["clock_domain"],
                "verified_bytes": flow["correctness"]["verified_bytes"]})
        start = min(f["receiver_start_ns"] for f in observation["flows"])
        end = max(f["receiver_end_ns"] for f in observation["flows"])
        common_ns = end-start
        cases.append({"case_id": observation["case_id"], "kind": observation["kind"],
            "node": observation["node"], "mode": observation["mode"], "flows": flows,
            "common_makespan_ns": common_ns, "common_makespan_ms": common_ns/1e6,
            "common_aggregate_GBps": sum(f["bytes"] for f in flows)/common_ns,
            "overlap": observation["overlap"], "nic": observation.get("actual_nic_payload_evidence"),
            "nic_windows": [{"flow_id": f["flow_id"],
                "snapshot_ms": (f["nic_snapshot_after"]["monotonic_ns"]-f["nic_snapshot_before"]["monotonic_ns"])/1e6,
                "deltas": f["nic_counter_delta"]["deltas"]} for f in observation["flows"]]})
    indexed = {case["case_id"]: case for case in cases}
    pairs = []
    for prefix in ("network", "h2d_node27", "h2d_node29"):
        pair = indexed[prefix+"_pair"]
        singles = {i: indexed[prefix+"_single_"+letter]["flows"][0] for i, letter in enumerate("ab")}
        expected_ns = max(f["duration_ns"] for f in singles.values())
        short_ns = min(f["duration_ns"] for f in singles.values())
        raw = next(row for row in summary["observations"] if row["case_id"] == pair["case_id"])
        skew_ns = max(f["receiver_start_ns"] for f in raw["flows"])-min(f["receiver_start_ns"] for f in raw["flows"])
        ratio = expected_ns / pair["common_makespan_ns"]
        pairs.append({"case_id": pair["case_id"], "independent_expected_ms": expected_ns/1e6,
            "pair_makespan_ms": pair["common_makespan_ms"], "independence_completion_ratio": ratio,
            "elapsed_increase_percent": (1/ratio-1)*100,
            "pair_common_aggregate_GBps": pair["common_aggregate_GBps"],
            "isolated_rate_sum_GBps": sum(f["GBps"] for f in singles.values()),
            "receiver_start_skew_us": skew_ns/1e3, "start_skew_fraction": skew_ns/short_ns,
            "overlap_fraction": pair["overlap"]["overlap_fraction"],
            "per_flow_slowdown": {str(f["flow_id"]): f["duration_ns"]/singles[f["flow_id"]]["duration_ns"] for f in pair["flows"]},
            "exploratory_degradation": ratio <= .85 and skew_ns/short_ns <= .05 and pair["overlap"]["overlap_fraction"] >= .8})
    return cases, pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    if RESULT_ROOT not in out.parents or not out.name.startswith("calibration_analysis_"):
        raise ValueError("use a new calibration_analysis_* output directory")
    summary = load(run / "summary.json")
    assert summary["status"] == "passed"
    before, after = load(run / "inventory_before.json"), load(run / "inventory_after.json")
    audit = load(args.audit / "summary.json")
    cases, pairs = compute(summary)
    assert len(cases) == 9 and len(pairs) == 3
    out.mkdir(parents=True, exist_ok=False)
    files = [run / name for name in ("summary.json", "protocol.json", "inventory_before.json", "inventory_after.json")]
    files += [run / f"rank_{rank}.process.json" for rank in range(4)]
    files += [args.audit / "summary.json", args.audit / "report.md"]
    application_paths = [ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01" / f"worker_{node}_{gpu}.process.json"
                         for node in ("node27", "node29") for gpu in (0, 1)]
    session = load(summary["session"])
    application_paths += [Path(path) for _, path in session["new_engine_processes"]]
    applications = [{"path": str(path), "sha256": sha(path), "env": load(path)["env"]} for path in application_paths]
    files += application_paths
    result = {"status": "passed", "development": "v0.1.23.3", "task": "task2", "mode": "offline_analysis",
        "source_run": str(run), "new_gpu_samples": 0, "unique_source_configuration_observations": 9,
        "input_sha256": {str(p): sha(p) for p in files}, "analysis_script_sha256": sha(__file__),
        "cases": cases, "pairs": pairs, "application_process_specs": applications,
        "capacity_is_physical_upper_bound": False, "application_profile_ready": False,
        "hardware": {node: {"gpus": [gpu for gpu in data["topology"]["gpus"] if gpu["logical_gpu"] in (0, 1)],
                            "io": [row for row in data["topology"]["io"] if row["name"] in ("ib0", "shca_0")],
                            "fingerprint": data["topology"]["fingerprint"]} for node, data in before["nodes"].items()},
        "preservation": summary["before_after_preservation"], "cleanup": summary["cleanup"],
        "rank_resource_sampling": summary["rank_resource_sampling"], "shared_resource": summary["shared_resource"],
        "audit_status": "passed" if audit["passed"] else "failed", "audit_checks": len(audit.get("checks", []))}
    (out / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n")
    fields = ["case_id", "flow_a_ms", "flow_a_GBps", "flow_b_ms", "flow_b_GBps", "common_makespan_ms",
              "common_aggregate_GBps", "independence_completion_ratio", "overlap_fraction", "start_skew_us"]
    pair_map = {pair["case_id"]: pair for pair in pairs}
    with (out / "rates.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for case in cases:
            row = {"case_id": case["case_id"], "common_makespan_ms": f"{case['common_makespan_ms']:.9f}",
                   "common_aggregate_GBps": f"{case['common_aggregate_GBps']:.9f}"}
            for flow in case["flows"]:
                letter = "a" if flow["flow_id"] == 0 else "b"
                row[f"flow_{letter}_ms"] = f"{flow['duration_ms']:.9f}"
                row[f"flow_{letter}_GBps"] = f"{flow['GBps']:.9f}"
            if case["case_id"] in pair_map:
                pair = pair_map[case["case_id"]]
                row.update(independence_completion_ratio=f"{pair['independence_completion_ratio']:.9f}",
                           overlap_fraction=f"{pair['overlap_fraction']:.9f}", start_skew_us=f"{pair['receiver_start_skew_us']:.6f}")
            writer.writerow(row)
    print(json.dumps({"cases": cases, "pairs": pairs, "hardware": result["hardware"],
                      "resource": summary["rank_resource_sampling"], "reclamation": summary["cleanup"]["physical_hbm_reclamation"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
