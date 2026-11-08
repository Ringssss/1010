"""Diagnose existing execution costs; no GPU work or modification of measured sources."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def inspect_flow(flow):
    records = {(w['worker'], r['op_id']): r for w in flow['execution'] for r in w['records']}
    target_totals = {}
    operations = []
    for op in flow['plan']['ops']:
        source = records[(op['src_w'], op['op_id'])]['elapsed_s']
        target = records[(op['dst_w'], op['op_id'])]['elapsed_s']
        rate = flow['plan']['profile'].get('directed_path_rates', {}).get(op['path'])
        operations.append({'op_id': op['op_id'], 'path': op['path'], 'bytes': op['n'],
            'source_observed_s': source, 'target_observed_s': target,
            'endpoint_duration_difference_s': source-target,
            'profile_rate_GBps': rate, 'linear_prediction_s': op['n']/1e9/rate if rate else None})
        target_totals[op['dst_w']] = target_totals.get(op['dst_w'], 0)+target
    maximum = max(w['execute_s'] for w in flow['execution'])
    return {'prediction_s': flow['plan']['estimated_transfer_s'], 'execute_wall_s': flow['execute_wall_s'],
        'error_s': flow['execute_wall_s']-flow['plan']['estimated_transfer_s'],
        'max_worker_execute_s': maximum, 'wall_minus_max_worker_s': flow['execute_wall_s']-maximum,
        'target_operation_sums_s': target_totals, 'operations': operations,
        'outside_execute': {'prepare_s': flow['prepare_s'], 'verify_publish_s': flow['verify_and_publish_s']},
        'caution': 'endpoint intervals overlap; differences are evidence of skew/waiting, not independent additive costs'}


def main(out):
    root = ROOT/'codex_coding/results/v0.1.23.3/task2'
    multi = json.loads((root/'multi03/summary.json').read_text())['multi_acceptance']
    discovery = json.loads((root/'discovery01/summary.json').read_text())
    full = json.loads((root/'cluster06/analysis.json').read_text())
    restores = {r['scenario']: inspect_flow(r['flow']) for r in multi['restores']}
    comparisons = []
    probe = multi['feedback_measurement']
    path = probe['plan']['ops'][0]['path']
    old = next(r for r in discovery['path_samples'] if r['task']['path'] == path)
    comparisons.append({'path': path, 'bytes': old['task']['nbytes'],
        'discovery_execute_s': old['flow']['execute_wall_s'], 'later_isolated_execute_s': probe['execute_wall_s'],
        'discovery_GBps': old['task']['nbytes']/1e9/old['flow']['execute_wall_s'],
        'later_GBps': old['task']['nbytes']/1e9/probe['execute_wall_s'],
        'qualification': 'same physical path and size, different worker sessions; not a controlled cold/warm experiment'})
    logs = []
    for name in ('worker_node29_0.log', 'worker_node27_3.log'):
        path = root/'multi03'/name
        hits = [{'line': i, 'text': line} for i, line in enumerate(path.read_text().splitlines(), 1)
                if ('Init START' in line or 'Init COMPLETE' in line or 'cluster_op_begin' in line or 'cluster_op_end' in line)]
        logs.append({'file': str(path.relative_to(ROOT)), 'events': hits})
    report = {'development': 'v0.1.23.3', 'task': 'task2', 'source': 'existing artifacts only; no new GPU measurements',
        'restores': restores, 'post_feedback_restore': inspect_flow(multi['feedback_restore']),
        'same_size_path_comparisons': comparisons, 'full_model': {k: full['models'][0][k] for k in
            ('load_s', 'execute_wall_s', 'predicted_transfer_s', 'segments_s')},
        'log_evidence': logs,
        'findings': [
            'First-use communication initialization is present inside the execute window and absent from the linear byte/rate objective.',
            'A one-size first-use sample folds fixed initialization into bandwidth; scaling that rate to smaller byte counts scales fixed cost incorrectly.',
            'Sender duration can include waiting for the receiver to finish earlier operations; summing both endpoints double counts.',
            'Discovery topology evidence can transfer between compatible sessions, but communicator readiness cannot.',
            'The current EWMA mixes first-use and established-communication rates instead of conditioning on execution state.',
            'The full-model calibrated result is close for transfer; its layout/export/control costs remain outside the transfer objective.',
            'Migration break-even figures use assumed verification/fixed costs and uncertain predicted savings; they are not measured break-even guarantees.'
        ],
        'next_model': {'operation_service': 'pair_first_use_s + per_op_s + bytes / warm_GBps + chunks * sync_overhead_s',
            'execution': 'schedule operation dependencies across both participating workers; charge each communication pair activation once per incarnation',
            'loading': 'add nonoverlapping layout, export, map, dispatch and readiness segments to execution',
            'calibration': 'same disposable worker session, same directed path, two sizes with first/warm state labeled; usually one sample per state',
            'optimization': 'retain bandwidth resource constraints; model pair activation with binary variables, or explicitly prewarm before the load timer and charge its cost elsewhere',
            'limits': 'current logs cannot uniquely split RCCL initialization, registration, synchronization and transport; cold penalties here are observations, not fitted constants'}}
    out.mkdir(parents=True, exist_ok=False)
    (out/'analysis.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: {x: v[x] for x in ('prediction_s', 'execute_wall_s', 'max_worker_execute_s', 'wall_minus_max_worker_s')}
                      for k, v in restores.items()}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    main(parser.parse_args().out)
