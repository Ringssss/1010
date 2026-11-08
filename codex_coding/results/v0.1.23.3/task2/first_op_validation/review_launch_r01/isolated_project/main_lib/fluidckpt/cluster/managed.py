"""Versioned placement contracts, exclusive scenarios, and bounded transitions.

Only explicitly managed replicas may be replaced. Immutable base replicas must
cover the entire demand. Hardware observations are headroom, not reservations;
the controller rechecks them before every allocation under a single coordinator.
"""
import copy
import hashlib
import json
import math

from .joint import joint_placement
from .planning import check_retirement, covers, extent, path_for, restore_plan
from .wire import target_header
from .multi_joint import aggregate_cost, joint_scenarios
from .feedback import migration_gate, validate_policy
from .path_catalog import check_profile_evidence
from .service_cost import estimate_execution, new_state

TIERS = ("dram", "hbm")
FIELDS = ("id", "identity", "tp", "off", "n", "worker", "tier", "numa", "epoch", "generation",
          "state", "leases", "allocated_bytes")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum: raise ValueError(f"invalid {name}")
    return value


def aligned(n):
    return (n+4095)//4096*4096


def location(row):
    return f"{row['worker']}:{row['tier']}"


def physical(worker, tier):
    return f"dram:{worker['node']}:{worker['numa']}" if tier == "dram" else f"hbm:{worker['worker']}"


def rows(workers, valid_only=False):
    return [r for w in workers for r in w["replicas"] if not valid_only or r["state"] == "VALID"]


def state_hash(workers):
    return digest([{**{k: w[k] for k in ("worker", "node", "numa", "epoch", "session", "budget_bytes", "used_bytes")},
                    "replicas": sorted([{k: r[k] for k in FIELDS} for r in w["replicas"]], key=lambda r: r["id"]),
                    "transactions": sorted(w["transactions"])} for w in workers])


def check_snapshot(workers):
    if not workers or [w["worker"] for w in workers] != list(range(len(workers))):
        raise ValueError("invalid worker ordering")
    if len({w["session"] for w in workers}) != 1: raise ValueError("mixed worker sessions")
    if len({(w["node"], w["gpu"]) for w in workers}) != len(workers):
        raise ValueError("multiple workers alias the same physical GPU")
    ids = []
    for w in workers:
        if w["transactions"]: raise ValueError("placement requires an idle cluster")
        for r in w["replicas"]:
            if r["worker"] != w["worker"] or r["epoch"] != w["epoch"]: raise ValueError("replica owner epoch mismatch")
            if r["state"] not in ("VALID", "RELEASED") or r["leases"]: raise ValueError("busy replica")
            integer(r["off"], "replica offset"); integer(r["n"], "replica size", 1)
            if r["allocated_bytes"] != aligned(r["n"]): raise ValueError("allocation accounting mismatch")
            ids.append(r["id"])
        for tier in TIERS:
            used = sum(r["allocated_bytes"] for r in w["replicas"] if r["tier"] == tier and r["state"] != "RELEASED")
            if used != w["used_bytes"][tier] or used > w["budget_bytes"][tier]:
                raise ValueError("catalog capacity mismatch")
    if len(ids) != len(set(ids)): raise ValueError("duplicate cluster replica IDs")


def scenario_rows(q):
    return q.get('scenarios', [{'name': 'default', 'weight': 1., 'targets': q['targets']}])


def normalize_targets(targets, workers):
    ts = [target_header(t) for t in targets]
    if not ts or len({(t['identity'], t['tp']) for t in ts}) != len(ts):
        raise ValueError('single scenario requires one destination per model rank')
    if len({t['key'] for t in ts}) != len(ts):
        raise ValueError('duplicate target key')
    for t in ts:
        integer(t['worker'], 'target worker'); integer(t['tp'], 'TP rank')
        integer(t['off'], 'target offset'); integer(t['n'], 'target bytes', 1)
        if t['worker'] >= len(workers) or t.get('tier', 'hbm') != 'hbm':
            raise ValueError('unknown target worker or non-GPU restore target')
    return ts


