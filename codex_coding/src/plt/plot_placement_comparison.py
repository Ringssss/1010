"""Keep modeled transfer cost and actual complete loading time on separate panels."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser(); parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
# Input is placement_analysis.json. Left: predicted transfer seconds under the
# same candidate/budget model. Right: actual same-host additive load intervals.
data = json.loads(args.input.read_text())
fig, (ax, bx) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1.1, 1]})
names = {"base_only": "Remote DRAM only", "existing": "Existing placement", "single_greedy": "One-at-a-time greedy",
         "paired_greedy": "Paired greedy", "milp": "Joint MILP", "balanced_local": "Balanced local cache"}
rows = data["predicted_policies"]
values = [r["predicted_transfer_s"] for r in rows]
ax.barh(range(len(rows)), values, color=["#387daf" if r["policy"] == data["selected_policy"] else "#afb8c3" for r in rows])
ax.set_yticks(range(len(rows)), [names[r["policy"]] for r in rows]); ax.invert_yaxis()
for i, v in enumerate(values): ax.text(v+.02, i, f"{v:.3f}", va="center", fontsize=9)
ax.set_xlim(0, max(values)*1.18); ax.set_xlabel("Predicted transfer seconds")
ax.set_title("Same budget, finite candidate model", loc="left")
parts = [("Layout + export", "#a1a9b5", ["entry_and_start_skew", "layout", "export"]),
         ("Plan / map / dispatch", "#c29a50", ["detect_plan_dispatch", "map", "execute_dispatch"]),
         ("Transfer critical path", "#357ab7", ["last_copy_worker_execute"]),
         ("Final readiness", "#67a882", ["after_all_copies_until_ready"])]
for i, row in enumerate(data["measured"]):
    left = 0
    for label, color, keys in parts:
        value = sum(row["segments_s"][k] for k in keys)
        bx.barh(i, value, left=left, color=color, height=.48, label=label if i == 0 else None)
        left += value
    bx.text(left+.025, i, f"{left:.3f} s", va="center", fontsize=9)
bx.set_yticks([0, 1], ["Existing", "Optimized"]); bx.invert_yaxis()
bx.set_xlim(0, max(r["load_s"] for r in data["measured"])*1.18)
bx.set_title("Actual model load (one run each)", loc="left")
bx.set_xlabel("Weight-load start to both GPUs ready (s)")
bx.legend(loc="upper left", bbox_to_anchor=(0, -.17), ncol=2, fontsize=8, frameon=False)
for a in (ax, bx):
    a.set_axisbelow(True); a.grid(axis="x", alpha=.2)
    for edge in ("top", "right"): a.spines[edge].set_visible(False)
fig.suptitle("Fixed node28 full DRAM base + 8 GiB HBM / 2 GiB DRAM extra budget", fontsize=12)
fig.tight_layout(rect=(0, .1, 1, .95))
args.out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(args.out.with_suffix(".png"), dpi=160)
fig.savefig(args.out.with_suffix(".svg"))
