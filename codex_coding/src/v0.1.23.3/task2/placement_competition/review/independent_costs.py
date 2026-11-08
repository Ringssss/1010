"""Recompute frozen operation loads and worker queues without planner imports."""
from collections import defaultdict
from fractions import Fraction


def exact(value):
    return Fraction(str(value))


def physical_path(replica, target, workers, profile):
    """Expand the declared path resources independently of path_for."""
    src, dst = workers[replica["worker"]], workers[target["worker"]]
    same = src["worker"] == dst["worker"]
    kind = "local_" + replica["tier"] if same else "peer" if src["node"] == dst["node"] else "network"
    key = f"{src['worker']}:{replica['tier']}:{replica['numa']}->{dst['worker']}"
    resources = {f"in:{dst['worker']}": exact(profile["gpu_ingress_GBps"])}
    if replica["tier"] == "dram":
        resources[f"dram:{src['node']}:{replica['numa']}"] = exact(profile["dram_GBps"])
    if not same:
        resources[f"out:{src['worker']}"] = exact(profile["gpu_egress_GBps"])
    if kind == "network":
        resources[f"net_out:{src['node']}"] = exact(profile["network_node_GBps"])
        resources[f"net_in:{dst['node']}"] = exact(profile["network_node_GBps"])
    rate = profile.get("directed_path_rates", {}).get(key, profile.get("path_rates", {}).get(kind + ":" + replica["tier"], profile["rates"][kind]))
    return key, kind, exact(rate), resources


def recompute(case, profile, selected_ids, plan):
    replicas = {r["id"]: r for r in case["base"] + [c for c in case["candidates"] if c["id"] in selected_ids]}
    workers = {w["worker"]: w for w in case["workers"]}
    targets = {t["key"]: t for t in plan["targets"]}
    ready = defaultdict(Fraction)
    target_service, source_service, loads, caps = (defaultdict(Fraction) for _ in range(4))
    timeline, spans, inconsistencies = [], defaultdict(list), []
    receiver_finish = defaultdict(Fraction)
    flows = []
    for op in plan["ops"]:
        r, t = replicas[op["replica_id"]], targets[op["target"]]
        key, kind, rate, resources = physical_path(r, t, workers, profile)
        n = exact(op["n"])
        if (op["path"] != key or op["kind"] != kind or set(op["resources"]) != set(resources)
                or op["src_w"] != r["worker"] or op["dst_w"] != t["worker"]
                or op["identity"] != r["identity"] or op["identity"] != t["identity"]
                or op["tp"] != r["tp"] or op["tp"] != t["tp"]
                or op["off"] < r["off"] or op["off"] + op["n"] > r["off"] + r["n"]
                or op["source_offset"] != op["off"] - r["off"]):
            inconsistencies.append(op["op_id"])
        spans[t["key"]].append((op["off"], op["off"] + op["n"]))
        service = n / 1_000_000_000 / rate
        source, destination = op["src_w"], op["dst_w"]
        start = max(ready[source], ready[destination])
        end = start + service
        timeline.append({"op_id": op["op_id"], "start": float(start), "end": float(end),
                         "source_ready_before": float(ready[source]),
                         "target_ready_before": float(ready[destination]),
                         "source_worker": source, "target_worker": destination})
        ready[source] = ready[destination] = end
        receiver_finish[destination] = end
        flows.append((start, end, {**resources, "path:" + key: rate}, rate))
        source_service[source] += service
        target_service[destination] += service
        for resource, capacity in resources.items():
            loads[resource] += n
            caps[resource] = capacity
        loads["path:" + key] += n
        caps["path:" + key] = rate
    coverage = True
    for key, target in targets.items():
        cursor = target["off"]
        for begin, end in sorted(spans[key]):
            coverage &= begin == cursor and end > begin
            cursor = end
        coverage &= cursor == target["off"] + target["n"]
    lower = max((loads[k] / 1_000_000_000 / caps[k] for k in loads), default=Fraction(0))
    if profile.get("serial_target_execution"):
        lower = max(lower, max(target_service.values(), default=Fraction(0)))
    events = sorted({event for start, end, _, _ in flows for event in (start, end)})
    violations = []
    for begin, end in zip(events, events[1:]):
        demands = defaultdict(Fraction)
        capacities = {}
        for start, finish, resources, rate in flows:
            if start <= begin and finish >= end:
                for key, capacity in resources.items():
                    demands[key] += rate
                    capacities[key] = capacity
        violations.extend({"begin": float(begin), "end": float(end), "resource": key,
                           "rate": float(demand), "capacity": float(capacities[key])}
                          for key, demand in demands.items() if demand > capacities[key])
    return {"coverage": bool(coverage), "operation_inconsistencies": inconsistencies,
            "resource_bound": float(lower), "worker_dag": float(max(ready.values(), default=Fraction(0))),
            "timeline": timeline, "resource_load_bytes": {k: int(v) for k, v in loads.items()},
            "resource_capacity_GBps": {k: float(v) for k, v in caps.items()},
            "target_service": {str(k): float(v) for k, v in target_service.items()},
            "target_receiver_finish": {str(k): float(v) for k, v in receiver_finish.items()},
            "source_service": {str(k): float(v) for k, v in source_service.items()},
            "declared_symbolic_dag_schedule_capacity_feasible": not violations,
            "symbolic_capacity_violations": violations,
            "joint_shared_resource_optimum_certified": False}


def feasible_id_sets(case):
    candidates = case["candidates"]
    result = set()
    for mask in range(1 << len(candidates)):
        selected = [c for i, c in enumerate(candidates) if mask & (1 << i)]
        location_bytes = defaultdict(int)
        for c in selected:
            location_bytes[f"{c['worker']}:{c['tier']}"] += ((c["n"] + 4095) // 4096) * 4096
        if sum(location_bytes.values()) > case["budget"]["tier_budgets"]["hbm"]:
            continue
        if any(n > case["budget"]["location_budgets"][key] for key, n in location_bytes.items()):
            continue
        result.add(tuple(sorted(c["id"] for c in selected)))
    return result


def pareto(rows, score_key):
    points = {(row["cache_bytes"], round(row[score_key], 10)) for row in rows}
    return sorted((cache, cost) for cache, cost in points if not any(
        other_cache <= cache and other_cost <= cost and (other_cache < cache or other_cost < cost)
        for other_cache, other_cost in points))
