"""Plot measured first-token latency and SSD I/O for the local-DRAM pressure sweep."""
import argparse
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads((args.run_dir/'summary.json').read_text())
    rows = [r for r in report['samples'] if r['correct']]
    if not rows: raise ValueError('no verified measurements to plot')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    fig, (latency, io) = plt.subplots(1, 2, figsize=(8.4, 3.1), constrained_layout=True)
    styles = [('local_dram_ssd', 'Local DRAM + SSD', '#b35436', 's'),
              ('cluster_dram', 'Cluster DRAM', '#2166ac', 'o')]
    aggregated = []
    for policy, label, color, marker in styles:
        fractions = sorted({r['fraction'] for r in rows if r['policy'] == policy}, reverse=True)
        groups = [[r for r in rows if r['policy'] == policy and r['fraction'] == f] for f in fractions]
        values = [statistics.median(r['first_token_s'] for r in g) for g in groups]
        latency.plot([100*f for f in fractions], values, marker=marker, color=color, label=label,
                     linewidth=1.6, markersize=5)
        for f, group, value in zip(fractions, groups, values):
            aggregated.append({'policy': policy, 'local_fraction': f, 'median_first_token_s': value,
                               'n': len(group), 'min_s': min(r['first_token_s'] for r in group),
                               'max_s': max(r['first_token_s'] for r in group)})
        if policy == 'local_dram_ssd':
            kernel = [statistics.median(r['ssd_kernel_read_bytes'] for r in g)/2**30 for g in groups]
            logical = [statistics.median(r['ssd_logical_read_bytes'] for r in g)/2**30 for g in groups]
            io.plot([100*f for f in fractions], logical, 's--', color='#777777', label='Requested SSD bytes')
            io.plot([100*f for f in fractions], kernel, 'o-', color=color, label='Kernel storage reads')
    latency.set_ylabel('Restore to first correct token (s)')
    latency.set_title('(a) Recovery under local memory pressure')
    io.set_ylabel('Read volume per recovery (GiB)')
    io.set_title('(b) Observed SSD activity')
    for ax in (latency, io):
        ax.set_xlabel('Weights retained in local DRAM (%)')
        ax.set_xlim(105, -5)
        ax.set_xticks([100, 75, 50, 25, 0])
        ax.set_ylim(bottom=0)
        ax.grid(axis='y', alpha=.2)
        ax.legend(frameon=False, fontsize=8)
    for extension in ['pdf', 'svg', 'png']:
        fig.savefig(args.run_dir/f'recall_pressure.{extension}', dpi=200)
    (args.run_dir/'plot_data.json').write_text(json.dumps({'status': report['status'],
        'points': aggregated, 'measurement': 'single samples remain individual observations; no confidence interval implied'},
        indent=2)+'\n')
    print(json.dumps(aggregated, indent=2))


if __name__ == '__main__':
    main()
