"""Rebuild the gate-2 strategy matrix from the committed code alone.

Nothing here is a separate harness: it imports the same `contract`, `candidates`,
`scorer` and `strategies.run_strategy` that produced the archived numbers, so the
result file can be regenerated from source rather than from a side script.

Run with the numpy/scipy environment (the `joint` MILP needs it).
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path.insert(0, str(HERE.parent / "case"))
sys.path.insert(0, str(HERE))

from registered_fixture import (GiB, MODEL_PATH, base_replicas, budgets,
                                candidate_replicas, load, workers)
from scorer import (build_plan, make_state, profile_from_points, rank_end_s, score,
                    targets_of)
import strategies as S

STRATEGIES = ("base_only", "fastest_seq", "balanced", "strong_simple", "exchange", "joint")
# `base_only` is the lower-bound REFERENCE, not a competing strategy: it is scored and
# reported but never enters the spread or gate 2. Including it makes the spread equal to
# the whole base-vs-cached gap and reports a false pass.
REFERENCE = ("base_only",)
POINTS = (8, 12, 16, 24, 32, 36, 47)
REGISTERED = 47


def derive(by_name):
    """Spread and gate 2 over the competing strategies only."""
    ok = {k: v["ready_total_s"] for k, v in by_name.items()
          if v["status"] == "ok" and k not in REFERENCE}
    if not ok:
        return dict(spread_s=None, delta_s=0.1, gate2=False)
    best = min(ok.values())
    spread = max(ok.values()) - best
    delta = max(0.1, 0.05 * best)
    return dict(spread_s=spread, delta_s=delta, gate2=spread >= delta)


def context(gib):
    model, launch = load()
    workers_ = workers()
    profile = profile_from_points(model)
    state = make_state(model, workers_, MODEL_PATH)
    b = budgets(launch, None if gib is None else gib * GiB)
    ctx = dict(model=model, workers=workers_, profile=profile, state=state,
               targets=targets_of(model), location_budgets=b["location_budgets"],
               tier_budgets=b["tier_budgets"])
    ctx["score"] = lambda sel: score(model, build_plan(model, sel, workers_, profile),
                                     workers_, state)
    ctx["rank_end_s"] = lambda sel: rank_end_s(model, sel, workers_, profile, state)
    return model, launch, ctx


def row_for(gib):
    model, _, ctx = context(gib)
    candidates = candidate_replicas(model)
    case = dict(candidates=candidates, base=base_replicas(model),
                max_turns=len(candidates) + 1, fill=8)
    out = dict(hbm_per_card_gib=gib, strategies={})
    for name in STRATEGIES:
        started = time.monotonic()
        r = S.run_strategy(name, case, ctx)
        out["strategies"][name] = dict(
            status=r["status"], n=len(r.get("rows") or []),
            service_s=r.get("service_s"), ready_total_s=r.get("ready_total_s"),
            projection=r.get("projection"), error=r.get("error"),
            wall_s=round(time.monotonic() - started, 1),
            hbm_used=sum(c["n"] for c in r.get("rows") or [] if c["tier"] == "hbm"))
    out.update(derive(out["strategies"]))
    out["registered"] = gib == REGISTERED
    return out


def recompute(path):
    """Re-derive spread/gate2 from a stored matrix without re-running the strategies."""
    body = json.loads(Path(path).read_text())
    for row in body["rows"]:
        row.update(derive(row["strategies"]))
        cells = " ".join("%s=%s" % (k[:6], "----" if v["ready_total_s"] is None
                                    else "%.6f" % v["ready_total_s"])
                         for k, v in row["strategies"].items())
        print("%2d GiB %s spread=%s gate2=%s"
              % (row["hbm_per_card_gib"], cells,
                 "none" if row["spread_s"] is None else "%.6f" % row["spread_s"],
                 row["gate2"]))
    Path(path).write_text(json.dumps(body, indent=1))
    print("rewritten", path)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--recompute":
        return recompute(sys.argv[2])
    rows = []
    for gib in POINTS:
        row = row_for(gib)
        rows.append(row)
        cells = " ".join("%s=%s" % (k[:6], "----" if v["ready_total_s"] is None
                                    else "%.6f" % v["ready_total_s"])
                         for k, v in row["strategies"].items())
        print("%2d GiB %s spread=%s gate2=%s"
              % (gib, cells, "none" if row["spread_s"] is None else "%.6f" % row["spread_s"],
                 row["gate2"]), flush=True)
    body = dict(schema="decision_value_cpu_r01", date="2026-10-25",
                development="v0.1.23.3/task2", gpu_used=0, ssh_used=0,
                scorer="simple_exact_extent_model_v1 @ checkpoint_path_model.json",
                outer_residual_s=0.4235804238204,
                metric="T_ready_total = service_s + outer_residual_s",
                delta_rule="max(0.100s, 0.05 * best_T_ready_total)",
                note=("T_ready_total here contains T_exec + common outer residual only. "
                      "T_plan is reported separately and T_pre is not measured on CPU. "
                      "Every strategy's raw output passes through strategies.normalize "
                      "(one_class_per_extent projection) before scoring; dropped rows are "
                      "recorded per strategy under `projection`."),
                rows=rows)
    out = ROOT / "codex_coding/results/v0.1.23.3/task2/decision_value/execution_decision_value_cpu_r01"
    out.mkdir(parents=True, exist_ok=True)
    (out / "strategy_matrix_reproduced.json").write_text(json.dumps(body, indent=1))
    print("written", out / "strategy_matrix_reproduced.json")


if __name__ == "__main__":
    main()
