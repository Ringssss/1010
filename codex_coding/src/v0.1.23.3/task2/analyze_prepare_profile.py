"""Attribute target preparation without summing nested intervals or parallel ranks."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def main(directory):
    data = json.loads((directory/'summary.json').read_text())
    reports = []
    for sample in data.get('restores', []):
        tx = sample['flow']['tx']
        for w in sample['flow']['plan']['targets']:
            path = directory/'prepare_profiles'/f"worker{w['worker']}_{tx}.json"
            trace = json.loads(path.read_text())
            totals = defaultdict(lambda: {'wall_s': 0., 'cpu_s': 0., 'calls': 0})
            for row in trace['rows']:
                t = totals[row['stage']]
                t['wall_s'] += row['wall_s']; t['cpu_s'] += row['thread_cpu_s']; t['calls'] += 1
            events = {r['event']: r['monotonic_ns'] for r in sample['timing']['ranks'][str(w['tp'])]['events']}
            start, end = events['target_accepted'], events['target_mapped']
            gc_starts, gc_intervals = {}, []
            for e in trace['gc_events']:
                key = e['thread'], e['generation']
                if e['phase'] == 'start': gc_starts[key] = e['time_ns']
                elif key in gc_starts:
                    a, b = max(start, gc_starts.pop(key)), min(end, e['time_ns'])
                    if b > a: gc_intervals.append({'seconds': (b-a)/1e9, 'thread': key[0], 'generation': key[1]})
            reports.append({'variant': sample['variant'], 'worker': w['worker'], 'tp': w['tp'],
                'mapping_window_s': (end-start)/1e9, 'stages': dict(totals), 'gc_intervals': gc_intervals,
                'slow_leaf_calls': sorted([r for r in trace['rows'] if r['stage'] != 'hbm_target_total'],
                                          key=lambda r: r['wall_s'], reverse=True)[:12]})
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'run_status': data['status'], 'reports': reports,
              'scope': 'diagnostic replay, not an uninstrumented performance comparison; nested stages and GC intervals overlap'}
    (directory/'prepare_analysis.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--run-dir', type=Path, required=True)
    main(p.parse_args().run_dir)
