"""Parent-run bounded metadata collection through existing operations transport."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'
OPS = ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations'
DEFAULT_IMAGE = 'sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588'


def source():
    old = (OPS / 'host_readback.py').read_text().rsplit("if __name__ == '__main__':", 1)[0]
    combined = old + '\n' + (HERE / 'host_metadata.py').read_text()
    compile(combined, 'online_readonly_metadata', 'exec')
    return combined.encode()


def defaults(run_id, model_path, image):
    result = {}
    for node, host in [('node27', 'm03r2n07'), ('node28', 'm03r2n08'), ('node30', 'm03r2n10')]:
        roles = [dict(gpu=4, bdf='0000:85:00.0', numa=4)]
        if node == 'node27':
            roles.append(dict(gpu=5, bdf='0000:b5:00.0', numa=7))
        result[node] = dict(node=node, hostname=host, roles=roles, image_id=image,
            candidate_container_name='zjn-fluid-three-node-' + run_id + '-n' + node[-2:],
            ports=[36000, 36014, 36015, 36240, 36340] if node == 'node27' else [36014],
            minimum_host_available_bytes=(256 if node == 'node27' else 128) << 30,
            model_path=model_path if node == 'node27' else None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--model-path', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.add_argument('--image-id', default=DEFAULT_IMAGE)
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--scope', choices=('three_node', 'two_node'), default='three_node')
    parser.add_argument('--helper-gpus', choices=('4', '4-5'), default='4-5')
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9_]+', args.run_id):
        raise ValueError('invalid fresh run namespace')
    out = args.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('review_'):
        raise ValueError('fresh owned review result directory required')
    specs = json.loads(args.spec.read_text()) if args.spec else defaults(args.run_id, args.model_path, args.image_id)
    nodes = ('node27', 'node30') if args.scope == 'two_node' else ('node27', 'node28', 'node30')
    if args.scope == 'two_node' and not args.spec:
        specs = {node: specs[node] for node in nodes}
        if args.helper_gpus == '4-5':
            specs['node30']['roles'].append(dict(gpu=5, bdf='0000:b5:00.0', numa=7))
            specs['node30']['ports'] = [36014, 36015]
        else:
            specs['node30']['source_numa_nodes'] = [4, 7]
    if set(specs) != set(nodes):
        raise ValueError('exact explicit selected-node metadata scope required')
    for node, spec in specs.items():
        if spec['node'] != node or (node != 'node27' and spec.get('model_path')):
            raise ValueError('model metadata belongs only to selected target host')
    payload = source()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'metadata.source.py').write_bytes(payload)
    (out / 'metadata.spec.json').write_text(json.dumps(specs, indent=2) + '\n')
    summary = dict(read_only=True, GPU_launches=0, gpu_api_calls=0, remote_writes=0,
        node29_accesses=0, built_only=args.build_only, source_sha256=hashlib.sha256(payload).hexdigest())
    if args.build_only:
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary)); return
    loaded = importlib.util.spec_from_file_location('online_review_transport', OPS / 'control.py')
    ops = importlib.util.module_from_spec(loaded); loaded.loader.exec_module(ops)
    def one(node):
        process, raw = ops.old.record(ops.command(node, ['python3', '-', json.dumps(specs[node])]), out / node, 35, payload)
        if process['returncode'] != 0:
            return node, dict(passed=False, command=process)
        body = json.loads(raw)
        (out / node / 'stdout.json').write_text(json.dumps(body, indent=2) + '\n')
        return node, dict(passed=True, hostname=body['hostname'], boot_id=body['boot_id'],
            host_memory_admission=body['host_memory_admission'], requested_image_present=body['requested_image_present'],
            candidate_name_free=body['candidate_name_free'], ports_free=body['ports_free'])
    with ThreadPoolExecutor(max_workers=3) as pool:
        summary['nodes'] = dict(pool.map(one, nodes))
    summary['collection_passed'] = all(body['passed'] for body in summary['nodes'].values())
    summary['fresh_gpu_capacity_admission_passed'] = False
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    if not summary['collection_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
