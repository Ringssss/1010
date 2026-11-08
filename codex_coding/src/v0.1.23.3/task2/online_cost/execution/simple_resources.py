"""CPU-only resource contracts for a single remote sender coexisting with other jobs."""
import re
from pathlib import Path

GiB = 1 << 30


def numa_plan_checks(metadata, planned):
    return [dict(numa=int(numa), planned_bytes=n, margin_bytes=4*GiB,
                 available_estimate_bytes=metadata.get(str(numa), {}).get('free_plus_file_minus_shmem_bytes'),
                 passed=metadata.get(str(numa), {}).get('free_plus_file_minus_shmem_bytes', -1) is not None
                 and metadata.get(str(numa), {}).get('free_plus_file_minus_shmem_bytes', -1) >= n + 4*GiB)
            for numa, n in planned.items() if n]


def own_fdinfo_vram(root=Path('/proc/self/fdinfo')):
    """Read this process's DRM fdinfo only; missing driver counters remain unknown."""
    rows, errors, seen = [], [], set()
    files = sorted(root.iterdir(), key=lambda p:p.name)
    if len(files) > 128:
        return dict(available=False, vram_bytes=None, errors=['own fdinfo count exceeds128'], exact_task_attribution=False)
    for path in files:
        try:
            with path.open('rb') as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                errors.append('own fdinfo byte bound exceeded'); continue
            values = dict(line.split(':', 1) for line in raw.decode().splitlines() if ':' in line)
            if 'drm-client-id' not in values or 'drm-memory-vram' not in values:
                continue
            key = (values.get('drm-pdev', '').strip(), values['drm-client-id'].strip())
            if key in seen:
                continue
            seen.add(key)
            match = re.fullmatch(r'\s*(\d+)\s*(KiB|kB|MiB|B|bytes)?\s*', values['drm-memory-vram'])
            if not match:
                errors.append('unrecognized own DRM VRAM units'); continue
            factor = {'KiB':1024,'kB':1024,'MiB':1<<20,'B':1,'bytes':1,None:1}[match[2]]
            rows.append(dict(pci_device=key[0], client_id=key[1], vram_bytes=int(match[1])*factor))
        except (OSError, UnicodeError) as exc:
            errors.append(type(exc).__name__)
    return dict(available=bool(rows), vram_bytes=sum(r['vram_bytes'] for r in rows) if rows else None,
                clients=rows, errors=errors, exact_task_attribution=False,
                scope='own process DRM clients only; driver/RCCL completeness and shared-object attribution unknown')


def resource_snapshot(workers, roles, budgets, clock, allocation_by_worker=None, allocation_by_numa=None):
    allocation_by_worker = allocation_by_worker or {}
    allocation_by_numa = allocation_by_numa or {}
    nodes, failures, planned_by_node = {}, [], {}
    if len(workers) != len(roles):
        failures.append(dict(reason='worker_count_mismatch', actual=len(workers), expected=len(roles)))
    for worker, role in zip(workers, roles):
        planned_by_node[role['node']] = planned_by_node.get(role['node'],0) + allocation_by_worker.get(worker['worker'],0)
    for worker, role in zip(workers, roles):
        runtime, budget = worker['runtime_identity'], budgets[role['node']]
        free, total = worker['headroom_bytes']['hbm'], worker['hbm_total_bytes']
        current, hard = runtime['cgroup_memory_current_bytes'], runtime['cgroup_memory_max_bytes']
        helper = role['node'] == 'node30'
        card = dict(worker=worker['worker'], node=role['node'], gpu=worker['gpu'], bdf=runtime['actual_bdf'],
                    hbm_total_bytes=total, hbm_free_bytes=free, whole_card_used_bytes=total-free,
                    hbm_allocated_bytes=worker['hbm_allocated_bytes'], hbm_reserved_bytes=worker['hbm_reserved_bytes'],
                    own_hbm_ledger=worker.get('own_hbm_ledger'), own_process_gpu_memory=worker.get('own_process_gpu_memory'),
                    catalog_used_bytes=worker['used_bytes'], expected_role=role)
        sample=nodes.setdefault(role['node'],dict(dram_current_bytes=current,dram_cap_bytes=budget['task_dram_cap_bytes'],
                    container_hard_cap_bytes=hard,host_available_bytes=runtime['host_memory_available_bytes'],
                    planned_new_allocation_bytes=planned_by_node[role['node']],cards=[]))
        sample['cards'].append(card);sample['dram_current_bytes']=max(sample['dram_current_bytes'],current)
        checks=[('worker_assignment_changed',all(worker[k]==role[k] for k in ('node','gpu','numa'))),
                ('worker_boot_or_bdf_changed',runtime['boot_id']==role['boot_id'] and runtime['actual_bdf']==role['bdf']),
                ('physical_pci_numa_changed',runtime['pci_numa_node']==role['numa']),
                ('cgroup_hard_cap_changed',hard==budget['container_hard_cap_bytes']),
                ('node_operational_dram_cap',current+(256<<20)+planned_by_node[role['node']]<=budget['task_dram_cap_bytes']),
                ('host_available_floor_and_new_allocation',runtime['host_memory_available_bytes']>=budget['ongoing_host_available_floor_bytes']+planned_by_node[role['node']])]
        if helper:
            ledger=worker.get('own_hbm_ledger',{})
            checks.extend([('helper_live_whole_card_free_floor',free>=budget['ongoing_gpu_free_floor_bytes']),
                           ('helper_torch_reserved_budget',worker['hbm_reserved_bytes']<=budget['task_hbm_cap_bytes']),
                           ('helper_explicit_known_allocation_budget',ledger.get('known_unique_tensor_bytes',-1)>=0 and ledger['known_unique_tensor_bytes']<=budget['task_hbm_cap_bytes'])])
        else:
            checks.append(('target_selected_card_hbm_cap',total-free+(128<<20)<=budget['per_selected_gpu_hbm_cap_bytes']))
        split=allocation_by_numa.get(worker['worker'],allocation_by_numa.get(str(worker['worker']),{}))
        split=dict(split)
        split.update({numa:n for key,n in allocation_by_numa.items() if isinstance(key,tuple) and key[0]==worker['worker'] for numa in [key[1]]})
        planned=allocation_by_worker.get(worker['worker'],0)
        if planned and not split:
            if helper:
                checks.append(('helper_explicit_per_numa_plan_required',False))
            else:
                split={worker['numa']:planned}
        if split:
            checks.append(('per_numa_plan_total_matches_worker',sum(split.values())==planned))
            headroom=worker.get('numa_headroom_bytes',{str(worker['numa']):worker['headroom_bytes']['dram']})
            card['planned_new_allocation_by_numa']=split
            card['numa_headroom_bytes']=headroom
            checks.extend(('chosen_numa_planned_allocation_'+str(numa),headroom.get(str(numa),-1)>=n+4*GiB) for numa,n in split.items() if n)
        failures.extend(dict(card,reason=reason) for reason,passed in checks if not passed)
    return dict(passed=not failures,nodes=nodes,workers=workers,failures=failures,clock_domain=clock,peak_verified=False,
                task_hbm_attribution_complete=False,task_hbm_actual_bytes=None,
                accounting='target whole-card upper48GiB/card; helper planned additional2GiB, Torch reserved and explicit allocations bounded; helper whole-card free guard includes foreign jobs; RCCL/driver exact total unknown')
