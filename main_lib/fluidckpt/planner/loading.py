"""Byte-exact loading plans for a fixed, explicit set of replicas and resources."""
import math
import time


def validate_ops(ops, sizes):
    coverage = {int(tp): [] for tp in sizes}
    for op in ops:
        if op.get("kind") not in ("h2d", "ssd", "ib"):
            raise ValueError(f"unsupported transfer kind: {op}")
        tp, off, count = op["tp"], op["off"], op["n"]
        if any(type(v) is not int for v in (tp, off, count, op["dst_w"])):
            raise ValueError("rank, worker and byte ranges must be integers")
        if tp not in coverage or off < 0 or count <= 0 or off + count > sizes[tp]:
            raise ValueError(f"invalid byte range: {op}")
        if op["kind"] == "ib" and (type(op.get("src_w")) is not int or op["src_w"] == op["dst_w"]):
            raise ValueError("IB operation needs distinct source and target workers")
        coverage[tp].append((off, off + count, op["dst_w"]))
    for tp, ranges in coverage.items():
        cursor = 0
        destinations = set()
        for begin, end, worker in sorted(ranges):
            if begin != cursor:
                raise ValueError(f"gap or overlap in rank {tp}: cursor={cursor}, next={begin}")
            cursor = end
            destinations.add(worker)
        if cursor != sizes[tp] or len(destinations) != 1:
            raise ValueError(f"incomplete or ambiguous destination for rank {tp}")


def plan(demands, resource_caps, strategy="resource_lp", quantum=16 << 20):
    """Each candidate provides an executable op template, GB/s and shared resource names.

    Replica identity/capability filtering happens before this pure planner. Returns
    a valid byte partition; lower_bound_s excludes mapping and buffer setup costs.
    """
    start = time.monotonic()
    if strategy not in ("fixed_local", "fastest", "resource_lp") or quantum <= 0:
        raise ValueError("unsupported strategy or quantum")
    if not demands or len({d["tp"] for d in demands}) != len(demands):
        raise ValueError("nonempty unique demands required")
    variables = []
    for i, demand in enumerate(demands):
        if type(demand["nbytes"]) is not int or demand["nbytes"] <= 0 or not demand["candidates"]:
            raise ValueError("each demand needs positive bytes and a valid source")
        for candidate in demand["candidates"]:
            if not math.isfinite(candidate["GBps"]) or candidate["GBps"] <= 0:
                raise ValueError("candidate bandwidth must be measured and positive")
            if len(set(candidate["resources"])) != len(candidate["resources"]):
                raise ValueError("duplicate resource on one path")
            for resource in candidate["resources"]:
                if resource not in resource_caps or not math.isfinite(resource_caps[resource]) or resource_caps[resource] <= 0:
                    raise ValueError(f"missing measured capacity: {resource}")
            variables.append((i, candidate))
    values, lower = {}, None
    if strategy == "resource_lp":
        import numpy as np
        from scipy.optimize import linprog
        n = len(variables)
        equal = np.zeros((len(demands), n + 1))
        limits = []
        for k, (i, candidate) in enumerate(variables):
            equal[i, k] = 1
            row = np.zeros(n + 1)
            row[k], row[-1] = 1, -candidate["GBps"]
            limits.append(row)
        for resource, capacity in resource_caps.items():
            row = np.array([float(resource in c["resources"]) for _, c in variables] + [-capacity])
            limits.append(row)
        objective = np.zeros(n + 1); objective[-1] = 1
        solved = linprog(objective, A_eq=equal, b_eq=[d["nbytes"] / 1e9 for d in demands],
                         A_ub=np.array(limits), b_ub=np.zeros(len(limits)), bounds=(0, None),
                         method="highs", options={"time_limit": 5})
        if not solved.success:
            fallback = plan(demands, resource_caps, "fastest", quantum)
            fallback.update(requested_strategy=strategy, fallback_reason=solved.message,
                            planning_s=time.monotonic() - start)
            return fallback
        values = {k: max(0, solved.x[k] * 1e9) for k in range(n)}
        lower = float(solved.x[-1])
    else:
        for i, demand in enumerate(demands):
            choices = [k for k, (j, _) in enumerate(variables) if j == i]
            chosen = choices[0] if strategy == "fixed_local" else max(choices, key=lambda k: variables[k][1]["GBps"])
            values[chosen] = demand["nbytes"]
    ops, resource_bytes, path_seconds = [], {}, []
    for i, demand in enumerate(demands):
        chosen = [k for k, (j, _) in enumerate(variables) if j == i and values.get(k, 0) > 0]
        chosen.sort(key=lambda k: -values[k])
        if not chosen:
            raise RuntimeError("solver omitted a demand")
        sizes = {k: int(values[k] // quantum) * quantum for k in chosen[1:]}
        sizes[chosen[0]] = demand["nbytes"] - sum(sizes.values())
        off = 0
        for k in chosen:
            size = sizes[k]
            if not size:
                continue
            candidate = variables[k][1]
            op = dict(candidate["op"], tp=demand["tp"], off=off, n=size)
            ops.append(op)
            off += size
            path_seconds.append(size / 1e9 / candidate["GBps"])
            for resource in candidate["resources"]:
                resource_bytes[resource] = resource_bytes.get(resource, 0) + size
    validate_ops(ops, {d["tp"]: d["nbytes"] for d in demands})
    bound = max(path_seconds + [size / 1e9 / resource_caps[r] for r, size in resource_bytes.items()])
    return {"strategy": strategy, "ops": ops, "lower_bound_s": lower,
            "rounded_resource_bound_s": bound, "resource_bytes": resource_bytes,
            "planning_s": time.monotonic() - start,
            "assumptions": "fixed measured capacities; divisible bytes; excludes fixed setup and interference outside profile"}
