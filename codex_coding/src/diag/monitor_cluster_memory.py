"""Record physical HBM, host memory and experiment cgroup counters without a GPU context."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]


def sample(path):
    cgroup = Path('/sys/fs/cgroup')
    with path.open('a', buffering=1) as stream:
        while True:
            devices = {item.parts[-3]: int(item.read_text())
                       for item in Path('/sys/class/drm').glob('card[0-9]*/device/mem_info_vram_used')}
            host = {line.split(':')[0]: int(line.split()[1])*1024
                    for line in Path('/proc/meminfo').read_text().splitlines()
                    if line.split(':')[0] in ['MemTotal', 'MemAvailable', 'Cached', 'Shmem',
                                             'Unevictable', 'Mlocked', 'AnonPages']}
            counters = {}
            for name in ['memory.current', 'memory.peak', 'memory.max', 'memory.swap.current', 'memory.swap.max']:
                item = cgroup/name
                if item.exists():
                    value = item.read_text().strip()
                    counters[name] = int(value) if value.isdecimal() else value
            for name in ['memory.stat', 'memory.events']:
                item = cgroup/name
                if item.exists():
                    counters[name] = {key: int(value) for key, value in
                                      (line.split() for line in item.read_text().splitlines())}
            row = {'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(), 'host': socket.gethostname(),
                   'vram_used_bytes': devices, 'host_memory_bytes': host, 'cgroup': counters}
            stream.write(json.dumps(row)+'\n')
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'sample'])
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--container')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.action == 'sample':
        sample(args.out)
        return
    registry = args.run_dir/'monitors.json'
    if args.action == 'start':
        if registry.exists():
            raise ValueError('monitor registry already exists')
        records = []
        for node in [27, 28, 29]:
            spec = args.run_dir/f'monitor_node{node}.process.json'
            spec.write_text(json.dumps({'command': ['python3', str(Path(__file__).resolve()), 'sample',
                '--out', str(args.run_dir/f'node{node}_hbm.jsonl')], 'env': {}, 'cwd': str(ROOT),
                'log': str(args.run_dir/f'monitor_node{node}.log')})+'\n')
            records.append({'node': node, 'container': args.container, 'spec': str(spec)})
        registry.write_text(json.dumps({'processes': records,
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, indent=2)+'\n')
    else:
        records = json.loads(registry.read_text())['processes']
    for row in records:
        argv = ['docker', 'exec', row['container'], 'python3',
                str(ROOT/'codex_coding/src/task2/cluster/process.py'), args.action, '--spec', row['spec']]
        if row['node'] != 27:
            argv = ['ssh', '-o', 'BatchMode=yes', f'12.11.3.{row["node"]}', shlex.join(argv)]
        output = subprocess.check_output(argv, text=True, timeout=45)
        print(json.dumps({'node': row['node'], 'action': args.action, 'result': output}), flush=True)


if __name__ == '__main__':
    main()
