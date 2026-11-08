"""S1-S5 source contracts; applicability is separate from measured acceptance."""
import copy
import json
import math
from pathlib import Path

from fluidckpt.cluster.path_catalog import check_profile_evidence, topology_id
from fluidckpt.cluster.planning import covers, extent, path_for, restore_plan

GIB = 1 << 30
ROOT = Path(__file__).resolve().parents[3]
IPC_DRIVER = 'codex_coding/src/v0.1.23.3/task2/paper_e1_e8/e1/run_ipc_gate.py'
SCENARIOS = {
    'S1': {'name': '目标HBM完整兼容权重', 'roles': ['ipc'], 'full_host_required': False},
    'S2': {'name': '完整host保活，无active源', 'roles': ['base'], 'full_host_required': True},
    'S3': {'name': 'active逻辑实例TP对应复制，可host后备', 'roles': ['base', 'active'], 'full_host_required': False},
    'S4': {'name': '完整DRAM底本与分散8GiB额外HBM预算', 'roles': ['base', 'extra'], 'full_host_required': True},
    'S5': {'name': '共享资源证据门槛', 'roles': ['base', 'extra', 'active', 'ipc'], 'full_host_required': False},
}


def scenario_contract(scenario_id):
    return dict(copy.deepcopy(SCENARIOS[scenario_id]), scenario_id=scenario_id,
                experiment_namespace='e1-e8', development_stage='v0.1.23.3/task2')


def method_catalog(lock_path=None):
    lock = json.loads(Path(lock_path or ROOT/'lib_cite/baselines.lock.json').read_text())
    upstream = {r['name']: {k: r[k] for k in ('name', 'commit', 'source_url', 'path', 'status',
                                            'adaptation_status', 'reproduction_status')}
                for r in lock['repositories']}
    methods = {}
    def add(key, identity, implementation, reference=None, **extra):
        methods[key] = dict(method_id=key, identity=identity, implementation=implementation,
                            measurement_status='not_measured', **extra)
        if reference:
            methods[key]['upstream'] = copy.deepcopy(upstream[reference])
    for name, key in [('SGLang', 'sglang_original'), ('Janus', 'janus_original'),
                      ('BlitzScale', 'blitzscale_original'), ('ServerlessLLM', 'serverlessllm_original')]:
        add(key, 'original_system', 'pending', name, reason='官方源码已取得；本项目原版系统尚未复现')
    add('sglang_ipc_hip', 'hip_adaptation', 'implemented', 'SGLang', driver=IPC_DRIVER)
    add('janus_host', 'mechanism', 'implemented', 'Janus', scope='目标主机完整host权重，本地NUMA/H2D共同执行器近似')
    add('janus_d2d', 'mechanism', 'implemented', 'Janus', scope='最高EPDT单逻辑active源，TP对应；完整host后备')
    add('fastest', 'mechanism', 'implemented', scope='已有restore_plan fastest；共享合法IPC机会')
    add('resource_lp', 'mechanism', 'implemented', scope='已有restore_plan resource_lp；不接受静默求解回退')
    add('blitzscale_distribution', 'mechanism', 'unsupported', 'BlitzScale',
        reason='尚未映射参数池/拓扑分发及提前服务语义；强greedy不能命名为BlitzScale')
    add('serverlessllm_locality', 'mechanism', 'unsupported', 'ServerlessLLM',
        reason='固定目标子问题尚未映射其加载与选址能力；不以nearest代替原方法')
    return methods


