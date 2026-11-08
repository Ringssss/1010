"""Estimate host wall-clock offsets for coarse cross-node memory plots."""
import argparse
import json
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--nodes', nargs='+', default=['node28', 'node29', 'node30'])
args = parser.parse_args()
rows = {'node27': {'offset_ns': 0, 'rtt_ns': 0}}
for node in args.nodes:
    samples = []
    for _ in range(3):
        begin = time.time_ns()
        remote = int(subprocess.check_output(['ssh', node, 'date +%s%N'], text=True))
        end = time.time_ns()
        samples.append({'offset_ns': remote-(begin+end)//2, 'rtt_ns': end-begin})
    rows[node] = min(samples, key=lambda r: r['rtt_ns'])
args.out.write_text(json.dumps(rows, indent=2)+'\n')
print(json.dumps(rows))
