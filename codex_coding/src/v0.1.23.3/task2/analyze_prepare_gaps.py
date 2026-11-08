"""Reconcile event boundaries with leaf-call tracing, including time before trace startup."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def main(directory):
    reports = []
    for path in sorted((directory/'prepare_profiles').glob('worker*.json')):
        t = json.loads(path.read_text())
        read = next(r for r in t['rows'] if r['stage'] == 'pickle_read')
        exported = Path(read['path']); phase = exported.parent.name
        rank = int(exported.stem.removeprefix('rank'))
        metrics = [json.loads(line) for line in (directory/'metrics'/phase/f'rank{rank}.jsonl').read_text().splitlines()]
        events = {e['event']: e['monotonic_ns'] for e in metrics}
        if 'target_mapped' not in events: continue
        leaves = [r for r in t['rows'] if r['stage'] != 'hbm_target_total']
        stages = defaultdict(lambda: {'wall_s': 0., 'cpu_s': 0., 'calls': 0, 'max_s': 0.})
        for r in leaves:
            s = stages[r['stage']]
            s['wall_s'] += r['wall_s']; s['cpu_s'] += r['thread_cpu_s']
            s['calls'] += 1; s['max_s'] = max(s['max_s'], r['wall_s'])
        total = (events['target_mapped']-events['target_accepted'])/1e9
        leading = (t['begin_ns']-events['target_accepted'])/1e9
        leaf_total = sum(r['wall_s'] for r in leaves)
        reports.append({'phase': phase, 'rank': rank, 'worker': t['worker'], 'mapping_window_s': total,
            'before_trace_s': leading, 'leaf_wall_s': leaf_total,
            'remaining_bookkeeping_and_instrumentation_s': total-leading-leaf_total,
            'stages': dict(stages), 'slowest_calls': sorted(leaves, key=lambda r: r['wall_s'], reverse=True)[:8],
            'gc_events': t['gc_events'], 'trace_file': str(path),
            'caution': 'before_trace includes accepted-event emission and trace initialization, not IPC import; GC intervals can overlap stages'})
    result = {'reports': reports, 'scope': 'same-host event reconciliation; no cross-host clock subtraction'}
    (directory/'prepare_gaps.json').write_text(json.dumps(result, indent=2))
    print(json.dumps([{k: r[k] for k in ('phase', 'rank', 'mapping_window_s', 'before_trace_s', 'leaf_wall_s', 'stages')} for r in reports], indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--run-dir', type=Path, required=True)
    main(p.parse_args().run_dir)
