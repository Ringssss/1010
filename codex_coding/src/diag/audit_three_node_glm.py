"""Record read-only GLM and three-node readiness evidence without starting GPU work."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[3]
MODEL = Path('/data/node28/shared/zhujianian/GLM-5.3')
NSA = '/usr/local/lib/python3.10/dist-packages/sglang/srt/layers/attention/nsa/nsa_indexer.py'


def command(node, argv):
    effective = argv if node == 27 else [
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
        f'12.11.3.{node}', shlex.join(argv)]
    try:
        result = subprocess.run(effective, capture_output=True, text=True, timeout=45)
        return {'command': effective, 'returncode': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr}
    except subprocess.TimeoutExpired:
        return {'command': effective, 'error': '45-second timeout'}


def inspect_node(node):
    checks = {
        'hostname': ['hostname'],
        'devices': ['/opt/hyhal/bin/hy-smi', '--showmeminfo', 'vram', '--showuse'],
        'memory': ['cat', '/proc/meminfo'],
        'filesystems': ['df', '-T', '/tmp', '/home/zhujianian', '/data/node28/shared'],
        'containers': ['docker', 'ps', '--format', '{{.Names}} {{.Image}}'],
        'nsa_sha256': ['docker', 'exec', f'zjn-keep-v02-n{node}', 'sha256sum', NSA],
        'sglang_metadata': ['docker', 'exec', f'zjn-keep-v02-n{node}',
                            'bash', '-lc',
                            'head -n 4 /usr/local/lib/python3.10/dist-packages/sglang-*/METADATA'],
    }
    return f'node{node}', {name: command(node, argv) for name, argv in checks.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    config = json.loads((MODEL/'config.json').read_text())
    index = json.loads((MODEL/'model.safetensors.index.json').read_text())
    shards = sorted(set(index['weight_map'].values()))
    analysis_path = ROOT/'codex_coding/results/lifecycle/final03/analysis.json'
    analysis = json.loads(analysis_path.read_text())
    historical_errors = {}
    for name in ['base2_n0.log', 'base2_n1.log']:
        path = ROOT/'codex_coding/results/logs'/name
        lines = path.read_text(errors='replace').splitlines()
        matches = [(i+1, line) for i, line in enumerate(lines)
                   if 'KERNEL VMFault' in line or 'nsa_indexer.py' in line]
        historical_errors[str(path.relative_to(ROOT))] = {
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'vmfault_count': sum('KERNEL VMFault' in line for line in lines),
            'evidence_lines': matches[:12],
        }
    with ThreadPoolExecutor(3) as pool:
        nodes = dict(pool.map(inspect_node, [27, 28, 29]))
    report = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'read-only audit; no new GLM inference or lifecycle measurements',
        'model': {
            'path': str(MODEL), 'architecture': config['architectures'],
            'layers': config['num_hidden_layers'],
            'attention_heads': config['num_attention_heads'],
            'index_heads': config['index_n_heads'],
            'quantization': {k: v for k, v in config['quantization_config'].items()
                             if k != 'modules_to_not_convert'},
            'index_tensor_bytes': index['metadata']['total_size'],
            'safetensor_file_bytes': sum((MODEL/f).stat().st_size for f in shards),
            'shards': len(shards), 'tensor_keys': len(index['weight_map']),
            'config_sha256': hashlib.sha256((MODEL/'config.json').read_bytes()).hexdigest(),
        },
        'nodes': nodes,
        'historical_native_failures': historical_errors,
        'completed_lifecycle': {
            'source': str(analysis_path.relative_to(ROOT)),
            'passed': analysis['passed'], 'nodes_used': [27, 28, 29, 30],
            'comparisons': analysis['paired_observations'],
            'comparison_baseline': 'HBM eviction followed by SGLang disk reload in a warm engine',
            'resident_ipc_s': next(t['first_correct_token_s'] for t in analysis['transitions']
                                   if t['phase'] == 'baseline_ipc_hit'),
        },
        'candidate_three_node_configuration': {
            'status': 'proposed, not validated for GLM generation',
            'compute_nodes': ['node27', 'node29'], 'tp': 16, 'gpus_per_compute_node': 8,
            'backing_dram_nodes': ['node27', 'node28', 'node29'],
            'ssd_node': 'node28',
            'ssd_parent': '/data/node28/shared/zhujianian',
            'notes': [
                'Serve SSD extents on node28, where this filesystem is local NVMe.',
                'One DRAM host cannot hold the full model; reserve runtime and transfer peaks.',
                'The existing lifecycle driver hardcodes TP2 and node30 SSD; it needs adaptation.',
                'Validate native generation before FP8/NSA capture, alias and TP16 restore.',
            ],
        },
    }
    (args.out/'evidence.json').write_text(json.dumps(report, indent=2)+'\n')
    failures = [(node, name) for node, checks in nodes.items() for name, row in checks.items()
                if row.get('returncode') != 0]
    print(json.dumps({'output': str(args.out/'evidence.json'), 'failed_checks': failures,
                      'model_gib': report['model']['index_tensor_bytes']/(1 << 30),
                      'native_glm_retested': False}))


if __name__ == '__main__':
    main()
