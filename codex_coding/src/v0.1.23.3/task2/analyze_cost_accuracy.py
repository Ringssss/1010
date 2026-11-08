"""Report held-out cost accuracy; never refit using validation measurements."""
import argparse
import json
from pathlib import Path


def main(args):
    training = json.loads((args.calibration/'summary.json').read_text())
    validation = json.loads((args.validation/'summary.json').read_text())
    assert training['status'] == validation['status'] == 'passed'
    assert not training['cleanup_errors'] and not validation['cleanup_errors']
    assert not training['source_hash_mismatches'] and not validation['source_hash_mismatches']
    assert training['service_model'] == validation['service_model']
    # Original first-sample linear formulation, fitted to the same cold sample.
    cold = {r['path']: r for r in training['samples'] if r['label'] == 'cold_small'}
    rows = []
    for data in (training, validation):
        for r in data['validation']:
            f = r['flow']; old_targets = {}
            for op in f['plan']['ops']:
                sample = cold[op['path']]
                old_targets[op['dst_w']] = old_targets.get(op['dst_w'], 0)+op['n']/sample['nbytes']*sample['flow']['execute_wall_s']
            old = max(old_targets.values()); new = r['prediction']['estimated_execute_s']; actual = f['execute_wall_s']
            rows.append({'case': r['label'], 'old_first_sample_linear_s': old, 'corrected_s': new, 'actual_s': actual,
                         'old_abs_error_s': abs(old-actual), 'corrected_abs_error_s': abs(new-actual),
                         'corrected_relative_error_pct': 100*abs(new-actual)/actual,
                         'verified_bytes': f['verification']['checked_bytes'], 'timeline': r['prediction']['timeline']})
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'service_model': training['service_model'], 'held_out': rows,
              'scope': 'two directed source-tier paths, isolated serial executor, two warm fit sizes; predictions saved before held-out execution',
              'limitations': ['cold initialization remains session-dependent', 'not a full-model loading prediction or an optimized placement MILP',
                              'no shared-link contention calibration; fixed worker-order dependencies only'],
              'baseline_definition': 'same calibration cold sample scaled linearly by bytes, matching the old rate-only formulation'}
    (args.validation/'accuracy.json').write_text(json.dumps(result, indent=2))
    print(json.dumps([{k: v for k, v in r.items() if k != 'timeline'} for r in rows], indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--validation', type=Path, required=True)
    main(parser.parse_args())