def normalize_request(request, workers):
    check_snapshot(workers)
    q = copy.deepcopy(request)
    if q.get("schema_version") != 1 or not isinstance(q.get("scope"), str) or not q["scope"]:
        raise ValueError("placement request requires schema_version=1 and scope")
    q["executor"] = q.get("executor", "serial")
    if q["executor"] != "serial": raise ValueError("managed placement currently models the serial executor only")
    if 'scenarios' in q:
        if not isinstance(q['scenarios'], list) or not q['scenarios']:
            raise ValueError('empty scenarios')
        if q.get('scenario_mode', 'exclusive') != 'exclusive':
            raise ValueError('only mutually exclusive scenarios are supported')
        q['scenario_mode'] = 'exclusive'
        q['objective'] = q.get('objective', 'expected')
        if q['objective'] not in ('expected', 'worst'):
            raise ValueError('unsupported scenario objective')
        names = []
        for s in q['scenarios']:
            if not isinstance(s.get('name'), str) or not s['name']:
                raise ValueError('scenario requires a name')
            weight = s.get('weight', 1.)
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
                raise ValueError('scenario weight must be positive')
            s['weight'] = weight; s['targets'] = normalize_targets(s['targets'], workers)
            names.append(s['name'])
        if len(set(names)) != len(names):
            raise ValueError('duplicate scenario name')
        q.setdefault('targets', copy.deepcopy(q['scenarios'][0]['targets']))
    q['targets'] = normalize_targets(q['targets'], workers)
    layout = lambda ts: sorted((t['identity'], t['tp'], t['off'], t['n']) for t in ts)
    if any(layout(s['targets']) != layout(q['targets']) for s in scenario_rows(q)):
        raise ValueError('all scenarios must share one model layout and complete extent')
    if 'migration_policy' in q:
        validate_policy(q['migration_policy'])
    if 'service_model' in q:
        if q.get('service_pair_policy') != 'cold':
            raise ValueError('service placement requires explicit future cold pair policy')
        if q['service_model'].get('schema_version') != 2:
            raise ValueError('service placement requires request-aware model v2')
    q["base_ids"] = sorted(q["base_ids"]); q["managed_ids"] = sorted(q.get("managed_ids", []))
    all_ids = q["base_ids"]+q["managed_ids"]
    existing = {r["id"]: r for r in rows(workers, True)}
    if len(set(all_ids)) != len(all_ids) or not set(all_ids) <= existing.keys():
        raise ValueError("base/managed IDs must be disjoint valid replicas")
    demand_keys = {(t["identity"], t["tp"]) for t in q["targets"]}
    if any((existing[i]["identity"], existing[i]["tp"]) not in demand_keys for i in all_ids):
        raise ValueError("managed/base replica belongs to another demand")
    base = [existing[i] for i in q["base_ids"]]
    if any(not covers(base, t["identity"], t["tp"], *extent(t)) for t in q["targets"]):
        raise ValueError("immutable base does not cover the complete demand")
    q["block_bytes"] = integer(q.get("block_bytes", 1 << 30), "block size", 4096)
    if q["block_bytes"] % 4096: raise ValueError("planning block must be page-aligned")
    q["max_candidates"] = integer(q.get("max_candidates", 2048), "candidate limit", 1)
    q["minimum_gain_s"] = q.get("minimum_gain_s", .001)
    if not math.isfinite(q["minimum_gain_s"]) or q["minimum_gain_s"] < 0: raise ValueError("invalid gain threshold")
    budget = q["budget"]
    for field in ("tier_bytes", "temporary_tier_bytes"):
        budget[field] = {t: integer(budget.get(field, {}).get(t, 0), field) for t in TIERS}
    keys = []
    for loc in budget["locations"]:
        integer(loc["worker"], "location worker")
        if loc["worker"] >= len(workers) or loc["tier"] not in TIERS: raise ValueError("unknown memory location")
        for k in ("max_bytes", "temporary_bytes", "reserve_free_bytes"):
            loc[k] = integer(loc.get(k, 0), k)
        keys.append(location(loc))
    if len(keys) != len(set(keys)): raise ValueError("duplicate location budget")
    if not {location(existing[i]) for i in q["managed_ids"]} <= set(keys):
        raise ValueError("every managed location needs an explicit budget")
    return q


