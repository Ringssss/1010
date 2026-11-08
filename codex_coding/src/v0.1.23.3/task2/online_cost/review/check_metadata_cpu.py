"""Exercise protected-name coverage, bounded payload reads and metadata-only transport."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import types

from collect_metadata import RESULTS, defaults, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    assert out.parent == RESULTS and out.name.startswith('review_')
    out.mkdir(parents=True, exist_ok=False)
    payload = source()
    module = types.ModuleType('metadata_cpu_fixture')
    exec(compile(payload, 'metadata_cpu_fixture', 'exec'), module.__dict__)
    imports = {node.names[0].name if isinstance(node, ast.Import) else node.module
               for node in ast.walk(ast.parse(payload)) if isinstance(node, (ast.Import, ast.ImportFrom))}
    assert not any(name and name.split('.')[0] in ('torch', 'ctypes', 'subprocess') for name in imports)
    gets = []
    image = 'sha256:' + 'a' * 64
    names = ['zhujianian-fk-existing', 'zjn-fluid-existing', 'other-user-service']
    def docker(endpoint):
        gets.append(endpoint)
        if endpoint == '/containers/json?all=1':
            return [{'Id': str(i + 1) * 64} for i in range(len(names))]
        if endpoint.startswith('/containers/'):
            i = int(endpoint.split('/')[2][0]) - 1
            return {'Id': str(i + 1) * 64, 'Name': '/' + names[i], 'Image': image,
                    'State': {'Pid': 123, 'Running': True, 'StartedAt': 'CPU_FIXTURE'}, 'HostConfig': {}, 'Mounts': []}
        if endpoint.startswith('/images/'):
            return {'Id': image}
        raise AssertionError('unexpected nonmetadata Docker operation')
    module.docker = docker
    reads = []
    def text(filename, limit=1 << 20):
        filename = str(filename); reads.append(filename)
        assert not any(token in filename for token in ('mem_info_vram', '/kfd/', '/dev/', '/environ'))
        if filename == '/proc/meminfo':
            return 'MemAvailable: 400000000 kB\nMemTotal: 500000000 kB\n'
        if filename.endswith('/cgroup'):
            return '0::/CPU_FIXTURE'
        if filename.endswith('/memory.stat'):
            return 'anon 10\nfile 20\nshmem 3\n'
        if filename.startswith('/sys/devices/system/node/'):
            return 'Node 4 MemFree: 1000000 kB\nNode 4 FilePages: 50000000 kB\nNode 4 Shmem: 40000000 kB\n'
        if filename.endswith('/tcp') or filename.endswith('/tcp6'):
            return 'sl local_address rem_address st\n'
        if filename.endswith('/numa_node'):
            return '4'
        return 'CPU_FIXTURE'
    module.limited_text = text
    with tempfile.TemporaryDirectory(dir=out) as directory:
        root = Path(directory)
        config = b'{"model_type":"CPU_FIXTURE"}\n'
        (root / 'config.json').write_bytes(config)
        (root / 'model.safetensors.index.json').write_text(json.dumps({'metadata': {'total_size': 4}, 'weight_map': {'weight': 'model-00001.safetensors'}}))
        (root / 'model-00001.safetensors').write_bytes(b'1234')
        specs = defaults('cpu_fixture', str(root), image)
        specs['node27']['hostname'] = module.os.uname().nodename
        body = module.collect(specs['node27'])
        assert {row['name'].lstrip('/') for row in body['protected_containers']} == set(names)
        assert body['self_cgroup']['stat_bytes'] == {'anon': 10, 'file': 20, 'shmem': 3, 'file_mapped': None, 'file_dirty': None, 'file_writeback': None}
        assert body['model']['files']['config.json']['sha256'] == hashlib.sha256(config).hexdigest()
        assert body['model']['payload_read_bytes'] == 0 and body['model']['checkpoint_file_bytes'] == 4
        assert set(body['numa_memory']) == {'4', '7'}
        assert body['numa_memory']['4']['free_plus_file_minus_shmem_bytes'] == 11000000 * 1024
        assert body['numa_memory']['4']['rough_rank40GiB_estimate_passed'] is False
        assert body['numa_memory']['4']['estimate_is_exclusive_allocation_proof'] is False
        specs['node27']['inspect_all_numa'] = True
        assert set(module.numa_memory(specs['node27'])) == set(map(str, range(8)))
        assert all(body[key] == 0 for key in ('gpu_api_calls', 'device_opens', 'remote_file_writes', 'signals_sent', 'container_execs', 'node29_accesses'))
        (root / 'config.json').write_bytes(b' ' * ((1 << 20) + 1))
        try:
            module.model_metadata(root)
        except ValueError:
            pass
        else:
            raise AssertionError('oversized metadata accepted')
    result = dict(passed=True, evidence_kind='CPU_FIXTURE', GPU_launches=0, SSH_commands=0,
        remote_writes=0, protected_all_names=True, forbidden_gpu_owner_reads_absent=True,
        model_payload_reads=0, oversized_metadata_rejected=True, selected_NUMA_and_all8_scope_passed=True,
        NUMA_reclaim_estimate_not_exclusive_proof=True, source_sha256=hashlib.sha256(payload).hexdigest(),
        mock_docker_gets=gets, mock_metadata_reads=reads)
    (out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('passed', 'evidence_kind', 'GPU_launches', 'SSH_commands')}))


if __name__ == '__main__':
    main()
