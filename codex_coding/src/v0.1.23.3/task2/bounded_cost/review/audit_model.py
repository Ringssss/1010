"""Independent CPU arithmetic and rejection audit; no physical launch attestation."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[6]
MODEL = ROOT / 'codex_coding/src/v0.1.23.3/task2/bounded_cost/model/predictor.py'
OLD = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration/execution_actual_r02/measure'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('audited_predictor', MODEL)
    predictor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(predictor)
    package = predictor.load_frozen(args.model)
    model = package['model']
    original = json.loads((OLD / 'training.json').read_text())
    retry = json.loads((OLD / 'training_repeats.json').read_text())
    aggregate = json.loads((OLD / 'fit_input_observed.json').read_text())
    checks = []

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})

    raw = original + retry
    local = [r['service_s'] for r in raw if r['path'] == '4:hbm:0->4' and r['operations'] == 1]
    check('local_all_four_single_observations_constant', model['paths']['4:hbm:0->4']['operation_s'] == statistics.mean(local))
    check('local_bandwidth_unidentified', model['paths']['4:hbm:0->4']['warm_GBps'] is None and model['paths']['4:hbm:0->4']['byte_slope_s_per_byte'] is None)
    expected_request = statistics.median(r['target_s'] - r['service_s'] for r in aggregate if r['operations'] == 1)
    check('single_operation_worker_outer_residual_only', model['request_s'] == expected_request)
    check('dispatch_all_nine_aggregate_weighting', model['dispatch_s'] == statistics.median(r['dispatch_s'] for r in aggregate))
    check('full_request_control_original_nine_weighting', model['full_request_overhead']['to_filled_without_execute_s'] == statistics.median(r['request_timing']['request_to_filled_s'] - r['flow']['execute_wall_s'] for r in original))
    for path, cost in model['paths'].items():
        if path == '4:hbm:0->4':
            continue
        small, large = [next(r for r in aggregate if r['path'] == path and r['label'] == label) for label in ('warm_small', 'warm_large')]
        slope = (large['service_s'] - small['service_s']) / (large['nbytes'] - small['nbytes'])
        intercept = small['service_s'] - slope * small['nbytes']
        check(path + '_independent_positive_slope', slope > 0 and abs(cost['byte_slope_s_per_byte'] - slope) < 1e-24)
        check(path + '_independent_intercept', abs(cost['operation_s'] - max(0., intercept)) < 1e-15)
    check('model_prior_and_new_holdouts_unfitted', not package['independent_validation_complete'] and not package['decision_ready'] and not any('validation' in r['path'] for r in package['source_refs'].values()))

    workers = copy.deepcopy(json.loads((OLD / 'final_inventory.json').read_text())['workers'])
    for index, worker in enumerate(workers):
        worker.update(session='review_cpu_fixture_not_physical', epoch='fixture_epoch_' + str(index))
    source_lineage = {path: dict(replica_id='review_fixture_source_' + path, identity='review_fixture_identity_' + path,
                                generation=1, source_epoch=workers[cost['endpoints'][0]]['epoch'],
                                worker=cost['endpoints'][0], tier=cost['source_tier'], numa=cost['source_numa'])
                      for path, cost in model['paths'].items()}
    evidence = dict(physical_gate_passed=True, config_verified=True, source_lineage_verified=True,
                    control_mode='cached', executor='serial', chunk_bytes=32 << 20,
                    worker_physical=predictor.physical(workers), worker_incarnations=predictor.incarnations(workers),
                    model_source_ref=package['artifact_ref'], warm_pairs=['2:4', '4:5'],
                    source_lineage=source_lineage, fixture_only=True, physical_execution_certified=False)
    evidence_path = args.out / 'cpu_fixture_attestation.json'
    evidence_path.write_text(json.dumps(evidence, indent=2) + '\n')
    attestation = dict(evidence, evidence_ref=predictor.file_ref(evidence_path))
    bound = predictor.bind_session(package, workers, attestation)
    state = dict(incarnations=predictor.incarnations(workers), warm_pairs=['2:4', '4:5'])
    order = ['4:hbm:0->4', '2:dram:4->4', '4:ssd:0->5']
    ops = [dict(op_id=i, path=p, src_w=model['paths'][p]['endpoints'][0], dst_w=model['paths'][p]['endpoints'][1], n=8 << 20,
                kind=model['paths'][p]['operation_kind'], **{key: source_lineage[p][key] for key in ('replica_id', 'identity', 'generation', 'source_epoch')})
           for i, p in enumerate(order)]
    plan = dict(executor='serial', chunk_bytes=32 << 20, ops=ops)
    prediction = predictor.predict(plan, workers, bound, state)
    expected_service = sum(model['paths'][p]['operation_s'] + (0 if p == order[0] else (8 << 20) * model['paths'][p]['byte_slope_s_per_byte']) for p in order)
    check('mixed_shared_worker_chain_service_sum', abs(prediction['estimated_service_s'] - expected_service) < 1e-15)
    check('mixed_request_added_once', abs(prediction['estimated_worker_s'] - expected_service - expected_request) < 1e-15)
    check('mixed_dispatch_added_once', abs(prediction['estimated_execute_s'] - expected_service - expected_request - model['dispatch_s']) < 1e-15)
    check('prediction_does_not_certify_decisions', prediction['decision_ready'] is False and prediction['physical_capacity_certified'] is False)
    check('published_prediction_unidentified', prediction['estimated_full_published_s'] is None)

    def reject(name, changed_plan=plan, changed_workers=workers, changed_model=bound, changed_state=state):
        try:
            predictor.predict(changed_plan, changed_workers, changed_model, changed_state)
        except (ValueError, KeyError):
            check(name, True)
        else:
            check(name, False)

    for size in (1 << 20, 17 << 20):
        altered = copy.deepcopy(plan); altered['ops'][0]['n'] = size
        reject('reject_size_' + str(size), changed_plan=altered)
    altered = copy.deepcopy(plan); altered['ops'] = [ops[0], dict(ops[0], op_id=3)]
    reject('reject_repeated_path', changed_plan=altered)
    reject('reject_cold_pair', changed_state=dict(state, warm_pairs=[]))
    changed_workers = copy.deepcopy(workers); changed_workers[0]['epoch'] = 'stale'
    reject('reject_stale_worker_epoch', changed_workers=changed_workers)
    altered = dict(plan, chunk_bytes=64 << 20)
    reject('reject_chunk_mismatch', changed_plan=altered)
    changed_model = copy.deepcopy(bound); changed_model['request_s'] += 1e-6
    reject('reject_changed_coefficients', changed_model=changed_model)
    report = dict(status='passed' if all(c['passed'] for c in checks) else 'failed', checks=checks,
                  model_ref=predictor.file_ref(args.model), predictor_ref=predictor.file_ref(MODEL),
                  CPU_only=True, GPU_or_SSH=False, physical_launch_ready=False,
                  mixed8_prediction=prediction, request_residual_range_s=[min(r['target_s'] - r['service_s'] for r in aggregate if r['operations'] == 1), max(r['target_s'] - r['service_s'] for r in aggregate if r['operations'] == 1)])
    (args.out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(checks), 'out': str(args.out)}))
    if report['status'] != 'passed':
        sys.exit(1)


if __name__ == '__main__':
    main()
