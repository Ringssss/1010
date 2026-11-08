"""Split demands at replica boundaries, then solve shared-resource byte allocation."""
import math
import time

from .loading import validate_ops


def make_regions(demands, replicas, profile):
    regions = []
    for demand in demands:
        length = demand["nbytes"]
        if type(length) is not int or length <= 0:
            raise ValueError("positive demand length required")
        sources = [r for r in replicas if r["state"] == "VALID" and r["rank"] == demand["tp"]
                   and r["identity"] == demand["identity"] and r["worker"] == demand["dst_w"]]
        boundaries = {0, length}
        for replica in sources:
            begin = replica["logical_offset"]
            end = begin + replica["length"]
            if begin < 0 or replica["length"] <= 0 or end > length:
                raise ValueError("replica exceeds logical rank extent")
            boundaries.update((begin, end))
        points = sorted(boundaries)
        for begin, end in zip(points, points[1:]):
            candidates = []
            for r in sources:
                if r["logical_offset"] <= begin and r["logical_offset"] + r["length"] >= end:
                    key = f"{r['numa']}:{demand['gpu']}"
                    if key not in profile["paths"]:
                        continue
                    path = profile["paths"][key]
                    candidates.append({"replica": r, "path_id": key, "GBps": path["GBps"],
                                       "resources": path["resources"],
                                       "distance": demand["distances"][str(r["numa"])],
                                       "is_primary": bool(r.get("build", {}).get("primary", False))})
            if not candidates:
                raise ValueError(f"rank {demand['tp']} [{begin},{end}) has no executable source")
            candidates.sort(key=lambda c: (not c["is_primary"], c["distance"], c["replica"]["replica_id"]))
            regions.append({"demand": demand, "begin": begin, "end": end, "candidates": candidates})
    return regions


