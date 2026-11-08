"""Plot measured lifecycle boundaries; input is run_lifecycle.py's summary.json."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads((args.run_dir/'summary.json').read_text())
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    samples = data['memory_samples']
    clock_path = args.run_dir/'clock_offsets.json'
    clocks = json.loads(clock_path.read_text()) if clock_path.exists() else {}
    origin = samples[0]['time']
    for node in ['node27', 'node29']:
        monitor = args.run_dir/f'{node}_hbm.jsonl'
        if monitor.exists():
            first = json.loads(monitor.read_text().splitlines()[0])
            origin = min(origin, (first['wall_ns']-clocks.get(node, {}).get('offset_ns', 0))/1e9)
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), constrained_layout=True)
    for node, gpu in [('node27', 0), ('node27', 1), ('node29', 0)]:
        points = [(s['time']-origin, (w['hbm_total_bytes']-w['headroom_bytes']['hbm'])/2**30)
                  for s in samples for w in s['workers'] if w['node'] == node and w['gpu'] == gpu]
        monitor = args.run_dir/f'{node}_hbm.jsonl'
        if monitor.exists():
            rows = [json.loads(line) for line in monitor.read_text().splitlines()]
            topology = data['preflight'][node]['topology']['gpus']
            card = next(g['drm'][0] for g in topology if g['logical_gpu'] == gpu)
            offset = clocks.get(node, {}).get('offset_ns', 0)
            points = [((r['wall_ns']-offset)/1e9-origin, r['vram_used_bytes'][card]/2**30)
                      for r in rows if card in r['vram_used_bytes']]
            axes[0].plot(*zip(*points), label=f'{node} DCU{gpu}', alpha=.8)
        else:
            axes[0].scatter(*zip(*points), s=20, label=f'{node} DCU{gpu} (boundary samples)')
    axes[0].set(xlabel='Seconds from first memory observation', ylabel='Physical HBM used (GiB)',
                title='Physical HBM usage (includes all processes and service reserve)')
    axes[0].legend()
    transitions = data['transitions']
    times = [t['first_correct_token_s'] for t in transitions]
    colors = ['#d08050' if t['mode'] == 'disk' else '#4588a5' if t['mode'] == 'restore' else '#75a85a' for t in transitions]
    bars = axes[1].bar(np.arange(len(transitions)), times, color=colors)
    axes[1].set_xticks(np.arange(len(transitions)), [t['phase'] for t in transitions], rotation=30, ha='right')
    axes[1].set(ylabel='Resume request to first correct token (s)', title='Measured eager-mode recovery, one sample per transition')
    for b, t in zip(bars, times): axes[1].text(b.get_x()+b.get_width()/2, t, f'{t:.2f}', ha='center', va='bottom')
    for suffix in ['png', 'svg']:
        fig.savefig(args.run_dir/f'lifecycle.{suffix}', dpi=170)
    analysis_path = args.run_dir/'analysis.json'
    if analysis_path.exists():
        observations = json.loads(analysis_path.read_text()).get('paired_observations', [])
        if observations:
            paired, ax = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
            xs = np.arange(len(observations))
            for shift, key, label, color in [(-.18, 'baseline_s', 'Evicted weights: disk reload', '#d08050'),
                                            (.18, 'candidate_s', 'FluidCKPT', '#4588a5')]:
                bars = ax.bar(xs+shift, [r[key] for r in observations], width=.36, label=label, color=color)
                for bar, row in zip(bars, observations):
                    ax.text(bar.get_x()+bar.get_width()/2, row[key], f'{row[key]:.2f}', ha='center', va='bottom')
            ax.set_xticks(xs, ['A → B\nrelease + resume', 'B → A\nrelease + resume', 'Remote A\nresume request'][:len(observations)])
            ax.set(ylabel='Request to first correct token (s)', title='Two-model lifecycle on Hygon DCU: one trace')
            ax.legend(loc='upper left')
            ax.set_ylim(0, max(r['baseline_s'] for r in observations)*1.3)
            for suffix in ['png', 'svg']: paired.savefig(args.run_dir/f'paired.{suffix}', dpi=170)
    report = {'status': data['status'], 'transitions': [
        {k: t[k] for k in ['phase', 'mode', 'first_correct_token_s', 'correct']} for t in transitions],
        'scope': 'adapted HIP IPC baseline; eager warm engines; individual observations, not tail-latency estimates'}
    (args.run_dir/'comparison.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
