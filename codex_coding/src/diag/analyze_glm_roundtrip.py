"""Produce a scoped correctness report and physical memory plot from an archived GLM run."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.run_dir/'summary.json').read_text())
    restore = report.get('restore', {})
    verification = report.get('verification', {})
    analysis = {'status': report['status'], 'diagnostic_model': 'diagnostic_model' in report,
        'scope': report['scope'], 'ib_hca': report.get('ib_hca', 'automatic'),
        'logical_bytes': sum(row['n'] for row in report.get('sources', [])),
        'release_s': report.get('release_s'),
        'request_to_first_token_s': restore.get('request_to_first_token_s'),
        'output_matches_native_and_pre_release': bool(report.get('reference') and restore.get('correct')),
        'verification': {k: verification.get(k) for k in ['passed', 'checked_bytes', 'algorithm']},
        'memory': {}, 'limitations': ['No same-budget baseline or latency distribution in this roundtrip.',
            'Wall-clock alignment across hosts is approximate; token latency uses the controller clock.',
            'cgroup memory.current includes charged pages; shared page-cache attribution remains unresolved.']}
    if analysis['diagnostic_model']:
        analysis['limitations'].insert(0, 'Truncated eight-layer model; generated text is not full GLM semantic validation.')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    first_time = min(e['time'] for e in report['events'])
    series = {}
    for node in ['node27', 'node29', 'node28']:
        path = args.run_dir/f'{node}_hbm.jsonl'
        if not path.exists():
            continue
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        topology = json.loads((args.run_dir/f'{node}_topology.json').read_text())['topology']
        logical = set(range(2, 8) if node == 'node28' else range(8))
        cards = {card for gpu in topology['gpus'] if gpu['logical_gpu'] in logical for card in gpu['drm']}
        hbm = [sum(row['vram_used_bytes'][card] for card in cards) for row in rows]
        host = [row['cgroup'].get('memory.current') for row in rows]
        times = [row['wall_ns']/1e9-first_time for row in rows]
        series[node] = (times, hbm, host)
        axes[0].plot(times, [n/2**30 for n in hbm], label=node)
        valid = [(t, n) for t, n in zip(times, host) if isinstance(n, int)]
        if valid:
            axes[1].plot([t for t, _ in valid], [n/2**30 for _, n in valid], label=node)
        analysis['memory'][node] = {'sampled_hbm_peak_bytes': max(hbm),
            'sampled_cgroup_peak_bytes': max((n for _, n in valid), default=None), 'sample_count': len(rows),
            'drm_devices': sorted(cards), 'logical_gpus': sorted(logical)}
    for event in report['events']:
        if event['event'] in ['capture_reference', 'released', 'roundtrip_verified']:
            for ax in axes:
                ax.axvline(event['time']-first_time, color='gray', linestyle=':', linewidth=.8)
            if event['event'] != 'capture_reference':
                axes[0].annotate(event['event'], (event['time']-first_time, axes[0].get_ylim()[1]),
                                 rotation=90, va='top', fontsize=7)
    axes[0].set_ylabel('Managed DCUs physical HBM (GiB)')
    axes[1].set_ylabel('Experiment cgroup memory (GiB)')
    axes[1].set_xlabel('Seconds since first controller event')
    for ax in axes:
        ax.grid(alpha=.2); ax.legend(loc='upper left')
    title = 'Eight-layer diagnostic' if analysis['diagnostic_model'] else 'Full GLM-5.3'
    fig.suptitle(title+' capture / release / restore; '+report['status'])
    fig.tight_layout()
    fig.savefig(args.run_dir/'physical_memory.png', dpi=180)
    fig.savefig(args.run_dir/'physical_memory.svg')
    releases = [e['time'] for e in report['events'] if e['event'] == 'released']
    if releases and series:
        release = releases[0]-first_time
        zoom, ax = plt.subplots(figsize=(10, 4))
        for node, (times, hbm, _) in series.items():
            selected = [(t-release, n/2**30) for t, n in zip(times, hbm) if t >= release-3]
            ax.plot([t for t, _ in selected], [n for _, n in selected], label=node)
        ax.axvline(0, color='gray', linestyle=':', label='First release completed')
        ax.set(xlabel='Seconds from first release completion', ylabel='Physical HBM (GiB)',
               title=title+' release / restore / IPC validation window')
        ax.grid(alpha=.2); ax.legend(); zoom.tight_layout()
        zoom.savefig(args.run_dir/'lifecycle_memory.png', dpi=180)
        zoom.savefig(args.run_dir/'lifecycle_memory.svg')
    (args.run_dir/'analysis.json').write_text(json.dumps(analysis, indent=2)+'\n')
    print(json.dumps(analysis, indent=2))


if __name__ == '__main__':
    main()
