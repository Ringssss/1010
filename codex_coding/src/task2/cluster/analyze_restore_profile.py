"""Attribute restore windows and HIP device activity without summing concurrent workers."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3


def loading(sample):
    ranks = sample["timing"]["ranks"]
    assert len({r["host"] for r in ranks.values()}) == 1
    events = {k: {e["event"]: e["monotonic_ns"] for e in r["events"]} for k, r in ranks.items()}
    last = max(events, key=lambda k: events[k]["target_copy_complete"])
    start = min(e["load_begin"] for e in events.values())
    end = max(e["weights_ready"] for e in events.values())
    e = events[last]
    boundaries = [start, e["model_layout_begin"], e["model_layout_end"], e["target_exported"],
                  e["target_accepted"], e["target_mapped"], e["target_execute_begin"],
                  e["target_copy_complete"], end]
    assert boundaries == sorted(boundaries)
    names = ["entry_and_start_skew", "layout", "export", "detect_plan_dispatch", "map",
             "execute_dispatch", "last_copy_worker_execute", "after_all_copies_until_ready"]
    segments = dict(zip(names, [(b-a)/1e9 for a, b in zip(boundaries, boundaries[1:])]))
    assert abs(sum(segments.values())-sample["timing"]["load_s"]) < 1e-8
    workers = [{"worker": w["worker"], "execute_s": w["execute_s"],
                "ops": [{k: op[k] for k in ("op_id", "bytes", "role", "kind", "elapsed_s")}
                        for op in w["records"]]} for w in sample["flow"]["execution"] if w["records"]]
    return {"phase": sample["phase"], "load_s": (end-start)/1e9, "last_copy_rank": last,
            "segments_s": segments, "workers": workers,
            "copy_finish_skew_s": (max(e["target_copy_complete"] for e in events.values()) -
                                   min(e["target_copy_complete"] for e in events.values()))/1e9}


def union_ns(intervals):
    end, total = None, 0
    for a, b in sorted(intervals):
        total += max(0, b-max(a, end if end is not None else a))
        end = max(b, end if end is not None else b)
    return total


def trace_stats(events, begin, end):
    if not events:
        return {"groups": [], "device_activity_union_s": None, "window_s": (end-begin)/1e9,
                "outside_traced_device_activity_s": None}
    groups = defaultdict(list)
    for e in events:
        if e.get("ph") != "X" or "BeginNs" not in e.get("args", {}): continue
        a, b = int(e["args"]["BeginNs"]), int(e["args"]["EndNs"])
        if a < begin or b > end: continue
        name = e["name"]
        if e.get("cat") == "HIPCOPY":
            # This vendor trace labels the first D2D copy as D2H despite both
            # memory kinds being Device and the correlated API having kind=3.
            src, dst = e["args"].get("source memory kind"), e["args"].get("destination memory kind")
            if src == dst == "Device": name = "DeviceToDevice"
        key = (e.get("cat"), name)
        groups[key].append(e)
    result = []
    device = []
    for (cat, name), rows in groups.items():
        durations = [int(e["args"]["EndNs"])-int(e["args"]["BeginNs"]) for e in rows]
        byte_count = sum(int(e["args"].get("Bytes", 0)) for e in rows)
        duration = sum(durations)/1e9
        result.append({"category": cat, "name": name, "calls": len(rows), "sum_s": duration,
                       "raw_names": sorted({e["name"] for e in rows}),
                       "max_ms": max(durations)/1e6, "bytes": byte_count,
                       "GBps_during_activity": byte_count/duration/1e9 if byte_count and duration else None})
        if cat in ("HIPCOPY", "HIPOPS"):
            device += [(int(e["args"]["BeginNs"]), int(e["args"]["EndNs"])) for e in rows]
    active = union_ns(device)/1e9
    return {"groups": sorted(result, key=lambda x: -x["sum_s"]),
            "device_activity_union_s": active, "window_s": (end-begin)/1e9,
            "outside_traced_device_activity_s": (end-begin)/1e9-active}


def analyze(directory, baseline):
    data = json.loads((directory/"summary.json").read_text())
    report = {"development": data["development"], "status": data["status"],
              "baseline": [], "instrumented": [loading(s) for s in data["restores"]], "profiles": [],
              "limitations": ["single diagnostic observation with profiler overhead",
                              "RCCL kernel residency includes waiting for peer/data, not pure network transmission",
                              "host API, GPU activities and parallel workers overlap and must not be added",
                              "cross-node absolute clocks are not aligned; only within-process intervals used"]}
    if baseline:
        old = json.loads((baseline/"summary.json").read_text())
        report["baseline"] = [loading(s) for s in old["restores"]]
        if data["restores"]:
            report["byte_plan_matches_baseline"] = data["restores"][0]["byte_plan_sha256"] == old["restores"][0]["byte_plan_sha256"]
    members = [("node27", g) for g in range(4)]+[("node28", g) for g in (2, 3)]+[("node29", g) for g in (0, 1)]
    for worker, (node, gpu) in enumerate(members):
        candidates = list((directory/"profiles").glob(f"worker{worker}_*.json"))
        if not candidates: continue
        host = json.loads(candidates[0].read_text())
        path = directory/"profiles"/f"worker_{node}_{gpu}.json"
        events = json.loads(path.read_text())["traceEvents"] if path.exists() else []
        begin, end = host["begin"], host["end"]
        row = {"worker": worker, "node": node, "gpu": gpu,
               "execute_wall_s": (end["monotonic_ns"]-begin["monotonic_ns"])/1e9,
               "thread_cpu_s": (end["thread_cpu_ns"]-begin["thread_cpu_ns"])/1e9,
               "scheduler_runqueue_wait_s": (end["scheduler"][1]-begin["scheduler"][1])/1e9,
               "voluntary_switches": end["voluntary_switches"]-begin["voluntary_switches"],
               "involuntary_switches": end["involuntary_switches"]-begin["involuntary_switches"],
               "top_cpu_functions": host["cpu_functions"][:12],
               "trace_events": len(events), "device_trace_available": bool(events),
               "trace_categories": sorted({e.get("cat", "") for e in events}),
               "operations": []}
        for op in host["operations"]:
            a, b = op["begin"], op["end"]
            row["operations"].append({"op_id": op["op_id"],
                "thread_cpu_s": (b["thread_cpu_ns"]-a["thread_cpu_ns"])/1e9,
                "scheduler_wait_s": (b["scheduler"][1]-a["scheduler"][1])/1e9,
                **trace_stats(events, a["wall_ns"], b["wall_ns"])})
        report["profiles"].append(row)
    report["cleanup_errors"] = data.get("cleanup_errors")
    report["source_hash_mismatches_at_run_end"] = data.get("source_hash_mismatches", [])
    report["analyzer_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["verified_bytes"] = [s["verification"]["checked_bytes"] for s in data["restores"]]
    report["planner"] = [{k: s["flow"]["plan"][k] for k in ("requested_strategy", "strategy", "fallback_reason")}
                         for s in data["restores"]]
    if "profile_transfer" in data:
        flow = data["profile_transfer"]
        report["bulk_transfer"] = {"rank_bytes": data["rank_bytes"], "execute_wall_s": flow["execute_wall_s"],
                                   "verification": flow["verification"], "plan": flow["plan"]}
        report["limitations"].append("bulk diagnostic uses contiguous synthetic targets, not the real model's scattered storages")
        coverage = []
        for w in report["profiles"]:
            for op in w["operations"]:
                expected = next(o["n"] for o in flow["plan"]["ops"] if o["op_id"] == op["op_id"])
                actual = sum(g["bytes"] for g in op["groups"] if g["category"] == "HIPCOPY")
                coverage.append({"worker": w["worker"], "op_id": op["op_id"],
                                 "expected_bytes": expected, "trace_copy_bytes": actual, "passed": actual == expected})
        report["trace_byte_coverage"] = coverage
        report["diagnostic_usable"] = (len(coverage) == 4 and all(c["passed"] for c in coverage)
            and flow["verification"]["passed"] and data.get("cleanup_errors") == []
            and set(data.get("source_hash_mismatches", [])) <= {"codex_coding/src/task2/cluster/analyze_restore_profile.py"})
        report["provenance_note"] = "Harness status is retained verbatim. Only the offline analyzer changed during capture; execution library and launcher hashes matched. This is a diagnostic, not a clean benchmark acceptance."
    report["summary_sha256"] = hashlib.sha256((directory/"summary.json").read_bytes()).hexdigest()
    (directory/"profiling_analysis.json").write_text(json.dumps(report, indent=2))
    brief = {k: v for k, v in report.items() if k not in ("profiles", "baseline", "instrumented")}
    brief["windows"] = [{k: row[k] for k in ("phase", "load_s", "segments_s", "copy_finish_skew_s")}
                        for row in report["baseline"]+report["instrumented"]]
    brief["profiles"] = [{k: v for k, v in row.items() if k not in ("top_cpu_functions", "operations")}
                         for row in report["profiles"]]
    print(json.dumps(brief, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--schema", type=Path)
    args = parser.parse_args()
    if args.schema:
        con = sqlite3.connect(f"file:{args.schema}?mode=ro", uri=True)
        for name, sql in con.execute("select name, sql from sqlite_master where type='table'"):
            print(name, sql)
            print(con.execute('select * from "'+name.replace('"', '""')+'" limit 1').fetchall())
    else:
        analyze(args.run_dir, args.baseline)
