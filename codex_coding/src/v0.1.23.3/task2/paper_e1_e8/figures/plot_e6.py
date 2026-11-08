"""Plot measured e6 recovery times against charged extra HBM allocation.

Data source: a completed placement/run_matrix.py e6 summary.json. Each case's
timing.load_s is the same-host weight-ready span in seconds. Each build's
cache_page_rounded_bytes is charged extra HBM in bytes, not total system HBM or
a measured physical high-water mark. The fixed DRAM base is reported separately.
Predictions and route aliases never contribute observations to this figure.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

GIB = 1 << 30
SCENARIOS = ("target_a", "target_b")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_observations(summary_path):
    data = json.loads(summary_path.read_text())
    if data.get("stage") != "e6" or data.get("status") != "passed":
        raise ValueError("input must be a completed, passed e6 run")
    if data.get("cleanup_errors") or data.get("source_hash_mismatches") or data.get("owned_remaining"):
        raise ValueError("input has unresolved cleanup or source-integrity failures")
    builds = {}
    for build in data["builds"]:
        label = build["label"]
        if label in builds:
            raise ValueError(f"ambiguous repeated build label: {label}")
        builds[label] = build
    observations, phase_ids, inputs = [], set(), []
    for row in data["cases"]:
        phase = row["phase"]
        if phase in phase_ids:
            raise ValueError(f"duplicate observation phase: {phase}")
        phase_ids.add(phase)
        if row.get("status") != "passed" or row.get("correct") is not True or row.get("verification", {}).get("passed") is not True:
            raise ValueError(f"case is not correctness-verified: {phase}")
        if row["scenario"] not in SCENARIOS:
            raise ValueError(f"unsupported target: {row['scenario']}")
        build = builds[row["label"]]
        actual = build["cache_page_rounded_bytes"]
        budget = build["budget_bytes"]
        if not isinstance(actual, int) or actual < 0 or not isinstance(budget, int) or actual > budget:
            raise ValueError(f"invalid charged HBM accounting: {row['label']}")
        load_s = row["timing"]["load_s"]
        if not finite_number(load_s) or load_s <= 0:
            raise ValueError(f"invalid measured weight-ready time: {phase}")
        label_parts = row["label"].split("_", 1)
        if len(label_parts) != 2 or label_parts[1] not in ("greedy", "joint"):
            raise ValueError(f"unsupported measured policy label: {row['label']}")
        for metric in sorted((summary_path.parent / "metrics" / phase).glob("rank*.jsonl")):
            inputs.append({"path": str(metric.resolve()), "sha256": sha256(metric), "phase": phase})
        observations.append({"phase": phase, "label": row["label"], "scenario": row["scenario"],
                             "policy": label_parts[1], "routing": row["routing"],
                             "budget_bytes": budget, "cache_page_rounded_bytes": actual,
                             "charged_extra_hbm_gib": actual / GIB,
                             "T_ready_s": load_s,
                             "base_logical_bytes": build["base_logical_bytes"],
                             "base_page_rounded_bytes": build["base_page_rounded_bytes"],
                             "measurement_kind": "independent_measured_case"})
    if not observations:
        raise ValueError("no independently measured observations")
    for alias in data.get("route_aliases", []):
        if alias.get("new_observation") is not False:
            raise ValueError("unexpected alias claiming a new observation")
        if alias["source_phase"] not in phase_ids:
            raise ValueError("alias refers outside the measured run")
    base_sizes = {(row["base_logical_bytes"], row["base_page_rounded_bytes"]) for row in observations}
    if len(base_sizes) != 1:
        raise ValueError("the fixed DRAM base changes across measured cases")
    return data, observations, inputs


def aggregate(observations):
    grouped = defaultdict(list)
    for row in observations:
        # Budget remains part of the group so unequal budgets cannot be silently
        # pooled when both policies happen to allocate the same number of bytes.
        key = (row["policy"], row["routing"], row["budget_bytes"], row["cache_page_rounded_bytes"], row["scenario"])
        grouped[key].append(row)
    target_points, mean_points = [], []
    combinations = set()
    for (policy, routing, budget, actual, scenario), rows in sorted(grouped.items()):
        target_points.append({"policy": policy, "routing": routing, "scenario": scenario,
                              "budget_bytes": budget, "cache_page_rounded_bytes": actual,
                              "charged_extra_hbm_gib": actual / GIB,
                              "T_ready_s": statistics.mean(row["T_ready_s"] for row in rows),
                              "observation_count": len(rows), "phases": [row["phase"] for row in rows]})
        combinations.add((policy, routing, budget, actual))
    missing = []
    for policy, routing, budget, actual in sorted(combinations):
        rows_by_target = [grouped.get((policy, routing, budget, actual, scenario), []) for scenario in SCENARIOS]
        if not all(rows_by_target):
            missing.append({"policy": policy, "routing": routing, "budget_bytes": budget,
                            "reason": "both targets need independent measured cases; aliases are not expanded"})
            continue
        means = [statistics.mean(row["T_ready_s"] for row in rows) for rows in rows_by_target]
        mean_points.append({"policy": policy, "routing": routing, "budget_bytes": budget,
                            "cache_page_rounded_bytes": actual, "charged_extra_hbm_gib": actual / GIB,
                            "T_ready_s": statistics.mean(means), "scenario_weights": [0.5, 0.5],
                            "observation_count_by_target": [len(rows) for rows in rows_by_target],
                            "phases": [row["phase"] for rows in rows_by_target for row in rows]})
    return target_points, mean_points, missing


def render(summary_path, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary_path = summary_path.resolve()
    data, observations, metric_inputs = read_observations(summary_path)
    target_points, mean_points, missing = aggregate(observations)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none"})
    figure, axes = plt.subplots(1, 3, figsize=(12, 4.5), sharex=True, sharey=True)
    series = sorted({(row["policy"], row["routing"]) for row in observations})
    colors = {pair: plt.get_cmap("tab10")(index % 10) for index, pair in enumerate(series)}
    ticks = sorted({row["charged_extra_hbm_gib"] for row in observations})
    labels = {"target_a": "Target A", "target_b": "Target B", "mean": "Equal-weight target mean"}
    for axis, scenario in zip(axes, (*SCENARIOS, "mean")):
        points = mean_points if scenario == "mean" else [r for r in target_points if r["scenario"] == scenario]
        for pair in series:
            selected = sorted((r for r in points if (r["policy"], r["routing"]) == pair),
                              key=lambda r: (r["charged_extra_hbm_gib"], r["budget_bytes"]))
            if not selected:
                continue
            label = f"{pair[0]} / {pair[1]}"
            axis.plot([r["charged_extra_hbm_gib"] for r in selected], [r["T_ready_s"] for r in selected],
                      marker="o", markersize=5, linewidth=1.4, color=colors[pair], label=label)
            if scenario != "mean":
                raw = [r for r in observations if r["scenario"] == scenario and (r["policy"], r["routing"]) == pair]
                axis.scatter([r["charged_extra_hbm_gib"] for r in raw], [r["T_ready_s"] for r in raw],
                             s=20, color=colors[pair], alpha=0.65, zorder=3)
        if not points:
            axis.text(0.5, 0.5, "No independently measured pairs", ha="center", va="center", transform=axis.transAxes)
        axis.set_title(labels[scenario])
        axis.set_xlabel("Charged extra HBM (GiB)")
        axis.set_xticks(ticks)
        axis.grid(axis="y", color="0.88", linewidth=0.7)
    # Shared axes must see every target before a fixed upper limit is applied.
    axes[0].set_ylim(0, max(row["T_ready_s"] for row in observations) * 1.08)
    axes[0].set_ylabel("Weight-ready latency (s)")
    handles, legend_labels = [], []
    for axis in axes:
        for handle, label in zip(*axis.get_legend_handles_labels()):
            if label not in legend_labels:
                handles.append(handle)
                legend_labels.append(label)
    figure.legend(handles, legend_labels, loc="upper center", bbox_to_anchor=(0.5, 0.91),
                  frameon=False, ncol=min(4, len(handles)))
    figure.suptitle("e6: measured recovery versus extra HBM allocation", y=0.99)
    counts = [point["observation_count"] for point in target_points]
    exploratory = any(count < 2 for count in counts)
    note = "Exploratory: single observations; no confidence intervals." if exploratory else "Exploratory run; lines connect means at measured allocation points."
    aliases = len(data.get("route_aliases", []))
    note += f"  {aliases} route aliases excluded from sample counts."
    base_gib = observations[0]["base_logical_bytes"] / GIB
    note += f"\nFixed DRAM base: {base_gib:.2f} GiB. Extra HBM is page-rounded accounting; physical high-water is not asserted."
    figure.text(0.5, 0.025, note, ha="center", va="bottom", fontsize=8)
    figure.subplots_adjust(left=0.07, right=0.985, top=0.77, bottom=0.23, wspace=0.16)
    artifacts = []
    for extension in ("png", "pdf", "svg"):
        path = out / f"e6_budget_curve.{extension}"
        figure.savefig(path, dpi=200, bbox_inches="tight")
        artifacts.append({"path": str(path), "sha256": sha256(path)})
    plt.close(figure)
    sources = {"summary": {"path": str(summary_path), "sha256": sha256(summary_path)},
               "script": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__).resolve())},
               "metrics": metric_inputs, "artifacts": artifacts, "stage": "e6",
               "metric": "case.timing.load_s: same-host max(weights_ready)-min(load_begin)",
               "x_axis": "build.cache_page_rounded_bytes / 2**30; charged extra HBM, not physical high-water",
               "unplotted_requested_budget": "build.budget_bytes is retained per point below",
               "exploratory_single_observations": exploratory,
               "independent_observation_count": len(observations), "excluded_alias_count": aliases,
               "observations": observations, "target_points": target_points, "equal_weight_mean_points": mean_points,
               "unplotted_mean_groups": missing,
               "rules": ["No prediction plotted", "No alias expanded", "No missing budget imputed",
                         "Zero allocation retained when measured", "Mean gives each target weight one half"]}
    (out / "sources.json").write_text(json.dumps(sources, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(out), "observations": len(observations), "aliases_excluded": aliases,
                      "exploratory": exploratory, "artifacts": [row["path"] for row in artifacts]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True, help="Passed e6 run_matrix.py summary.json")
    parser.add_argument("--out", type=Path, required=True, help="New standalone figure output directory; never overwrite raw runs")
    args = parser.parse_args()
    render(args.summary, args.out)


if __name__ == "__main__":
    main()
