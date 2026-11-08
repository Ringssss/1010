"""Two structural checks that bound what a future round can possibly achieve.

Both are CPU-only and read the archived model; neither writes to it.

  totals_only   - is the score a pure function of per-(rank, tier) BYTE TOTALS?
                  If yes, any keep-alive cost that is linear over a uniform horizon
                  is a function of the same totals, so it can only re-price the two
                  totals and cannot make the per-extent assignment matter. That is
                  the same reason direction A (a tighter budget) failed.
  milp_vs_dag   - does the MILP objective rank layouts the way the DAG scores them?
                  The MILP sums source and target sides independently, while the DAG
                  serialises every op that touches the single sender (worker 2), so on
                  any layout that falls back to base the two disagree.

  subset_legality - gate 1's "subset-level legality recomputation". The plan's
                  `121/256` refers to the earlier 8-candidate case; this round has
                  132 candidates, so the equivalent check is exhaustive over every
                  subset of size <= 2 under every graded budget point, comparing
                  `strategies.legal` against an independent byte-accounting ledger.

Usage: python3 execution/checks.py [totals_only|milp_vs_dag|subset_legality|all]
"""
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "case"))
sys.path.insert(0, str(HERE))

from registered_fixture import (GiB, MODEL_PATH, base_replicas, budgets,
                                candidate_replicas, load, workers)
from scorer import (build_plan, make_state, profile_from_points, score, targets_of)


def _setup():
    model, launch = load()
    workers_ = workers()
    profile = profile_from_points(model)
    state = make_state(model, workers_, MODEL_PATH)
    return model, launch, workers_, profile, state


def _split(cands):
    hbm = {(c["tp"], c["off"]): c for c in cands if c["tier"] == "hbm"}
    local = {(c["tp"], c["off"]): c for c in cands if c["tier"] == "dram"}
    return hbm, local


def totals_only(trials=40, seed=11):
    """Score deviation among layouts that reach the SAME byte totals."""
    model, _, workers_, profile, state = _setup()
    hbm, local = _split(candidate_replicas(model))
    keys = sorted(hbm)
    by_rank = {r: [k for k in keys if k[0] == r] for r in sorted({k[0] for k in keys})}
    rng = random.Random(seed)
    seen = {}
    for _ in range(trials):
        pick = set()
        for rank, ks in by_rank.items():
            pick |= set(rng.sample(ks, rng.randint(0, len(ks))))
        sel = [hbm[k] if k in pick else local[k] for k in keys]
        totals = {}
        for c in sel:
            totals[(c["tp"], c["tier"])] = totals.get((c["tp"], c["tier"]), 0) + c["n"]
        svc = score(model, build_plan(model, sel, workers_, profile), workers_, state)["service_s"]
        seen.setdefault(tuple(sorted(totals.items())), []).append(svc)
    multi = [s for s in seen.values() if len(s) > 1]
    dev = max((max(s) - min(s) for s in multi), default=0.0)
    print("totals_only: %d distinct byte-total vectors, %d reached by >1 layout"
          % (len(seen), len(multi)))
    print("totals_only: max score deviation within identical totals = %.3e s" % dev)
    print("totals_only: score depends on totals only = %s" % (dev < 1e-12))
    return dev < 1e-12


