"""Freeze coefficients from the prior preregistered training, without holdouts."""
import argparse
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster.service_cost import fit_path
from predictor import CHUNK_BYTES, MIB, digest, file_ref, physical, require

OLD = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration/execution_actual_r02/measure'


def build():
    names = ('training', 'training_repeats', 'fit_input_observed', 'frozen_model', 'final_inventory')
    data = {n: json.loads((OLD / (n + '.json')).read_text()) for n in names}
    raw = data['training'] + data['training_repeats']
    require(len(data['training']) == 9 and len(data['training_repeats']) == 3, 'unexpected training count')
    require(all(r['path'] == '4:hbm:0->4' for r in data['training_repeats']), 'unexpected retry path')
    aggregate = data['fit_input_observed']
    require(len(aggregate) == 9, 'unexpected aggregate count')
    for row in aggregate:
        points = [r for r in raw if (r['path'], r['label']) == (row['path'], row['label'])]
        for key in ('target_s', 'service_s', 'dispatch_s'):
            require(row[key] == statistics.mean(r[key] for r in points), 'aggregation differs from all prescribed observations')
    paths, diagnostics = {}, {}
    for path in sorted({r['path'] for r in aggregate}):
        by = {r['label']: r for r in aggregate if r['path'] == path}
        small, large = by['warm_small'], by['warm_large']
        endpoints = [small['flow']['plan']['ops'][0][k] for k in ('src_w', 'dst_w')]
        diagnostic = {'raw_service_slope_s_per_byte': (large['service_s'] - small['service_s']) / (12 * MIB),
                      'raw_target_slope_s_per_byte': (large['target_s'] - small['target_s']) / (12 * MIB),
                      'single_op_fit_bytes': [4 * MIB, 16 * MIB],
                      'batch_qualification': {'total_bytes': 4 * MIB, 'operations': 4,
                                              'target_s': by['warm_batch']['target_s'],
                                              'prediction_domain_admitted': False}}
        if path == '4:hbm:0->4':
            values = [r['service_s'] for r in raw if r['path'] == path and r['operations'] == 1]
            require(len(values) == 4 and diagnostic['raw_service_slope_s_per_byte'] < 0, 'local plateau premises changed')
            cost = {'kind': 'bounded_constant', 'operation_s': statistics.mean(values),
                    'byte_slope_s_per_byte': None, 'warm_GBps': None, 'first_pair_s': None,
                    'empirical_service_range_s': [min(values), max(values)],
                    'fit_rule': 'Arithmetic mean of all four original plus prescribed-retry single-op service observations.'}
            diagnostic['raw_single_op_service_s'] = values
            diagnostic['byte_slope_status'] = 'unidentified; negative raw slope retained; no bandwidth inferred'
        else:
            old_fit = fit_path(small, small, large)
            require(old_fit == data['frozen_model']['fit_diagnostics']['schema1_paths'][path], 'existing fit changed')
            cost = dict(old_fit, kind='effective_affine', byte_slope_s_per_byte=1 / (old_fit['warm_GBps'] * 1e9))
            cost['first_pair_s'] = None
            diagnostic['fit_rule'] = 'Existing fit_path using unchanged aggregated service_s, warm-only; cold is unmeasured.'
        cost.update(endpoints=endpoints, single_op_domain_bytes=[4 * MIB, 16 * MIB],
                    operation_kind=small['flow']['plan']['ops'][0]['kind'],
                    source_tier=path.split(':')[1], source_numa=int(path.split(':')[2].split('->')[0]))
        paths[path] = cost
        diagnostics[path] = diagnostic
    single = [r for r in aggregate if r['operations'] == 1]
    residuals = [{'path': r['path'], 'label': r['label'], 'target_s': r['target_s'],
                  'service_s': r['service_s'], 'target_minus_service_s': r['target_s'] - r['service_s']} for r in single]
    require(all(r['target_minus_service_s'] >= 0 for r in residuals), 'negative request residual')
    # Keep the prior workflow control estimates and original nine-request weighting.
    original = data['training']
    control = {'to_filled_without_execute_s': statistics.median(r['request_timing']['request_to_filled_s'] - r['flow']['execute_wall_s'] for r in original),
               'verify_publish_s': statistics.median(r['flow']['verify_and_publish_s'] for r in original),
               'rule': 'Original nine training-request medians, same prior cached workflow; diagnostic portability hypothesis.',
               'publication_size_model_identified': False}
    model = {'schema_version': 'bounded_warm_serial_v1', 'worker_physical': physical(data['final_inventory']['workers']),
             'executor': 'serial', 'chunk_bytes': CHUNK_BYTES, 'control_mode': 'cached', 'paths': paths,
             'request_s': statistics.median(r['target_minus_service_s'] for r in residuals),
             'dispatch_s': statistics.median(r['dispatch_s'] for r in aggregate),
             'request_rule': 'Median of six aggregate single-op target-minus-service residuals, added once after the DAG; shared-overhead hypothesis.',
             'dispatch_rule': 'Median of nine aggregate training controller-minus-maximum-worker residuals; not exact dispatch decomposition.',
             'full_request_overhead': control,
             'domain': {'single_op_bytes': [4 * MIB, 16 * MIB], 'max_ops_per_path': 1, 'max_request_ops': 3,
                        'warm_only': True, 'required_binding_warm_pairs': ['2:4', '4:5'],
                        'batch_1mib_qualification_separate': True, 'simultaneous_shared_contention_certified': False},
             'scope': 'Bounded single-op effective service, serial source/target dependencies, warm-only explicit incarnation binding; no solver or capacity claim.'}
    refs = {n: file_ref(OLD / (n + '.json')) for n in names}
    refs.update(predictor=file_ref(HERE / 'predictor.py'), freeze=file_ref(__file__),
                existing_fit_source=file_ref(ROOT / 'main_lib/fluidckpt/cluster/service_cost.py'))
    return {'schema': 'bounded_cost_frozen_v1', 'model': model, 'model_sha256': digest(model), 'source_refs': refs,
            'fit_diagnostics': diagnostics, 'request_residuals': residuals,
            'prior_failure': {'model': None, 'schema2_failure': data['frozen_model']['fit_diagnostics']['schema2_failure'],
                              'unchanged_original_ref': refs['frozen_model']},
            'excluded_data': 'Prior validation and both 36MiB holdout timings are never read by this builder.',
            'independent_validation_complete': False, 'decision_ready': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    frozen = build()
    (out / 'frozen_model.json').write_text(json.dumps(frozen, indent=2, sort_keys=True, allow_nan=False) + '\n')
    print(json.dumps({'output': file_ref(out / 'frozen_model.json'), 'model_sha256': frozen['model_sha256']}))


if __name__ == '__main__':
    main()