def usage(selected, workers):
    value = {"tiers": {t: 0 for t in TIERS}, "locations": {}, "physical": {}}
    for r in selected:
        n = aligned(r["n"]); loc = location(r); group = physical(workers[r["worker"]], r["tier"])
        value["tiers"][r["tier"]] += n
        value["locations"][loc] = value["locations"].get(loc, 0)+n
        value["physical"][group] = value["physical"].get(group, 0)+n
    return value


def fits(value, limits, peak=False):
    prefix = "peak_" if peak else ""
    return all(n <= limits[prefix+kind].get(k, 0) for kind in ("tiers", "locations", "physical")
               for k, n in value[kind].items())


def capacity_limits(q, workers):
    managed = [r for r in rows(workers, True) if r["id"] in q["managed_ids"]]
    current = usage(managed, workers)
    budget = q["budget"]
    limits = {"tiers": dict(budget["tier_bytes"]), "locations": {}, "physical": {},
              "peak_tiers": {t: budget["tier_bytes"][t]+budget["temporary_tier_bytes"][t] for t in TIERS},
              "peak_locations": {}, "peak_physical": {}, "groups": {}}
    for loc in budget["locations"]:
        w, tier = workers[loc["worker"]], loc["tier"]
        key = location(loc); group = physical(w, tier)
        free = integer(w["headroom_bytes"][tier], "observed free memory")
        owned = current["locations"].get(key, 0)
        quota = w["budget_bytes"][tier]-(w["used_bytes"][tier]-owned)
        headroom = free+current["physical"].get(group, 0)-loc["reserve_free_bytes"]
        if headroom < 0: raise ValueError(f"future reserved capacity unavailable at {key}")
        limits["locations"][key] = min(loc["max_bytes"], quota, headroom)
        limits["peak_locations"][key] = min(loc["max_bytes"]+loc["temporary_bytes"], quota, headroom)
        limits["physical"][group] = min(limits["physical"].get(group, headroom), headroom)
        limits["peak_physical"][group] = limits["physical"][group]
        limits["groups"].setdefault(group, []).append(key)
    if not fits(current, limits, peak=True):
        raise ValueError("current layout already exceeds requested transition peak budget")
    return limits


def semantic(r):
    return tuple(r[k] for k in ("identity", "tp", "off", "n", "worker", "tier", "numa"))


def candidate_id(scope, row):
    return "managed/"+digest([scope, semantic(row)])[:32]


