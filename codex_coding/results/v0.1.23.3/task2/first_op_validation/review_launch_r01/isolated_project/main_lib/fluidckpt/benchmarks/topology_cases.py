"""Frozen topology cases and CPU adapters for existing restore/placement planners."""
import copy
import hashlib
import json
import math
import time

from fluidckpt.benchmarks.basic_scenarios import build_method_plan
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.planning import covers, extent, restore_plan, validate_plan

SCHEMA = 'topology_case.v1'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def allocated(row):
    return (row['n'] + 4095) // 4096 * 4096


def validate_case(case):
    """Validate immutable identities, explicit capacity, and scenario semantics."""
    if case.get('schema_version') != SCHEMA or case.get('development') != 'v0.1.23.3/task2':
        raise ValueError('case schema/development mismatch')
    if case.get('scenario_semantics') != 'mutually_exclusive_restores':
        raise ValueError('multi_joint supports mutually exclusive restores only')
    workers = case['workers']
    wm = {w['worker']: w for w in workers}
    if len(wm) != len(workers) or topology_id(workers) != case['topology_id']:
        raise ValueError('worker topology mismatch')
    fmt, base, scenarios = case['format_contract'], case['base'], case['restore_scenarios']
    if any(fmt.get(k) is None for k in ('model_version','dtype','quantization','tp','processed_snapshot_id')):
        raise ValueError('snapshot format fields incomplete')
    if fmt.get('tp') != 2 or len(base) != 2 or not scenarios:
        raise ValueError('fixed complete TP2 base required')
    if {b['tp'] for b in base} != {0, 1} or len({b['id'] for b in base}) != 2:
        raise ValueError('duplicate or missing base rank')
    if any(b['state'] != 'VALID' or b['tier'] != 'dram' or b['worker'] not in wm
           or b.get('leases', 0) != 0 for b in base):
        raise ValueError('base must be retained VALID DRAM with no outstanding leases')
    for b in base:
        if (type(b['n']) is not int or b['n'] <= 0 or type(b.get('off',0)) is not int
                or b.get('off',0) < 0 or b.get('allocated_bytes',allocated(b)) < allocated(b)
                or any(b.get(k) != wm[b['worker']].get(k) for k in ('node','gpu','numa','epoch'))):
            raise ValueError('base extent or physical worker incarnation mismatch')
    if fmt['processed_snapshot_id'] != [b['identity'] for b in sorted(base, key=lambda b: b['tp'])]:
        raise ValueError('processed rank identity mismatch')
    rank_base = {b['tp']: b for b in base}
    if len({s['name'] for s in scenarios}) != len(scenarios):
        raise ValueError('duplicate restore scenario')
    for scenario in scenarios:
        if not math.isfinite(scenario['weight']) or scenario['weight'] <= 0:
            raise ValueError('scenario weight must be positive')
        targets = scenario['targets']
        if len(targets) != 2 or {t['tp'] for t in targets} != {0, 1}:
            raise ValueError('each mutually exclusive restore must include all TP ranks')
        if len({t['worker'] for t in targets}) != 2:
            raise ValueError('TP ranks require distinct target GPUs')
        for t in targets:
            b = rank_base[t['tp']]
            if (t['worker'] not in wm or t['identity'] != b['identity']
                    or extent(t) != extent(b) or t.get('tier') != 'hbm'):
                raise ValueError('target identity/layout differs from full base')
    budget = case['budget']
    for cap in [*budget['tier_budgets'].values(), *budget['location_budgets'].values(),
                budget['base_dram_bytes'], budget['target_allocation_bytes'], budget['staging_bytes']]:
        if type(cap) is not int or cap < 0:
            raise ValueError('budgets require nonnegative integer bytes')
    if budget['base_dram_bytes'] != sum(b.get('allocated_bytes', allocated(b)) for b in base):
        raise ValueError('complete DRAM base must be charged exactly once')
    if budget['target_allocation_bytes'] != sum(allocated(b) for b in base):
        raise ValueError('complete target allocation must be charged')
    locations = budget['location_budgets']
    if set(locations) != {f'{w}:hbm' for w in wm}:
        raise ValueError('every location needs an explicit extra HBM cap, including zero')
    if set(budget['tier_budgets']) != {'hbm'}:
        raise ValueError('this stage only selects extra HBM; DRAM base is fixed')
    candidates = case['candidates']
    if len({c['id'] for c in candidates}) != len(candidates):
        raise ValueError('duplicate candidate')
    for c in candidates:
        if (c.get('preview_only') is not True or c['tier'] != 'hbm' or c['worker'] not in wm
                or type(c['off']) is not int or type(c['n']) is not int
                or c['n'] <= 0 or c['off'] < 0 or c['state'] != 'VALID'
                or not covers(base, c['identity'], c['tp'], *extent(c))):
            raise ValueError('candidate must be a buildable hypothetical full-format extent')
        if any(c[k] != wm[c['worker']][k] for k in ('node','gpu','numa')):
            raise ValueError('candidate physical location mismatch')
        if allocated(c) > locations[f"{c['worker']}:hbm"]:
            raise ValueError('candidate exceeds explicit location cap')
    by_id = {c['id']: c for c in candidates}
    for layout in case['fixed_layouts']:
        ids = layout['candidate_ids']
        if len(set(ids)) != len(ids) or any(i not in by_id for i in ids):
            raise ValueError('layout references unknown or duplicate candidate')
        selected = [by_id[i] for i in ids]
        if sum(allocated(c) for c in selected) > budget['tier_budgets']['hbm']:
            raise ValueError('layout exceeds global extra budget')
        for location, cap in locations.items():
            if sum(allocated(c) for c in selected if f"{c['worker']}:hbm" == location) > cap:
                raise ValueError('layout exceeds per-location extra budget')
    if case['execution_contract'].get('schedule') != 'rank_interleaved':
        raise ValueError('all methods must receive the shared executor order improvement')
    replica_block_case = (case.get('collection_kind') == 'synthetic_blocks'
                          and all(t.get('mode') == 'replica' for s in scenarios for t in s['targets']))
    allowed_verification = ('after_weights_ready', 'before_replica_publish') if replica_block_case else ('after_weights_ready',)
    if case['execution_contract'].get('verification_position') not in allowed_verification:
        raise ValueError('verification position must be common')
    if budget.get('budget_origin') != 'externally_assigned_scenario_constraints':
        raise ValueError('budget origin must distinguish scenario constraints from physical free memory')
    if case.get('demand_origin') != 'synthetic_preregistered_weights_not_trace_prediction':
        raise ValueError('scenario weights must explicitly identify their origin')
    return {'passed': True, 'case_sha256': canonical_hash(case), 'full_base_protected': True,
            'candidate_count': len(candidates), 'physical_peak_verified': False}


