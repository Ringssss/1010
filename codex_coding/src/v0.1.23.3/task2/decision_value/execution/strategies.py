"""Six registered strategies over one frozen candidate set, budget and scorer.

Every strategy sees identical candidates, identical capacity and is scored by the
same archived model. Only the selection rule differs.

Two constraints are part of the CONTRACT and are applied to every strategy
equally, never as a per-strategy filter:

  * one_class_per_extent - a layout assigns each extent to at most one tier. The
    product control never had to state this because its registered incremental
    domain is HBM-only (`topology_cases.validate_case` rejects any non-HBM
    candidate). This scenario adds a local-DRAM class, so the constraint is
    stated explicitly here.
  * capacity - the frozen location/tier ledger (`scorer.ledger`).

`strong_simple` keeps the product greedy's zero-gain plateau: it is the
registered control and may be neither weakened nor strengthened.
"""
import itertools
import time

from registered_fixture import GiB
from scorer import ledger, evaluate_selection

HBM, LOCAL = 0, 1                       # class order: hbm is faster than local dram


def extents_of(candidates):
    """Unique (tp, off, n) keys, in registered rank-then-offset order."""
    seen = {}
    for c in candidates:
        seen.setdefault((c["tp"], c["off"], c["n"]), []).append(c)
    return [seen[k] for k in sorted(seen, key=lambda k: (k[0], k[1], k[2]))]


def covers_ok(selected, cand):
    """Contract: one class per extent - refuse a second copy of the same extent."""
    return not any((r["tp"], r["off"], r["n"]) == (cand["tp"], cand["off"], cand["n"])
                   for r in selected)


def pick(classes, selected, ctx):
    """Fastest class among `classes` that keeps the layout legal, else None."""
    for cls in (HBM, LOCAL):
        for cand in classes:
            if cand["class_rank"] != cls:
                continue
            if not covers_ok(selected, cand):
                continue
            try:
                ledger(selected + [cand], ctx["location_budgets"], ctx["tier_budgets"])
            except ValueError:
                continue
            return cand
    return None


def normalize(selected):
    """Contract projection: collapse duplicate copies of one extent to the fastest class.

    `one_class_per_extent` says a layout assigns each extent to at most one tier. The
    product MILP does not enforce it, so at wide budgets it returns a redundant second
    copy of an extent (67 rows for 66 extents at 47 GiB/card, `r0/e21` held in both hbm
    and local dram). Projecting that answer onto one class per extent is a CONTRACT
    step, applied to every strategy's raw output by `run_strategy`, never to `joint`
    alone. The dropped copy is always the slower class and is always a dead copy: the
    score is unchanged to 1e-12 in every case measured.

    Returns (kept, dropped) so the removal is recorded rather than silent.
    """
    best = {}
    for cand in selected:
        key = (cand["tp"], cand["off"], cand["n"])
        if key not in best or cand["class_rank"] < best[key]["class_rank"]:
            best[key] = cand
    kept = [c for c in selected if best[(c["tp"], c["off"], c["n"])] is c]
    return kept, [c for c in selected if c not in kept]


def legal(selected, ctx):
    """Full legality check applied to every strategy's final answer."""
    if len({c["id"] for c in selected}) != len(selected):
        return False
    keys = [(c["tp"], c["off"], c["n"]) for c in selected]
    if len(set(keys)) != len(keys):
        return False
    try:
        ledger(selected, ctx["location_budgets"], ctx["tier_budgets"])
    except ValueError:
        return False
    return True


def base_only(case, ctx):
    return [], [dict(step="base_only", ids=[])]


def fastest_seq(case, ctx):
    """Per extent, take the fastest class that still fits, in extent order.

    Never improves on the remote base, which is always available at no capacity
    cost. This is the capacity-aware "nearest/fastest" rule.
    """
    selected, steps = [], []
    for classes in extents_of(case["candidates"]):
        cand = pick(classes, selected, ctx)
        if cand is not None:
            selected.append(cand)
            steps.append(dict(step="accept", ids=[cand["id"]]))
    return selected, steps


