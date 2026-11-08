"""Pure planners for explicit cluster replicas; bandwidth estimates are not guarantees."""
import copy
import math
import time

from .path_catalog import check_profile_evidence, path_key


def extent(row):
    return row.get("off", 0), row.get("off", 0) + row["n"]


def covers(rows, identity, rank, begin, end):
    intervals = sorted(extent(r) for r in rows if r["state"] == "VALID"
                       and r["identity"] == identity and r["tp"] == rank)
    cursor = begin
    for a, b in intervals:
        if a > cursor:
            break
        cursor = max(cursor, b)
    return cursor >= end


def check_retirement(replicas, replica_id):
    source = next(r for r in replicas if r["id"] == replica_id)
    if source.get("leases", 0):
        raise ValueError("source has active readers")
    if not covers([r for r in replicas if r["id"] != replica_id], source["identity"],
                  source["tp"], *extent(source)):
        raise ValueError("retirement would remove the last valid coverage")
    return source


def path_for(replica, target, workers, profile):
    src, dst = workers[replica["worker"]], workers[target["worker"]]
    key = path_key(replica['worker'], replica['tier'], replica['numa'], dst['worker'], target.get('tier', 'hbm'))
    if 'verified_paths' in profile and key not in profile['verified_paths']:
        return None
    same = src["worker"] == dst["worker"]
    kind = ("local_" + replica["tier"] if same else
            "peer" if src["node"] == dst["node"] else "network")
    if kind == "network" and "network_pairs" in profile:
        if [src["worker"], dst["worker"]] not in profile["network_pairs"]:
            return None
    if kind not in profile["rates"] or kind not in src["capabilities"] or kind not in dst["capabilities"]:
        return None
    resources = {f"in:{dst['worker']}": profile["gpu_ingress_GBps"]}
    ssd_locations = []
    if replica['tier'] == 'ssd': ssd_locations.append(replica.get('storage_node', src['node']))
    if target.get('tier') == 'ssd': ssd_locations.append(target.get('storage_node', dst['node']))
    for location in ssd_locations:
        resources[f'ssd:{location}'] = profile.get('ssd_GBps', 0.5)
        resources[f'store_network:{location}'] = profile.get('store_network_GBps', 1.0)
    if replica["tier"] == "dram":
        resources[f"dram:{src['node']}:{replica['numa']}"] = profile["dram_GBps"]
    if not same:
        resources[f"out:{src['worker']}"] = profile["gpu_egress_GBps"]
    if kind == "network":
        resources[f"net_out:{src['node']}"] = profile["network_node_GBps"]
        resources[f"net_in:{dst['node']}"] = profile["network_node_GBps"]
    rate = profile.get('directed_path_rates', {}).get(key,
        profile.get("path_rates", {}).get(kind+":"+replica["tier"], profile["rates"][kind]))
    if ssd_locations:
        rate = min(rate, profile.get('ssd_GBps', 0.5), profile.get('store_network_GBps', 1.0))
    if not math.isfinite(rate) or rate <= 0: raise ValueError("invalid path bandwidth")
    return {"kind": kind, "GBps": rate, "resources": resources,
            "distance": 0 if same else 1 if src["node"] == dst["node"] else 2,
            "path": key}


def validate_plan(plan):
    if len({t["key"] for t in plan["targets"]}) != len(plan["targets"]):
        raise ValueError("duplicate target")
    if len({o["op_id"] for o in plan["ops"]}) != len(plan["ops"]):
        raise ValueError("duplicate operation")
    known = {t["key"] for t in plan["targets"]}
    if any(o["target"] not in known for o in plan["ops"]):
        raise ValueError("unknown target")
    for t in plan["targets"]:
        begin, end = extent(t)
        cursor = begin
        for o in sorted((o for o in plan["ops"] if o["target"] == t["key"]), key=lambda o: o["off"]):
            if any(type(o[k]) is not int for k in ("off", "n", "src_w", "dst_w", "source_offset")):
                raise ValueError("integer byte ranges required")
            if (o["off"] != cursor or o["n"] <= 0 or o["dst_w"] != t["worker"]
                    or o["identity"] != t["identity"] or o["tp"] != t["tp"]):
                raise ValueError("invalid coverage or target identity")
            cursor += o["n"]
        if cursor != end:
            raise ValueError("incomplete target coverage")


