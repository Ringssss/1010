"""Target-only full-Qwen physical-pause diagnostic; no cost calibration."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time

import controller as native

WEIGHTS = [35164859056, 35164859056]


def arg_value(argv, key):
    return argv[argv.index(key) + 1]


def validate_plan(plan):
    roles = [r for r in plan['processes'] if r['kind'] == 'worker']
    engines = [r for r in plan['processes'] if r['kind'] == 'engine']
    if (set(plan['nodes']) != {'node27'} or len(plan['processes']) != 3
            or len(engines) != 1 or len(roles) != 2
            or [(r['node'], r['gpu'], r['worker_rank']) for r in roles]
            != [('node27', 4, 0), ('node27', 5, 1)]):
        raise ValueError('local pause requires only node27 GPU4/5 two keepers and one engine')
    if plan.get('tp_size') != 2 or plan.get('weights_bytes_by_rank') != WEIGHTS:
        raise ValueError('full processed Qwen TP2 weights required')
    if plan.get('warm_pairs') != []:
        raise ValueError('explicit empty local diagnostic warm_pairs required')
    for role in roles:
        if arg_value(role['argv'], '--mesh') != 'node27:4-5':
            raise ValueError('local keeper mesh must exclude remote helpers')
        if arg_value(role['argv'], '--session') != plan['run_id']:
            raise ValueError('fresh diagnostic keeper session required')
    engine = engines[0]
    if (engine['node'] != 'node27' or engine['gpus'] != [4, 5]
            or arg_value(engine['argv'], '--base-gpu-id') != '4'
            or engine['env'].get('FLUIDCKPT_TP_SIZE') != '2'
            or engine['env'].get('FLUIDCKPT_TAG') != plan['run_id'] + '_bootstrap'):
        raise ValueError('fresh local GPU4/5 TP2 capture engine required')
    if any(r['env'].get('HIP_VISIBLE_DEVICES') != '0,1,2,3,4,5,6,7'
           for r in plan['processes']):
        raise ValueError('preserve full visible GPU numbering')
    return roles


def persistence(manifests, captures, workers, replicas):
    expected = {r['id']: r for capture in captures for r in capture[0]['regions']}
    actual = {r['id']: r for r in replicas}
    fields = ('identity', 'tp', 'off', 'n', 'worker', 'tier', 'numa', 'epoch', 'generation')
    lineage = set(expected) == set(actual) and all(
        all(actual[key].get(field) == prior.get(field) for field in fields)
        and actual[key]['state'] == 'VALID' and not actual[key]['leases']
        for key, prior in expected.items())
    allocated = [sum(r['allocated_bytes'] for r in capture[0]['regions']) for capture in captures]
    allocations = len(workers) == 2 and all(
        w['used_bytes']['dram'] == allocated[rank]
        and w['used_bytes']['hbm'] == 0 and w['used_bytes']['ssd'] == 0
        and not w['transactions'] for rank, w in enumerate(workers))
    return {'passed': lineage and allocations, 'catalog_lineage_unchanged': lineage,
            'exact_local_dram_allocation': allocations, 'regions': len(actual),
            'page_aligned_allocation_bytes_by_rank': allocated,
            'complete_weight_bytes': sum(m['n'] for m in manifests),
            'scope': 'capture full SHA before pause; same complete VALID DRAM owners after pause; no post-pause IPC import'}


class LocalPause(native.Run):
    def __init__(self, args):
        validate_plan(json.loads(args.plan.read_text()))
        args.bootstrap = None
        super().__init__(args)

    def init(self):
        mesh = self.cluster.call_all('mesh', [{} for _ in self.roles])
        warm = self.cluster.call_all('warm', [{'pairs': []} for _ in self.roles])
        self.command(self.plan['run_id'] + '_bootstrap', self.runtime / 'metrics/bootstrap')
        native.write(self.out / 'init.json', {
            'status': 'passed', 'session': self.plan['run_id'], 'mesh': mesh,
            'warm': warm, 'warm_pairs': [], 'resource_gate': self.gate(),
            'plan_ref': native.file_ref(self.args.plan), 'diagnostic_only': True,
            'source_code_sha256': self.plan.get('source_assets_sha256', {})})

    def pause(self):
        binding = json.loads(self.args.binding.read_text())
        if (binding.get('status') != 'passed' or binding.get('session') != self.plan['run_id']
                or binding.get('plan_ref') != native.file_ref(self.args.plan)
                or binding.get('warm_pairs') != []):
            raise ValueError('current exact local init binding required')
        phase = self.plan['run_id'] + '_bootstrap'
        started = time.monotonic_ns()
        native.wait_for_exports(Path(self.plan['handoff']) / phase, ranks=2, timeout=1800)
        native.write(self.out / 'checkpoint_exports.json', {
            'session': self.plan['run_id'], 'exports_ready': True,
            'resource_gate': self.gate(allocation_by_worker=dict(enumerate(WEIGHTS)))})

        def capture(rank):
            row = native.request(self.endpoints[rank], 'capture_regions', {
                'tag': phase, 'snap': self.plan['run_id'], 'block_bytes': native.GiB,
                'timeout': 1800, 'numa_by_rank': {str(rank): [self.roles[rank]['numa']] * 33}}, timeout=1800)
            native.write(self.out / f'checkpoint_capture_rank{rank}.json', row)
            return row

        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(capture, (0, 1)))
        manifests = [r[0]['manifest'] for r in rows]
        if [m['n'] for m in manifests] != WEIGHTS or [m['tp'] for m in manifests] != [0, 1]:
            raise ValueError('complete actual processed Qwen TP2 manifest changed')
        if not all(r[0]['verification']['passed']
                   and r[0]['verification']['checked_bytes'] == m['n']
                   and r[0]['verification']['mapping'] == 'same single IPC import used for capture and verification'
                   for r, m in zip(rows, manifests)):
            raise ValueError('single-mapping complete capture SHA required')
        native.write(self.out / 'checkpoint_capture.json', {
            'session': self.plan['run_id'], 'capture': rows, 'manifests': manifests,
            'full_sha_passed': True, 'capture_end_controller_ns': time.monotonic_ns()})
        deadline = time.monotonic() + 600
        while True:
            try:
                native.engine_call(self.engine_endpoint, 'get_model_info', timeout=3)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        reference = self.golden()
        native.write(self.out / 'checkpoint_reference.json', {
            'reference': reference, 'sampling': {'temperature': 0, 'max_new_tokens': 32}})
        self.cluster.call_all('release_live', [{'tag': phase} for _ in self.roles])
        before, before_replicas = self.cluster.inventory(compact=True)
        native.write(self.out / 'checkpoint_before_park.json', {
            'workers': before, 'replicas': before_replicas,
            'resource_gate': self.gate(before), 'observed_controller_ns': time.monotonic_ns()})
        self.park()
        after, replicas = self.cluster.inventory(compact=True)
        native.write(self.out / 'checkpoint_after_park.json', {
            'workers': after, 'replicas': replicas, 'observed_controller_ns': time.monotonic_ns(),
            'release_events': self.parked_evidence()})
        parked = self.check_park(manifests, before, after, 'local')
        kept = persistence(manifests, rows, after, replicas)
        native.write(self.out / 'checkpoint_persistence.json', kept)
        if not kept['passed']:
            raise ValueError('full captured local DRAM source persistence failed')
        gate = self.gate(after)
        native.write(self.out / 'local_pause.json', {
            'status': 'passed', 'session': self.plan['run_id'], 'diagnostic_only': True,
            'scope': 'target-only complete Qwen capture and physical pause; stop before helper/cache/calibration/C0',
            'full_sha_passed': True, 'verified_bytes': sum(WEIGHTS), 'manifests': manifests,
            'reference': reference, 'physical_park_passed': True, 'physical_park': parked,
            'persistence': kept, 'resource_gate': gate, 'parked': True,
            'workers': after, 'replicas': replicas, 'ownership_prefix': self.plan['run_id'] + '/',
            'plan_ref': native.file_ref(self.args.plan), 'binding_ref': native.file_ref(self.args.binding),
            'diagnostic_duration_s': (time.monotonic_ns() - started) / 1e9,
            'online_cost_samples': 0, 'online_cost_qualified': False,
            'owner_stop_required': True, 'automatic_source_retirement': False})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--action', choices=['init', 'pause'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--binding', type=Path)
    parser.add_argument('--deadline-s', type=int, default=1800)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not 30 <= args.deadline_s <= 3600:
        parser.error('intrinsic phase deadline must be 30..3600 seconds')
    if not args.execute:
        print(json.dumps({'status': 'not_executed', 'action': args.action, 'GPU_or_RPC_calls': 0}))
        return
    if args.action == 'pause' and not args.binding:
        parser.error('pause requires actual local init binding')
    run = LocalPause(args)
    finished = threading.Event()

    def deadline():
        if not finished.wait(args.deadline_s):
            try:
                native.write(run.out / 'deadline.json', {
                    'status': 'deadline_exceeded', 'action': args.action,
                    'deadline_s': args.deadline_s, 'owner_cleanup_required': True,
                    'engine_parked': run.engine_parked,
                    'active_transactions': list(run.cluster.active)})
            finally:
                os._exit(124)

    threading.Thread(target=deadline, daemon=True, name='local-pause-deadline').start()
    try:
        {'init': run.init, 'pause': run.pause}[args.action]()
        print(json.dumps({'status': 'completed', 'action': args.action, 'out': str(run.out)}))
    except BaseException as exc:
        native.write(run.out / 'failure.json', {
            'status': 'failed', 'error': repr(exc), 'action': args.action,
            'diagnostic_only': True, 'online_cost_samples': 0,
            'engine_parked': run.engine_parked, 'active_transactions': list(run.cluster.active),
            'resource_gate_failure_ref': getattr(run, 'latest_gate_failure_ref', None),
            'completed_checkpoint_files': sorted(p.name for p in run.out.glob('checkpoint_*.json')),
            'owner_cleanup_required': True, 'automatic_source_retirement': False})
        raise
    finally:
        finished.set()


if __name__ == '__main__':
    main()
