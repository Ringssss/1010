"""Place complete immutable coverage under reduced memory budgets using a MILP."""
import time


def plan_eviction(sources, locations, scenarios, block_bytes=256 << 20):
    import numpy as np
    from scipy.optimize import milp, Bounds, LinearConstraint
    from scipy.sparse import coo_matrix
    start = time.monotonic()
    blocks = [(s, off, min(block_bytes, s['n']-off)) for s in sources
              for off in range(0, s['n'], block_bytes)]
    choices = [(bi, li) for bi, (source, _, _) in enumerate(blocks)
               for li, location in enumerate(locations) if location['tp'] == source['tp']]
    nx, ns = len(choices), len(scenarios)
    rr, cc, vv, lower, upper = [], [], [], [], []

    def add(values, lo=-np.inf, hi=np.inf):
        i = len(lower); lower.append(lo); upper.append(hi)
        for j, value in values.items():
            rr.append(i); cc.append(j); vv.append(value)

    for bi in range(len(blocks)):
        add({k: 1 for k, (b, _) in enumerate(choices) if b == bi}, 1, 1)
    for li, location in enumerate(locations):
        add({k: blocks[bi][2]/1e6 for k, (bi, l) in enumerate(choices) if l == li},
            hi=location['capacity_bytes']/1e6)
    for si, scenario in enumerate(scenarios):
        for rank in sorted({s['tp'] for s in sources}):
            values = {}
            for k, (bi, li) in enumerate(choices):
                source, _, n = blocks[bi]
                if source['tp'] != rank: continue
                location = locations[li]
                rate = (scenario['ssd_GBps'] if location['tier'] == 'ssd' else
                        scenario['local_GBps'] if location['node'] == scenario['node'] else scenario['remote_GBps'])
                values[k] = n/1e6/rate
            add(values | {nx+si: -1}, hi=0)
    cost = np.r_[np.zeros(nx), np.ones(ns)/ns]
    result = milp(cost, integrality=np.r_[np.ones(nx), np.zeros(ns)],
        bounds=Bounds(np.zeros(nx+ns), np.r_[np.ones(nx), np.full(ns, np.inf)]),
        constraints=LinearConstraint(coo_matrix((vv, (rr, cc)), shape=(len(lower), nx+ns)).tocsc(), lower, upper),
        options={'time_limit': 10, 'mip_rel_gap': .001})
    if result.x is None: raise RuntimeError(result.message)
    selected = {}
    for k, (bi, li) in enumerate(choices):
        if result.x[k] > .5: selected[bi] = li
    if len(selected) != len(blocks): raise RuntimeError('MILP did not cover every block')
    extents = []
    for bi, (source, off, n) in enumerate(blocks):
        li = selected[bi]
        location = locations[li]
        if extents and extents[-1]['tp'] == source['tp'] and extents[-1]['location'] == li and extents[-1]['off']+extents[-1]['n'] == off:
            extents[-1]['n'] += n
        else:
            extents.append({'tp': source['tp'], 'identity': source['identity'], 'off': off, 'n': n,
                            'location': li, 'worker': location['worker'], 'tier': location['tier'],
                            **{k: location[k] for k in ['store_endpoint', 'storage_node'] if k in location}})
    usage = {str(li): sum(e['n'] for e in extents if e['location'] == li) for li in range(len(locations))}
    if any(usage[str(li)] > loc['capacity_bytes'] for li, loc in enumerate(locations)):
        raise RuntimeError('MILP exceeds exact byte budget')
    return {'extents': extents, 'usage_bytes': usage, 'locations': locations, 'scenarios': scenarios,
            'predicted_mean_s': float(result.fun)/1000, 'solve_s': time.monotonic()-start,
            'mip_gap': float(result.mip_gap), 'optimal': bool(result.success),
            'scope': 'one-copy block placement, configured serial path estimates, two exclusive future destinations'}