def balanced(case, ctx):
    """Bottleneck equalisation on the real score, one extent at a time.

    The ranks sit on disjoint workers and the makespan is their max, so the rule
    is: give the next extent to whichever rank's chain currently finishes last,
    in that rank's fastest class that fits. Unlike `fastest_seq` this consults
    the scorer, so it can spend a scarce class on the critical rank instead of
    walking extents in registration order.
    """
    groups = extents_of(case["candidates"])
    by_rank = {}
    for g in groups:
        by_rank.setdefault(g[0]["tp"], []).append(g)
    selected, steps = [], []
    for _ in range(len(groups)):
        held = {(c["tp"], c["off"], c["n"]) for c in selected}
        live = {r: [g for g in gs if (g[0]["tp"], g[0]["off"], g[0]["n"]) not in held]
                for r, gs in by_rank.items()}
        live = {r: gs for r, gs in live.items() if gs}
        if not live:
            break
        ends = ctx["rank_end_s"](selected)
        rank = max(sorted(live), key=lambda r: (ends.get(r, 0.0), r))
        cand = pick(live[rank][0], selected, ctx)
        if cand is None:
            continue
        selected.append(cand)
        steps.append(dict(step="balanced", ids=[cand["id"]], rank=rank))
    return selected, steps


def strong_simple(case, ctx):
    """Product marginal greedy: singleton + cross-rank paired bundles, gain>1e-9.

    Ported from `fluidckpt/benchmarks/frozen_layout.py:94` with only the contract's
    one-class-per-extent rule added. The zero-gain plateau is intentional: this is
    the registered control.
    """
    from fluidckpt.benchmarks.topology_cases import allocated

    candidates = case["candidates"]
    bundles = [[c] for c in candidates]
    bundles += [[a, b] for a, b in itertools.combinations(candidates, 2)
                if a["tp"] != b["tp"] and (a["off"], a["n"]) == (b["off"], b["n"])]
    selected, steps = [], []
    current = ctx["score"](selected)["service_s"]
    while True:
        ids = {c["id"] for c in selected}
        choices = []
        for bundle in bundles:
            addition = [c for c in bundle if c["id"] not in ids]
            kept = []
            for c in addition:
                if covers_ok(selected + kept, c):
                    kept.append(c)
            addition = kept
            if not addition:
                continue
            try:
                ledger(selected + addition, ctx["location_budgets"], ctx["tier_budgets"])
            except ValueError:
                continue
            score = ctx["score"](selected + addition)["service_s"]
            gain = current - score
            if gain > 1e-9:
                density = gain / (sum(allocated(c) for c in addition) / GiB)
                choices.append((-round(density, 12), round(score, 12),
                                tuple(c["id"] for c in addition), score, addition))
        if not choices:
            break
        _, _, _, current, addition = min(choices, key=lambda r: r[:3])
        selected += addition
        steps.append(dict(step="accept", ids=[c["id"] for c in addition], service_s=current))
    return selected, steps


