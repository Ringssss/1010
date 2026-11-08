"""Account for target-local loading windows without subtracting cross-host timestamps."""
import argparse
import hashlib
import json
from pathlib import Path

BOUNDARIES = ["load_begin", "model_layout_begin", "model_layout_end", "target_exported",
              "target_filled", "weights_ready"]
NAMES = ["loader_entry_s", "model_layout_s", "export_s", "target_wait_s", "finalize_s"]


def analyze(directory):
    raw = (directory / "summary.json").read_bytes()
    data = json.loads(raw)
    report = {"run": data["run"], "status": data["status"], "samples": [],
              "summary_sha256": hashlib.sha256(raw).hexdigest(),
              "scope": "exact additive target-host segments; coordinator durations are nested diagnostics, not extra load time"}
    for sample in data["restores"]:
        ranks = sample["timing"]["ranks"]
        critical = max(ranks, key=lambda k: ranks[k]["end_ns"])
        first = min(r["begin_ns"] for r in ranks.values())
        if len({r["host"] for r in ranks.values()}) != 1:
            raise ValueError("target clocks span hosts")
        points = {}
        for event in ranks[critical]["events"]:
            if event["event"] in points: raise ValueError("duplicate event")
            points[event["event"]] = event["monotonic_ns"]
        values = [points[b] for b in BOUNDARIES]
        if values != sorted(values): raise ValueError("invalid event order")
        segments = {"critical_rank_start_skew_s": (values[0]-first)/1e9}
        segments.update(zip(NAMES, [(b-a)/1e9 for a, b in zip(values, values[1:])]))
        if abs(sum(segments.values())-sample["timing"]["load_s"]) > 1e-8:
            raise ValueError("segments do not sum to total")
        detail_order = ["target_exported", "target_accepted", "target_mapped", "target_execute_begin",
                        "target_copy_complete", "target_ready_publish", "target_filled"]
        detail_names = ["detection_planning_dispatch_s", "target_mapping_s", "execute_dispatch_wait_s",
                        "worker_execution_s", "completion_coordination_s", "ready_notice_wait_s"]
        detailed = None
        if all(k in points for k in detail_order):
            stamps = [points[k] for k in detail_order]
            if stamps != sorted(stamps): raise ValueError("invalid detailed event order")
            detailed = dict(zip(detail_names, [(b-a)/1e9 for a, b in zip(stamps, stamps[1:])]))
        flow = sample["flow"]
        result = {"phase": sample["phase"], "node": sample["node"], "strategy": sample["strategy"],
                  "load_s": sample["timing"]["load_s"], "critical_rank": critical,
                  "segments_s": segments, "wait_details_s": detailed,
                  "coordinator_durations_s": {k: flow[k] for k in
                      ("prepare_s", "execute_wall_s", "verify_and_publish_s", "coordinator_wall_s")},
                  "coordinator_plan_s": sample.get("plan_wall_s"),
                  "source_bytes": flow["source_bytes"], "passed": sample["passed"]}
        report["samples"].append(result)
        print(json.dumps(result, ensure_ascii=False))
    (directory / "loading_windows.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", required=True)
    analyze(Path(parser.parse_args().run_dir))
