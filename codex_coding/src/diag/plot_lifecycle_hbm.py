"""Plot hardware HBM samples over the completed GLM/Qwen lifecycle window."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.run_dir/'summary.json').read_text())
    begin = next(e['time'] for e in report['events'] if e['event']=='lifecycle_B_ready')
    end = next(e['time'] for e in report['events'] if e['event']=='lifecycle_complete')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    fig, ax = plt.subplots(figsize=(9, 3.5), constrained_layout=True)
    points = {}
    for node in ['node27', 'node29', 'node28']:
        topology = json.loads((args.run_dir/f'{node}_topology.json').read_text())['topology']
        logical = set(range(2, 8) if node=='node28' else range(8))
        devices = {card for gpu in topology['gpus'] if gpu['logical_gpu'] in logical for card in gpu['drm']}
        samples = []
        with (args.run_dir/f'{node}_hbm.jsonl').open() as stream:
            for line in stream:
                row = json.loads(line); wall = row['wall_ns']/1e9
                if begin <= wall <= end:
                    samples.append([(wall-begin)/60, sum(row['vram_used_bytes'][c] for c in devices)/2**30])
        points[node] = samples
        ax.plot([s[0] for s in samples], [s[1] for s in samples], label=node, linewidth=1.3)
    ax.set(xlabel='Minutes since Qwen first became ready', ylabel='Physical HBM used (GiB)',
           title='GLM / Qwen switching and checkpoint migration on three DCU nodes', ylim=(0, None))
    ax.grid(axis='y', alpha=.2); ax.legend(frameon=False, loc='upper right')
    for extension in ['png', 'pdf', 'svg']: fig.savefig(args.run_dir/f'lifecycle_hbm.{extension}', dpi=180)
    (args.run_dir/'lifecycle_hbm_plot_data.json').write_text(json.dumps({'begin': begin, 'end': end,
        'samples': points, 'scope': 'hardware counters on assigned DCUs, including engine and keeper overhead; excludes node28 GPU0/1'},
        indent=2)+'\n')


if __name__ == '__main__':
    main()