def generate_candidates(q, workers, profile, limits):
    existing = [r for r in rows(workers, True) if r["id"] in q["managed_ids"]]
    reusable = {semantic(r): r["id"] for r in existing}
    candidates, skipped, coverage = {}, {}, []
    all_existing = {r["id"]: r for r in rows(workers)}
    base = [r for r in rows(workers, True) if r["id"] in q["base_ids"]]
    wm = {w["worker"]: w for w in workers}
    def add(c):
        key = semantic(c)
        c["id"] = reusable.get(key, candidate_id(q["scope"], c))
        old = all_existing.get(c["id"])
        if old and old["state"] != "RELEASED" and c["id"] not in q["managed_ids"]:
            raise ValueError("candidate ID collides with an unmanaged replica")
        if c["id"] not in q["managed_ids"]:
            build_target = dict(c, key=c["id"])
            reachable = [r for r in base if r["identity"] == c["identity"] and r["tp"] == c["tp"]
                         and path_for(r, build_target, wm, profile) is not None]
            if not covers(reachable, c["identity"], c["tp"], *extent(c)):
                skipped["unbuildable_from_base"] = skipped.get("unbuildable_from_base", 0)+1
                return
        candidates[key] = c
        if len(candidates) > q["max_candidates"]: raise ValueError("candidate limit exceeded; increase planning block size")
    covered = set()
    for t in [t for s in scenario_rows(q) for t in s['targets']]:
        begin, end = extent(t); blocks = 0
        for off in range(begin, end, q["block_bytes"]):
            n = min(q["block_bytes"], end-off); blocks += 1
            for loc in q["budget"]["locations"]:
                w = workers[loc["worker"]]
                c = {"identity": t["identity"], "tp": t["tp"], "off": off, "n": n,
                     "worker": w["worker"], "node": w["node"], "numa": w["numa"],
                     "tier": loc["tier"], "state": "VALID", "generation": 1, "candidate_only": True}
                reason = ("capacity" if aligned(n) > min(limits["locations"][location(c)], limits["tiers"][c["tier"]])
                          else "unreachable" if not path_for(c, t, {x["worker"]: x for x in workers}, profile) else None)
                if reason: skipped[reason] = skipped.get(reason, 0)+1
                else: add(c)
        coverage_key = (t['identity'], t['tp'], begin, end)
        if coverage_key not in covered:
            coverage.append({"identity": t["identity"], "tp": t["tp"], "off": begin, "n": end-begin, "blocks": blocks})
            covered.add(coverage_key)
    # Existing merged replicas remain selectable as complete allocations, not free partial slices.
    for r in existing:
        if aligned(r["n"]) <= limits["locations"][location(r)]:
            add({**{k: r[k] for k in ("identity", "tp", "off", "n", "worker", "tier", "numa")},
                 "node": workers[r["worker"]]["node"], "state": "VALID", "generation": 1, "candidate_only": True})
    return list(candidates.values()), {"count": len(candidates), "coverage": coverage, "filtered": skipped,
                                       "scope": "full demand blocks plus reusable existing allocations; candidate_only is never publication"}


def coalesce(selected, q):
    out = []
    for r in sorted(selected, key=lambda r: (r["identity"], r["tp"], r["worker"], r["tier"], r["numa"], r["off"])):
        c = {k: r[k] for k in ("id", "identity", "tp", "off", "n", "worker", "tier", "numa")}
        if (out and c["id"] not in q["managed_ids"] and out[-1]["id"] not in q["managed_ids"]
                and all(out[-1][k] == c[k] for k in ("identity", "tp", "worker", "tier", "numa"))
                and out[-1]["off"]+out[-1]["n"] == c["off"]):
            out[-1]["n"] += c["n"]; out[-1]["id"] = candidate_id(q["scope"], out[-1])
        else: out.append(c)
    return out


def simulate_action(workers, action):
    ws = copy.deepcopy(workers); r = action["replica"]; w = ws[r["worker"]]
    old = next((x for x in w["replicas"] if x["id"] == r["id"]), None)
    if action["kind"] == "retire":
        check_retirement(rows(ws, True), r["id"])
        if old is None: raise ValueError("retire target missing")
        old["state"] = "RELEASED"; w["used_bytes"][old["tier"]] -= old["allocated_bytes"]
    elif action["kind"] == "build":
        if old and old["state"] != "RELEASED": raise ValueError("build ID occupied")
        new = dict(r, epoch=w["epoch"], generation=old["generation"]+1 if old else 1,
                   state="VALID", leases=0, allocated_bytes=aligned(r["n"]))
        if old: w["replicas"].remove(old)
        w["replicas"].append(new); w["used_bytes"][r["tier"]] += new["allocated_bytes"]
    else: raise ValueError("unknown transition action")
    return ws