def plan_regions(demands, replicas, profile, strategy="resource_lp", quantum=64 << 20):
    started = time.monotonic()
    if strategy not in ("fixed", "nearest", "fastest", "greedy", "balanced", "resource_lp") or quantum <= 0:
        raise ValueError("unsupported regional strategy")
    if len({d["tp"] for d in demands}) != len(demands):
        raise ValueError("duplicate target rank")
    regions = make_regions(demands, replicas, profile)
    variables = [(i, c) for i, r in enumerate(regions) for c in r["candidates"]]
    caps = profile["resources"]
    for _, c in variables:
        if not math.isfinite(c["GBps"]) or c["GBps"] <= 0:
            raise ValueError("invalid path capacity")
        for resource in c["resources"]:
            if resource not in caps or not math.isfinite(caps[resource]) or caps[resource] <= 0:
                raise ValueError(f"unmeasured capacity {resource}")
    assigned, lower = {}, None
    if strategy == "resource_lp":
        import numpy as np
        from scipy.optimize import linprog
        count = len(variables)
        eq = np.zeros((len(regions), count + 1))
        for k, (i, _) in enumerate(variables):
            eq[i, k] = 1
        ub = []
        # A physical path is shared across regions; its cap must not be counted per region.
        for pid in sorted({c["path_id"] for _, c in variables}):
            rate = next(c["GBps"] for _, c in variables if c["path_id"] == pid)
            ub.append([float(c["path_id"] == pid) for _, c in variables] + [-rate])
        for resource, rate in caps.items():
            ub.append([float(resource in c["resources"]) for _, c in variables] + [-rate])
        objective = np.zeros(count + 1); objective[-1] = 1
        solution = linprog(objective, A_eq=eq, b_eq=[(r["end"]-r["begin"]) / 1e9 for r in regions],
                           A_ub=np.array(ub), b_ub=np.zeros(len(ub)), bounds=(0, None),
                           method="highs", options={"time_limit": max(0.001, 5-(time.monotonic()-started))})
        if not solution.success:
            result = plan_regions(demands, replicas, profile, "nearest", quantum)
            result.update(requested_strategy=strategy, fallback_reason=solution.message,
                          planning_s=time.monotonic()-started)
            return result
        lower = float(solution.x[-1])
        # Equivalent throughput solutions should not create remote traffic for sub-percent noise.
        tie_objective = np.array([float(c["distance"] - min(x["distance"] for x in regions[i]["candidates"]))
                                  for i, c in variables] + [0.0])
        remaining = 5-(time.monotonic()-started)
        if tie_objective.any() and remaining > 0:
            t_bound = np.zeros(count+1); t_bound[-1] = 1
            tie = linprog(tie_objective, A_eq=eq, b_eq=[(r["end"]-r["begin"])/1e9 for r in regions],
                          A_ub=np.vstack([ub, t_bound]), b_ub=[0.0]*len(ub)+[lower*1.01],
                          bounds=(0, None), method="highs", options={"time_limit": remaining})
            if tie.success: solution = tie
        assigned = {k: max(0, float(solution.x[k]) * 1e9) for k in range(count)}
    else:
        path_load, resource_load = {}, {}
        for i, region in enumerate(regions):
            choices = [k for k, (j, _) in enumerate(variables) if j == i]
            remain = region["end"] - region["begin"]
            if strategy == "balanced":
                for k in choices:
                    assigned[k] = remain / len(choices)
                continue
            for size in ([remain] if strategy != "greedy" else [min(quantum, remain-p) for p in range(0, remain, quantum)]):
                if strategy == "fixed":
                    chosen = choices[0]
                elif strategy == "nearest":
                    chosen = min(choices, key=lambda k: (variables[k][1]["distance"], variables[k][1]["replica"]["replica_id"]))
                elif strategy == "fastest":
                    fastest = max(variables[k][1]["GBps"] for k in choices)
                    tied = [k for k in choices if variables[k][1]["GBps"] >= 0.98 * fastest]
                    chosen = min(tied, key=lambda k: (variables[k][1]["distance"], variables[k][1]["replica"]["replica_id"]))
                else:
                    def score(k):
                        c = variables[k][1]
                        return max([(path_load.get(c["path_id"], 0)+size)/c["GBps"]] +
                                   [(resource_load.get(r, 0)+size)/caps[r] for r in c["resources"]])
                    chosen = min(choices, key=lambda k: (score(k), variables[k][1]["distance"]))
                assigned[chosen] = assigned.get(chosen, 0) + size
                c = variables[chosen][1]
                path_load[c["path_id"]] = path_load.get(c["path_id"], 0) + size
                for resource in c["resources"]:
                    resource_load[resource] = resource_load.get(resource, 0) + size
    ops, loads, path_loads = [], {}, {}
    for i, region in enumerate(regions):
        selected = [k for k, (j, _) in enumerate(variables) if j == i and assigned.get(k, 0) > 0]
        selected.sort(key=lambda k: -assigned[k])
        if not selected:
            raise RuntimeError("solver omitted a region")
        rounded = {k: int(assigned[k] // quantum) * quantum for k in selected[1:]}
        rounded[selected[0]] = region["end"] - region["begin"] - sum(rounded.values())
        cursor = region["begin"]
        for k in selected:
            n = rounded[k]
            if not n:
                continue
            c, d = variables[k][1], region["demand"]
            r = c["replica"]
            ops.append({"kind": "h2d", "tp": d["tp"], "dst_w": d["dst_w"], "off": cursor, "n": n,
                        "demand_id": f"{d['tp']}:{region['begin']}:{region['end']}",
                        "replica_id": r["replica_id"], "generation": r["generation"], "identity": r["identity"],
                        "source_offset": cursor-r["logical_offset"], "path_id": c["path_id"],
                        "resources": c["resources"]})
            cursor += n
            path_loads[c["path_id"]] = path_loads.get(c["path_id"], 0)+n
            for resource in c["resources"]:
                loads[resource] = loads.get(resource, 0)+n
    validate_ops(ops, {d["tp"]: d["nbytes"] for d in demands})
    bound = max([n / 1e9 / profile["paths"][p]["GBps"] for p, n in path_loads.items()] +
                [n / 1e9 / caps[r] for r, n in loads.items()])
    for op in ops:
        op["rate_GBps"] = path_loads[op["path_id"]] / 1e9 / bound
    return {"strategy": strategy, "ops": ops, "regions": len(regions), "lower_bound_s": lower,
            "near_preference_time_slack": 0.01 if strategy == "resource_lp" else None,
            "rounded_resource_bound_s": bound, "resource_bytes": loads,
            "planning_s": time.monotonic()-started,
            "scope": "fixed local DRAM replicas; bandwidth lower bound excludes setup"}
