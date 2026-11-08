"""Exact three-worker source assignment for the single remote sender workflow."""
from contracts import CHUNK, GiB, PROFILE, target
from fluidckpt.cluster.planning import restore_plan, validate_plan
from fluidckpt.cluster.wire import planning_context

WORKFLOW = 'simple_two_node_single_sender_v1'
RANK_BYTES = 35164859056
SOURCE_NUMA = {0: 4, 1: 7}
WARM_PAIRS = [[0, 2], [1, 2]]


def validate_roles(plan):
    roles = [r for r in plan['processes'] if r['kind'] == 'worker']
    if plan.get('workflow') != WORKFLOW or len(roles) != 3:
        raise ValueError('explicit simple workflow and exactly three workers required')
    expected = [('node27', 4, 4), ('node27', 5, 7), ('node30', 4, 4)]
    if [(r['node'], r['gpu'], r['numa']) for r in roles] != expected:
        raise ValueError('simple physical roles changed; freeze a newly reviewed configuration')
    if plan.get('remote_source_numa_by_rank') != {'0': 4, '1': 7}:
        raise ValueError('both rank-specific remote source NUMAs must be explicit')
    return roles


def source_spec(rank, layout, extent):
    if layout not in ('C0_mixed', 'Hnear', 'Hremote') or rank not in SOURCE_NUMA:
        raise ValueError('unregistered simple layout/rank')
    if extent == 0:
        return rank, 'hbm', SOURCE_NUMA[rank]
    remote = layout == 'Hremote' or layout == 'C0_mixed' and 1 <= extent <= 16
    return (2 if remote else rank), 'dram', SOURCE_NUMA[rank]


def layout_plan(manifests, replicas, workers, endpoints, layout, phase, metrics):
    if len(workers) != 3 or [w['worker'] for w in workers] != [0, 1, 2]:
        raise ValueError('simple worker mesh must be ordered 0,1,2')
    if [m['n'] for m in manifests] != [RANK_BYTES, RANK_BYTES]:
        raise ValueError('complete actual Qwen TP2 capture size changed')
    targets = [target(m, f'{phase}/rank{r}', r, tag=phase, metrics_directory=metrics)
               for r, m in enumerate(manifests)]
    ops = []
    for extent in range((RANK_BYTES + GiB - 1) // GiB):
        for rank, (manifest, live) in enumerate(zip(manifests, targets)):
            off, n = extent * GiB, min(GiB, manifest['n'] - extent * GiB)
            owner, tier, numa = source_spec(rank, layout, extent)
            sources = [r for r in replicas if r['state'] == 'VALID' and r['worker'] == owner
                       and r['tier'] == tier and r['numa'] == numa and r['tp'] == rank
                       and r['identity'] == manifest['identity'] and r['off'] <= off
                       and r['off'] + r['n'] >= off + n]
            if len(sources) != 1:
                raise ValueError(f'exactly one assigned source required: {layout}/{rank}/{extent}: {len(sources)}')
            fragment = restore_plan(sources, [dict(live, off=off, n=n)], workers, PROFILE, 'nearest', quantum=GiB)
            if len(fragment['ops']) != 1:
                raise ValueError('source-assigned extent unexpectedly split')
            ops.append(dict(fragment['ops'][0], op_id=len(ops)))
    plan = dict(targets=targets, ops=ops, executor='serial', chunk_bytes=CHUNK,
                strategy='fixed_source_assignment', estimated_transfer_s=RANK_BYTES / 1e9,
                profile=PROFILE, control_context=planning_context(workers, endpoints),
                workflow=WORKFLOW, scope='Native worker DAG; one sender shares both rank streams; fixed source assignment.')
    validate_plan(plan)
    return plan


def serialized_sender_evidence(execution, plan):
    """Report only source-host intervals; never compare clocks across hosts."""
    remote = {op['op_id'] for op in plan['ops'] if op['src_w'] == 2 and op['kind'] == 'network'}
    if not remote:
        return dict(required=False, passed=True, intervals=[], scope='No remote operations in this layout.')
    rows = [row for row in execution if row['worker'] == 2]
    if len(rows) != 1:
        raise ValueError('single sender execution evidence missing')
    begins, intervals = {}, []
    for event in rows[0].get('op_clock_events', []):
        op_id = event['op_id']
        if op_id not in remote:
            continue
        if event['ev'] == 'cluster_op_begin':
            if op_id in begins:
                raise ValueError('duplicate sender operation start')
            begins[op_id] = event
        elif event['ev'] == 'cluster_op_end':
            start = begins.get(op_id)
            if start is None or start['clock_domain'] != event['clock_domain'] or start['monotonic_ns'] >= event['monotonic_ns']:
                raise ValueError('invalid sender operation interval')
            intervals.append(dict(op_id=op_id, begin_ns=start['monotonic_ns'],
                                  end_ns=event['monotonic_ns'], clock_domain=start['clock_domain']))
    expected_order = [op['op_id'] for op in plan['ops'] if op['op_id'] in remote]
    if [row['op_id'] for row in intervals] != expected_order or len({row['clock_domain'] for row in intervals}) != 1:
        raise ValueError('sender evidence does not cover ordered remote DAG')
    if any(left['end_ns'] > right['begin_ns'] for left, right in zip(intervals, intervals[1:])):
        raise ValueError('single native sender unexpectedly overlaps operations')
    return dict(required=True, passed=True, intervals=intervals,
                scope='Actual native source-worker ordered intervals on its own host clock; no cross-host subtraction.')
