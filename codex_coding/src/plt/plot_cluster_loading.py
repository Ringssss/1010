"""Plot exact target-host load segments from the fixed-layout policy analysis (seconds)."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
# Input: analyze_policy_comparison.py output. Each row is one complete model restore,
# with nested target_wait stages replacing that interval, never added a second time.
data = json.loads(Path(args.input).read_text())
rows = data["rows"]
labels = {"primary_only": "Primary only", "nearest": "Nearest source",
          "balanced": "Balanced split", "resource_lp": "Resource LP",
          "legacy": "Legacy control", "compact": "Compact control"}
parts = [
    ("Model layout + export", "#a1a9b5", lambda r: sum(r["target_segments_s"][k] for k in
        ("critical_rank_start_skew_s", "loader_entry_s", "model_layout_s", "export_s"))),
    ("Peer readiness / planning / dispatch", "#e2ab52", lambda r: r["target_wait_details_s"]["detection_planning_dispatch_s"]),
    ("Mapping + dispatch wait", "#ab8cce", lambda r: sum(r["target_wait_details_s"][k] for k in
        ("target_mapping_s", "execute_dispatch_wait_s"))),
    ("Transfer + source waits", "#377eb8", lambda r: r["target_wait_details_s"]["worker_execution_s"]),
    ("Completion + finalization", "#69ad89", lambda r: sum(r["target_wait_details_s"][k] for k in
        ("completion_coordination_s", "ready_notice_wait_s"))+r["target_segments_s"]["finalize_s"]),
]
fig, ax = plt.subplots(figsize=(10, 4.6))
left = [0.0]*len(rows)
for label, color, getter in parts:
    values = [getter(r) for r in rows]
    ax.barh(range(len(rows)), values, left=left, label=label, color=color, height=0.6)
    left = [a+b for a, b in zip(left, values)]
for i, (total, row) in enumerate(zip(left, rows)):
    if abs(total-row["load_s"]) > 1e-8: raise ValueError("stack does not sum to load window")
    ax.text(total+0.025, i, f"{total:.3f} s", va="center", fontsize=10)
ax.set_yticks(range(len(rows)), [labels[r.get("policy", r.get("control_mode"))] for r in rows]); ax.invert_yaxis()
ax.set_xlabel("Weight-load start to all target GPUs ready (seconds)")
ax.set_title("Qwen TP2 restore on node29 — identical retained replicas", loc="left", fontsize=13)
ax.set_xlim(0, max(left)*1.13); ax.set_axisbelow(True); ax.grid(axis="x", alpha=0.2)
for side in ("top", "right", "left"): ax.spines[side].set_visible(False)
ax.legend(loc="upper left", bbox_to_anchor=(0, -0.19), ncol=2, frameon=False, fontsize=9)
fig.text(0.02, 0.015, "One observation per configuration; fixed order. Control-plane and target clocks are never subtracted across hosts.", fontsize=8)
fig.subplots_adjust(left=0.17, right=0.96, top=0.87, bottom=0.32)
out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(out.with_suffix(".png"), dpi=180); fig.savefig(out.with_suffix(".svg"))
print(out.with_suffix(".png"))
