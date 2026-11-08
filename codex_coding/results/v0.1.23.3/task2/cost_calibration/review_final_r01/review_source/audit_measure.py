"""Review actual warm calibration artifacts without importing GPU code."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster.service_cost import fit_request_model, estimate_execution, new_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, refs, errors = [], [], []

    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))

    def freeze(path):
        path = path.resolve()
        raw = path.read_bytes()
        target = args.out / 'evidence' / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        item = dict(source_path=str(path), path=str(target), sha256=hashlib.sha256(raw).hexdigest())
        refs.append(item)
        return raw

    def load(path):
        return json.loads(freeze(path))

    def same_ref(reference):
        raw = freeze(Path(reference['path']))
        return hashlib.sha256(raw).hexdigest() == reference['sha256']

    def close(x, y):
        return math.isclose(x, y, rel_tol=1e-10, abs_tol=1e-12)

    def transfer(row, label):
        flow = row['flow']
        verification = flow['verification']
        ops = flow['plan']['ops']
        proof = verification['operations']
        check(label + '_all_SHA', verification['passed'] is True and
              verification['checked_bytes'] == row['nbytes'] == sum(op['n'] for op in ops) and
              sum(op['n'] for op in proof.values()) == row['nbytes'] and
              all(len(op['source_sha256']) == 64 and op['source_sha256'] == op['target_sha256']
                  for op in proof.values()))
        timing = row['request_timing']
        inner = flow['controller_timing']
        names = ['request_begin_ns', 'inventory_end_ns', 'gate_end_ns', 'plan_end_ns', 'filled_ns', 'published_ns']
        stamps = [timing[name] for name in names]
        check(label + '_same_clock_full_window', timing['clock_domain'] == inner['clock_domain'] and
              stamps == sorted(stamps) and timing['plan_end_ns'] <= inner['load_begin_ns'] and
              timing['filled_ns'] == inner['all_targets_filled_ns'] and
              timing['published_ns'] == inner['all_targets_published_ns'] and
              close(timing['request_to_filled_s'], (stamps[-2] - stamps[0]) / 1e9) and
              close(timing['request_to_published_s'], (stamps[-1] - stamps[0]) / 1e9))
        check(label + '_serial_32MiB_chunk', flow['executor'] == 'serial' and flow['plan']['chunk_bytes'] == 32 << 20)
        check(label + '_HIP_shared_cgroup_gate', all(
            node['dram_current_bytes'] + (256 << 20) <= node['dram_limit_bytes'] == 6 << 30 and
            node['hbm_absolute_upper_bytes'] + node['planned_incremental_upper_bytes'] <= node['hbm_limit_bytes'] == 2 << 30
            for node in row['gate'].values()))
        target_workers = {op['dst_w'] for op in ops}
        target_s = max(worker['execute_s'] for worker in flow['execution'] if worker['worker'] in target_workers)
        residual = max(0., flow['execute_wall_s'] - max(worker['execute_s'] for worker in flow['execution']))
        check(label + '_target_and_residual_definition', close(row['target_s'], target_s) and close(row['dispatch_s'], residual))
        check(label + '_pending_hash', same_ref(row['pending_prediction_ref']))
        pending = load(Path(row['pending_prediction_ref']['path']))
        saved = load(args.run_dir / 'measure' / (row['label'] + '.prediction_freeze.json'))
        check(label + '_pending_before_transfer', pending['plan'] == flow['plan'] and
              pending['prediction'] == row['prediction'] and
              saved['freeze_saved_ns'] < inner['load_begin_ns'])

    root = args.run_dir
    measure = root / 'measure'
    summary = load(root / 'summary.json')
    setup = load(root / 'runtime_setup.json')
    mesh = load(root / 'mesh' / 'mesh.json')
    query = load(root / 'mesh' / 'inventory_ab.json')
    decision = load(root / 'mesh' / 'inventory_decision.json')
    protocol = load(measure / 'protocol.json')
    frozen = load(measure / 'frozen_model.json')
    training = load(measure / 'training.json')
    aggregate = load(measure / 'fit_input_observed.json')
    inputs = load(measure / 'fit_inputs.json')
    validation = load(measure / 'validation.json')
    journal = load(measure / 'journal.json')
    final = load(measure / 'final_inventory.json')
    source = ROOT / 'main_lib/fluidckpt/cluster/service_cost.py'
    freeze(source)
    original = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration/review_design_r01/evidence/main_lib/fluidckpt/cluster/service_cost.py'
    check('product_service_source_preserved', source.read_bytes() == freeze(original))
    check('actual_six_workers_nine_supervisors', summary['status'] == 'completed_pending_independent_review' and
          summary['GPU_launches'] == 6 and len(summary['workers']) == 9 and
          all(row['returncode'] == 0 for row in summary['workers'].values()) and
          all(row['passed'] for row in summary['collection'].values()))
    check('warm_pair_incarnations', mesh['necessary_pairs'] == [[2, 4], [4, 5]] and
          len(mesh['warm']) == 6 and all(row['warm_s'] >= 0 for row in mesh['warm']) and
          mesh['incarnations'] == new_state(mesh['workers'])['incarnations'])
    check('query_exact_legacy2_cached2', [row['mode'] for row in query] == ['legacy', 'legacy', 'cached', 'cached'])
    query_summary = {}
    for mode in ('legacy', 'cached'):
        selected = [row for row in query if row['mode'] == mode]
        query_summary[mode] = dict(controller_median_s=statistics.median(row['elapsed_s'] for row in selected),
            maximum_worker_median_s=statistics.median(max(w['inventory_diagnosis']['worker_inventory_elapsed_ns'] for w in row['workers']) / 1e9 for row in selected),
            maximum_library_median_s=statistics.median(max(sum(w['inventory_diagnosis']['segments_ns'][name] for name in ('library_resolution', 'cdll_load')) for w in row['workers']) / 1e9 for row in selected))
        for row in selected:
            check(mode + '_' + str(row['index']) + '_full_rpc_clock', close(row['elapsed_s'], (row['end_ns'] - row['begin_ns']) / 1e9))
            for w in row['workers']:
                d = w['inventory_diagnosis']
                check(mode + '_' + str(row['index']) + '_w' + str(w['worker']) + '_segment_conservation',
                      d['mode'] == mode and d['segment_sum_ns'] == sum(d['segments_ns'].values()) and
                      d['worker_inventory_elapsed_ns'] == d['worker_inventory_end_ns'] - d['worker_inventory_start_ns'] ==
                      d['segment_sum_ns'] + d['residual_ns'] and d['residual_ns'] >= 0)
                check(mode + '_' + str(row['index']) + '_w' + str(w['worker']) + '_cache_origin',
                      d['library_cache_hit'] == (mode == 'cached') and d['cache_origin_sequence'] <= d['sequence'])
    for index in range(6):
        group = [row['workers'][index] for row in query]
        check('query_w' + str(index) + '_fresh_counter_and_samples',
              all(group[i]['headroom_sample_ns'] < group[i + 1]['headroom_sample_ns'] and
                  group[i]['inventory_diagnosis']['sequence'] < group[i + 1]['inventory_diagnosis']['sequence']
                  for i in range(3)) and all(w['inventory_diagnosis']['fresh_fields'] for w in group))
        check('query_w' + str(index) + '_exact_stable_identity', all(
              len({json.dumps(w['runtime_identity'][key], sort_keys=True) for w in group}) == 1
              for key in ('boot_id', 'pid', 'start_ticks', 'actual_bdf', 'pci_numa_node', 'cgroup_path')))
    check('cached_measured_gate_admitted', decision['cached_admitted'] and decision['selected_mode'] == 'cached')
    expected = [('node28', 0, 0), ('node28', 1, 3), ('node30', 4, 4),
                ('node30', 5, 7), ('node27', 0, 0), ('node27', 4, 4)]
    check('actual_current_mapping', [(w['node'], w['gpu'], w['numa']) for w in mesh['workers']] == expected and
          all(w['session'] == setup['session'] for w in mesh['workers']))
    sizes = {'warm_small': (4 << 20, 1), 'warm_large': (16 << 20, 1), 'warm_batch': (4 << 20, 4)}
    check('nine_observed_train_no_cold', len(training) == 9 and
          all(row['label'] in sizes and (row['nbytes'], row['operations']) == sizes[row['label']] for row in training))
    paths = sorted({row['path'] for row in training})
    check('exact_three_paths', paths == ['2:dram:4->4', '4:hbm:0->4', '4:ssd:0->5'])
    for index, row in enumerate(training):
        # Training.json normalizes the label; the actual transfer file preserves its unique request label.
        actual = load(measure / ('p' + str(index // 3) + '_' + row['label'] + '.json'))
        transfer(actual, 'train_' + str(index))
        check('train_' + str(index) + '_record_matches_raw', all(actual[key] == row[key]
              for key in ('flow', 'target_s', 'service_s', 'dispatch_s', 'nbytes', 'operations')))
    repeats = frozen['repeated_paths']
    expected_repeats = [index for index in range(3) if training[index * 3 + 1]['target_s'] <= training[index * 3]['target_s']]
    check('retry_only_predeclared_nonpositive_paths', repeats == expected_repeats and
          'no third run' in protocol['nonincreasing_retry_rule'])
    for row in aggregate:
        if row.get('observations'):
            check('aggregate_' + row['path'] + '_' + row['label'], len(row['observations']) == 2 and
                  all(close(row[key], statistics.mean(obs[key] for obs in row['observations']))
                      for key in ('target_s', 'service_s', 'dispatch_s')))
    if repeats:
        repeated = load(measure / 'training_repeats.json')
        check('at_most_three_extra_configs_each_failed_path', len(repeated) == 3 * len(repeats))
        for row in repeated:
            raw = load(measure / (row['flow']['plan']['targets'][0]['id'].split('/')[-2] + '.json'))
            transfer(raw, raw['label'])
    cold = [row for row in inputs if row['label'] == 'cold_small']
    check('cold_inputs_explicitly_derived_unobserved', len(cold) == 3 and all(
          row['observed'] is False and row['synthetic'] is True and
          row['derived_from']['label'] == 'warm_small' and
          same_ref(row['derived_from']['fit_input_observed_ref']) for row in cold))
    check('fit_input_refs_and_training_refs', all(same_ref(frozen[key]) for key in
          ('training_ref', 'fit_input_observed_ref', 'fit_inputs_ref')))
    workers = final['workers']
    model = frozen['model']
    check('model_identified', model is not None)
    if model is not None:
        recomputed = fit_request_model(workers, inputs)
        check('original_fit_recomputed_exactly', all(model[key] == recomputed[key] for key in
              ('schema_version', 'topology_id', 'executor', 'paths', 'request_s', 'dispatch_s', 'fit_diagnostics')))
        check('warm_scope_gamma_unmeasured', model['cold_measurement_count'] == 0 and
              'unmeasured' in model['scope'] and all(cost['first_pair_s'] == 0 for cost in model['paths'].values()))
        check('training_only_public_overhead', close(model['full_request_overhead']['to_filled_without_execute_s'],
              statistics.median(row['request_timing']['request_to_filled_s'] - row['flow']['execute_wall_s'] for row in training)) and
              close(model['full_request_overhead']['verify_publish_s'], statistics.median(row['flow']['verify_and_publish_s'] for row in training)))
    preregistered = load(measure / 'holdout_preregistered_prediction.json')
    model_freeze = load(measure / 'model_freeze.json')
    check('unique_cached_holdout_and_legacy_control', [row['label'] for row in validation] ==
          ['holdout_prediction', 'holdout_legacy_control'])
    for row in validation:
        transfer(row, row['label'])
        ops = row['flow']['plan']['ops']
        check(row['label'] + '_unseen_mixed_dependency', len(ops) == 3 and
              [op['n'] for op in ops] == [12 << 20] * 3 and
              [(op['src_w'], op['dst_w']) for op in ops] == [(4, 4), (2, 4), (4, 5)])
    cached = validation[0]
    legacy = validation[1] if len(validation) > 1 else None
    check('model_and_prediction_frozen_before_request', same_ref(cached['frozen_model_ref']) and
          same_ref(cached['preregistered_prediction_ref']) and
          model_freeze['freeze_ns'] <= preregistered['saved_ns'] < cached['request_timing']['request_begin_ns'])
    if model is not None:
        state = new_state(workers)
        state['warm_pairs'] = ['2:4', '4:5']
        prediction = estimate_execution(cached['flow']['plan'], workers, model, state)
        check('holdout_original_DAG_prediction_exact', prediction == cached['prediction'] == preregistered['prediction'])
        timeline = prediction['timeline']
        check('holdout_source_receiver_ready_dependency', timeline[1]['start_s'] == timeline[0]['end_s'] and
              timeline[2]['start_s'] == timeline[1]['end_s'])
        for name, predicted, actual in [('execute', prediction['estimated_execute_s'], cached['flow']['execute_wall_s']),
                ('worker', prediction['estimated_worker_s'], max(w['execute_s'] for w in cached['flow']['execution'])),
                ('workflow_filled', cached['predicted_request_to_filled_s'], cached['request_timing']['request_to_filled_s']),
                ('workflow_published', cached['predicted_request_to_published_s'], cached['request_timing']['request_to_published_s'])]:
            errors.append(dict(metric=name, predicted_s=predicted, actual_s=actual,
                               signed_relative_error=(predicted - actual) / actual, absolute_error_s=abs(predicted - actual)))
    if legacy:
        def signature(row):
            return [{k: op[k] for k in ('replica_id', 'generation', 'source_epoch', 'identity', 'tp',
                                       'src_w', 'dst_w', 'n', 'path', 'source_offset', 'off')}
                    for op in row['flow']['plan']['ops']]
        check('legacy_identical_source_ranges_order', signature(cached) == signature(legacy))
        check('only_cached_predicted', legacy['prediction'] is None and legacy['frozen_model_ref'] is None)
        check('fixed_cached_then_legacy_order', cached['request_timing']['published_ns'] < legacy['request_timing']['request_begin_ns'])
    check('exact_owned_final_cleanup', journal['status'] == 'complete' and not journal.get('cleanup_failure') and
          not final['replicas'] and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in workers) and
          all(row['files']==0 for row in final['ssd'].values()))
    result = dict(status='passed' if all(row['passed'] for row in checks) else 'failed', checks=checks,
                  errors=errors, source_refs=refs, repeats=repeats, query_summary=query_summary,
                  fit_diagnostics=model['fit_diagnostics'] if model else frozen['fit_diagnostics'], CPU_only=True,
                  cold_gamma_observed=False, shared_capacity_certified=False, physical_peak_measured=False,
                  layout_sorting_certified=False, statistical_query_causality_certified=False,
                  validation_scope='one preregistered warm mixed holdout; legacy is a fixed-order end-to-end observation')
    (args.out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(status=result['status'], checks=len(checks), errors=errors)))
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
