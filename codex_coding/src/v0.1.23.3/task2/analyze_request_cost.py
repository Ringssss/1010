"""Audit independent request-cost tests and finite-proposal layout selection."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def main(args):
    runs = [json.loads((d/'summary.json').read_text()) for d in (args.calibration, args.current, args.selected)]
    for r in runs:
        assert r['status'] == 'passed' and not r['cleanup_errors'] and not r['source_hash_mismatches']
    assert all(r['service_model'] == runs[0]['service_model'] for r in runs)
    changed = [p for p, sha in runs[-1]['code_sha256'].items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest() != sha]
    assert not changed, changed
    accuracy = []
    for run in runs:
        for r in run['validation']:
            assert r['flow']['verification']['passed']
            actual = r['flow']['execute_wall_s']; predicted = r['prediction']['estimated_execute_s']
            accuracy.append({'case': r['label'], 'actual_s': actual, 'predicted_s': predicted,
                'absolute_error_ms': 1000*abs(predicted-actual), 'relative_error_pct': 100*abs(predicted-actual)/actual,
                'verified_bytes': r['flow']['verification']['checked_bytes']})
    selection = runs[2]['placement']
    assert selection['solver']['status'] == 'service_reranked' and runs[2]['applied']['status'] == 'applied'
    comparisons = {}
    for phase in ('cold', 'warm'):
        a = next(r for r in accuracy if r['case'] == 'current_'+phase)
        b = next(r for r in accuracy if r['case'] == 'selected_'+phase)
        comparisons[phase] = {'current_s': a['actual_s'], 'selected_s': b['actual_s'],
                              'change_pct': 100*(b['actual_s']/a['actual_s']-1)}
    archives = json.loads((ROOT/'codex_coding/results/v0.1.23.3/task2/archive_baseline_0930.json').read_text())
    archive_changed = [r['path'] for r in archives if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest() != r['sha256']]
    assert not archive_changed
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'passed': True, 'accuracy': accuracy,
        'service_model': runs[0]['service_model'], 'selection': selection['solver'],
        'layout_decision': selection['decision'], 'comparisons': comparisons, 'archive_changed': archive_changed,
        'limitations': ['one sample per case; different isolated worker sessions for current and selected layouts',
                       'future cold pair policy explicitly requested, not a prediction of automatic runtime readiness',
                       'finite proposal reranking; bandwidth MILP objective is not globally replaced',
                       'two calibrated paths; no concurrent shared-link contention or full-model validation',
                       'request overhead is pooled across paths; negative fitted terms are explicitly clamped']}
    (args.selected/'delivery.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ('passed', 'accuracy', 'layout_decision', 'comparisons')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('calibration', 'current', 'selected'):
        parser.add_argument('--'+name, type=Path, required=True)
    main(parser.parse_args())