def milp_vs_dag(cases=((47, 47), (16, 0), (8, 0), (4, 0))):
    """MILP objective vs DAG makespan on the layout the MILP itself chose."""
    from fluidckpt.cluster.multi_joint import joint_scenarios

    model, launch, workers_, profile, state = _setup()
    b = budgets(launch, None)
    tiers = {"hbm": b["tier_budgets"]["hbm"], "dram": b["tier_budgets"]["dram"]}
    cands = candidate_replicas(model)
    scenario = dict(name="all_ranks", weight=1.0, targets=targets_of(model))
    print("%-22s %-9s %14s %14s %10s %8s"
          % ("case", "status", "MILP objective", "DAG makespan", "gap_s", "gap_pct"))
    for hbm_gib, local_gib in cases:
        loc = {"0:hbm": hbm_gib * GiB, "1:hbm": hbm_gib * GiB,
               "0:dram": local_gib * GiB, "1:dram": local_gib * GiB,
               "2:dram": 96 * GiB}          # every candidate needs an explicit budget
        try:
            res = joint_scenarios(base=base_replicas(model), candidates=cands,
                                  scenarios=[scenario], workers=workers_, profile=profile,
                                  tier_budgets=tiers, location_budgets=loc,
                                  solver_options=dict(time_limit=30, mip_rel_gap=1e-7))
        except (RuntimeError, ValueError) as exc:
            print("%-22s FAILED    %s" % ("hbm=%d/dram=%d" % (hbm_gib, local_gib), exc))
            continue
        dag = score(model, build_plan(model, res["selected"], workers_, profile),
                    workers_, state)["service_s"]
        gap = dag - res["objective_s"]
        print("%-22s %-9s %14.9f %14.9f %10.6f %7.1f%%"
              % ("hbm=%d/dram=%d" % (hbm_gib, local_gib), res["status"],
                 res["objective_s"], dag, gap, 100 * gap / max(dag, 1e-12)), flush=True)


def subset_legality():
    """Gate 1: does `legal` agree with an independent ledger on every small subset?

    Independent of `scorer.ledger` on purpose: it recomputes bytes per (worker:tier)
    and per tier from the raw candidate rows and compares against the frozen caps.
    It also restates the contract's one_class_per_extent rule, so both halves of
    `strategies.legal` are reproduced without calling it.
    """
    from itertools import combinations
    import strategies as S

    model, launch, workers_, profile, state = _setup()
    cands = candidate_replicas(model)
    points = (8, 12, 16, 24, 32, 36, 47)
    checked = agreed = 0
    offenders = []
    for gib in points:
        b = budgets(launch, gib * GiB)
        loc, tier = b["location_budgets"], b["tier_budgets"]

        def independent(sel):
            # Keys must match the frozen budget ledger, which is keyed "<worker>:<tier>"
            # for locations and "hbm"/"dram" for tiers.
            if len({x["id"] for x in sel}) != len(sel):
                return False
            # half of the contract: one class per extent (each (tp, off, n) at most once)
            ext = [(x["tp"], x["off"], x["n"]) for x in sel]
            if len(set(ext)) != len(ext):
                return False
            used, per_tier = {}, {}
            for c in sel:
                k = "%s:%s" % (c["worker"], c["tier"])
                used[k] = used.get(k, 0) + c["n"]
                per_tier[c["tier"]] = per_tier.get(c["tier"], 0) + c["n"]
            if any(v > loc.get(k, 0) for k, v in used.items()):
                return False
            if any(v > tier.get(k, 0) for k, v in per_tier.items()):
                return False
            return True

        subsets = [()]
        subsets += [(c,) for c in cands]
        subsets += list(combinations(cands, 2))
        ctx = dict(model=model, workers=workers_, profile=profile, state=state,
                   targets=targets_of(model), location_budgets=loc, tier_budgets=tier)
        for sub in subsets:
            sub = list(sub)
            a, b2 = S.legal(sub, ctx), independent(sub)
            checked += 1
            if a == b2:
                agreed += 1
            elif len(offenders) < 5:
                offenders.append((gib, [c["id"] for c in sub], a, b2))
    print("subset_legality: %d subsets x %d budget points" % (len(subsets), len(points)))
    print("subset_legality: checks=%d agreed=%d disagreements=%d"
          % (checked, agreed, checked - agreed))
    if offenders:
        print("subset_legality: sample disagreements:", offenders)
    return checked == agreed


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "totals_only"):
        totals_only()
    if what in ("all", "milp_vs_dag"):
        milp_vs_dag()
    if what in ("all", "subset_legality"):
        subset_legality()
