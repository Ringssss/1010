"""CPU replay of target-only diagnostic boundaries and evidence persistence."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import local_pause as local


def fixture_plan():
    roles = [{'kind': 'worker', 'node': 'node27', 'gpu': rank + 4,
              'worker_rank': rank, 'numa': [4, 7][rank], 'endpoint': f'CPU:{rank}',
              'argv': ['--mesh', 'node27:4-5', '--session', 'online_CPU_local'],
              'env': {'HIP_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7'}} for rank in (0, 1)]
    roles.append({'kind': 'engine', 'node': 'node27', 'gpus': [4, 5],
                  'argv': ['--base-gpu-id', '4'],
                  'env': {'HIP_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7',
                          'FLUIDCKPT_TP_SIZE': '2', 'FLUIDCKPT_TAG': 'online_CPU_local_bootstrap'}})
    return {'nodes': {'node27': {}}, 'processes': roles, 'run_id': 'online_CPU_local',
            'tp_size': 2, 'weights_bytes_by_rank': local.WEIGHTS, 'warm_pairs': [], 'handoff': 'CPU'}


def capture_fixture(rank):
    manifest = {'tp': rank, 'n': local.WEIGHTS[rank], 'identity': f'CPU_identity_{rank}'}
    regions = []
    for index in range(33):
        offset = index * local.native.GiB
        size = min(local.native.GiB, manifest['n'] - offset)
        regions.append({'id': f'online_CPU_local/rank{rank}/block{index}',
                        'identity': manifest['identity'], 'tp': rank, 'off': offset,
                        'n': size, 'allocated_bytes': (size + 4095) // 4096 * 4096,
                        'worker': rank, 'tier': 'dram', 'numa': [4, 7][rank],
                        'epoch': 'CPU_epoch', 'generation': 1, 'state': 'VALID', 'leases': 0})
    return [{'manifest': manifest, 'regions': regions,
             'verification': {'passed': True, 'checked_bytes': manifest['n'],
                              'mapping': 'same single IPC import used for capture and verification'}}]


def replay(out, plan, binding, parked_ok=True, capture_error=False):
    out.mkdir()
    run = local.LocalPause.__new__(local.LocalPause)
    run.plan, run.roles, run.endpoints = plan, plan['processes'][:2], ['CPU:0', 'CPU:1']
    run.out, run.engine_endpoint = out, 'CPU_engine'
    run.args = SimpleNamespace(plan=binding.parent / 'plan.json', binding=binding)
    run.engine_parked = False
    captures = [capture_fixture(rank) for rank in (0, 1)]
    replicas = [region for row in captures for region in row[0]['regions']]
    calls = []
    workers = [{'worker': rank, 'headroom_bytes': {'hbm': 100},
                'used_bytes': {'dram': (local.WEIGHTS[rank] + 4095) // 4096 * 4096, 'hbm': 0, 'ssd': 0},
                'transactions': []} for rank in (0, 1)]
    after = copy.deepcopy(workers)
    for rank, worker in enumerate(after):
        worker['headroom_bytes']['hbm'] += local.WEIGHTS[rank] if parked_ok else 0
    inventory_count = 0

    class Cluster:
        active = []

        def call_all(self, operation, bodies):
            calls.append((operation, bodies))
            if operation not in ('release_live', 'mesh', 'warm'):
                raise AssertionError('unexpected downstream operation')
            return [{'passed': True}] * 2

        def inventory(self, compact=True):
            nonlocal inventory_count
            inventory_count += 1
            return (workers if inventory_count == 1 else after), replicas

    run.cluster = Cluster()
    run.gate = lambda *args, **kwargs: {'passed': True, 'CPU_fixture': True}
    run.golden = lambda: {'text': 'CPU golden'}
    def park():
        calls.append(('park', None))
        run.engine_parked = True
    run.park = park
    run.parked_evidence = lambda: [
        {'release_begin': {'allocated_bytes': n}, 'release_end': {'allocated_bytes': 0}}
        for n in local.WEIGHTS]

    def request(endpoint, operation, body, timeout):
        if operation != 'capture_regions':
            raise AssertionError('unexpected RPC')
        rank = int(endpoint[-1])
        if capture_error and rank == 1:
            raise RuntimeError('CPU capture failure')
        return captures[rank]

    with patch.object(local.native, 'wait_for_exports'), patch.object(local.native, 'request', request), \
            patch.object(local.native, 'engine_call', return_value={}):
        try:
            run.pause()
            error = None
        except Exception as exc:
            error = exc
    return run, calls, error, captures, workers, after, replicas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
    plan = fixture_plan()
    check('two_local_workers_valid', len(local.validate_plan(plan)) == 2)
    for name, mutate in (
        ('remote_node_rejected', lambda p: p['nodes'].update(node30={})),
        ('remote_mesh_rejected', lambda p: p['processes'][0]['argv'].__setitem__(1, 'node27:4-5,node30:4-5')),
        ('nonempty_warm_rejected', lambda p: p.update(warm_pairs=[[0, 2]])),
        ('changed_weights_rejected', lambda p: p.update(weights_bytes_by_rank=[32, 32])),
        ('gpu_numbering_rejected', lambda p: p['processes'][2]['env'].update(HIP_VISIBLE_DEVICES='4,5')),
    ):
        changed = copy.deepcopy(plan)
        mutate(changed)
        try:
            local.validate_plan(changed)
            rejected = False
        except ValueError:
            rejected = True
        check(name, rejected)
    plan_path = args.out / 'plan.json'
    local.native.write(plan_path, plan)
    binding = args.out / 'init.json'
    local.native.write(binding, {'status': 'passed', 'session': plan['run_id'],
                                'plan_ref': local.native.file_ref(plan_path), 'warm_pairs': []})
    run, calls, error, captures, workers, after, replicas = replay(args.out / 'pass', plan, binding)
    check('full_pause_replay_passed', error is None)
    result = json.loads((run.out / 'local_pause.json').read_text())
    check('no_bootstrap_or_cost_qualification', not (run.out / 'bootstrap.json').exists()
          and result['online_cost_samples'] == 0 and not result['online_cost_qualified'])
    check('stop_before_helpers_cache_and_restore', [c[0] for c in calls] == ['release_live', 'park'])
    check('full66_local_regions_persist', result['persistence']['passed']
          and result['persistence']['regions'] == 66 and result['verified_bytes'] == sum(local.WEIGHTS))
    check('both_raw_pause_inventories_saved', all((run.out / name).exists() for name in
          ('checkpoint_before_park.json', 'checkpoint_after_park.json', 'local_physical_park.json')))
    broken = copy.deepcopy(replicas)
    broken[0]['generation'] += 1
    check('changed_owner_lineage_rejected', not local.persistence(
          [r[0]['manifest'] for r in captures], captures, after, broken)['passed'])
    run, _, error, *_ = replay(args.out / 'park_failed', plan, binding, parked_ok=False)
    check('physical_pause_failure_keeps_raw_evidence', isinstance(error, MemoryError)
          and (run.out / 'checkpoint_after_park.json').exists()
          and (run.out / 'local_physical_park.json').exists()
          and not (run.out / 'local_pause.json').exists())
    run, _, error, *_ = replay(args.out / 'capture_failed', plan, binding, capture_error=True)
    check('partial_capture_persisted_before_peer_failure', isinstance(error, RuntimeError)
          and (run.out / 'checkpoint_capture_rank0.json').exists()
          and not (run.out / 'local_pause.json').exists())
    changed_binding = args.out / 'changed_binding.json'
    local.native.write(changed_binding, {'status': 'passed', 'session': 'online_wrong',
                                         'plan_ref': local.native.file_ref(plan_path), 'warm_pairs': []})
    _, calls, error, *_ = replay(args.out / 'binding_failed', plan, changed_binding)
    check('wrong_init_binding_rejected_before_RPC', isinstance(error, ValueError) and not calls)
    for path in (Path(local.__file__), Path(__file__)):
        compile(path.read_text(), str(path), 'exec')
    check('syntax_passed', True)
    result = {'passed': all(c['passed'] for c in checks), 'checks': checks,
              'scope': 'CPU replay only; no GPU, SSH, Docker or cost qualification',
              'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(local.__file__), Path(__file__))}}
    local.native.write(args.out / 'qualification.json', result)
    print(json.dumps({'passed': result['passed'], 'checks': len(checks), 'out': str(args.out)}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
