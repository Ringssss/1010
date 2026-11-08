"""Refresh the motivation figure whenever the running experiment commits a sample."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    previous = -1
    while True:
        report = json.loads((args.run_dir/'summary.json').read_text())
        count = len(report.get('samples', []))
        if count and count != previous:
            subprocess.run([sys.executable, str(Path(__file__).with_name('plot_recall_pressure.py')),
                            '--run-dir', str(args.run_dir)], check=True, stdout=subprocess.DEVNULL)
            previous = count
        status = {'experiment_status': report['status'], 'plotted_samples': max(previous, 0), 'updated': time.time()}
        (args.run_dir/'figure_status.json').write_text(json.dumps(status)+'\n')
        if report['status'] in ['passed', 'failed', 'prerequisite_failed']:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
