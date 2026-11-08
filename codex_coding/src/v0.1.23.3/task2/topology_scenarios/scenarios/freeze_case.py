#!/usr/bin/env python3
"""Freeze three bounded scenarios from protected inputs; CPU planning only."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.benchmarks.topology_cases import (
    SCHEMA, allocated, calibration_gate, canonical_hash, preview_case, validate_case)
from fluidckpt.cluster.path_catalog import topology_id

RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2'
SESSION = RESULTS / 'baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json'
PREFLIGHT = RESULTS / 'basic_scenarios/topology_stage12_preflight_r01/summary.json'
FORMAT = RESULTS / 'basic_scenarios/s4_r01/summary.json'
BASELINE = RESULTS / 'baseline_loading/summary_r02/revised_export_r01/summary.json'
GIB = 1 << 30


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def input_workers(preflight, session, session_sha):
    if preflight.get('status') != 'passed' or preflight.get('session_sha256') != session_sha:
        raise ValueError('passed preflight must refer to exact owned overlay')
    probes = preflight['endpoint_probes']
    if len(probes) != len(session['members']) or any(p.get('status') != 'passed' for p in probes):
        raise ValueError('all four protected endpoints must be present and passed')
    workers = []
    for p in sorted(probes, key=lambda p: p['worker']):
        inv = p['inventory']
        if (inv['worker'] != p['worker'] or session['members'][p['worker']] != [inv['node'], inv['gpu']]
                or session['endpoints'][p['worker']] != p['endpoint']
                or inv.get('transactions') or inv.get('transaction_phases')):
            raise ValueError('preflight worker mapping or transaction state differs')
        workers.append({k: copy.deepcopy(inv[k]) for k in (
            'worker', 'node', 'gpu', 'numa', 'epoch', 'session', 'capabilities',
            'budget_bytes', 'used_bytes', 'staging_bytes', 'headroom_bytes')})
    return workers


def inventory_preflight(inventory, session, session_sha, calibration=None, before=None):
    """Adapt the richer readonly GPU/NUMA/IO inventory to existing preflight fields."""
    inventory=copy.deepcopy(inventory)
    if inventory.get('status') is None and calibration and before:
        if (calibration.get('schema_version')=='topology_calibration.v1'
                and calibration.get('status')=='passed'
                and calibration.get('session_overlay_sha256')==session_sha
                and all(calibration.get(k,{}).get('passed') is True for k in
                        ('before_after_preservation','cleanup','code_preservation','input_preservation'))
                and inventory.get('protected_catalog_signature')==before.get('protected_catalog_signature')):
            inventory.update(status='passed',session_overlay_sha256=session_sha)
    admission=inventory.get('resource_admission',{})
    if (inventory.get('status')!='passed' or inventory.get('read_only') is not True
            or inventory.get('session_overlay_sha256')!=session_sha
            or admission.get('passed') is not True or admission.get('status')!='passed'
            or not inventory.get('protected_catalog_signature')):
        raise ValueError('readonly topology inventory admission/session/protection failed')
    probes=[dict(worker=w['worker'],endpoint=session['endpoints'][w['worker']],
                 status='passed',inventory=w) for w in inventory['workers']]
    return dict(status='passed',session_sha256=session_sha,endpoint_probes=probes)


def make_cases(session, preflight, session_sha, format_contract):
    workers = input_workers(preflight, session, session_sha)
    model = session['models']['B']
    fields = ('id', 'identity', 'tp', 'off', 'n', 'tier', 'worker', 'node', 'gpu',
              'numa', 'epoch', 'generation', 'state', 'leases', 'allocated_bytes')
    base = [{k: r[k] for k in fields if k in r} for r in sorted(model['sources'], key=lambda r: r['tp'])]
    for b in base:
        inv = next(p['inventory'] for p in preflight['endpoint_probes'] if p['worker'] == b['worker'])
        actual = next((r for r in inv['replicas'] if r['id'] == b['id']), None)
        if not actual or any(actual.get(k) != b.get(k) for k in fields):
            raise ValueError('retained base identity/generation/epoch/bytes changed')
    if (model['identity'] != format_contract['model_version'] or model['tp'] != format_contract['tp']):
        raise ValueError('format source no longer matches retained model')
    configs = json.loads((Path(__file__).parent / 'case_specs.json').read_text())
    cases = []
    wm = {w['worker']: w for w in workers}
    for spec in configs['cases']:
        scenarios = []
        for slot, weight in spec['target_slots']:
            targets = [dict(key=f'{slot}/rank{b["tp"]}', tp=b['tp'], identity=b['identity'],
                            off=b.get('off', 0), n=b['n'], tier='hbm',
                            worker=session['slots'][slot]['workers'][b['tp']]) for b in base]
            scenarios.append(dict(name=slot, weight=weight, targets=targets))
        candidates = []
        for rank, worker, off_gib in spec['candidate_extents']:
            b, w = base[rank], wm[worker]
            candidates.append(dict(id=f'preview/{spec["case_id"]}/{rank}/{worker}/{off_gib}',
                                   identity=b['identity'], tp=rank, off=off_gib*GIB, n=2*GIB,
                                   tier='hbm', worker=worker, node=w['node'], gpu=w['gpu'], numa=w['numa'],
                                   state='VALID', generation=1, leases=0, preview_only=True))
        layouts = [dict(name=name, candidate_ids=[candidates[i]['id'] for i in indices])
                   for name, indices in spec['fixed_layout_indices']]
        case = dict(schema_version=SCHEMA, development='v0.1.23.3/task2',
                    case_id=spec['case_id'], case_class=spec['case_class'],
                    associated_experiments=['e3', 'e4', 'e5'], description=spec['description'],
                    scenario_semantics='mutually_exclusive_restores', objective='expected',
                    demand_origin='synthetic_preregistered_weights_not_trace_prediction',
                    workers=workers, topology_id=topology_id(workers),
                    format_contract=format_contract, base=base, restore_scenarios=scenarios,
                    candidates=candidates, fixed_layouts=layouts,
                    budget=dict(base_dram_bytes=sum(b['allocated_bytes'] for b in base),
                                tier_budgets={'hbm':8*GIB},
                                location_budgets={f'{i}:hbm':cap*GIB for i, cap in enumerate(spec['location_gib'])},
                                target_allocation_bytes=sum(allocated(b) for b in base),
                                staging_bytes=sum(w['staging_bytes'] for w in workers),
                                budget_origin='externally_assigned_scenario_constraints',
                                physical_free_memory_is_budget=False,
                                required_headroom_reserve_bytes=2*GIB,
                                runtime_KV_and_other_services='protected; not reclaimed; allocation peak needs runtime samples',
                                setup_ledger_fields=['cache_build_s', 'cache_build_bytes', 'owner_setup_s',
                                                     'format_setup_s', 'pool_setup_s', 'online_planning_s',
                                                     'prepare_s', 'staging_peak_bytes', 'physical_peak_bytes']),
                    execution_contract=dict(executor='serial', schedule='rank_interleaved',
                                            schedule_scope='common existing operation reorder; no new optimization claim',
                                            IPC_opportunity='all methods may use compatible full target owners when legal; current8GiB cap has none',
                                            legal_sources='same complete DRAM base and same selected extra cache for fixed layout controls',
                                            verification_position='after_weights_ready',
                                            correctness='full logical-byte SHA256 plus frozen exact 32-token output',
                                            primary_metric='same-host max(weights_ready)-min(load_begin)',
                                            cross_host_metric='per-host windows and coordinator all-ready; no monotonic subtraction across hosts'),
                    protocol=dict(repetitions=1, retry='separate run; second only if ambiguity',
                                  same_decision='no repeated GPU unless independent mechanism difference',
                                  protected_fixture='four keepers, full DRAM, both parked engines; no cleanup or mutation'),
                    evidence_requirements=spec['evidence_requirements'])
        validate_case(case)
        cases.append(case)
    return cases


def physical_admission(case):
    """Separate imposed cache caps from measured snapshot headroom and target peak."""
    rows = []
    for w in case['workers']:
        cache = case['budget']['location_budgets'][f"{w['worker']}:hbm"]
        target = max((sum(allocated(t) for t in s['targets'] if t['worker'] == w['worker'])
                      for s in case['restore_scenarios']), default=0)
        required = cache + target + case['budget']['required_headroom_reserve_bytes']
        rows.append(dict(worker=w['worker'], cache_cap_bytes=cache, target_bytes=target,
                         required_with_guard_bytes=required, snapshot_headroom_bytes=w['headroom_bytes']['hbm'],
                         snapshot_admissible=required <= w['headroom_bytes']['hbm']))
    return dict(status='snapshot_admissible' if all(r['snapshot_admissible'] for r in rows) else 'blocked',
                rows=rows, runtime_preflight_required=True, physical_peak_verified=False)


def baseline_scope():
    return [
        dict(method='Janus式host', identity='mechanism', adapter='basic_scenarios.janus_host',
             partial_sources='no arbitrary partial-source reconstruction; remote-only warm host is infeasible'),
        dict(method='fastest/resource_lp', identity='mechanism', adapter='basic_scenarios.build_method_plan',
             partial_sources='same extents, legal sources, budget, IPC opportunities and interleaved executor'),
        dict(method='joint_scenarios', identity='mechanism', adapter='cluster.multi_joint.joint_scenarios',
             partial_sources='finite candidates only; replication capability differs from fixed layouts; strong comparator still needed'),
        dict(method='vendor SGLang native', identity='hardware_adaptation', adapter='baseline_loading native path',
             partial_sources='full native checkpoint path passed; partial-source same-budget recovery not mapped'),
        dict(method='BlitzScale TP分发HIP', identity='hardware_adaptation', adapter='baseline_loading Blitz path',
             partial_sources='complete active TP2 source passed; no complete active owner at8GiB cap; fragments do not represent original system'),
        dict(method='ServerlessLLM Store HIP', identity='hardware_adaptation', adapter='baseline_loading Store path',
             partial_sources='full partition + pinned pool passed; arbitrary distributed fragments not mapped; 66GiB pool separately charged'),
        dict(method='SGLang/Janus/BlitzScale/ServerlessLLM原版完整系统', identity='original_system',
             adapter='pending', partial_sources='not reproduced; no latency or speed ranking assigned')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, default=SESSION)
    parser.add_argument('--preflight', type=Path, default=PREFLIGHT)
    parser.add_argument('--format-evidence', type=Path, default=FORMAT)
    parser.add_argument('--calibration', type=Path)
    parser.add_argument('--inventory', type=Path, help='prefer newer readonly topology inventory for snapshot budgets')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--include-joint', action='store_true')
    args = parser.parse_args()
    if args.include_joint and not args.preview:
        parser.error('--include-joint requires --preview')
    started = time.monotonic()
    inputs = {'session': args.session, 'preflight': args.preflight, 'format_evidence': args.format_evidence,
              'case_specs': Path(__file__).parent / 'case_specs.json', 'baseline_evidence': BASELINE}
    if args.calibration:
        inputs['calibration'] = args.calibration
        for name in ('protocol','inventory_before','inventory_after'):
            path=args.calibration.parent/(name+'.json')
            if path.is_file():
                inputs['calibration_'+name]=path
    if args.inventory:
        inputs['topology_inventory']=args.inventory
    snapshots = {name: (path.read_bytes(), digest(path)) for name, path in inputs.items()}
    session = json.loads(snapshots['session'][0])
    preflight = json.loads(snapshots['preflight'][0])
    fmt = json.loads(snapshots['format_evidence'][0])['format_contract']
    calibration = json.loads(snapshots['calibration'][0]) if args.calibration else None
    if args.inventory:
        latest_after=(args.calibration and args.inventory.resolve()==
                      (args.calibration.parent/'inventory_after.json').resolve())
        preflight=inventory_preflight(json.loads(snapshots['topology_inventory'][0]),
                                      session,snapshots['session'][1],
                                      calibration if latest_after else None,
                                      json.loads(snapshots['calibration_inventory_before'][0])
                                      if latest_after and 'calibration_inventory_before' in snapshots else None)
    cases = make_cases(session, preflight, snapshots['session'][1], fmt)
    if args.output.exists():
        raise FileExistsError('new output directory required; frozen/raw results cannot be overwritten')
    args.output.mkdir(parents=True)
    manifest = dict(schema_version='topology_freeze.v1', development='v0.1.23.3/task2',
                    measurement_status='not_measured', new_GPU_samples=0, GPU_or_SSH_mutations=False,
                    created_wall_time=time.time(), command=[sys.executable,*sys.argv],
                    inputs={name: dict(path=str(path.resolve()), sha256=snapshots[name][1])
                            for name, path in inputs.items()},
                    code_sha256={str(p.relative_to(ROOT)): digest(p) for p in (
                        Path(__file__), ROOT/'main_lib/fluidckpt/benchmarks/topology_cases.py',
                        ROOT/'main_lib/fluidckpt/benchmarks/basic_scenarios.py',
                        ROOT/'main_lib/fluidckpt/cluster/planning.py',
                        ROOT/'main_lib/fluidckpt/cluster/joint.py', ROOT/'main_lib/fluidckpt/cluster/multi_joint.py',
                        ROOT/'main_lib/fluidckpt/cluster/worker.py', ROOT/'lib_cite/baselines.lock.json',
                        Path(__file__).parent/'register_case.py')},
                    baseline_scope=baseline_scope(), cases=[])
    for name, (content, _) in snapshots.items():
        path = args.output / 'inputs' / (name + '.json')
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
    for case in cases:
        key = case['case_id']
        gate = calibration_gate(calibration, case, snapshots['session'][1])
        admitted = physical_admission(case)
        profile_qualified = gate['application_profile_qualified']
        profile = calibration['application_profile']['profile'] if profile_qualified else session['profile']
        source_kind = 'verified_application_effective' if profile_qualified else 'functional_provisional_assumption'
        adapter = dict(case_id=key, base=case['base'], candidates=case['candidates'],
                       scenarios=case['restore_scenarios'], workers=case['workers'], profile=profile,
                       tier_budgets=case['budget']['tier_budgets'], location_budgets=case['budget']['location_budgets'],
                       objective=case['objective'], profile_source=source_kind,
                       adapter='fluidckpt.cluster.multi_joint.joint_scenarios',
                       invocation_scope='CPU preview only; not an authorized GPU command')
        write_json(args.output / 'cases' / (key + '.json'), case)
        write_json(args.output / 'adapter_inputs' / (key + '.json'), adapter)
        preview = preview_case(case, profile, args.include_joint) if args.preview else None
        if preview:
            preview['profile_source'] = source_kind
            preview['estimated_values_are_performance_evidence'] = False
            write_json(args.output / 'previews' / (key + '.json'), preview)
        status = ('blocked' if admitted['status'] == 'blocked' else
                  'frozen_shared_claim_conditional' if case['case_class'] == 'shared_resources' and
                  not gate['physical_shared_case_qualified'] else 'frozen_functional_contract')
        readiness = dict(contract_valid=True, plan_adapter_ready=True, restore_runner_ready=False,
                         snapshot_restore_admissible=admitted['status']=='snapshot_admissible',
                         empirical_full_model_execution='separate e4/e5 phase; runtime adapter and fresh preflight required',
                         prediction_ready=profile_qualified,
                         physical_contention_claim_ready=gate['physical_shared_case_qualified'],
                         GPU_execution_authorized_by_freeze=False)
        manifest['cases'].append(dict(case_id=key, status=status, case_sha256=canonical_hash(case),
                                      calibration_gate=gate, physical_admission=admitted,
                                      readiness=readiness,
                                      profile_source=source_kind, preview_status='complete' if preview else 'not_requested',
                                      decision_aliases=preview['decision_aliases'] if preview else [],
                                      execution_entry='scenarios/register_case.py --manifest <manifest> --case <id> --output <new_directory>',
                                      missing_interfaces=['strong simple placement comparator with identical replication opportunity',
                                                          'runtime adapter must apply frozen candidates/caps, interleave schedule and setup ledger',
                                                          'full-model execution remains separate e4/e5 phase']))
    changed = [name for name, path in inputs.items() if digest(path) != snapshots[name][1]]
    if changed:
        raise RuntimeError('inputs changed while freezing: ' + ','.join(changed))
    manifest['inputs_unchanged_after_freeze'] = True
    manifest['freeze_wall_s'] = time.monotonic() - started
    manifest['manifest_sha256_scope'] = 'canonical manifest excluding self hash'
    manifest['manifest_sha256'] = canonical_hash(manifest)
    write_json(args.output / 'manifest.json', manifest)
    lines = ['# 同预算拓扑场景冻结（CPU入口）', '',
             '完整DRAM、TP2格式和底本身份固定。预算是外部指定实验限制；0.5/0.5为预登记合成需求。互斥恢复场景不会合并成并发需求。', '',
             '| case | 类别 | 每位置extra HBM(GiB) | 目标 | 状态 | profile |',
             '| --- | --- | --- | --- | --- | --- |']
    for case, row in zip(cases, manifest['cases']):
        caps = ', '.join(f'{k}={v/GIB:g}' for k, v in case['budget']['location_budgets'].items())
        lines.append(f'| {case["case_id"]} | {case["case_class"]} | {caps} | '
                     f'{", ".join(s["name"] for s in case["restore_scenarios"])} | {row["status"]} | {row["profile_source"]} |')
    lines += ['', '所有方法共用rank_interleaved执行改进及合法IPC机会。8GiB预算不能建立完整约65.5GiB目标owner；fragment来源不表示原版外部系统复现。', '',
              'CPU预览的估计值不支持速度排名或真实收益。单流有效速率不是真实容量上界；RCCL并发微probe也不自动证明当前串行恢复执行器物理共享争用。', '',
              'T1/T2具有合法预算、snapshot余量与functional CPU注册入口；预测校准与物理争用论证分别gate。T1既有runner只需配置接受共用rank交错；T2还需接受外部layout/per-location caps并逐次绑定互斥目标。register_case.py可生成runner所需base/candidates/targets/profile/budget与CPU计划，实机适配属下阶段。', '',
              '有限候选joint接口、完整底本账本与固定布局取数对照已复用。尚需同复制机会的强简单placement对照；本轮不建设新求解器。', '',
              'planning/prepare/cache建立/owner建立/格式转换/host pool建立与restore窗口分别记账；目标完整分配、staging和运行峰值单列。', '',
              'CPU复核命令（使用新output目录）：', '',
              '```bash', shlex.quote(sys.executable) + ' ' + shlex.quote(str(Path(__file__).relative_to(ROOT))) +
              ' --session ' + shlex.quote(str(args.session)) + ' --preflight ' + shlex.quote(str(args.preflight)) +
              ' --format-evidence ' + shlex.quote(str(args.format_evidence)) +
              (' --inventory ' + shlex.quote(str(args.inventory)) if args.inventory else '') +
              (' --calibration ' + shlex.quote(str(args.calibration)) if args.calibration else '') +
              ' --output ' + shlex.quote(str(args.output) + '_new') +
              (' --preview' if args.preview else '') + (' --include-joint' if args.include_joint else ''), '```', '']
    with (args.output / 'comparison.md').open('x') as stream:
        stream.write('\n'.join(lines))
    print(json.dumps({'status':'frozen', 'manifest':str(args.output/'manifest.json'),
                      'cases':[{k:r[k] for k in ('case_id','status','profile_source')} for r in manifest['cases']],
                      'new_GPU_samples':0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
