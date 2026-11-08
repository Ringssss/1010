"""Independently verify archived metadata scope and report host/NUMA admission separately."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    assert out.parent == RESULTS and out.name.startswith('review_') and not out.exists()
    metadata = args.metadata.resolve()
    summary = json.loads((metadata / 'summary.json').read_text())
    specs = json.loads((metadata / 'metadata.spec.json').read_text())
    assert set(specs) == set(summary['nodes'])
    assert set(specs) in ({'node27', 'node28', 'node30'}, {'node27', 'node30'})
    assert hashlib.sha256((metadata / 'metadata.source.py').read_bytes()).hexdigest() == summary['source_sha256']
    checks, nodes, sources = [], {}, []
    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        assert passed, name
    for node, spec in specs.items():
        body = json.loads((metadata / node / 'stdout.json').read_text())
        command = json.loads((metadata / node / 'command.json').read_text())
        check(node + ' bounded collector succeeded', command['returncode'] == 0 and not command['timed_out'] and command['timeout_s'] == 35)
        check(node + ' correct host and node', body['node'] == node and body['hostname'] == spec['hostname'])
        for key in ('gpu_api_calls', 'device_opens', 'gpu_resource_queries', 'remote_file_writes', 'signals_sent', 'container_execs', 'node29_accesses'):
            check(node + ' ' + key, body[key] == 0)
        check(node + ' unavailable GPU capacity remains unknown', body['physical_gpu_available'] is None)
        check(node + ' requested exact image exists', body['images'][spec['image_id']]['Id'] == spec['image_id'])
        check(node + ' new container name and ports free', body['candidate_name_free'] and all(body['ports_free'].values()))
        check(node + ' exact complete selected role set',
              {(r['gpu'], r['bdf'], r['numa']) for r in spec['roles']} ==
              {(r['gpu'], r['bdf'], r['numa']) for r in body['selected_gpus']} and
              len(spec['roles']) == len(body['selected_gpus']))
        for role, actual in zip(spec['roles'], body['selected_gpus']):
            check(node + ' requested safe PCI NUMA exists ' + str(role['gpu']),
                  actual['bdf'] == role['bdf'] and actual['exists'] and int(actual['safe_pci']['numa_node']) == role['numa'])
        available = body['host_memory']['MemAvailable']
        minimum = spec['minimum_host_available_bytes']
        check(node + ' host gate independently reconstructed', body['host_memory_admission']['passed'] == (available >= minimum))
        nodes[node] = {'host_available_bytes': available, 'initial_minimum_bytes': minimum,
            'host_admission_passed': available >= minimum, 'protected_container_count': len(body['protected_containers']),
            'selected_NUMA': {n: record for n, record in body.get('numa_memory', {}).items() if record['selected_role_numa']},
            'other_NUMA': {n: record for n, record in body.get('numa_memory', {}).items() if not record['selected_role_numa']}}
        if node == 'node27':
            check('target model payload never read', body['model']['payload_read_bytes'] == 0)
        for name in ('stdout.json', 'command.json'):
            path = metadata / node / name
            sources.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    out.mkdir(parents=True)
    result = {'status': 'independent_metadata_integrity_passed', 'check_count': len(checks), 'checks': checks,
        'nodes': nodes, 'sources': sources, 'GPU_launches_by_reviewer': 0, 'SSH_commands_by_reviewer': 0,
        'topology_scope': 'two_node_27_30' if set(specs) == {'node27', 'node30'} else 'three_node_27_28_30',
        'GPU_capacity_admission_qualified': False, 'NUMA_reclaim_estimate_is_allocation_proof': False}
    (out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'checks': len(checks), 'host_gates': {n: d['host_admission_passed'] for n, d in nodes.items()}, 'GPU_capacity_admission_qualified': False}))


if __name__ == '__main__':
    main()
