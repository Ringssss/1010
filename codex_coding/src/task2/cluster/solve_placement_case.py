"""Compare bounded placement policies with an identical executor-aware cost model."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/"main_lib"))
from fluidckpt.cluster.joint import joint_placement
from fluidckpt.cluster.planning import restore_plan


def solve(case):
    base, candidates = case["base"], case["candidates"]
    tiers, locations = case["tier_budgets"], case["location_budgets"]
    evaluations = 0

    def legal(selected):
        return (all(sum((r["n"]+4095)//4096*4096 for r in selected if r["tier"] == t) <= b for t, b in tiers.items())
                and all(sum((r["n"]+4095)//4096*4096 for r in selected if f"{r['worker']}:{r['tier']}" == k) <= b
                        for k, b in locations.items()))

    def score(selected):
        nonlocal evaluations
        evaluations += 1
        p = restore_plan(base+selected, case["targets"], case["workers"], case["profile"], "resource_lp")
        if p["strategy"] != "resource_lp": raise RuntimeError("cost evaluation LP fell back")
        return p["estimated_transfer_s"]

    def greedy(bundles):
        started = time.monotonic(); selected = []; current = score([])
        while True:
            ids = {r["id"] for r in selected}
            options = []
            for bundle in bundles:
                if any(c["id"] in ids for c in bundle) or not legal(selected+bundle): continue
                value = score(selected+bundle)
                gain = current-value
                if gain > 1e-9: options.append((gain/sum(c["n"] for c in bundle), value, bundle))
            if not options: break
            _, current, bundle = max(options, key=lambda x: x[0]); selected += bundle
        return {"selected": selected, "objective_s": current, "solve_wall_s": time.monotonic()-started}

    rows = {"base_only": {"selected": [], "objective_s": score([])},
            "existing": {"selected": case["existing"], "objective_s": score(case["existing"])}}
    rows["single_greedy"] = greedy([[c] for c in candidates])
    bundles = {}
    for c in candidates: bundles.setdefault((c["block"], c["placement_kind"]), []).append(c)
    rows["paired_greedy"] = greedy(list(bundles.values()))
    rows["milp"] = joint_placement(base, candidates, case["targets"], case["workers"], case["profile"], tiers, locations)
    # An explicit simple candidate gives the integer solver a reviewable baseline.
    simple = [c for c in candidates if (c["placement_kind"] == "local_hbm" and c["block"] < 4)
              or (c["placement_kind"] == "local_dram" and c["block"] == 4)]
    rows["balanced_local"] = {"selected": simple, "objective_s": score(simple)}
    for name, row in rows.items():
        if not legal(row["selected"]): raise AssertionError(f"budget violation: {name}")
        row["tier_bytes"] = {t: sum((c["n"]+4095)//4096*4096 for c in row["selected"] if c["tier"] == t) for t in tiers}
    # Choose the simplest policy matching the finite-candidate optimum, otherwise deploy MILP.
    best = rows["milp"]["rounded_plan_s"]
    chosen = next((k for k in ("balanced_local", "paired_greedy", "single_greedy")
                   if rows[k]["objective_s"] <= best+1e-6), "milp")
    return {"policies": rows, "selected_policy": chosen, "lp_evaluations": evaluations,
            "scope": "single known destination, fixed candidate blocks, tier budgets, serial service model; no global hardware optimum claim"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = solve(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({"selected": result["selected_policy"], "predictions":
                     {k: r["objective_s"] for k, r in result["policies"].items()}}))
