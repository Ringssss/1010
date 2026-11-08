"""CPU fixtures for exact source placement, sender DAG and immutable predictions."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'model')]
from cpu_check import fixtures
from evaluate import digest, evaluate, file_ref
from simple_contracts import CHUNK, GiB, RANK_BYTES, WORKFLOW, layout_plan, serialized_sender_evidence, validate_roles
from simple_model import CALIBRATION_SIZES, TAIL_BYTES, POLICY, build_model, calibrate_outer, freeze, physical, plan_workload, predict, transfer_service
from simple_controller import SimpleRun
import simple_controller as controller_adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, passed):
        checks.append(dict(name=name, passed=bool(passed)))
        assert passed, name
    def rejects(name, fn):
        try:
            fn()
        except (ValueError, KeyError):
            check(name, True)
        else:
            check(name, False)
    workers, manifests, replicas = fixtures()
    workers = workers[:3]
    for worker in workers:
        worker['runtime_identity'] = dict(actual_bdf='CPU_BDF' + str(worker['worker']))
        worker['transactions'] = []
    for row in replicas:
        if row['worker'] == 3:
            row['worker'], row['epoch'] = 2, workers[2]['epoch']
    runner = SimpleRun.__new__(SimpleRun)
    runner.bootstrap = dict(manifests=manifests)
    runner.endpoints, runner.clock = ['CPU'] * 3, 'CPU_controller_no_hardware'
    plans = {name: layout_plan(manifests, replicas, workers, runner.endpoints, name, 'CPU_' + name, 'CPU')
             for name in ('C0_mixed', 'Hnear', 'Hremote')}
    for name, plan in plans.items():
        check(name + '_complete66ops70329718112B', len(plan['ops']) == 66 and sum(op['n'] for op in plan['ops']) == 2 * RANK_BYTES)
        check(name + '_same_rank_interleaving', [op['dst_w'] for op in plan['ops']] == [0, 1] * 33)
        remote = [op for op in plan['ops'] if op['kind'] == 'network']
        check(name + '_registered_remote_count', len(remote) == {'C0_mixed': 32, 'Hnear': 0, 'Hremote': 64}[name])
        check(name + '_remote_only_worker2_correctNUMA', all(op['src_w'] == 2 and op['path'].startswith('2:dram:' + str(4 if op['tp'] == 0 else 7) + '->') for op in remote))
    check('identical_model_workload_across_layouts', len({digest(plan_workload(plan)) for plan in plans.values()}) == 1)
    check('complete_helper70GB_on_two_NUMAs', sum(r['n'] for r in replicas if r['worker'] == 2) == 2 * RANK_BYTES and
          {(r['tp'], r['numa']) for r in replicas if r['worker'] == 2} == {(0, 4), (1, 7)})
    wrong = copy.deepcopy(replicas)
    next(r for r in wrong if r['worker'] == 2 and r['tp'] == 1)['numa'] = 4
    rejects('reject_rank1_remote_wrongNUMA', lambda: layout_plan(manifests, wrong, workers, runner.endpoints, 'Hremote', 'bad', 'CPU'))
    wrong = copy.deepcopy(replicas); wrong.append(copy.deepcopy(next(r for r in wrong if r['worker'] == 2)))
    rejects('reject_ambiguous_remote_source', lambda: layout_plan(manifests, wrong, workers, runner.endpoints, 'Hremote', 'bad', 'CPU'))
    roles = [dict(kind='worker', node=w['node'], gpu=w['gpu'], numa=w['numa']) for w in workers]
    role_plan = dict(processes=roles, workflow=WORKFLOW, remote_source_numa_by_rank={'0': 4, '1': 7})
    check('exact_three_roles_explicitNUMA', len(validate_roles(role_plan)) == 3)
    rejects('reject_implicit_remoteNUMA', lambda: validate_roles(dict(role_plan, remote_source_numa_by_rank=None)))
    rejects('reject_legacy_four_workers', lambda: validate_roles(dict(role_plan, processes=roles + [roles[-1]])))
    context = runner.context(workers, replicas)
    check('six_paths_include_worker2_rank1NUMA7', len(context['path_context']) == 6 and '2:dram:7->1' in context['path_context'])
    check('no_shared_disjoint_multiplier_registered', context['shared_resource_paths'] == {})
    raw = args.out / 'CPU_fixture_raw.json'
    raw.write_text(json.dumps(dict(CPU_fixture=True, GPU_samples=0)))
    runner.bootstrap['binding_ref'] = file_ref(raw)
    state = runner.state(workers, replicas, context)
    check('warm_single_sender_pairs', state['warm_pairs'] == ['0:2', '1:2'])
    samples, refs = {}, [file_ref(raw)]
    def sample_raw(path, role, n, service, index):
        target = args.out / f'CPU_point_{index}_{role}_{n}.json'
        target.write_text(json.dumps(dict(CPU_fixture=True, path=path, sample_role=role, session='CPU_fixture',
            config_sha256=digest(context), resource_gate=dict(passed=True), target_retired=True,
            flow=dict(verification=dict(passed=True, checked_bytes=n), execution=[dict(records=[dict(elapsed_s=service)])]))))
        ref = file_ref(target); refs.append(ref)
        return ref
    for index, (path, fields) in enumerate(context['path_context'].items()):
        warmup = sample_raw(path, 'warmup', GiB, .1, index)
        samples[path] = []
        for n in CALIBRATION_SIZES:
            # Remote fixtures deliberately decrease with size; this is valid
            # positive service evidence without a bandwidth slope inference.
            service = (.073129 if n == TAIL_BYTES else .066855) if fields['operation_kind'] == 'network' else .001+n/1e9/20
            ref = sample_raw(path, 'cal', n, service, index)
            samples[path].append(dict(nbytes=n, service_s=service, evidence_kind='actual_path_calibration',
                passed=True, verified_bytes=n, config_sha256=digest(context), session='CPU_fixture', CPU_fixture=True,
                warmup_ref=warmup, source_ref=ref))
    model = build_model(context, workers, samples, {}, refs)
    check('nonmonotonic_positive_exact_service_points_accepted', model['point_costs']['2:dram:4->0'][str(TAIL_BYTES)]['service_s'] > model['point_costs']['2:dram:4->0'][str(GiB)]['service_s'])
    rejects('reject_shared_multiplier_in_simple_mode', lambda: build_model(context, workers, samples, {'bogus': []}, refs))
    old_context = copy.deepcopy(context); old_context.pop('path_cost_policy')
    rejects('reject_old_fit_context', lambda: build_model(old_context, workers, samples, {}, refs))
    bad = copy.deepcopy(samples); bad['2:dram:4->0'][0]['nbytes'] = GiB//2
    rejects('reject_old512MiB_domain', lambda: build_model(context, workers, bad, {}, refs))
    bad = copy.deepcopy(samples); bad['2:dram:4->0'][0]['warmup_ref'] = bad['2:dram:7->1'][0]['warmup_ref']
    rejects('reject_other_paths_warmup', lambda: build_model(context, workers, bad, {}, refs))
    services = {name: transfer_service(plan, workers, model, state) for name, plan in plans.items()}
    for name, result in services.items():
        check(name + '_every_exact_operation_matches_observed_cost', all(abs(row['service_s'] - model['point_costs'][row['path']][str(op['n'])]['service_s']) < 1e-12 for row, op in zip(result['timeline'], plans[name]['ops'])))
    for name in ('C0_mixed', 'Hremote'):
        remote_timeline = [row for row in services[name]['timeline'] if row['path'].startswith('2:')]
        check(name + '_DAG_serializes_both_remote_paths', all(left['end_s'] <= right['start_s'] for left, right in zip(remote_timeline, remote_timeline[1:])))
        check(name + '_no_shared_qualifier_applied', services[name]['shared_phase_qualifiers'] == {} and
              services[name]['worker_dag_s'] == services[name]['isolated_worker_dag_s'])
    c0_ns = round((services['C0_mixed']['worker_dag_s'] + 2.5) * 1e9)
    c0 = dict(evidence_kind='actual_model_online_calibration', CPU_fixture=True, full_sha_passed=True,
              all_target_weights_usable=True, exact_reference_output_passed=True,
              verification_position='after_all_weights_ready', resource_gate_passed=True, cleanup_passed=True,
              verified_bytes=2 * RANK_BYTES, plan=plans['C0_mixed'], workers=workers, state=state,
              config_sha256=digest(context), clock_domain=runner.clock, clock_scope='single_controller',
              load_begin_ns=0, all_weights_ready_ns=c0_ns, source_ref=file_ref(raw), session='CPU_fixture')
    model = calibrate_outer(model, c0)
    import freeze as complex_freeze
    import predictor as complex_predictor
    check('complex_freeze_predictor_globals_unchanged', complex_freeze.predict is complex_predictor.predict)
    check('C0_residual_added_once', abs(model['online_outer_s'] - 2.5) < 1e-9)
    rejects('reject_unwarmed_rank1_pair', lambda: predict(plans['Hremote'], workers, model, dict(state, warm_pairs=['0:2'])))
    rejects('reject_original_state_context_mutation', lambda: predict(plans['Hremote'], workers, model, dict(state, config_sha256='changed')))
    bad = copy.deepcopy(plans['Hremote']); bad['ops'][2]['n'] = 900000000
    rejects('reject_unobserved_size_no_interpolation', lambda: predict(bad, workers, model, state))
    bad = copy.deepcopy(samples); bad['2:dram:4->0'][0]['service_s'] = -1
    rejects('reject_nonpositive_point_service', lambda: build_model(context, workers, bad, {}, refs))
    stale = copy.deepcopy(state); next(r for r in stale['source_lineage'].values() if r['worker'] == 2 and r['numa'] == 7)['numa'] = 4
    rejects('reject_source_lineage_NUMA_mutation', lambda: predict(plans['Hremote'], workers, model, stale))
    common = dict(weight_identity=[m['identity'] for m in manifests],
                  target_signature=[{k: t[k] for k in ('worker', 'identity', 'tp', 'off', 'n')} for t in plans['Hnear']['targets']],
                  allowed_budgets={'CPU': True}, comparison_group='CPU_fixture', clock_domain=runner.clock,
                  plan_workload=plan_workload(plans['Hnear']), requires_shared_overlap=False)
    protocol = dict(schema='online_cost_protocol_v1', registered_wall_ns=time.time_ns(), max_relative_error=.10,
                    verification_position='after_all_weights_ready', model_weights_ready_required=True,
                    independence_mode='new_request_current_incarnation', scenarios={name: dict(logical_bytes=2*RANK_BYTES,
                    validation_samples=1, context=dict(common, layout=name)) for name in ('Hnear', 'Hremote')})
    inputs = dict(calibration_run_ids=['CPU_C0'], calibration_incarnation_digests=[digest(state['incarnations'])],
                  scenarios={name: dict(plan=plans[name], workers=workers, state=state) for name in protocol['scenarios']})
    for filename, value in [('protocol.json', protocol), ('model.json', model), ('inputs.json', inputs)]:
        (args.out / filename).write_text(json.dumps(value, indent=2))
    frozen = freeze(args.out / 'protocol.json', args.out / 'model.json', args.out / 'inputs.json')
    (args.out / 'frozen_prediction.json').write_text(json.dumps(frozen, indent=2))
    observations = {name: [dict(scenario=name, context_sha256=digest(spec['context']), session='CPU_fixture',
                    run_id='CPU_' + name, incarnation_sha256=digest(state['incarnations']),
                    load_begin_wall_ns=frozen['payload']['freeze_wall_ns'] + 1, fresh_binding_verified=True,
                    all_target_weights_usable=True, exact_reference_output_passed=True,
                    verification_position='after_all_weights_ready', full_sha_passed=True, verified_bytes=2*RANK_BYTES,
                    resource_gate_passed=True, cleanup_passed=True, clock_domain=runner.clock,
                    load_begin_ns=0, all_weights_ready_ns=frozen['payload']['predictions'][name]['estimated_online_ns'],
                    source_refs=[file_ref(raw)], evidence_kind='CPU_fixture')]
                    for name, spec in protocol['scenarios'].items()}
    result = evaluate(frozen, observations)
    check('freeze_both_transferable_heldouts_before_requests', set(frozen['payload']['predictions']) == {'Hnear', 'Hremote'} and result['all_scenario_error_gates_passed'])
    check('CPU_chain_never_qualifies_actual_model', not result['actual_model_online_qualified'])
    bad = copy.deepcopy(observations); bad['Hremote'][0]['all_weights_ready_ns'] *= 2
    check('individual10pct_failure_cannot_average_away', not evaluate(frozen, bad)['all_scenario_error_gates_passed'])
    bad = copy.deepcopy(observations); bad['Hremote'][0]['load_begin_wall_ns'] = frozen['payload']['freeze_wall_ns']
    rejects('reject_heldout_not_after_freeze', lambda: evaluate(frozen, bad))
    bad = copy.deepcopy(observations); bad['Hnear'][0]['run_id'] = 'CPU_C0'
    rejects('reject_reused_C0_request', lambda: evaluate(frozen, bad))
    bad = copy.deepcopy(frozen); bad['payload']['predictions']['Hremote']['estimated_online_ns'] += 1
    rejects('reject_mutated_frozen_prediction', lambda: evaluate(bad, observations))
    remote = [op for op in plans['Hremote']['ops'] if op['src_w'] == 2]
    events = []
    for index, op in enumerate(remote):
        events.extend([dict(op_id=op['op_id'], ev=ev, monotonic_ns=index*20+offset, clock_domain='CPU_sender_host')
                       for ev, offset in [('cluster_op_begin', 1), ('cluster_op_end', 10)]])
    execution = [dict(worker=2, op_clock_events=events)]
    check('sender_same_host_ordered_intervals_cover64ops', len(serialized_sender_evidence(execution, plans['Hremote'])['intervals']) == 64)
    bad = copy.deepcopy(execution); bad[0]['op_clock_events'][2]['monotonic_ns'] = 5
    rejects('reject_sender_operation_overlap', lambda: serialized_sender_evidence(bad, plans['Hremote']))
    bad = copy.deepcopy(execution); bad[0]['op_clock_events'][1]['clock_domain'] = 'foreign_host'
    rejects('reject_cross_host_clock_subtraction', lambda: serialized_sender_evidence(bad, plans['Hremote']))
    rejects('reject_missing_sender_clock_evidence', lambda: serialized_sender_evidence([], plans['Hremote']))
    check('Hnear_no_remote_evidence_required', serialized_sender_evidence([], plans['Hnear'])['passed'])
    # Run the adapter's real control flow with explicit CPU substitutes at I/O
    # boundaries. Persisted fixture records cannot claim real-model evidence.
    flow_dir = args.out / 'CPU_controller_flow'
    flow_dir.mkdir()
    calls = []
    runner.out, runner.runtime = flow_dir, flow_dir / 'runtime'
    runner.plan = dict(run_id='CPU_fixture', handoff=str(flow_dir / 'handoff'))
    runner.engine_endpoint, runner.pending, runner.engine_parked = 'CPU_no_endpoint', None, True
    runner.bootstrap.update(replicas=replicas, reference=dict(text='CPU reference'), ownership_prefix='CPU/')
    runner.command = lambda *args: None
    runner.parked_evidence = lambda: [dict(CPU_fixture=True)] * 2
    runner.check_park = lambda *args: dict(passed=True, CPU_fixture=True)
    def gate(*args, **kwargs):
        calls.append(('gate', runner.engine_parked))
        return dict(passed=True, CPU_fixture=True)
    runner.gate = gate
    def golden():
        calls.append(('golden', runner.engine_parked))
        return dict(text='CPU reference')
    runner.golden = golden
    class Cluster:
        def inventory(self, compact=False):
            return workers, replicas
        def transfer(self, plan, **kwargs):
            calls.append(('transfer', kwargs))
            return dict(tx='CPU_tx', plan=plan, execution=execution)
        def verify(self, tx):
            calls.append(('verify', (flow_dir / 'CPU_Hremote_raw.json').exists(), runner.engine_parked))
            return dict(passed=True, checked_bytes=2*RANK_BYTES)
        def finish(self, tx):
            calls.append(('finish', tx))
    runner.cluster = Cluster()
    def engine(*args, **kwargs):
        calls.append(('resume_ACK', runner.engine_parked))
        return dict(CPU_fixture=True)
    def park():
        runner.engine_parked = True
        calls.append(('park', True))
    runner.park = park
    def fixture_write(path, value):
        value = dict(value, CPU_fixture=True)
        if 'evidence_kind' in value:
            value['evidence_kind'] = 'CPU_fixture'
        controller_adapter.native.write(path, value)
    with patch.object(controller_adapter.native, 'engine_call', engine), \
         patch.object(controller_adapter, 'wait_for_exports', lambda *args, **kwargs: None), \
         patch.object(controller_adapter, 'summarize', lambda *args, **kwargs: dict(CPU_fixture=True)), \
         patch.object(controller_adapter, 'write', fixture_write):
        online = runner.online('Hremote', 'CPU_Hremote', context)
    check('real_adapter_online_retains_native_live_semantics', next(row[1] for row in calls if row[0] == 'transfer') == dict(verify_before_ready=False, retain=True))
    check('inclusive_clock_raw_exists_before_fullSHA', next(row[1:] for row in calls if row[0] == 'verify') == (True, False))
    check('postready_live_resource_gate_precedes_verification', ('gate', False) in calls and calls.index(('gate', False)) < next(i for i, row in enumerate(calls) if row[0] == 'verify'))
    check('ready_resource_evidence_persisted_before_park', (flow_dir / 'CPU_Hremote_ready_resource_gate.json').exists() and calls[-1][0] == 'gate' and online['engine_parked'])
    check('integration_fixture_cannot_claim_real_model_evidence', json.loads((flow_dir / 'CPU_Hremote.json').read_text())['evidence_kind'] == 'CPU_fixture')
    capture_dir = args.out / 'CPU_capture_one_rank_failure'
    capture_dir.mkdir()
    runner.out = capture_dir
    runner.args = SimpleNamespace(binding=capture_dir / 'binding.json', plan=capture_dir / 'plan.json')
    runner.args.plan.write_text(json.dumps(runner.plan))
    runner.args.binding.write_text(json.dumps(dict(session='CPU_fixture', workflow=WORKFLOW, status='passed', plan_ref=file_ref(runner.args.plan))))
    def capture(endpoint, operation, body, **kwargs):
        rank = 0 if endpoint == 'CPU_rank0' else 1
        if rank == 1:
            raise RuntimeError('CPU injected rank1 capture failure')
        return [dict(manifest=manifests[0], verification=dict(passed=True, checked_bytes=RANK_BYTES))]
    runner.endpoints = ['CPU_rank0', 'CPU_rank1', 'CPU_helper']
    with patch.object(controller_adapter, 'request', capture), \
         patch.object(controller_adapter, 'wait_for_exports', lambda *args, **kwargs: None), \
         patch.object(controller_adapter, 'write', fixture_write):
        try:
            runner.setup()
        except RuntimeError as exc:
            check('injected_rank_failure_preserved', 'rank1 capture failure' in str(exc))
        else:
            check('injected_rank_failure_preserved', False)
    check('rank0_success_survives_rank1_failure', (capture_dir / 'checkpoint_capture_rank0.json').exists() and (capture_dir / 'checkpoint_capture_rank1_failed.json').exists())
    check('partial_capture_cannot_publish_bootstrap', not (capture_dir / 'bootstrap.json').exists())
    source_refs = [file_ref(HERE / name) for name in ('simple_controller.py', 'simple_contracts.py', 'simple_model.py', 'check_simple_core_cpu.py')]
    report = dict(status='CPU_passed', checks=checks, check_count=len(checks), new_GPU_samples=0,
                  actual_model_online_qualified=False, source_refs=source_refs,
                  model_fixture_predictions={name: predict(plans[name], workers, model, state) for name in ('Hnear', 'Hremote')},
                  fixture_validation=result, no_SSH_Docker_GPU_actions=True)
    (args.out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.out / 'report.md').write_text(f'CPU 检查 {len(checks)} 项通过。固定完整 TP2 66 段、单发送者 worker2、远端 NUMA4/7；逐路径 1GiB 预热证据绑定，805120688B/1GiB 精确服务时间查表、非单调正服务样本、原生 DAG 串行、C0 一次残差和留出前冻结均通过。未知大小直接拒绝，不做带宽拟合或插值，复杂模式全局函数未改动。夹具不是硬件样本，实际整体 10% 资格仍未知。\n')
    print(json.dumps(dict(status=report['status'], checks=len(checks), GPU_samples=0, out=str(args.out))))


if __name__ == '__main__':
    main()
