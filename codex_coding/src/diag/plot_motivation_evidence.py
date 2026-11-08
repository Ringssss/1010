"""Present measured capacity, execution-model and migration-recall evidence together."""
import argparse
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pressure-dir', type=Path, required=True)
    parser.add_argument('--cost-dir', type=Path, required=True)
    parser.add_argument('--lifecycle-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    pressure = json.loads((args.pressure_dir/'summary.json').read_text())
    cost = json.loads((args.cost_dir/'summary.json').read_text())
    lifecycle = json.loads((args.lifecycle_dir/'migration_recall_plot_data.json').read_text())
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.2), constrained_layout=True)
    for policy, label, color, marker in [('local_dram_ssd', 'Local DRAM + SSD', '#b35436', 's'),
                                         ('cluster_dram', 'Cluster DRAM', '#2166ac', 'o')]:
        rows = sorted((r for r in pressure['samples'] if r['policy'] == policy), key=lambda r: -r['fraction'])
        axes[0].plot([100*(1-r['fraction']) for r in rows], [r['first_token_s'] for r in rows],
                     marker=marker, color=color, label=label, linewidth=1.6)
    axes[0].set(title='(a) Local capacity and recovery', xlabel='Weights moved out of local DRAM (%)',
                ylabel='First correct token (s)', xlim=(-3, 103), ylim=(0, None), xticks=[0, 50, 100])
    axes[0].legend(frameon=False, fontsize=8)
    for i, (policy, color, text) in enumerate([('parallel_path_model', '#b35436', '476 MiB SSD'),
                                              ('serial_target_model', '#2166ac', '0 SSD bytes')]):
        values = [r['first_token_s'] for r in cost['samples'] if r['policy'] == policy]
        mean = statistics.mean(values)
        axes[1].bar(i, mean, width=.58, color=color, alpha=.75)
        axes[1].scatter([i-.07, i+.07], values, s=18, color='black', zorder=3)
        axes[1].text(i, mean+.14, text, ha='center', fontsize=8)
    axes[1].set(title='(b) Cost model and source choice', ylabel='First correct token (s)', ylim=(0, 3.2),
                xticks=[0, 1], xticklabels=['Parallel-path\nassumption', 'Serial-target\nmodel'])
    rows = [r for r in lifecycle['points'] if r['model']=='A' and r['progress_percent']>0]
    axes[2].plot([r['progress_percent'] for r in rows], [r['first_token_s'] for r in rows],
                 marker='o', color='#2166ac', linewidth=1.6, label='GLM-5.3 TP16')
    axes[2].set(title='(c) Recovery during migration', xlabel='Completed migration blocks (%)',
                ylabel='First correct token (s)', xlim=(20, 103), ylim=(0, 16), xticks=[25, 50, 75, 100])
    axes[2].legend(frameon=False, fontsize=8)
    for ax in axes: ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True)
    args.out.mkdir(parents=True, exist_ok=True)
    for ext in ['pdf', 'svg', 'png']: fig.savefig(args.out/f'motivation.{ext}', dpi=220)
    (args.out/'sources.json').write_text(json.dumps({'pressure': str(args.pressure_dir/'summary.json'),
        'cost': str(args.cost_dir/'summary.json'), 'lifecycle': str(args.lifecycle_dir/'migration_recall_plot_data.json'),
        'scope': 'exploratory measured points; panel b has two observations per condition; panel c is one real trajectory',
        'layout': 'independent design; referenced Meld Figure 4 was not accessible'}, indent=2)+'\n')


if __name__ == '__main__':
    main()
