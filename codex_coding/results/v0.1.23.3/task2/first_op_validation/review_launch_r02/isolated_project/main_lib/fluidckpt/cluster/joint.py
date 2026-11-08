"""Single-scenario finite-candidate placement MILP with the restore LP's resource model."""
import math
import time

from .planning import covers, extent, path_for, restore_plan


def joint_placement(base, candidates, targets, workers, profile, tier_budgets, location_budgets,
                    shared_location_budgets=None, solver_options=None):
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    started = time.monotonic()
    wm = {w["worker"]: w for w in workers}
    if len({r["id"] for r in base+candidates}) != len(base+candidates):
        raise ValueError("duplicate replica IDs")
    for c in candidates:
        if c["tier"] not in tier_budgets or f"{c['worker']}:{c['tier']}" not in location_budgets:
            raise ValueError("candidate has no explicit capacity budget")
        if c["n"] <= 0 or not covers(base, c["identity"], c["tp"], *extent(c)):
            raise ValueError("candidate has no valid build source")
    if any(not math.isfinite(v) or v < 0 for v in list(tier_budgets.values())+list(location_budgets.values())):
        raise ValueError("invalid budget")
    regions, flows, resources = [], [], {}
    for target in targets:
        begin, end = extent(target)
        rows = [r for r in base+candidates if r["identity"] == target["identity"] and r["tp"] == target["tp"]]
        points = sorted({begin, end} | {v for r in rows for v in extent(r) if begin < v < end})
        for a, b in zip(points, points[1:]):
            rid = len(regions)
            regions.append((target, a, b-a))
            for replica in rows:
                lo, hi = extent(replica)
                if lo <= a and hi >= b:
                    path = path_for(replica, target, wm, profile)
                    if path:
                        flows.append((rid, replica, path))
                        resources.update(path["resources"])
                        resources["path:"+path["path"]] = path["GBps"]
    nc, nf = len(candidates), len(flows)
    total = nc+nf+1
    ci = {c["id"]: i for i, c in enumerate(candidates)}
    rr, cc, vv, lows, highs = [], [], [], [], []

    def constraint(values, lo=-np.inf, hi=np.inf):
        row = len(lows); lows.append(lo); highs.append(hi)
        for col, value in values.items():
            rr.append(row); cc.append(col); vv.append(value)

    for i, (_, _, size) in enumerate(regions):
        constraint({nc+k: 1 for k, (r, _, _) in enumerate(flows) if r == i}, size/1e9, size/1e9)
    for k, (r, replica, _) in enumerate(flows):
        if replica["id"] in ci:
            constraint({nc+k: 1, ci[replica["id"]]: -regions[r][2]/1e9}, hi=0)
    for tier, budget in tier_budgets.items():
        constraint({i: ((c["n"]+4095)//4096*4096)/1e9 for i, c in enumerate(candidates) if c["tier"] == tier}, hi=budget/1e9)
    for key, budget in location_budgets.items():
        constraint({i: ((c["n"]+4095)//4096*4096)/1e9 for i, c in enumerate(candidates)
                    if f"{c['worker']}:{c['tier']}" == key}, hi=budget/1e9)
    for group in (shared_location_budgets or {}).values():
        if not math.isfinite(group["bytes"]) or group["bytes"] < 0: raise ValueError("invalid shared capacity")
        constraint({i: ((c["n"]+4095)//4096*4096)/1e9 for i, c in enumerate(candidates)
                    if f"{c['worker']}:{c['tier']}" in group["members"]}, hi=group["bytes"]/1e9)
    for res, cap in resources.items():
        values = {nc+k: 1 for k, (_, _, p) in enumerate(flows)
                  if res in p["resources"] or res == "path:"+p["path"]}
        values[total-1] = -cap
        constraint(values, hi=0)
    if profile.get("serial_target_execution"):
        for w in {t["worker"] for t in targets}:
            values = {nc+k: 1/p["GBps"] for k, (r, _, p) in enumerate(flows) if regions[r][0]["worker"] == w}
            values[total-1] = -1
            constraint(values, hi=0)
    objective = np.zeros(total); objective[-1] = 1
    upper = np.full(total, np.inf); upper[:nc] = 1
    matrix = coo_matrix((vv, (rr, cc)), shape=(len(lows), total)).tocsc()
    options = {"time_limit": 20, "mip_rel_gap": 1e-7}
    for k, v in (solver_options or {}).items():
        if k not in options or not math.isfinite(v) or v < 0: raise ValueError("invalid solver option")
        options[k] = v
    result = milp(objective, integrality=np.r_[np.ones(nc), np.zeros(nf+1)],
                  bounds=Bounds(np.zeros(total), upper), constraints=LinearConstraint(matrix, lows, highs),
                  options=options)
    if result.x is None or (not result.success and result.status != 1):
        raise RuntimeError(f"joint MILP has no usable solution: {result.message}")
    x = result.x
    if (not np.all(np.isfinite(x)) or np.any(x < -1e-7) or np.any(x > upper+1e-7)
            or np.any(np.abs(x[:nc]-np.round(x[:nc])) > 1e-6)
            or np.any(matrix@x < np.asarray(lows)-1e-7) or np.any(matrix@x > np.asarray(highs)+1e-7)):
        raise RuntimeError("joint MILP incumbent violates numerical feasibility checks")
    used = {r["id"] for k, (_, r, _) in enumerate(flows) if result.x[nc+k] > 1e-8}
    selected = [c for i, c in enumerate(candidates) if result.x[i] > .5 and c["id"] in used]
    for key, cap in location_budgets.items():
        if sum((c["n"]+4095)//4096*4096 for c in selected if f"{c['worker']}:{c['tier']}" == key) > cap:
            raise RuntimeError("joint MILP incumbent exceeds exact location budget")
    for tier, cap in tier_budgets.items():
        if sum((c["n"]+4095)//4096*4096 for c in selected if c["tier"] == tier) > cap:
            raise RuntimeError("joint MILP incumbent exceeds exact tier budget")
    for group in (shared_location_budgets or {}).values():
        if sum((c["n"]+4095)//4096*4096 for c in selected if f"{c['worker']}:{c['tier']}" in group["members"]) > group["bytes"]:
            raise RuntimeError("joint MILP incumbent exceeds exact shared capacity")
    plan = restore_plan(base+selected, targets, workers, profile, "resource_lp")
    if plan["strategy"] != "resource_lp": raise RuntimeError("verification LP fell back")
    def finite_or_none(value):
        return float(value) if value is not None and math.isfinite(value) else None
    return {"selected": selected, "objective_s": float(result.fun), "rounded_plan_s": plan["estimated_transfer_s"],
            "status": "optimal" if result.success else "feasible_limit", "solver_message": result.message,
            "mip_gap": finite_or_none(getattr(result, "mip_gap", None)),
            "dual_bound_s": finite_or_none(getattr(result, "mip_dual_bound", None)),
            "solve_wall_s": time.monotonic()-started, "candidate_count": nc, "flow_variables": nf,
            "tier_bytes": {tier: sum((c["n"]+4095)//4096*4096 for c in selected if c["tier"] == tier) for tier in tier_budgets},
            "scope": "optimal only within supplied finite candidates, one restore scenario and approximate resource/service model"}
