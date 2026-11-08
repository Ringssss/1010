"""Collect bounded CPU metadata without importing Torch or reading model payloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import time

MAX_METADATA = 4 << 20


def limited_read(path, maximum=MAX_METADATA):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f'metadata exceeds bound: {path}')
    return data


def read_text(path):
    try:
        return limited_read(path, 1 << 20).decode().strip()
    except OSError as exc:
        return {'error': repr(exc)}


def inspect(model_path, bdfs):
    started = time.monotonic_ns()
    model = Path(model_path)
    metadata = {}
    for filename in ('config.json', 'model.safetensors.index.json', 'generation_config.json'):
        path = model / filename
        if not path.is_file():
            metadata[filename] = {'exists': False}
            continue
        data = limited_read(path)
        parsed = json.loads(data)
        item = {'exists': True, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if filename == 'config.json':
            item['config'] = parsed
        elif filename == 'model.safetensors.index.json':
            item['metadata'] = parsed.get('metadata', {})
            item['tensor_names'] = len(parsed.get('weight_map', {}))
            item['shards'] = sorted(set(parsed.get('weight_map', {}).values()))
        metadata[filename] = item
    shards = []
    for path in model.glob('*.safetensors'):
        if len(shards) >= 256:
            raise ValueError('too many model shards for bounded metadata probe')
        stat = path.stat()
        shards.append({'name': path.name, 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
    pci = {}
    for bdf in bdfs:
        path = Path('/sys/bus/pci/devices') / bdf
        pci[bdf] = {'exists': path.exists(), 'numa_node': read_text(path / 'numa_node'),
                    'vendor': read_text(path / 'vendor'), 'device': read_text(path / 'device')}
    cg_raw = read_text('/proc/self/cgroup')
    cg = Path('/sys/fs/cgroup')
    if isinstance(cg_raw, str):
        relative = next((r.split(':', 2)[2] for r in cg_raw.splitlines() if r.startswith('0::')), None)
        if relative and (cg / relative.lstrip('/') / 'memory.current').exists():
            cg = cg / relative.lstrip('/')
    meminfo = read_text('/proc/meminfo')
    mem = {}
    if isinstance(meminfo, str):
        for line in meminfo.splitlines():
            name, values = line.split(':', 1)
            if name in ('MemTotal', 'MemAvailable', 'Shmem'):
                mem[name + '_bytes'] = int(values.split()[0]) << 10
    return {'schema': 'online_cost_metadata.v1', 'status': 'metadata_collected',
            'hostname': socket.gethostname(), 'boot_id': read_text('/proc/sys/kernel/random/boot_id'),
            'pid': os.getpid(), 'pid_namespace': os.readlink('/proc/self/ns/pid'),
            'cgroup': str(cg), 'host_memory': mem,
            'cgroup_fields': {n: read_text(cg / n) for n in ('memory.current', 'memory.max', 'memory.events', 'pids.current')},
            'model_path': str(model), 'model_directory_exists': model.is_dir(),
            'metadata': metadata, 'shards': sorted(shards, key=lambda r: r['name']),
            'checkpoint_file_bytes': sum(r['bytes'] for r in shards), 'candidate_pci': pci,
            'gpu_runtime_initialized': False, 'gpu_memory_queried': False, 'model_payload_read_bytes': 0,
            'physical_gpu_available': None, 'fresh_hardware_admission_passed': False,
            'elapsed_ns': time.monotonic_ns() - started}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model-path', required=True)
    p.add_argument('--bdf', action='append', default=[])
    p.add_argument('--deadline-s', type=int, default=15)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    if not 1 <= a.deadline_s <= 30:
        p.error('deadline must be 1..30 seconds')
    def expired(signum, frame):
        raise TimeoutError('metadata deadline exceeded')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(a.deadline_s)
    try:
        row = inspect(a.model_path, a.bdf)
    except Exception as exc:
        row = {'schema': 'online_cost_metadata.v1', 'status': 'failed', 'error': repr(exc),
               'gpu_runtime_initialized': False, 'fresh_hardware_admission_passed': False}
    finally:
        signal.alarm(0)
    encoded = json.dumps(row, indent=2, ensure_ascii=False) + '\n'
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        with a.out.open('x') as stream:
            stream.write(encoded)
    print(encoded, end='')
    return 0 if row['status'] == 'metadata_collected' and row['metadata']['config.json']['exists'] and row['shards'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