def _allocated(row):
    value = row.get('allocated_bytes', (row['n']+4095)//4096*4096)
    if type(value) is not int or value < row['n']:
        raise ValueError('allocated_bytes must cover logical bytes')
    return value


def _artifact_path(path):
    value = Path(path)
    return value if value.is_absolute() else ROOT/value


def _ledger(case):
    allocations = {}
    for row in case['replicas']:
        role = row.get('source_role')
        if role not in ('base', 'extra', 'active', 'ipc'):
            raise ValueError('every replica requires an explicit source_role')
        if row['tier'] != ('dram' if role == 'base' else 'hbm'):
            raise ValueError('source_role and tier disagree')
        aid = row.get('allocation_id', row['id'])
        entry = {'allocation_id': aid, 'role': role, 'tier': row['tier'],
                 'worker': row['worker'], 'allocated_bytes': _allocated(row)}
        if aid in allocations and allocations[aid] != entry:
            raise ValueError('aliases disagree on physical allocation metadata')
        allocations[aid] = entry
    values = list(allocations.values())
    total = lambda role: sum(r['allocated_bytes'] for r in values if r['role'] == role)
    location = {}
    for row in values:
        if row['role'] in ('extra', 'ipc'):
            key = f"{row['worker']}:hbm"
            location[key] = location.get(key, 0)+row['allocated_bytes']
    budget = case['budget']
    for key in ('base_dram_bytes', 'extra_hbm_bytes', 'target_allocation_bytes', 'staging_bytes'):
        if type(budget.get(key)) is not int or budget[key] < 0:
            raise ValueError(f'invalid budget field {key}')
    for key, cap in budget.get('location_extra_hbm_bytes', {}).items():
        if type(cap) is not int or cap < 0 or not key.endswith(':hbm'):
            raise ValueError('invalid location budget')
    active_class = {}
    for instance in case.get('active_instances', []):
        evidence = instance.get('survival_evidence', {})
        if evidence.get('external_existing') is not None and type(evidence['external_existing']) is not bool:
            raise ValueError('external_existing must be boolean or unknown')
        for field in ('additional_lifetime_s', 'additional_hbm_byte_seconds'):
            value = evidence.get(field)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError('invalid active lifetime cost')
        for rid in instance.get('replica_ids', []):
            active_class[rid] = evidence.get('external_existing')
    active_allocations = {}
    for row in case['replicas']:
        if row['source_role'] == 'active':
            aid = row.get('allocation_id', row['id'])
            classification = active_class.get(row['id'])
            if aid in active_allocations and active_allocations[aid] is not classification:
                raise ValueError('active aliases disagree on incremental cost classification')
            active_allocations[aid] = classification
    active_total = lambda classification: sum(allocations[aid]['allocated_bytes']
        for aid, value in active_allocations.items() if value is classification)
    return {'allocations': values, 'full_base_dram_bytes': total('base'),
            'extra_cache_hbm_bytes': total('extra'), 'ipc_owner_hbm_bytes': total('ipc'),
            'active_total_hbm_bytes': total('active'),
            'existing_active_hbm_bytes': active_total(True),
            'incremental_active_hbm_bytes': active_total(False),
            'active_cost_unclassified_hbm_bytes': active_total(None),
            'extra_location_bytes': location, 'target_allocation_bytes': budget['target_allocation_bytes'],
            'staging_bytes': budget['staging_bytes'],
            'total_declared_physical_bytes': sum(r['allocated_bytes'] for r in values)
                +budget['target_allocation_bytes']+budget['staging_bytes'],
            'active_source_lifetime': [dict(logical_id=i['logical_id'],
                **{k: i.get('survival_evidence', {}).get(k) for k in (
                    'external_existing', 'additional_lifetime_s', 'additional_hbm_byte_seconds',
                    'interference_evidence_path')}) for i in case.get('active_instances', [])],
            'physical_peak_verified': False}


def _complete(rows, targets):
    return all(covers(rows, t['identity'], t['tp'], *extent(t)) for t in targets)


def _host_sources(rows, targets, workers, profile, rejections):
    selected = []
    for target in targets:
        groups = {}
        for row in rows:
            if row['source_role'] == 'base' and row['tp'] == target['tp'] and row['identity'] == target['identity']:
                if workers[row['worker']]['node'] != workers[target['worker']]['node']:
                    rejections[row['id']] = 'remote_host_is_not_Janus_warm_cache'
                    continue
                groups.setdefault(row['worker'], []).append(row)
        complete = [group for group in groups.values() if _complete(group, [target])]
        if not complete:
            return []
        # Keep one complete host rank source; do not reconstruct Janus from arbitrary pieces.
        group = min(complete, key=lambda rs: (-min(path_for(r, target, workers, profile)['GBps']
                                                  for r in rs), rs[0]['worker']))
        selected.extend(group)
    return selected


def _active_groups(case, admitted, rejections, exclude_target=False):
    groups = []
    by_id = {r['id']: r for r in admitted if r['source_role'] == 'active'}
    for instance in case.get('active_instances', []):
        logical_id = instance['logical_id']
        evidence = instance.get('survival_evidence', {})
        epdt = instance.get('epdt')
        ids = instance.get('replica_ids', [])
        rows = [by_id[rid] for rid in ids if rid in by_id]
        reason = None
        if type(epdt) not in (int, float) or not math.isfinite(epdt):
            reason = 'missing_finite_epdt'
        elif (not evidence.get('path') or not _artifact_path(evidence['path']).is_file()
              or evidence.get('alive_before') is not True
              or evidence.get('protected_during_copy') is not True):
            reason = 'active_survival_or_copy_protection_unproven'
        elif len(set(ids)) != len(ids) or len(rows) != len(ids) or not _complete(rows, case['targets']):
            reason = 'logical_instance_incomplete'
        elif any(len({r['worker'] for r in rows if r['tp'] == t['tp']}) != 1 for t in case['targets']):
            reason = 'tp_rank_has_multiple_source_workers'
        elif exclude_target and (logical_id == case.get('target_logical_id') or any(
                row['worker'] in {t['worker'] for t in case['targets']} for row in rows)):
            reason = 'Janus_D2D_source_is_the_target'
        if reason:
            rejections['active:'+logical_id] = reason
        else:
            groups.append((instance, rows))
    return groups


def _shared_gate(case):
    evidence = case.get('shared_resource_evidence', {})
    if evidence.get('measured') is not True or not evidence.get('evidence_path'):
        return 'shared_resource_measurement_missing'
    try:
        artifact = json.loads(_artifact_path(evidence['evidence_path']).read_text())
    except (OSError, ValueError):
        return 'shared_resource_artifact_unreadable'
    if (artifact.get('measurement_kind') != 'shared_resource_contention'
            or artifact.get('source') != 'runtime' or artifact.get('mock') is True
            or artifact.get('topology_id') != topology_id(case['workers'])
            or artifact.get('executor') != case.get('executor', 'serial')):
        return 'shared_resource_artifact_context_mismatch'
    samples = artifact.get('samples', [])
    wm = {w['worker']: w for w in case['workers']}
    shared_paths = {}
    for row in case['replicas']:
        if row['state'] != 'VALID' or row['worker'] not in wm:
            continue
        for target in case['targets']:
            if row['tp'] != target['tp'] or row['identity'] != target['identity']:
                continue
            path = path_for(row, target, wm, case['profile'])
            if path:
                for resource in path['resources']:
                    shared_paths.setdefault(resource, set()).add(path['path'])
    for sample in samples:
        if (sample.get('resource') in evidence.get('resources', [])
                and len(shared_paths.get(sample['resource'], [])) >= 2
                and type(sample.get('bytes')) is int and sample['bytes'] > 0
                and type(sample.get('elapsed_s')) in (int, float)
                and math.isfinite(sample['elapsed_s']) and sample['elapsed_s'] > 0
                and type(sample.get('concurrent_flows')) is int and sample['concurrent_flows'] >= 2):
            return None
    return 'shared_resource_concurrent_sample_missing'


def assess_case(case, method_id, lock_path=None):
    """Return source eligibility and missing gates without claiming performance."""
    method = method_catalog(lock_path)[method_id]
    scenario = scenario_contract(case['scenario_id'])
    result = {'schema_version': 1, 'scenario_id': case['scenario_id'], 'method': method,
              'method_id': method_id, 'identity': method['identity'], 'status': 'pending',
              'measurement_status': 'not_measured', 'source_ids': [], 'rejections': {},
              'driver': None, 'plan': None, 'reasons': [],
              'fairness': {'metric': 'same-host max(weights_ready)-min(load_begin)',
                  'cross_host_metric': 'per-host windows plus coordinator all-ready; no cross-host monotonic subtraction',
                  'format_contract': copy.deepcopy(case.get('format_contract')),
                  'online_planning_and_prepare_reported': True, 'verification_position': 'after_weights_ready',
                  'build_cost_reported_separately': True, 'opportunity_scope': 'same legal sources and budgets'}}
    def finish(status, reason=None):
        result['status'] = status
        if reason:
            result['reasons'].append(reason)
        return result
    if method['implementation'] != 'implemented':
        return finish(method['implementation'], method['reason'])
    fmt = case.get('format_contract', {})
    if any(fmt.get(k) is None for k in ('model_version', 'dtype', 'quantization', 'tp', 'processed_snapshot_id')):
        return finish('pending', 'format_contract_incomplete')
    targets = case['targets']
    if type(fmt['tp']) is not int or fmt['tp'] <= 0 or {t['tp'] for t in targets} != set(range(fmt['tp'])):
        raise ValueError('targets must contain all fixed TP ranks')
    if len({t['tp'] for t in targets}) != len(targets) or any(t.get('tier', 'hbm') != 'hbm' for t in targets):
        raise ValueError('one HBM target per TP rank required')
    if len({r['id'] for r in case['replicas']}) != len(case['replicas']):
        raise ValueError('duplicate replica id')
    if len({w['worker'] for w in case['workers']}) != len(case['workers']):
        raise ValueError('duplicate worker id')
    ledger = result['resource_ledger'] = _ledger(case)
    budget = case['budget']
    if (ledger['full_base_dram_bytes'] > budget['base_dram_bytes']
            or ledger['extra_cache_hbm_bytes']+ledger['ipc_owner_hbm_bytes'] > budget['extra_hbm_bytes']
            or any(n > budget.get('location_extra_hbm_bytes', {}).get(loc, 0)
                   for loc, n in ledger['extra_location_bytes'].items())):
        return finish('infeasible', 'physical_allocation_budget_exceeded')
    if case['scenario_id'] == 'S4':
        limits = [n for n in budget.get('location_extra_hbm_bytes', {}).values() if n > 0]
        if budget['extra_hbm_bytes'] != 8*GIB or len(limits) < 2 or max(limits) >= 8*GIB:
            return finish('infeasible', 'S4_requires_distributed_8GiB_budget')
    wm = {w['worker']: w for w in case['workers']}
    admitted = []
    for r in case['replicas']:
        reason = ('invalid_state' if r['state'] != 'VALID' else
                  'role_not_allowed_in_scenario' if r['source_role'] not in scenario['roles'] else
                  'unknown_worker' if r['worker'] not in wm else
                  'format_mismatch' if r.get('format_contract', fmt) != fmt else
                  'identity_or_tp_mismatch' if not any(r['identity'] == t['identity'] and r['tp'] == t['tp'] for t in targets) else None)
        if reason:
            result['rejections'][r['id']] = reason
        else:
            admitted.append(r)
    base = [r for r in admitted if r['source_role'] == 'base']
    if scenario['full_host_required'] and not _complete(base, targets):
        return finish('infeasible', 'complete_host_base_missing')
    groups = _active_groups(case, admitted, result['rejections'], exclude_target=method_id == 'janus_d2d')
    active_ids = {r['id'] for _, rows in groups for r in rows}
    for r in admitted:
        if r['source_role'] == 'active' and r['id'] not in active_ids:
            result['rejections'][r['id']] = 'not_in_a_live_complete_logical_instance'
    admitted = [r for r in admitted if r['source_role'] != 'active' or r['id'] in active_ids]
    result['active_source_evidence'] = [dict(logical_id=i['logical_id'],
        survival_status='verified_after_copy' if i['survival_evidence'].get('alive_after') is True else 'post_copy_pending',
        evidence=copy.deepcopy(i['survival_evidence'])) for i, _ in groups]
    if case['scenario_id'] == 'S5':
        gate = _shared_gate(case)
        if gate:
            return finish('pending', gate)
    ipc_rows = [r for r in admitted if r['tier'] == 'hbm' and all(
        r['worker'] == t['worker'] for t in targets if r['tp'] == t['tp'])]
    ipc_opportunity = (method_id in ('fastest', 'resource_lp') and _complete(ipc_rows, targets)
                       and case.get('ipc_evidence', {}).get('compatible') is True)
    if case['scenario_id'] == 'S1' or method_id == 'sglang_ipc_hip' or ipc_opportunity:
        if method_id not in ('sglang_ipc_hip', 'fastest', 'resource_lp'):
            return finish('unsupported', 'this_mechanism_has_no_IPC_operation')
        if not _complete(ipc_rows, targets):
            return finish('infeasible', 'complete_target_hbm_missing')
        evidence = case.get('ipc_evidence', {})
        if (evidence.get('compatible') is not True or evidence.get('owner_alive') is not True
                or not evidence.get('evidence_path') or not _artifact_path(evidence['evidence_path']).is_file()
                or evidence.get('driver') != IPC_DRIVER):
            return finish('pending', 'dedicated_ipc_driver_and_compatibility_evidence_required')
        result.update(driver='ipc', driver_path=IPC_DRIVER, source_ids=[r['id'] for r in ipc_rows])
        return finish('applicable')
    try:
        check_profile_evidence(case['workers'], case['profile'])
    except ValueError as exc:
        return finish('pending', str(exc))
    executable = []
    for r in admitted:
        if any(r['tp'] == t['tp'] and r['identity'] == t['identity']
               and path_for(r, t, wm, case['profile']) for t in targets):
            executable.append(r)
        else:
            result['rejections'][r['id']] = 'no_executable_path'
    if method_id == 'janus_host':
        selected = _host_sources(executable, targets, wm, case['profile'], result['rejections'])
    elif method_id == 'janus_d2d':
        eligible = [(i, [r for r in rows if r in executable]) for i, rows in groups]
        eligible = [(i, rows) for i, rows in eligible if _complete(rows, targets)]
        if eligible:
            instance, selected = min(eligible, key=lambda pair: (-pair[0]['epdt'], pair[0]['logical_id']))
            result['selected_logical_instance'] = instance['logical_id']
            result['source_mode'] = 'active_d2d'
        else:
            selected = _host_sources(executable, targets, wm, case['profile'], result['rejections'])
            result['source_mode'] = 'host_fallback'
    else:
        selected = executable
    if not _complete(selected, targets):
        return finish('infeasible', 'complete_target_host_cache_missing'
                      if method_id == 'janus_host' or result.get('source_mode') == 'host_fallback'
                      else 'complete_executable_source_missing')
    result.update(driver='cluster', source_ids=[r['id'] for r in selected],
                  strategy=method_id if method_id in ('fastest', 'resource_lp') else 'fastest')
    return finish('applicable')


def build_method_plan(case, method_id, lock_path=None):
    result = assess_case(case, method_id, lock_path)
    if result['status'] != 'applicable' or result['driver'] == 'ipc':
        return result
    ids = set(result['source_ids'])
    try:
        result['plan'] = restore_plan([r for r in case['replicas'] if r['id'] in ids], case['targets'],
                                     case['workers'], case['profile'], result['strategy'])
    except ImportError as exc:
        result.update(status='pending', plan=None)
        result['reasons'].append('planner_dependency_unavailable: '+str(exc))
    except (ValueError, RuntimeError) as exc:
        result.update(status='infeasible', plan=None)
        result['reasons'].append(str(exc))
    if result['plan'] and result['plan']['strategy'] != result['strategy']:
        result.update(status='pending')
        result['reasons'].append('planner_fallback_cannot_be_scored_as_requested_method')
    return result
