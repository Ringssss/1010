"""Start staged full-GLM validation only after the matching native run has finished cleanup."""
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
    parser.add_argument('--native-result', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    out = ROOT/'codex_coding/results/glm_followup'/args.run
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'waiting_for_native', 'native_result': str(args.native_result), 'events': []}
    def event(name, **fields):
        report['events'].append({'time': time.time(), 'event': name, **fields})
        (out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report['events'][-1]), flush=True)
    event('waiting_for_native_cleanup')
    while True:
        native = json.loads(args.native_result.read_text())
        if native['events'] and native['events'][-1]['event'] == 'finished':
            break
        time.sleep(2)
    if native['status'] != 'generated_repeatably' or native.get('cleanup_error'):
        report['status'] = 'native_failed'
        event('native_failed', native_status=native['status'])
        raise SystemExit(1)
    command = [sys.executable, str(ROOT/'codex_coding/src/task2/cluster/run_glm_roundtrip.py'),
        '--run', args.run, '--native-result', str(args.native_result), '--model', native['args']['model'],
        '--timeout', '7200', '--spill-ranks-per-node', '2', '--ib-hca', 'shca_0',
        '--monitor-memory', '--ipc-baseline', '--keep-running', '--keep-on-failure']
    report.update(status='running_roundtrip', command=command)
    event('starting_roundtrip')
    with (out/'roundtrip.log').open('w') as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    report.update(status='passed' if result.returncode == 0 else 'roundtrip_failed',
                  returncode=result.returncode)
    event('roundtrip_finished')
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