def transition_plan(workers, q, selected, limits):
    current = {r["id"]: r for r in rows(workers, True)}
    kept = {r["id"] for r in selected} & set(q["managed_ids"])
    removals = [current[i] for i in q["managed_ids"] if i not in kept]
    builds = [r for r in selected if r["id"] not in kept]
    ws = copy.deepcopy(workers); active = {i: current[i] for i in q["managed_ids"]}
    actions = []; peak = usage(list(active.values()), ws)
    def append(kind, replica):
        nonlocal ws
        action = {"kind": kind, "replica": {k: replica[k] for k in ("id", "identity", "tp", "off", "n", "worker", "tier", "numa")},
                  "before_state": state_hash(ws)}
        next_ws = simulate_action(ws, action)
        if kind == "build": active[replica["id"]] = replica
        else: active.pop(replica["id"])
        value = usage(list(active.values()), next_ws)
        if not fits(value, limits, peak=True): raise ValueError("transition exceeds peak capacity")
        for category in peak:
            for key, n in value[category].items(): peak[category][key] = max(peak[category].get(key, 0), n)
        action.update(after_state=state_hash(next_ws), usage=value)
        actions.append(action); ws = next_ws
    for c in builds:
        while not fits(usage(list(active.values())+[c], ws), limits, peak=True):
            if not removals: raise ValueError("no capacity-feasible transition under protected-base policy")
            # Retiring a complete redundant allocation is always coverage-safe due to the immutable base.
            append("retire", removals.pop(0))
        append("build", c)
    for r in removals: append("retire", r)
    if not fits(usage(list(active.values()), ws), limits): raise ValueError("final layout exceeds steady capacity")
    return {"actions": actions, "peak_usage": peak, "final_usage": usage(list(active.values()), ws),
            "final_state": state_hash(ws), "base_ids": q["base_ids"],
            "scope": "single coordinator, immutable complete base, verified per-replica publication; not global atomic replacement"}


