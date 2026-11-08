"""Clean only process records and labelled containers owned by an interrupted run."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from run_system import SystemRun, NODES

parser = argparse.ArgumentParser()
parser.add_argument('--run', required=True)
args = parser.parse_args()
out = ROOT/'codex_coding/results/v0.1.23.3/task2'/args.run
data = json.loads((out/'summary.json').read_text())
runner = SystemRun.__new__(SystemRun)
errors = []
owned = [(e['node'], e['name']) for e in data['events'] if e['event'] == 'isolated_container_created']
for node, name in owned:
    label = runner.docker_host(node, ['inspect', name, '--format', '{{index .Config.Labels "fluidckpt.session"}}']).strip()
    if label != args.run:
        raise RuntimeError('container ownership mismatch')
    NODES[node]['container'] = name
for e in reversed(data['events']):
    if e['event'] != 'process_started':
        continue
    try:
        runner.remote(e['node'], ['python3', 'codex_coding/src/task2/cluster/process.py', 'stop', '--spec', e['process']['spec']], timeout=45)
    except Exception as exc:
        errors.append(repr(exc))
for node, name in owned:
    try:
        runner.docker_host(node, ['rm', '-f', name])
    except Exception as exc:
        errors.append(repr(exc))
(out/'interruption_cleanup.json').write_text(json.dumps({'time': time.time(), 'status': 'interrupted',
    'reason': 'launcher absent; stale running summary; original summary preserved', 'errors': errors}, indent=2))
print(json.dumps({'cleanup_errors': errors}))
if errors:
    raise SystemExit(1)