def restore_plan(replicas, targets, worker_rows, profile, strategy="nearest", quantum=4 << 20):
    started = time.monotonic()
    check_profile_evidence(worker_rows, profile)
    requested_strategy = strategy
    if strategy not in ("nearest", "fastest", "balanced", "resource_lp") or quantum <= 0:
        raise ValueError("unsupported planner")
    workers = {w["worker"]: w for w in worker_rows}
    for rate in list(profile["rates"].values()) + [profile[k] for k in (
            "gpu_ingress_GBps", "gpu_egress_GBps", "dram_GBps", "network_node_GBps")]:
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("invalid bandwidth")
    regions, variables, caps, path_caps = [], [], {}, {}
    for target in targets:
        begin, end = extent(target)
        if type(begin) is not int or type(target["n"]) is not int or begin < 0 or end <= begin:
            raise ValueError("invalid target range")
        sources = [r for r in replicas if r["state"] == "VALID" and r["tp"] == target["tp"]
                   and r["identity"] == target["identity"] and r["worker"] in workers]
        points = sorted({begin, end} | {v for r in sources for v in extent(r) if begin < v < end})
        for a, b in zip(points, points[1:]):
            choices = []
            for r in sources:
                ra, rb = extent(r)
                if ra <= a and rb >= b:
                    p = path_for(r, target, workers, profile)
                    if p:
                        choices.append((r, p))
            if not choices:
                raise ValueError(f"no executable source for {target['key']} [{a},{b})")
            choices.sort(key=lambda rp: (rp[1]["distance"], -rp[1]["GBps"], rp[0]["id"]))
            region = {"target": target, "off": a, "n": b-a, "choices": []}
            for r, p in choices:
                region["choices"].append(len(variables))
                variables.append((len(regions), r, p))
                caps.update(p["resources"])
                path_caps[p["path"]] = p["GBps"]
            regions.append(region)
    if not regions:
        raise ValueError("empty restore request")
    values, lower, fallback = {}, None, None
    if strategy == "resource_lp":
        import numpy as np
        from scipy.optimize import linprog
        n = len(variables)
        eq = np.zeros((len(regions), n+1))
        for k, (i, _, _) in enumerate(variables):
            eq[i, k] = 1
        # MB scaling preserves one-byte tail constraints above the solver feasibility tolerance.
        rows = [[float(res in p["resources"]) for _, _, p in variables] + [-cap*1000]
                for res, cap in caps.items()]
        rows += [[float(p["path"] == pid) for _, _, p in variables] + [-cap*1000]
                 for pid, cap in path_caps.items()]
        if profile.get("serial_target_execution"):
            rows += [[(1/(p["GBps"]*1000) if regions[i]["target"]["worker"] == w else 0)
                      for i, _, p in variables]+[-1] for w in {t["worker"] for t in targets}]
        solution = linprog([0]*n+[1], A_eq=eq, b_eq=[r["n"]/1e6 for r in regions],
                           A_ub=rows, b_ub=[0]*len(rows), bounds=(0, None), method="highs",
                           options={"time_limit": 2})
        if solution.success:
            values = {k: max(0, float(solution.x[k])*1e6) for k in range(n)}
            lower = float(solution.x[-1])
        else:
            fallback, strategy = solution.message, "nearest"
    if strategy != "resource_lp":
        for region in regions:
            choices = region["choices"]
            if strategy == "balanced":
                for k in choices:
                    values[k] = region["n"]/len(choices)
            else:
                chosen = choices[0] if strategy == "nearest" else max(choices, key=lambda k: variables[k][2]["GBps"])
                values[chosen] = region["n"]
    ops, loads, serial_times = [], {}, {}
    for region in regions:
        selected = sorted((k for k in region["choices"] if values.get(k, 0) > 0), key=lambda k: -values[k])
        if not selected:
            raise RuntimeError("solver omitted a region")
        counts = {k: int(values[k] // quantum)*quantum for k in selected[1:]}
        counts[selected[0]] = region["n"]-sum(counts.values())
        cursor, t = region["off"], region["target"]
        for k in selected:
            n = counts[k]
            if not n:
                continue
            _, r, p = variables[k]
            op = {"op_id": len(ops), "target": t["key"], "tp": t["tp"], "identity": t["identity"],
                  "off": cursor, "n": n, "source_offset": cursor-r["off"], "replica_id": r["id"],
                  "generation": r["generation"], "src_w": r["worker"], "dst_w": t["worker"],
                  "source_epoch": r.get("epoch"),
                  "kind": p["kind"], "path": p["path"], "resources": list(p["resources"])}
            ops.append(op)
            serial_times[t["worker"]] = serial_times.get(t["worker"], 0)+n/1e9/p["GBps"]
            for key in p["resources"]:
                loads[key] = loads.get(key, 0)+n
            key = "path:"+p["path"]
            loads[key] = loads.get(key, 0)+n
            caps[key] = p["GBps"]
            cursor += n
    plan = {"targets": targets, "ops": ops, "strategy": strategy, "requested_strategy": requested_strategy, "fallback_reason": fallback,
            "bandwidth_lower_bound_s": lower, "estimated_transfer_s": max(loads[k]/1e9/caps[k] for k in loads),
            "planning_s": time.monotonic()-started, "profile": profile,
            "scope": "fixed layout; provisional capacity model excludes setup, chunk order and contention outside profile"}
    validate_plan(plan)
    if profile.get("serial_target_execution"):
        plan["estimated_transfer_s"] = max(plan["estimated_transfer_s"], max(serial_times.values()))
        plan["serial_target_times_s"] = serial_times
        plan["scope"] = "resource bounds plus additive target service demand using measured end-to-end path rates; excludes setup and cross-pair scheduling waits"
    return plan


def placement_plan(replicas, requests, workers, profile, policy="nearest", scenarios=None,
                   extra_budget=8 << 30, build_penalty=0.0):
    """Choose one location per requested region, or optional additions by predicted marginal gain.

    Requests contain candidate worker/tier locations. Joint greedy uses scenario-weighted
    restore time estimates; it is not a globally optimal or calibrated placement claim.
    """
    if policy not in ("nearest", "capacity", "joint_greedy"):
        raise ValueError("unknown placement policy")
    usage = {(w["worker"], tier): w["used_bytes"].get(tier, 0) for w in workers for tier in ("dram", "hbm")}
    wm = {w["worker"]: w for w in workers}
    layout, decisions, used = copy.deepcopy(replicas), [], 0
    evaluation_counts, fallback_reasons = {}, []
    def score(rs):
        total = 0.0
        for s in scenarios:
            p = restore_plan(rs, s["targets"], workers, profile, "resource_lp")
            evaluation_counts[p["strategy"]] = evaluation_counts.get(p["strategy"], 0)+1
            if p["fallback_reason"] and p["fallback_reason"] not in fallback_reasons:
                fallback_reasons.append(p["fallback_reason"])
            total += s.get("weight", 1)*p["estimated_transfer_s"]
        return total
    before = current = score(layout) if policy == "joint_greedy" and scenarios else None
    if policy == "joint_greedy" and not scenarios:
        raise ValueError("joint placement requires future restore scenarios")
    pending = list(requests)
    while pending:
        choices = []
        for i, req in enumerate(pending):
            if not covers(layout, req["identity"], req["tp"], *extent(req)):
                raise ValueError("placement region has no valid source")
            if any(r["id"] == req["id"] and r["state"] != "RELEASED" for r in layout):
                raise ValueError("replica id already exists")
            for loc in req["locations"]:
                w, tier = wm[loc["worker"]], loc["tier"]
                n = (req["n"]+4095)//4096*4096
                free = w["budget_bytes"][tier]-usage[(w["worker"], tier)]
                if n > free or used+n > extra_budget:
                    continue
                candidate = {k: req[k] for k in ("id", "identity", "tp", "off", "n")}
                candidate.update(worker=w["worker"], node=w["node"], gpu=w["gpu"], numa=w["numa"],
                                 tier=tier, state="VALID", generation=1, leases=0)
                after = score(layout+[candidate]) if current is not None else None
                gain = current-after-build_penalty*n/1e9 if current is not None else 0
                preferred = wm[req.get("preferred_worker", w["worker"])]
                distance = (0 if w["worker"] == preferred["worker"] else
                            1 if w["node"] == preferred["node"] else 2)
                priority = (-gain/n, w["worker"]) if policy == "joint_greedy" else (
                    -free if policy == "capacity" else distance, w["worker"])
                choices.append((priority, i, candidate, n, after, gain))
        if not choices:
            if policy == "joint_greedy": break
            raise MemoryError("no legal placement under capacity and extra-byte budget")
        _, i, chosen, n, after, gain = min(choices, key=lambda row: row[0])
        if policy == "joint_greedy" and gain <= 1e-12:
            break
        usage[(chosen["worker"], chosen["tier"])] += n
        used += n
        layout.append(chosen)
        decisions.append({"replica": chosen, "marginal_predicted_gain_s": gain if current is not None else None})
        current = after
        pending.pop(i)
    return {"policy": policy, "decisions": decisions, "extra_bytes": used, "budget_bytes": extra_budget,
            "evaluation_requested_strategy": "resource_lp" if policy == "joint_greedy" else None,
            "evaluation_actual_strategy_counts": evaluation_counts, "evaluation_fallback_reasons": fallback_reasons,
            "estimated_before_s": before, "estimated_after_s": current,
            "unplaced": [r["id"] for r in pending], "scope": "fixed candidate regions; heuristic placement under provisional bandwidth estimates"}
