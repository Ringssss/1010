"""Plot same-host critical-path accounting and measured device activity for the long tail."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser()
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--device-input", type=Path, required=True)
args = parser.parse_args()
# Source: profiling_analysis.json. All times are seconds. Baseline and instrumented
# observations are separate panels, since tracing perturbs wall time.
data = json.loads(args.input.read_text())
device_data = json.loads(args.device_input.read_text())
fig, (ax, bx) = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={"height_ratios": [1, 1.4]})
parts = [("Layout + export", "#a1a9b5", ["entry_and_start_skew", "layout", "export"]),
         ("Plan / map / dispatch", "#c29a50", ["detect_plan_dispatch", "map", "execute_dispatch"]),
         ("Transfer critical path", "#357ab7", ["last_copy_worker_execute"]),
         ("After all copies", "#67a882", ["after_all_copies_until_ready"])]
for i, row in enumerate(data["baseline"]):
    left = 0
    for label, color, keys in parts:
        value = sum(row["segments_s"][k] for k in keys)
        ax.barh(i, value, left=left, color=color, label=label if i == 0 else None, height=.5)
        left += value
    ax.text(left+.02, i, f"{left:.3f} s", va="center")
ax.set_yticks(range(len(data["baseline"])), [r["phase"].replace("pipeline_", "") for r in data["baseline"]])
ax.set_xlim(0, max(r["load_s"] for r in data["baseline"])*1.12)
ax.set_title("Normal runs: weight-load start to both GPUs ready", loc="left")
ax.legend(loc="upper left", bbox_to_anchor=(0, -.25), ncol=4, fontsize=8, frameon=False)
colors = ["#cc8746", "#357ab7", "#67a882", "#e3e6eb"]
labels = ["H2D copy", "RCCL kernel (includes waits)", "D2D / other device work", "Outside device activity"]
row_names = []
for i, w in enumerate(r for r in device_data["profiles"] if r["worker"] in (4, 5, 6, 7)):
    op = max(w["operations"], key=lambda o: o["window_s"])
    groups = op["groups"]
    h2d = sum(g["sum_s"] for g in groups if g["category"] == "HIPCOPY" and g["name"] == "HostToDevice")
    comm = sum(g["sum_s"] for g in groups if g["category"] == "HIPOPS" and "nccl" in g["name"].lower())
    other = sum(g["sum_s"] for g in groups if g["category"] in ("HIPOPS", "HIPCOPY"))-h2d-comm
    # Serial executor should have disjoint device operations; reject a misleading stack otherwise.
    if abs(h2d+comm+other-op["device_activity_union_s"]) > .003:
        raise ValueError("device intervals overlap; cannot stack their summed times")
    gap = op["window_s"]-h2d-comm-other
    left = 0
    for j, value in enumerate((h2d, comm, other, gap)):
        bx.barh(i, value, left=left, color=colors[j], label=labels[j] if i == 0 else None, height=.55)
        if value > .08: bx.text(left+value/2, i, f"{value:.3f}", ha="center", va="center", fontsize=9)
        left += value
    bx.text(left+.02, i, f"{left:.3f} s", va="center")
    row_names.append(f"{w['node']} GPU{w['gpu']} ({'source' if w['worker'] < 6 else 'target'})")
bx.set_yticks(range(len(row_names)), row_names)
bx.set_xlim(0, bx.get_xlim()[1]*1.12)
bx.set_title("Bulk-path diagnostic: 8 GiB per rank, contiguous target (rows run in parallel)", loc="left")
bx.legend(loc="upper left", bbox_to_anchor=(0, -.18), ncol=2, fontsize=9, frameon=False)
for a in (ax, bx):
    a.invert_yaxis(); a.set_xlabel("Seconds"); a.grid(axis="x", alpha=.18); a.set_axisbelow(True)
    for side in ("top", "right"): a.spines[side].set_visible(False)
fig.text(.02, .01, "One sample per mode. Profiling timings are diagnostic, not comparable benchmark scores.", fontsize=9)
fig.tight_layout(h_pad=4, rect=(0, .07, 1, 1))
args.out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(args.out.with_suffix(".png"), dpi=170)
fig.savefig(args.out.with_suffix(".svg"))
