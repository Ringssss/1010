"""Reconstruct task2 stage boundaries from recorded logs without rerunning inference.

Server log timestamps have one-second resolution. Controller durations use a
monotonic clock; reconstructed boundaries must not be treated as precise probes.
"""
import json
from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "codex_coding/results/task2"
summary = json.loads((RESULTS / "loop03/summary.json").read_text())
markers = {
    "config": "server_args=ServerArgs(",
    "tp0_dist_begin": "TP0] Init torch distributed begin.",
    "tp1_dist_begin": "TP1] Init torch distributed begin.",
    "load_begin": "Load weight begin.",
    "load_end": "Load weight end.",
    "http_up": "Uvicorn running on",
    "fired_up": "The server is fired up",
}
report = {"log_timestamp_resolution_s": 1, "phases": {}}
for phase in ("baseline", "capture", "restore1", "restore2"):
    run = "loop02" if phase == "baseline" else "loop03"
    lines = (RESULTS / run / f"{phase}.log").read_text().splitlines()
    events = [json.loads(line) for line in (RESULTS / run / "events.jsonl").read_text().splitlines()]
    launch = next(e["time"] for e in events if e["event"] == "launch" and e["name"] == phase)
    bounds = {"launch": launch}
    for name, marker in markers.items():
        timestamps = []
        for line in lines:
            if marker not in line:
                continue
            matched = re.search(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
            stamp = datetime.strptime(matched[1], "%Y-%m-%d %H:%M:%S")
            timestamps.append(stamp.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        bounds[name] = max(timestamps)
    measured = summary["phases"][phase]
    bounds["ready"] = launch + measured["ready_s"]
    order = ["launch", "config", "tp0_dist_begin", "load_begin", "load_end", "http_up", "ready"]
    intervals = {f"{a}_to_{b}": bounds[b] - bounds[a] for a, b in zip(order, order[1:])}
    row = {"bounds_epoch_s": bounds, "intervals_s": intervals, "ready_s": measured["ready_s"],
           "pre_weight_s": bounds["load_begin"] - launch,
           "weight_window_s": bounds["load_end"] - bounds["load_begin"],
           "post_weight_s": bounds["ready"] - bounds["load_end"],
           "rank_arrival_gap_s": bounds["tp1_dist_begin"] - bounds["tp0_dist_begin"],
           "request_s": [r["elapsed_s"] for r in measured["replies"]]}
    row["physical_vram_GiB"] = {
        name: sum(int(measured[name][f"card{gpu}"]["vram Total Used Memory (MiB)"])
                  for gpu in range(2)) / 1024
        for name in ("memory_ready", "memory_stopped") if name in measured}
    if "flow" in measured:
        row["controller_flow_s"] = measured["flow_s"]
        row["flow_workers"] = [{"gpu": gpu, "prep_s": f["prep_s"],
                                "after_prep_s": f["total_s"] - f["prep_s"],
                                "total_s": f["total_s"], "bytes": f["h2d_bytes"]}
                               for gpu, f in enumerate(measured["flow"])]
    report["phases"][phase] = row
    print(phase, json.dumps(intervals), "total_s:", measured["ready_s"],
          "vram_GiB:", row["physical_vram_GiB"])
baseline = report["phases"]["baseline"]
report["restore2_minus_baseline_s"] = {
    field: report["phases"]["restore2"][field] - baseline[field]
    for field in ("pre_weight_s", "weight_window_s", "post_weight_s", "ready_s")}
report["capture_workers"] = summary["phases"]["capture"]["workers"]
report["pinned_allocation_prediction"] = {
    "requested_bytes_per_rank": 35164859056,
    "power_of_two_allocation_bytes_per_rank": 1 << (35164859056 - 1).bit_length(),
    "basis": "installed ATen/core/CachingHostAllocator.h:251, PowerOf2Ceil(size)",
    "full_run_allocator_stats_collected": False,
}
(RESULTS / "loop03/breakdown.json").write_text(json.dumps(report, indent=2))
print("delta", json.dumps(report["restore2_minus_baseline_s"], indent=2))
