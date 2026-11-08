"""Independently reconstruct owned-context admission from immutable local raw artifacts."""
import argparse
import hashlib
import json
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('setup', 'prepared', 'admission', 'out'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    assert out.parent == RESULTS and out.name.startswith('review_') and not out.exists()
    bindings = json.loads((args.setup / 'setup.json').read_text())
    result = json.loads((args.admission / 'admission.json').read_text())
    checks, cards, sources = [], {}, []
    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        assert passed, name
    check('exact two node bindings', result['containers'] == bindings['containers'] and set(result['containers']) == {'node27', 'node30'})
    check('four owned GPU queries succeeded', result['passed'] and result['GPU_queries'] == 4)
    for node in ('node27', 'node30'):
        spec = json.loads((args.prepared / (node + '.setup.spec.json')).read_text())
        before_dir = args.admission / ('admission_before_' + node)
        after_dir = args.admission / ('admission_after_' + node)
        query_dir = args.admission / ('gpu_query_' + node)
        before = json.loads((before_dir / 'stdout.json').read_text())
        after = json.loads((after_dir / 'stdout.json').read_text())
        gpu = json.loads((query_dir / 'stdout.json').read_text())
        command = json.loads((query_dir / 'command.json').read_text())
        identity = bindings['containers'][node]
        check(node + ' bounded successful query', command['returncode'] == 0 and not command['timed_out'] and command['timeout_s'] == 55)
        argv = shlex.split(command['argv'][-1]) if Path(command['argv'][0]).name == 'ssh' else command['argv']
        query = json.loads(argv[-1])
        check(node + ' exact small whitelisted query', set(query) == {'node', 'roles', 'minimum_initial_gpu_free_bytes', 'per_selected_gpu_hbm_cap_bytes', 'task_hbm_cap_bytes'} and len(argv[-1].encode()) < 16384)
        check(node + ' exact owned docker ID', identity['container_id'] in argv)
        for label, body in (('before', before), ('after', after)):
            ci = body['owned_container']
            check(node + label + ' live ID/start/boot/image', ci['Id'] == identity['container_id'] and ci['State']['StartedAt'] == identity['container_started_at'] and body['boot_id'] == identity['boot_id'] and ci['Image'] == spec['image_id'] and ci['State']['Running'])
            check(node + label + ' owned pids1', body['owned_cgroup']['fields']['pids.current'] == '1')
            check(node + label + ' explicit hard cap', ci['HostConfig']['Memory'] == spec['container_hard_cap_bytes'] and ci['HostConfig']['MemorySwap'] == spec['container_hard_cap_bytes'])
            check(node + label + ' original129 asset hashes', len(spec['assets']) == 129 and all(body['source_assets'][row['relative_path']] == row['sha256'] for row in spec['assets']))
        old_by_id = {row['id']: row for row in after['protected_containers']}
        for old in before['protected_containers']:
            new = old_by_id[old['id']]
            check(node + ' protected ' + old['name'], all(old[key] == new[key] for key in ('id', 'name', 'image_id', 'running', 'started_at', 'pid', 'host_config')) and sorted(json.dumps(m, sort_keys=True) for m in old['mounts']) == sorted(json.dumps(m, sort_keys=True) for m in new['mounts']))
        check(node + ' original XML preserved', before['original_xml_sha256'] == after['original_xml_sha256'])
        check(node + ' complete actual HIP binding', len(gpu['gpus']) == 2 and {(row['gpu'], row['actual_bdf'], row['pci_numa']) for row in gpu['gpus']} == {(row['gpu'], row['bdf'], row['numa']) for row in spec['roles']})
        for row in gpu['gpus']:
            check(node + ' card' + str(row['gpu']) + ' free arithmetic and gate', row['bdf'] == row['actual_bdf'] and row['used_bytes'] == row['total_bytes'] - row['free_bytes'] and row['free_bytes'] >= spec['minimum_initial_gpu_free_bytes'] and row['passed'])
        total = sum(row['used_bytes'] for row in gpu['gpus'])
        check(node + ' aggregate upper arithmetic', total == gpu['aggregate_used_upper_bytes'] and gpu['aggregate_passed'] and gpu['passed'])
        if node == 'node30':
            check('source two cards plus256MiB <=2GiB', total + (256 << 20) <= 2 << 30)
        cards[node] = gpu
        for folder, filename in ((before_dir, 'stdout.json'), (after_dir, 'stdout.json'), (query_dir, 'stdout.json'), (query_dir, 'command.json')):
            path = folder / filename
            sources.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    output = {'status': 'independent_owned_admission_passed', 'checks': checks, 'check_count': len(checks),
              'cards': cards, 'sources': sources, 'new_model_restore_samples': 0,
              'GPU_launches_by_reviewer': 0, 'SSH_commands_by_reviewer': 0,
              'qualification_scope': 'fresh empty owned HIP contexts only; postmesh and full-model resource gates remain mandatory',
              'full_model_prediction_qualified': False}
    out.mkdir(parents=True)
    (out / 'qualification.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'status': output['status'], 'checks': len(checks), 'new_model_restore_samples': 0}))


if __name__ == '__main__':
    main()
