"""Extract existing keepalive evidence without launching or stopping any service."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--history', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    evidence = {'kind': 'historical_artifact_audit', 'new_gpu_experiment': False,
                'root': str(root), 'runs': [], 'historical_commands': []}
    names = ['cluster06', 'local01', 'baselocation03', 'preplan01', 'multi03']
    for name in names:
        path = root / 'codex_coding/results/v0.1.23.3/task2' / name / 'summary.json'
        data = json.loads(path.read_text())
        changed = [p for p, sha in data.get('code_sha256', {}).items()
                   if (root / p).is_file() and digest(root / p) != sha]
        row = {'path': str(path.relative_to(root)), 'sha256': digest(path),
               'status': data.get('status'), 'scope': data.get('scope'),
               'current_source_differs': changed, 'restores': []}
        for restore in data.get('restores', []):
            verification = restore.get('verification', {})
            timing = restore.get('timing', {})
            row['restores'].append({
                'phase': restore['phase'], 'passed': restore.get('passed'),
                'load_s': timing.get('load_s'),
                'dispatch_to_ready_s': restore.get('dispatch_to_ready_s'),
                'verified': verification.get('passed'),
                'checked_bytes': verification.get('checked_bytes'),
                'generation_count': len(restore.get('replies', [])),
                'first_token_latency_recorded': False})
        evidence['runs'].append(row)
    path = root / 'codex_coding/results/v0.2.0/compare06_b2fix/summary.json'
    data = json.loads(path.read_text())
    evidence['ipc_comparison'] = {
        'path': str(path.relative_to(root)), 'sha256': digest(path),
        'implementation': 'FluidCKPT capture/attach hook; not upstream Weight Cache Daemon',
        'producer_load_s': data['producer_load_s']['load_s'],
        'consumer_load_s': data['consumer_load_s']['load_s'],
        'producer_ready_s': data['producer_ready_s'],
        'consumer_ready_s': data['consumer_ready_s'],
        'outputs_match': data['output_matches_producer'], 'passed': data['passed']}
    path = root / 'codex_coding/results/v0.2.0/armf02_plan/summary.json'
    data = json.loads(path.read_text())
    evidence['arm_f_plan'] = {'path': str(path.relative_to(root)), 'sha256': digest(path),
        'snapshot': data['snap'], 'shard_GiB': data['shard_GiB'],
        'plan': data['plan'], 'measured_restore': False,
        'layout': 'ingest/raw layout; not the processed capture layout'}
    path = root / 'codex_coding/results/v0.2.0/armf12/producer.log'
    log = path.read_text(errors='replace')
    evidence['arm_f_latest'] = {'path': str(path.relative_to(root)),
        'sha256': digest(path), 'capture_timeout': 'keeper did not fill rank 1 within 2400.0s' in log,
        'summary_present': path.with_name('summary.json').exists()}
    if args.history:
        needles = ['arm_f_keepalive.py --out', 'cluster/xfer.py node27 codex_coding/src/task2/cluster/arm_b2.py',
                   'cluster/keepctl.py start', 'cluster/mesh_init_run.py 2>&1']
        matches = {}
        for line in args.history.read_text().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            content = entry.get('message', {}).get('content', [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get('type') != 'tool_use':
                    continue
                command = block.get('input', {}).get('command', '')
                for needle in needles:
                    if needle in command:
                        matches[needle] = {'timestamp': entry.get('timestamp'), 'command': command}
        evidence['historical_commands'] = list(matches.values())
        evidence['history_source'] = str(args.history)
        evidence['historical_commands_notice'] = 'Recorded commands, including failed runs; do not replay cleanup or reuse run directories.'
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(path), 'runs': len(evidence['runs']),
                      'historical_commands': len(evidence['historical_commands']),
                      'new_gpu_experiment': False}))


if __name__ == '__main__':
    main()
