"""Run the real A/B trajectory after the full-model roundtrip has completed."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    out = ROOT/'codex_coding/results/glm_followup'/args.run
    marker = out/'lifecycle_driver.json'
    if marker.exists(): raise ValueError('lifecycle continuation is already registered')
    marker.write_text(json.dumps({'status': 'waiting_for_roundtrip', 'started': time.time()})+'\n')
    while True:
        report = json.loads((out/'summary.json').read_text())
        if report['status'] in ['passed', 'roundtrip_failed', 'native_failed']:
            break
        time.sleep(2)
    if report['status'] != 'passed':
        marker.write_text(json.dumps({'status': 'prerequisite_failed', 'prerequisite': report['status']})+'\n')
        raise SystemExit(1)
    routes = json.loads((ROOT/'codex_coding/results/transport_validation/lifecycle_pairs04/summary.json').read_text())
    if routes['status'] != 'passed': raise RuntimeError('the lifecycle transfer pairs have not passed')
    command = [sys.executable, str(ROOT/'codex_coding/src/task2/cluster/continue_glm_ab.py'),
               '--session', str(ROOT/'codex_coding/results/glm_roundtrip'/args.run/'session.json')]
    marker.write_text(json.dumps({'status': 'running', 'command': command, 'started': time.time()}, indent=2)+'\n')
    with (out/'lifecycle.log').open('w') as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    marker.write_text(json.dumps({'status': 'passed' if result.returncode == 0 else 'failed',
                                 'returncode': result.returncode, 'finished': time.time()}, indent=2)+'\n')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
