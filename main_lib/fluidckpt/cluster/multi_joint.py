"""Shared placement binaries with independent flows for mutually exclusive scenarios."""
import math
import time

from .planning import covers, extent, path_for, restore_plan


def aggregate_cost(values, scenarios, objective):
    if objective == 'worst':
        return max(values)
    if objective != 'expected':
        raise ValueError('unsupported scenario objective')
    total = sum(s['weight'] for s in scenarios)
    return sum(v*s['weight']/total for v, s in zip(values, scenarios))


def joint_scenarios(base, candidates, scenarios, workers, profile, tier_budgets, location_budgets,
                    shared_location_budgets=None, solver_options=None, objective='expected'):
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    started = time.monotonic()
    if not scenarios or len({s['name'] for s in scenarios}) != len(scenarios):
        raise ValueError('empty or duplicate scenarios')
    if any(not math.isfinite(s['weight']) or s['weight'] <= 0 for s in scenarios):
        raise ValueError('scenario weights must be positive')
    aggregate_cost([0]*len(scenarios), scenarios, objective)
    if len({r['id'] for r in base+candidates}) != len(base+candidates):
        raise ValueError('duplicate replica IDs')
    for c in candidates:
        if c['tier'] not in tier_budgets or f"{c['worker']}:{c['tier']}" not in location_budgets:
            raise ValueError('candidate has no explicit capacity budget')
        if c['n'] <= 0 or not covers(base, c['identity'], c['tp'], *extent(c)):
            raise ValueError('candidate has no valid build source')
    wm = {w['worker']: w for w in workers}
    regions, flows, resources = [], [], {}
    for si, scenario in enumerate(scenarios):
        for target in scenario['targets']:
            begin, end = extent(target)
            replicas = [r for r in base+candidates if r['identity'] == target['identity'] and r['tp'] == target['tp']]
            points = sorted({begin, end} | {v for r in replicas for v in extent(r) if begin < v < end})
            for a, b in zip(points, points[1:]):
                ri = len(regions)
                regions.append((si, target, b-a))
                for r in replicas:
                    lo, hi = extent(r)
                    path = path_for(r, target, wm, profile) if lo <= a and hi >= b else None
                    if path:
                        flows.append((ri, r, path))
                        resources.setdefault(si, {}).update(path['resources'])
                        resources[si]['path:'+path['path']] = path['GBps']
    nc, nf, ns = len(candidates), len(flows), len(scenarios)
    nt = nc+nf+ns+int(objective == 'worst')
    ci = {r['id']: i for i, r in enumerate(candidates)}
    rr, cc, vv, lower, upper = [], [], [], [], []
    def add(values, lo=-np.inf, hi=np.inf):
        index = len(lower); lower.append(lo); upper.append(hi)
        for col, value in values.items():
            rr.append(index); cc.append(col); vv.append(value)
    for ri, (_, _, n) in enumerate(regions):
        add({nc+k: 1 for k, (i, _, _) in enumerate(flows) if i == ri}, n/1e6, n/1e6)
    for k, (ri, r, _) in enumerate(flows):
        if r['id'] in ci:
            add({nc+k: 1, ci[r['id']]: -regions[ri][2]/1e6}, hi=0)
    groups = [(lambda c, tier=t: c['tier'] == tier, cap) for t, cap in tier_budgets.items()]
    groups += [(lambda c, key=key: f"{c['worker']}:{c['tier']}" == key, cap) for key, cap in location_budgets.items()]
    groups += [(lambda c, members=g['members']: f"{c['worker']}:{c['tier']}" in members, g['bytes'])
               for g in (shared_location_budgets or {}).values()]
    for predicate, cap in groups:
        if not math.isfinite(cap) or cap < 0:
            raise ValueError('invalid capacity budget')
        add({i: ((c['n']+4095)//4096*4096)/1e6 for i, c in enumerate(candidates) if predicate(c)}, hi=cap/1e6)
    for si, scenario in enumerate(scenarios):
        tc = nc+nf+si
        for res, cap in resources.get(si, {}).items():
            values = {nc+k: 1 for k, (ri, _, p) in enumerate(flows) if regions[ri][0] == si and
                      (res in p['resources'] or res == 'path:'+p['path'])}
            add(values | {tc: -cap*1000}, hi=0)
        if profile.get('serial_target_execution'):
            for w in {t['worker'] for t in scenario['targets']}:
                values = {nc+k: 1/(p['GBps']*1000) for k, (ri, _, p) in enumerate(flows)
                          if regions[ri][0] == si and regions[ri][1]['worker'] == w}
                add(values | {tc: -1}, hi=0)
        if objective == 'worst':
            add({tc: 1, nt-1: -1}, hi=0)
    cost = np.zeros(nt)
    if objective == 'worst':
        cost[-1] = 1
    else:
        cost[nc+nf:] = [s['weight']/sum(s['weight'] for s in scenarios) for s in scenarios]
    bounds = np.full(nt, np.inf); bounds[:nc] = 1
    matrix = coo_matrix((vv, (rr, cc)), shape=(len(lower), nt)).tocsc()
    options = {'time_limit': 20, 'mip_rel_gap': 1e-7}
    for k, v in (solver_options or {}).items():
        if k not in options or not math.isfinite(v) or v < 0:
            raise ValueError('invalid solver option')
        options[k] = v
    result = milp(cost, integrality=np.r_[np.ones(nc), np.zeros(nt-nc)], bounds=Bounds(np.zeros(nt), bounds),
                  constraints=LinearConstraint(matrix, lower, upper), options=options)
    x = result.x
    if x is None or (not result.success and result.status != 1):
        raise RuntimeError(f'joint scenario MILP has no usable solution: {result.message}')
    if (not np.all(np.isfinite(x)) or np.any(x < -1e-7) or np.any(x > bounds+1e-7)
            or np.any(np.abs(x[:nc]-np.round(x[:nc])) > 1e-6)
            or np.any(matrix@x < np.asarray(lower)-1e-7) or np.any(matrix@x > np.asarray(upper)+1e-7)):
        raise RuntimeError('joint scenario incumbent violates feasibility')
    used = {r['id'] for k, (_, r, _) in enumerate(flows) if x[nc+k] > 1e-9}
    selected = [c for i, c in enumerate(candidates) if x[i] > .5 and c['id'] in used]
    for predicate, cap in groups:
        if sum((c['n']+4095)//4096*4096 for c in selected if predicate(c)) > cap:
            raise RuntimeError('joint scenario incumbent exceeds exact budget')
    plans = [restore_plan(base+selected, s['targets'], workers, profile, 'resource_lp') for s in scenarios]
    if any(p['strategy'] != 'resource_lp' for p in plans):
        raise RuntimeError('scenario verification LP fell back')
    def finite(value):
        return float(value) if value is not None and math.isfinite(value) else None
    times = [p['estimated_transfer_s'] for p in plans]
    return {'selected': selected, 'objective_s': float(result.fun),
            'rounded_plan_s': aggregate_cost(times, scenarios, objective),
            'scenario_times_s': dict(zip([s['name'] for s in scenarios], times)),
            'objective': objective, 'status': 'optimal' if result.success else 'feasible_limit',
            'mip_gap': finite(getattr(result, 'mip_gap', None)),
            'dual_bound_s': finite(getattr(result, 'mip_dual_bound', None)),
            'solver_message': result.message, 'solve_wall_s': time.monotonic()-started,
            'candidate_count': nc, 'flow_variables': nf,
            'scope': 'finite candidates, mutually exclusive scenarios, approximate service model; not simultaneous restores'}