def calibration_gate(calibration, case, session_sha256):
    """Microbenchmark contention does not automatically qualify a serial application."""
    reasons = []
    if not calibration:
        return {'status': 'conditional', 'reasons': ['calibration_missing'],
                'physical_shared_case_qualified': False, 'application_profile_qualified': False}
    if calibration.get('schema_version') != 'topology_calibration.v1':
        reasons.append('calibration_schema_mismatch')
    if calibration.get('topology_id') != case['topology_id']:
        reasons.append('calibration_topology_mismatch')
    if calibration.get('session_overlay_sha256') != session_sha256:
        reasons.append('calibration_session_mismatch')
    if calibration.get('capacity_is_physical_upper_bound') is not False:
        reasons.append('effective_rate_must_not_be_declared_physical_capacity')
    if calibration.get('status') != 'passed':
        reasons.append('calibration_not_passed')
    def passed(value):
        if not isinstance(value,dict) or not any(k in value for k in ('status','passed')):
            return False
        return (('status' not in value or value['status']=='passed')
                and ('passed' not in value or value['passed'] is True))
    for field in ('resource_admission','before_after_preservation','cleanup',
                  'code_preservation','input_preservation'):
        if not passed(calibration.get(field,{})):
            reasons.append('calibration_' + field + '_not_passed')
    shared = calibration.get('shared_resource', {})
    application = calibration.get('application_profile', {})
    microprobe_verified = not reasons and shared.get('gate_status') == 'qualified'
    qualified = (not reasons and shared.get('gate_status') == 'qualified'
                 and shared.get('application_executor') == 'serial'
                 and shared.get('application_executor_contention_verified') is True
                 and bool(shared.get('application_evidence_refs'))
                 and bool(shared.get('application_executor_code_sha256')))
    compatible = calibration.get('application_executor_compatible',
                                 calibration.get('profile',{}).get('application_executor_compatible'))
    profile_ok = (not reasons and compatible is True
                  and application.get('executor') == 'serial'
                  and application.get('measurement_status') == 'verified'
                  and isinstance(application.get('profile'), dict))
    if profile_ok:
        p = application['profile']
        required = ('rates', 'gpu_ingress_GBps', 'gpu_egress_GBps', 'dram_GBps',
                    'network_node_GBps', 'verified_paths', 'directed_path_rates', 'network_pairs')
        if any(k not in p for k in required) or not p.get('verified_paths'):
            reasons.append('application_profile_missing_explicit_path_evidence')
            profile_ok = False
        elif any(key not in p['directed_path_rates'] for key in p['verified_paths']):
            reasons.append('application_profile_path_rate_missing')
            profile_ok = False
    if case['case_class'] == 'shared_resources' and not qualified:
        reasons.append('serial_application_shared_contention_unverified')
    return {'status': 'qualified' if not reasons and (qualified or profile_ok) else 'conditional',
            'reasons': reasons, 'physical_shared_case_qualified': qualified,
            'application_profile_qualified': profile_ok,
            'application_profile_reason': None if profile_ok else 'serial_application_profile_unverified_or_executor_incompatible',
            'physical_micro_shared_path_evidence': microprobe_verified,
            'microprobe_shared_status': shared.get('gate_status', 'missing')}


