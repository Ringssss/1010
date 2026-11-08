"""Build exact source-assigned extents through native planning and validate overlap evidence."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster.planning import restore_plan, validate_plan
from fluidckpt.cluster.wire import planning_context

GiB = 1 << 30
CHUNK = 32 << 20
PROFILE = {'rates': {'local_dram': 1., 'local_hbm': 1., 'local_ssd': .5, 'peer': 1., 'network': 1.},
           'gpu_ingress_GBps': 1., 'gpu_egress_GBps': 1., 'dram_GBps': 1., 'network_node_GBps': 1.,
           'serial_target_execution': True}


def target(manifest, key, worker, mode='live', **fields):
    return dict(key=key, id=key, mode=mode, worker=worker, tier='hbm', identity=manifest['identity'],
                tp=manifest['tp'], off=0, n=manifest['n'], meta=manifest['meta'], index=manifest['index'], **fields)


def layout_plan(manifests, replicas, workers, endpoints, layout, phase, metrics):
    if layout not in ('C0_mixed', 'Hnear', 'Hremote'):
        raise ValueError('unregistered full-model layout')
    targets = [target(m, f'{phase}/rank{r}', r, tag=phase, metrics_directory=metrics) for r, m in enumerate(manifests)]
    ops = []
    count = max((m['n'] + GiB - 1) // GiB for m in manifests)
    for extent in range(count):
        for rank, (m, live) in enumerate(zip(manifests, targets)):
            off = extent * GiB
            if off >= m['n']:
                continue
            n = min(GiB, m['n'] - off)
            tier, owner = ('hbm', rank) if extent == 0 else ('dram', rank + 2 if layout == 'Hremote' or layout == 'C0_mixed' and 1 <= extent <= 16 else rank)
            matching = [r for r in replicas if r['state'] == 'VALID' and r['worker'] == owner and r['tier'] == tier
                        and r['identity'] == m['identity'] and r['tp'] == rank and r['off'] <= off and r['off'] + r['n'] >= off + n]
            if len(matching) != 1:
                raise ValueError(f'expected exactly one trusted assigned source for {layout}/{rank}/{extent}: {len(matching)}')
            partial = dict(live, off=off, n=n)
            fragment = restore_plan(matching, [partial], workers, PROFILE, 'nearest', quantum=GiB)
            if len(fragment['ops']) != 1:
                raise ValueError('source-assigned extent unexpectedly split')
            ops.append(dict(fragment['ops'][0], op_id=len(ops)))
    plan = {'targets': targets, 'ops': ops, 'executor': 'serial', 'chunk_bytes': CHUNK,
            'strategy': 'fixed_source_assignment', 'estimated_transfer_s': max(sum(o['n'] for o in ops if o['dst_w'] == w) for w in (0, 1)) / 1e9,
            'profile': PROFILE, 'control_context': planning_context(workers, endpoints),
            'scope': 'native path construction; explicit fixed extent source assignment, no placement optimization claim'}
    validate_plan(plan)
    return plan


def paired_overlap(execution, plan):
    intervals = []
    remote_ids = {o['op_id']: o for o in plan['ops'] if o['kind'] == 'network' and o['dst_w'] in (0, 1)}
    for worker in execution:
        if worker['worker'] not in (0, 1):
            continue
        begins = {}
        for event in worker.get('op_clock_events', []):
            op = remote_ids.get(event['op_id'])
            if not op:
                continue
            if event['ev'] == 'cluster_op_begin':
                begins[event['op_id']] = event
            elif event['ev'] == 'cluster_op_end' and event['op_id'] in begins:
                start = begins[event['op_id']]
                if start['clock_domain'] != event['clock_domain'] or event['monotonic_ns'] <= start['monotonic_ns']:
                    raise ValueError('invalid same-target-host operation interval')
                intervals.append({'op_id': op['op_id'], 'target_worker': worker['worker'], 'path': op['path'],
                                  'begin_ns': start['monotonic_ns'], 'end_ns': event['monotonic_ns'],
                                  'clock_domain': event['clock_domain']})
    if not intervals:
        return {'confirmed': False, 'overlap_ns': 0, 'intervals': []}
    if len({i['clock_domain'] for i in intervals}) != 1:
        raise ValueError('never subtract remote host clocks')
    overlap = sum(max(0, min(a['end_ns'], b['end_ns']) - max(a['begin_ns'], b['begin_ns']))
                  for a in intervals if a['target_worker'] == 0 for b in intervals if b['target_worker'] == 1)
    return {'confirmed': overlap > 0, 'overlap_ns': overlap, 'intervals': intervals,
            'scope': 'actual operation intervals on common target host monotonic clock'}


def resource_snapshot(workers, roles, budgets, clock, allocation_by_worker=None):
    nodes, failures, planned_by_node = {}, [], {}
    for worker, role in zip(workers, roles):
        planned_by_node[role['node']] = planned_by_node.get(role['node'], 0) + (allocation_by_worker or {}).get(worker['worker'], 0)
    if len(workers) != len(roles):
        failures.append({'reason': 'worker_count_mismatch', 'actual': len(workers), 'expected': len(roles)})
    for worker, role in zip(workers, roles):
        runtime, budget = worker['runtime_identity'], budgets[role['node']]
        current, hard = runtime['cgroup_memory_current_bytes'], runtime['cgroup_memory_max_bytes']
        upper = worker['hbm_total_bytes'] - worker['headroom_bytes']['hbm']
        planned = (allocation_by_worker or {}).get(worker['worker'], 0)
        card = {'worker': worker['worker'], 'node': role['node'], 'gpu': worker['gpu'], 'bdf': runtime['actual_bdf'],
                'expected_role': {k: role[k] for k in ('node', 'gpu', 'numa', 'bdf', 'boot_id')},
                'physical_pci_numa': runtime['pci_numa_node'], 'host_source_numa': worker['numa'],
                'hbm_total_bytes': worker['hbm_total_bytes'], 'hbm_free_bytes': worker['headroom_bytes']['hbm'],
                'hbm_upper_bytes': upper, 'hbm_cap_bytes': budget['per_selected_gpu_hbm_cap_bytes'],
                'hbm_margin_bytes': 128 << 20, 'hbm_allocated_bytes': worker['hbm_allocated_bytes'],
                'hbm_reserved_bytes': worker['hbm_reserved_bytes'], 'catalog_used_bytes': worker['used_bytes'],
                'chosen_numa_reclaim_estimate_bytes': worker['headroom_bytes']['dram'], 'planned_new_allocation_bytes': planned}
        sample = nodes.setdefault(role['node'], {'dram_current_bytes': current, 'dram_cap_bytes': budget['task_dram_cap_bytes'],
                    'container_hard_cap_bytes': hard, 'expected_container_hard_cap_bytes': budget['container_hard_cap_bytes'],
                    'host_available_bytes': runtime['host_memory_available_bytes'],
                    'host_available_floor_bytes': budget['ongoing_host_available_floor_bytes'],
                    'planned_new_allocation_bytes': planned_by_node[role['node']], 'cgroup_memory_stat': runtime['cgroup_memory_stat'], 'cards': []})
        sample['cards'].append(card)
        sample['dram_current_bytes'] = max(sample['dram_current_bytes'], current)
        checks = [
            ('worker_assignment_changed', all(worker[k] == role[k] for k in ('node', 'gpu', 'numa'))),
            ('worker_boot_or_bdf_changed', runtime['boot_id'] == role['boot_id'] and runtime['actual_bdf'] == role['bdf']),
            ('cgroup_hard_cap_changed', hard == budget['container_hard_cap_bytes']),
            ('node_operational_dram_cap', current + (256 << 20) <= budget['task_dram_cap_bytes']),
            ('selected_card_hbm_cap', upper + (128 << 20) <= budget['per_selected_gpu_hbm_cap_bytes']),
            ('chosen_numa_planned_allocation', not planned or worker['headroom_bytes']['dram'] >= planned + 4 * GiB),
            ('host_available_floor_and_new_allocation', runtime['host_memory_available_bytes'] >= budget['ongoing_host_available_floor_bytes'] + planned_by_node[role['node']]),
        ]
        failures.extend(dict(card, reason=reason, cgroup_memory_current_bytes=current,
                             task_dram_cap_bytes=budget['task_dram_cap_bytes']) for reason, passed in checks if not passed)
    for node, sample in nodes.items():
        sample['aggregate_hbm_upper_bytes'] = sum(c['hbm_upper_bytes'] for c in sample['cards'])
        sample['aggregate_hbm_cap_bytes'] = budgets[node]['task_hbm_cap_bytes']
        sample['aggregate_hbm_margin_bytes'] = len(sample['cards']) * (128 << 20)
        if sample['aggregate_hbm_upper_bytes'] + sample['aggregate_hbm_margin_bytes'] > sample['aggregate_hbm_cap_bytes']:
            failures.append({'reason': 'node_aggregate_hbm_cap', 'node': node,
                             'upper_bytes': sample['aggregate_hbm_upper_bytes'], 'cap_bytes': sample['aggregate_hbm_cap_bytes'],
                             'margin_bytes': sample['aggregate_hbm_margin_bytes']})
    return {'passed': not failures, 'nodes': nodes, 'workers': workers, 'failures': failures, 'clock_domain': clock,
            'peak_verified': False, 'accounting': 'full actual worker snapshot; shared cgroup once/node; selected-card upper includes all background/runtime/IPC/keeper'}


def physical_park_snapshot(manifests, before, after, events):
    rows = []
    for rank, manifest in enumerate(manifests):
        lifecycle = events[rank]
        allocated_drop = lifecycle['release_begin']['allocated_bytes'] - lifecycle['release_end']['allocated_bytes']
        free_gain = after[rank]['headroom_bytes']['hbm'] - before[rank]['headroom_bytes']['hbm']
        passed = 10 * allocated_drop >= 9 * manifest['n'] and 10 * free_gain >= 9 * manifest['n']
        rows.append({'rank': rank, 'weight_bytes': manifest['n'], 'engine_allocated_drop_bytes': allocated_drop,
                     'physical_free_gain_bytes': free_gain, 'release_events': lifecycle, 'passed': passed})
    return {'passed': all(r['passed'] for r in rows), 'threshold': 'both engine allocated drop and physical selected-card free gain >=90% of complete rank weight bytes',
            'rows': rows, 'before_workers': before, 'after_workers': after, 'background_drift_subtracted': False}
