"""Parent-only bounded selected helper sysfs read; GPU4 or explicit node28GPU0/1."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'
OPS = ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations/control.py'
HOST_SOURCE = '''import json, os, signal, sys, time
from pathlib import Path
spec = json.loads(sys.argv[1])
if spec['node'] not in ('node28', 'node30') or os.uname().nodename != spec['hostname']:
    raise ValueError('fixed helper host identity mismatch')
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('helper metadata8s')))
signal.alarm(8)
boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if boot != spec['boot_id']:
    raise ValueError('helper boot changed; new freeze required')
allowed = [(4, '0000:85:00.0')]
alternatives = [(0, '0000:09:00.0'), (1, '0000:36:00.0')]
node30_gpu5 = [(5, '0000:b5:00.0')]
roles = [(row['gpu'], row['bdf']) for row in spec['roles']]
if roles != allowed and not (spec['node']=='node28' and roles == alternatives) and not (spec['node']=='node30' and roles==node30_gpu5):
    raise ValueError('only explicit helper4/node28GPU0/1/node30GPU5 scope allowed')
gpus = []
for gpu, bdf in roles:
    root = Path('/sys/bus/pci/devices') / bdf
    values = {}
    for name in ('mem_info_vram_used', 'mem_info_vram_total'):
        with (root / name).open('rb') as stream:
            raw = stream.read(129)
        if len(raw)>128:
            raise ValueError('bounded selected PCI metadata unavailable')
        value = raw.decode().strip()
        if name.startswith('mem_info_vram') and not value.isdigit():
            raise ValueError('numeric selected VRAM metadata unavailable')
        values[name] = int(value)
    used, total = values['mem_info_vram_used'], values['mem_info_vram_total']
    if not 0 <= used <= total or total <= 0:
        raise ValueError('invalid selected VRAM counters')
    gpus.append(dict(gpu=gpu,bdf=bdf,used_bytes=used,total_bytes=total,free_bytes=total-used))
print(json.dumps(dict(node=spec['node'], hostname=spec['hostname'], boot_id=boot, selected_gpus=gpus,
    source='selected PCI sysfs only', gpu_api_calls=0, GPU_launches=0,
    remote_writes=0, owner_table_walks=0, node27_accesses=0, node29_accesses=0,
    observation_wall_ns=time.time_ns(), final_same_HIP_admission_passed=False)))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--node28-alternatives', action='store_true')
    parser.add_argument('--node30-gpu5', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('review_'):
        raise ValueError('fresh owned review_* output required')
    specs = {}
    if args.node28_alternatives and args.node30_gpu5:
        raise ValueError('one explicit selected helper scope required')
    nodes = ('node30',) if args.node30_gpu5 else ('node28',) if args.node28_alternatives else ('node28', 'node30')
    for node in nodes:
        body = json.loads((args.metadata / node / 'stdout.json').read_text())
        specs[node] = {key: body[key] for key in ('node', 'hostname', 'boot_id')}
        specs[node]['roles'] = ([dict(gpu=5,bdf='0000:b5:00.0')] if args.node30_gpu5 else
            [dict(gpu=0,bdf='0000:09:00.0'),dict(gpu=1,bdf='0000:36:00.0')] if args.node28_alternatives else [dict(gpu=4,bdf='0000:85:00.0')])
    compile(HOST_SOURCE, 'selected_helper_gpu4_metadata', 'exec')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'metadata.source.py').write_text(HOST_SOURCE)
    (out / 'metadata.spec.json').write_text(json.dumps(specs, indent=2) + '\n')
    summary = dict(source_sha256=hashlib.sha256(HOST_SOURCE.encode()).hexdigest(),
                   GPU_launches=0, gpu_api_calls=0, remote_writes=0, final_same_HIP_admission_passed=False)
    if not args.build_only:
        loaded = importlib.util.spec_from_file_location('helper_metadata_ops', OPS)
        ops = importlib.util.module_from_spec(loaded); loaded.loader.exec_module(ops)
        def one(node):
            process, raw = ops.old.record(ops.command(node, ['python3', '-', json.dumps(specs[node])]), out / node, 15, HOST_SOURCE.encode())
            if process['returncode'] != 0:
                return node, dict(collection_passed=False, command=process)
            body = json.loads(raw)
            (out / node / 'stdout.json').write_text(json.dumps(body, indent=2) + '\n')
            return node, dict(collection_passed=True, selected_gpus=body['selected_gpus'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            summary['nodes'] = dict(pool.map(one, nodes))
        summary['collection_passed'] = all(row['collection_passed'] for row in summary['nodes'].values())
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    if not args.build_only and not summary['collection_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
