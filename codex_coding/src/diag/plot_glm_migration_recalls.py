"""Plot measured GLM/Qwen recalls during a real block migration."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.run_dir/'summary.json').read_text())
    lifecycle = report['lifecycle']
    totals = [e['total'] for e in report['events'] if e['event'] == 'lifecycle_migration_progress']
    total = totals[-1]
    points = []
    for row in lifecycle['recalls']:
        reason = row['reason']
        if reason.startswith('mid_migration_') or reason.startswith('continue_after_'):
            x = 100*int(reason.rsplit('_', 1)[1])/total
        elif reason in ['bootstrap', 'first_switch']:
            x = 0.
        elif reason == 'return_after_migration':
            x = 100.
        else:
            continue
        points.append({'model': row['model'], 'progress_percent': x,
                       'first_token_s': row['request_to_first_token_s'], 'correct': row['correct']})
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    fig, ax = plt.subplots(figsize=(6.2, 3.4), constrained_layout=True)
    for model, label, color, marker in [('A', 'GLM-5.3 (TP16)', '#2166ac', 'o'),
                                         ('B', 'Qwen3.6-35B-A3B (TP2)', '#b35436', 's')]:
        rows = sorted((r for r in points if r['model'] == model and r['correct']),
                      key=lambda r: r['progress_percent'])
        ax.plot([r['progress_percent'] for r in rows], [r['first_token_s'] for r in rows],
                marker=marker, color=color, linewidth=1.6, label=label)
    ax.set(xlabel='Completed migration blocks (%)', ylabel='Restore to first correct token (s)',
           xlim=(-3, 103), ylim=(0, None), xticks=[0, 25, 50, 75, 100])
    ax.grid(axis='y', alpha=.2)
    ax.legend(frameon=False)
    for ext in ['pdf', 'svg', 'png']:
        fig.savefig(args.run_dir/f'migration_recalls.{ext}', dpi=200)
    (args.run_dir/'migration_recall_plot_data.json').write_text(json.dumps({
        'status': lifecycle['status'], 'total_blocks': total, 'points': points,
        'scope': 'one real trajectory; restore window excludes earlier release, migration and later SHA validation'},
        indent=2)+'\n')


if __name__ == '__main__':
    main()