def solve_joint_request(workers, request, profile):
    q = normalize_request(request, workers)
    check_profile_evidence(workers, profile)
    if not profile.get("serial_target_execution"):
        raise ValueError("managed serial placement requires an executor-aware cost profile")
    if len({w["node"] for w in workers}) > 1 and "network_pairs" not in profile:
        raise ValueError("multi-node managed placement requires an explicit network pair policy")
    if "network_pairs" in profile:
        if not isinstance(profile["network_pairs"], list) or any(not isinstance(p, list) or len(p) != 2 or any(type(i) is not int or not 0 <= i < len(workers) for i in p)
               for p in profile["network_pairs"]):
            raise ValueError("invalid verified network pair list")
    limits = capacity_limits(q, workers)
    current = [r for r in rows(workers, True) if r["id"] in q["managed_ids"]]
    protected = [r for r in rows(workers, True) if r["id"] not in q["managed_ids"]]
    # A complete but unreachable base cannot guarantee recovery after evicting redundant caches.
    base = [r for r in protected if r["id"] in q["base_ids"]]
    scenarios = scenario_rows(q)
    objective = q.get('objective', 'expected')
    for s in scenarios:
        restore_plan(base, s['targets'], workers, profile, 'nearest')
    candidates, summary = generate_candidates(q, workers, profile, limits)
    def evaluate(layout):
        plans = [restore_plan(layout, s['targets'], workers, profile, 'resource_lp') for s in scenarios]
        if any(p['strategy'] != 'resource_lp' for p in plans):
            raise RuntimeError('scenario evaluation LP fell back')
        times = ([estimate_execution(p, workers, q['service_model'], new_state(workers))['estimated_execute_s']
                  for p in plans] if 'service_model' in q else [p['estimated_transfer_s'] for p in plans])
        return aggregate_cost(times, scenarios, objective), dict(zip([s['name'] for s in scenarios], times))
    before_s, before_times = evaluate(protected+current)
    fallback = None
    try:
        options = dict(shared_location_budgets={k: {'members': members, 'bytes': limits['physical'][k]}
                        for k, members in limits['groups'].items()}, solver_options=q.get('solver_options'))
        if 'scenarios' in q:
            solver = joint_scenarios(protected, candidates, scenarios, workers, profile,
                limits['tiers'], limits['locations'], objective=objective, **options)
        else:
            solver = joint_placement(protected, candidates, q['targets'], workers, profile,
                limits['tiers'], limits['locations'], **options)
        selected = coalesce(solver["selected"], q)
        after_s, after_times = evaluate(protected+[dict(c, state='VALID', generation=1) for c in selected])
        if 'service_model' in q:
            alternatives = [('bandwidth_milp', selected, after_s, after_times)]
            base_s, base_times = evaluate(protected)
            alternatives.append(('base_only', [], base_s, base_times))
            if fits(usage(current, workers), limits):
                alternatives.append(('current', coalesce(current, q), before_s, before_times))
            winner = min(alternatives, key=lambda a: (a[2], 0 if a[0] == 'current' else 1))
            selected, after_s, after_times = winner[1:]
            solver = {'status': 'service_reranked', 'selected_proposal': winner[0],
                      'bandwidth_solver': {k: v for k, v in solver.items() if k != 'selected'},
                      'proposals': [{'name': a[0], 'predicted_s': a[2]} for a in alternatives],
                      'scope': 'best of current, base-only and bandwidth-MILP proposal under cold service scoring; not globally service-optimal'}
        decision = "replace"
        if before_s-after_s <= q['minimum_gain_s'] and fits(usage(current, workers), limits):
            selected = coalesce(current, q); after_s = before_s; after_times = before_times; decision = 'keep_current'
    except RuntimeError as exc:
        if not fits(usage(current, workers), limits): raise RuntimeError(f"solver failed and current layout violates steady budget: {exc}") from exc
        selected = coalesce(current, q); after_s = before_s; after_times = before_times
        decision = "fallback_keep_current"; fallback = str(exc)
        solver = {"status": "failed", "scope": "no placement optimum claimed"}
    if not fits(usage(selected, workers), limits): raise ValueError("selected layout violates exact byte budgets")
    transition = transition_plan(workers, q, selected, limits)
    economics = None
    if 'migration_policy' in q and decision == 'replace':
        economics = migration_gate(before_s, after_s, transition, base, workers, profile, q['migration_policy'],
                                   current_fits=fits(usage(current, workers), limits), service_model=q.get('service_model'))
        if not economics['allowed']:
            selected = coalesce(current, q); after_s = before_s; after_times = before_times
            decision = 'keep_current'; transition = transition_plan(workers, q, selected, limits)
    plan = {"schema_version": 1, "request": q, "initial_state": state_hash(workers), "profile_hash": digest(profile),
            "protected_source_ids": [r["id"] for r in protected],
            "profile_provenance": profile.get("provenance"), "limits": limits, "candidates": summary,
            "selected": selected, "transition": transition, "decision": decision, "fallback_reason": fallback,
            "solver": {k: v for k, v in solver.items() if k != "selected"},
            'predicted_before_s': before_s, 'predicted_after_s': after_s, 'economics': economics,
            'cost_scope': 'cold serial execution, including request overhead' if 'service_model' in q else 'bandwidth transfer model',
            'scenario_costs': {s['name']: {'before_s': before_times[s['name']], 'after_s': after_times[s['name']]}
                               for s in scenarios}}
    plan["plan_id"] = digest(plan)
    return plan


def verify_plan_hash(plan):
    if plan.get("schema_version") != 1 or plan.get("plan_id") != digest({k: v for k, v in plan.items() if k != "plan_id"}):
        raise ValueError("placement plan was modified or has an unsupported version")
