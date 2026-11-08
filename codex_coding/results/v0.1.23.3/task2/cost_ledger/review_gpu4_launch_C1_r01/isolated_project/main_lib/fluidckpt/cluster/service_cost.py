"""State-conditioned execution estimates for an already constructed serial plan.

This scorer does not change the bandwidth LP objective. Pair activation is tied
to a worker incarnation; never infer readiness from a topology-only catalog.
"""
import math
import statistics

from .path_catalog import topology_id


def pair_key(src, dst):
    return ':'.join(map(str, sorted((src, dst))))


def fit_path(cold, small, large):
    """Fit two warm sizes; reserve different sizes/plans for validation."""
    n0, n1 = small['nbytes'], large['nbytes']
    t0, t1 = small['service_s'], large['service_s']
    if n1 <= n0 or min(t0, t1) <= 0 or t1 <= t0 or cold['nbytes'] != n0:
        raise ValueError('invalid or non-increasing calibration samples')
    slope = (t1-t0)/(n1-n0)
    fixed = max(0., t0-slope*n0)
    # A negative intercept cannot represent negative setup; use aggregate throughput.
    if t0-slope*n0 < 0:
        slope = (t0+t1)/(n0+n1)
    return {'warm_GBps': 1/(slope*1e9), 'operation_s': fixed,
            'first_pair_s': max(0., cold['service_s']-(fixed+slope*cold['nbytes'])),
            'fit_bytes': [n0, n1], 'fit_scope': 'per-op fixed plus effective chunked byte service; chunk overhead folded into slope'}


def fit_model(workers, samples):
    paths = sorted({s['path'] for s in samples})
    costs = {}
    for path in paths:
        by = {s['label']: s for s in samples if s['path'] == path}
        costs[path] = fit_path(by['cold_small'], by['warm_small'], by['warm_large'])
    return {'schema_version': 1, 'topology_id': topology_id(workers), 'executor': 'serial',
            'paths': costs, 'dispatch_s': statistics.median(s['dispatch_s'] for s in samples),
            'scope': 'isolated calibration, explicit pair state, worker-order dependency scheduling; no simultaneous shared-link contention calibration'}


def new_state(workers):
    return {'incarnations': [[w['session'], w['epoch']] for w in workers], 'warm_pairs': []}


def check_state(workers, state):
    if [list(x) for x in state['incarnations']] != [[w['session'], w['epoch']] for w in workers]:
        raise ValueError('communication state belongs to different worker incarnations')


def observe_success(plan, workers, state):
    check_state(workers, state)
    pairs = set(state['warm_pairs'])
    pairs.update(pair_key(o['src_w'], o['dst_w']) for o in plan['ops'] if o['src_w'] != o['dst_w'])
    state['warm_pairs'] = sorted(pairs)


def estimate_execution(plan, workers, model, state):
    check_state(workers, state)
    if plan.get('chunk_bytes', 32 << 20) != model.get('chunk_bytes', 32 << 20):
        raise ValueError('service model chunk size mismatch; recalibrate before scoring')
    if model.get('schema_version') not in (1, 2) or model['topology_id'] != topology_id(workers):
        raise ValueError('service model topology mismatch')
    if plan.get('executor', 'serial') != model['executor'] or model['executor'] != 'serial':
        raise ValueError('service model executor mismatch')
    warm = set(state['warm_pairs']); ready = {w['worker']: 0. for w in workers}; timeline = []
    for op in plan['ops']:
        if op['path'] not in model['paths']:
            raise ValueError('uncalibrated service path: '+op['path'])
        c = model['paths'][op['path']]
        if any(not math.isfinite(c[k]) or c[k] < 0 for k in ('warm_GBps', 'operation_s', 'first_pair_s')) or c['warm_GBps'] == 0:
            raise ValueError('invalid service coefficients')
        src, dst = op['src_w'], op['dst_w']; pair = pair_key(src, dst)
        cold = src != dst and pair not in warm
        setup = c['first_pair_s'] if cold else 0.
        duration = setup+c['operation_s']+op['n']/1e9/c['warm_GBps']
        start = max(ready[src], ready[dst]); end = start+duration
        timeline.append({'op_id': op['op_id'], 'path': op['path'], 'start_s': start, 'end_s': end,
                         'service_s': duration, 'first_pair_s': setup,
                         'source_wait_s': start-ready[src], 'target_wait_s': start-ready[dst]})
        ready[src] = ready[dst] = end
        if src != dst: warm.add(pair)
    request_s = model.get('request_s', 0.)
    if any(not math.isfinite(v) or v < 0 for v in (request_s, model['dispatch_s'])):
        raise ValueError('invalid request overhead')
    return {'estimated_execute_s': max(ready.values())+model['dispatch_s']+request_s,
            'estimated_worker_s': max(ready.values())+request_s, 'request_s': request_s,
            'dispatch_s': model['dispatch_s'], 'timeline': timeline,
            'scope': model['scope'], 'strategy': 'stateful_serial_service',
            'bandwidth_plan_estimate_s': plan['estimated_transfer_s']}


def fit_request_model(workers, samples):
    """Separate one request cost from per-operation cost with equal-byte batching.

    Each path uses warm one-op small/large and four-op equal-small-byte cases.
    Negative unconstrained terms are reported and clamped, never hidden.
    """
    costs, requests, diagnostics = {}, [], {}
    for path in sorted({s['path'] for s in samples}):
        by = {s['label']: s for s in samples if s['path'] == path}
        small, large, batch = (by[k] for k in ('warm_small', 'warm_large', 'warm_batch'))
        slope = (large['target_s']-small['target_s'])/(large['nbytes']-small['nbytes'])
        if not math.isfinite(slope) or slope <= 0 or batch['nbytes'] != small['nbytes']:
            raise ValueError('invalid request calibration slope or equal-byte batching')
        raw_op = (batch['target_s']-small['target_s'])/(batch['operations']-1)
        op = max(0., raw_op)
        raw_request = small['target_s']-op-slope*small['nbytes']
        requests.append(max(0., raw_request))
        cold = by['cold_small']
        costs[path] = {'warm_GBps': 1/(slope*1e9), 'operation_s': op,
                      'first_pair_s': max(0., cold['target_s']-small['target_s']),
                      'fit_bytes': [small['nbytes'], large['nbytes']]}
        diagnostics[path] = {'raw_operation_s': raw_op, 'raw_request_s': raw_request,
                             'clamped': raw_op < 0 or raw_request < 0}
    return {'schema_version': 2, 'topology_id': topology_id(workers), 'executor': 'serial', 'paths': costs,
            'request_s': statistics.median(requests),
            'dispatch_s': statistics.median(s['dispatch_s'] for s in samples if s['label'] != 'cold_small'),
            'fit_diagnostics': diagnostics,
            'scope': 'target-side service; equal-byte operation-count calibration; one shared request overhead; serial dependencies, no contention guarantee'}