def exchange(case, ctx):
    """Bounded neighbourhood: drop 1-2 then add 0-2, accept strict improvement.

    Seeded from `balanced`, matching the registered `case_active_r01` rule. The add
    pool is restricted to (a) class alternatives for a dropped extent and (b) the
    `fill` cheapest unheld candidates: the raw 1-2 x 0-2 neighbourhood is ~8e6
    plans over 132 candidates and is not evaluable. The restriction can only
    weaken this baseline, never strengthen it.
    """
    start, steps = balanced(case, ctx)

    def key(sel, score):
        book = ledger(sel, ctx["location_budgets"], ctx["tier_budgets"])
        return (round(score, 10), book["allocated_bytes"], tuple(sorted(c["id"] for c in sel)))

    best, best_score = list(start), ctx["score"](start)["service_s"]
    best_key = key(best, best_score)
    for rounds in range(case["max_turns"]):
        chosen = {c["id"] for c in best}
        held = {(c["tp"], c["off"], c["n"]) for c in best}
        free = [c for c in case["candidates"]
                if c["id"] not in chosen and (c["tp"], c["off"], c["n"]) not in held]
        free.sort(key=lambda c: (c["class_rank"], c["index"], c["id"]))
        moved = False
        for drop_n in (1, 2):
            for drop in itertools.combinations(best, min(drop_n, len(best))):
                kept = [c for c in best if c not in drop]
                freed = {(d["tp"], d["off"], d["n"]) for d in drop}
                upgrades = [c for c in free if (c["tp"], c["off"], c["n"]) in freed]
                fills = [c for c in free if c not in upgrades][:case["fill"]]
                pool = upgrades + fills
                for add_n in (0, 1, 2):
                    for add in itertools.combinations(pool, min(add_n, len(pool))):
                        trial = kept + list(add)
                        if not legal(trial, ctx):
                            continue
                        score = ctx["score"](trial)["service_s"]
                        k = key(trial, score)
                        if k < best_key:
                            best, best_score, best_key = trial, score, k
                            moved = True
        steps.append(dict(step="exchange_round", round=rounds + 1, service_s=best_score))
        if not moved:
            break
    return best, steps


def joint(case, ctx):
    """Product joint solver over the same candidates and budgets."""
    from fluidckpt.cluster.multi_joint import joint_scenarios

    scenario = dict(name="all_ranks", weight=1.0, targets=ctx["targets"])
    tier_budgets = {"hbm": ctx["tier_budgets"]["hbm"], "dram": ctx["tier_budgets"]["dram"]}
    location_budgets = {k: v for k, v in ctx["location_budgets"].items() if v > 0}
    try:
        result = joint_scenarios(
            base=case["base"], candidates=case["candidates"], scenarios=[scenario],
            workers=ctx["workers"], profile=ctx["profile"], tier_budgets=tier_budgets,
            location_budgets=location_budgets,
            solver_options=dict(time_limit=30, mip_rel_gap=1e-7))
    except (RuntimeError, ValueError) as exc:
        return None, [dict(step="joint_failed", reason=str(exc))]
    return result["selected"], [dict(step="joint", status=result["status"],
                                     objective_s=result["objective_s"],
                                     solve_wall_s=result["solve_wall_s"])]


REGISTRY = dict(base_only=base_only, fastest_seq=fastest_seq, balanced=balanced,
                strong_simple=strong_simple, exchange=exchange, joint=joint)


def run_strategy(name, case, ctx):
    started = time.monotonic()
    try:
        selected, steps = REGISTRY[name](case, ctx)
    except Exception as exc:                      # noqa: BLE001 - recorded, not hidden
        return dict(name=name, status="error", error=f"{type(exc).__name__}: {exc}",
                    wall_s=time.monotonic() - started)
    if selected is None:
        return dict(name=name, status="infeasible", steps=steps,
                    wall_s=time.monotonic() - started)
    raw_rows = len(selected)
    selected, dropped = normalize(selected)
    projection = dict(raw_rows=raw_rows, dropped=[c["id"] for c in dropped])
    if not legal(selected, ctx):
        return dict(name=name, status="illegal", steps=steps, projection=projection,
                    wall_s=time.monotonic() - started)
    result = evaluate_selection(ctx["model"], selected, ctx["workers"], ctx["profile"],
                                ctx["state"], ctx["location_budgets"], ctx["tier_budgets"])
    return dict(name=name, status="ok", rows=list(selected), selected=result["selected"],
                steps=steps, service_s=result["service_s"],
                ready_total_s=result["ready_total_s"], ledger=result["ledger"],
                projection=projection, wall_s=time.monotonic() - started)