def interleave(plan):
    result = copy.deepcopy(plan)
    groups = [[o for o in result['ops'] if o['target'] == t['key']] for t in result['targets']]
    result['ops'] = [g[i] for i in range(max(map(len, groups))) for g in groups if i < len(g)]
    validate_plan(result)
    result['shared_schedule'] = 'rank_interleaved'
    return result


def preview_case(case, profile, include_joint=False):
    """Build bounded CPU plans; no catalog mutations, live allocations, or GPU calls."""
    validate_case(case)
    started = time.monotonic()
    by_id = {c['id']: c for c in case['candidates']}
    rows = []
    for layout in case['fixed_layouts']:
        replicas = copy.deepcopy(case['base']) + [copy.deepcopy(by_id[i]) for i in layout['candidate_ids']]
        for r in replicas:
            r.update(source_role='base' if r['tier'] == 'dram' else 'extra', allocation_id=r['id'])
        for scenario in case['restore_scenarios']:
            for method in ('janus_host', 'fastest', 'resource_lp', 'sglang_ipc_hip'):
                basic = dict(scenario_id='S4', workers=case['workers'], replicas=replicas,
                             targets=scenario['targets'], profile=profile,
                             format_contract=case['format_contract'], executor='serial',
                             budget=dict(base_dram_bytes=case['budget']['base_dram_bytes'],
                                         extra_hbm_bytes=case['budget']['tier_budgets']['hbm'],
                                         location_extra_hbm_bytes=case['budget']['location_budgets'],
                                         target_allocation_bytes=case['budget']['target_allocation_bytes'],
                                         staging_bytes=case['budget']['staging_bytes']))
                result = build_method_plan(basic, method)
                plan = interleave(result['plan']) if result.get('plan') else None
                signature = canonical_hash([{k: o[k] for k in ('target', 'identity', 'tp', 'off', 'n',
                                                              'src_w', 'dst_w', 'replica_id')} for o in plan['ops']]) if plan else None
                rows.append(dict(layout=layout['name'], scenario=scenario['name'], method=method,
                                 status=result['status'], reasons=result['reasons'], plan=plan,
                                 operation_signature=signature, identity=result['identity'],
                                 measurement_status='not_measured'))
    aliases = []
    for i, row in enumerate(rows):
        if row['operation_signature']:
            same = [r['method'] for r in rows[:i] if r['layout'] == row['layout']
                    and r['scenario'] == row['scenario'] and r['operation_signature'] == row['operation_signature']]
            if same:
                aliases.append(dict(layout=row['layout'], scenario=row['scenario'], method=row['method'],
                                    same_decision_as=same, action='do_not_repeat_GPU_without_other_mechanism_difference'))
    joint = {'status': 'not_requested'}
    if include_joint:
        try:
            from fluidckpt.cluster.multi_joint import joint_scenarios
            b = case['budget']
            joint = joint_scenarios(case['base'], case['candidates'], case['restore_scenarios'],
                                    case['workers'], profile, b['tier_budgets'], b['location_budgets'],
                                    objective=case['objective'], solver_options={'time_limit': 5})
            joint['measurement_status'] = 'not_measured'
            joint['capability_boundary'] = 'joint may replicate a region; fixed layout controls are not a strong replication-aware greedy comparator'
        except (ImportError, ValueError, RuntimeError) as exc:
            joint = {'status': 'blocked', 'error': str(exc)}
    return {'schema_version': 'topology_preview.v1', 'rows': rows, 'decision_aliases': aliases,
            'joint': joint, 'cpu_preview_wall_s': time.monotonic() - started,
            'new_GPU_samples': 0, 'scope': 'CPU adapters only; hypothetical candidates are not live source evidence'}
