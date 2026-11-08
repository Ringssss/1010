"""Finite native-Qwen capture, calibration and independent online restores."""
import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'
sys.path[:0] = [str(HERE), str(HERE.parent / 'model'), str(ROOT / 'main_lib')]
from contracts import CHUNK, GiB, PROFILE, layout_plan, paired_overlap, target, resource_snapshot, physical_park_snapshot
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.cluster.planning import restore_plan
from fluidckpt.cluster.wire import planning_context
from fluidckpt.cluster.path_catalog import path_key
from fluidckpt.cluster.service_cost import new_state
from fluidckpt.serving.handoff import wait_for_exports
from fluidckpt.serving.measure import summarize
from predictor import build_model, calibrate_outer, physical, plan_workload
from evaluate import digest, evaluate, file_ref
from freeze import freeze

PROMPT = 'The capital of France is'


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def engine_call(endpoint, operation, body=None, timeout=300):
    data = None if body is None else json.dumps(body).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(endpoint + '/' + operation, data=data,
                                 headers={'Content-Type': 'application/json'})
    with opener.open(req, timeout=timeout) as response:
        raw = response.read((8 << 20) + 1)
    if len(raw) > 8 << 20:
        raise ValueError('engine metadata response exceeds bound')
    return json.loads(raw) if raw else {}


class Run:
    def __init__(self, args):
        self.args = args
        self.plan = json.loads(args.plan.read_text())
        if not self.plan['main_approved'] or not self.plan['fresh_admission_passed']:
            raise ValueError('main-approved fresh hardware plan required')
        self.roles = [r for r in self.plan['processes'] if r['kind'] == 'worker']
        self.endpoints = [r['endpoint'] for r in self.roles]
        self.cluster = Cluster(self.endpoints, PROFILE, control_mode='compact', executor='serial')
        self.engine = next(r for r in self.plan['processes'] if r['kind'] == 'engine')
        self.engine_endpoint = self.plan.get('engine_endpoint', 'http://12.11.3.27:36240')
        self.runtime = Path(self.engine['out']).parent
        self.clock = 'online_controller/' + socket.gethostname() + '/' + Path('/proc/sys/kernel/random/boot_id').read_text().strip() + '/' + self.plan['run_id'] + '/monotonic_ns'
        self.out = args.out.resolve()
        try:
            relative = self.out.relative_to(RESULTS)
        except ValueError:
            raise ValueError('output outside online result root')
        if not relative.parts or not relative.parts[0].startswith('execution_'):
            raise ValueError('new owned execution_* output directory required')
        self.out.mkdir(parents=True, exist_ok=False)
        self.bootstrap = json.loads(args.bootstrap.read_text()) if args.bootstrap else None
        self.pending = None
        self.engine_parked = self.bootstrap is not None

    def gate(self, workers=None, allocation_by_worker=None):
        workers = self.cluster.inventory(compact=True)[0] if workers is None else workers
        snapshot = resource_snapshot(workers, self.roles, self.plan['nodes'], self.clock, allocation_by_worker)
        snapshot['observed_controller_ns'] = time.monotonic_ns()
        self.gate_count = getattr(self, 'gate_count', 0) + 1
        self.last_gate_facts = snapshot
        if not snapshot['passed']:
            path = self.out / f'resource_gate_{self.gate_count:03d}_failed.json'
            write(path, snapshot)
            self.latest_gate_failure_ref = file_ref(path)
            failure = snapshot['failures'][0]
            raise MemoryError('resource gate failed: ' + json.dumps(failure, sort_keys=True))
        return snapshot

    def command(self, phase, metrics):
        path = Path(self.plan['engine_command_path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.new')
        with tmp.open('x') as stream:
            json.dump({'tag': phase, 'metrics': str(metrics), 'mode': 'restore'}, stream)
        tmp.replace(path)

    def golden(self):
        return engine_call(self.engine_endpoint, 'generate', {'text': PROMPT,
                           'sampling_params': {'temperature': 0, 'max_new_tokens': 32}}, timeout=180)

    def park(self):
        engine_call(self.engine_endpoint, 'release_memory_occupation', {'tags': ['weights']}, timeout=300)
        self.engine_parked = True

    def parked_evidence(self):
        command = json.loads(Path(self.plan['engine_command_path']).read_text())
        directory = Path(command['metrics'])
        evidence = []
        for rank in (0, 1):
            rows = [json.loads(line) for line in (directory / f'rank{rank}.jsonl').read_text().splitlines()]
            lifecycle = [row for row in rows if row['event'] in ('load_begin', 'weights_ready', 'release_begin', 'release_end', 'load_failed')]
            if not lifecycle or lifecycle[-1]['event'] != 'release_end':
                raise ValueError('current lifecycle events do not prove parked engine')
            begins = [row for row in lifecycle if row['event'] == 'release_begin']
            if not begins or begins[-1]['monotonic_ns'] >= lifecycle[-1]['monotonic_ns']:
                raise ValueError('physical pause requires complete release begin/end evidence')
            evidence.append({'release_begin': begins[-1], 'release_end': lifecycle[-1]})
        return evidence

    def check_park(self, manifests, before, after, label):
        facts = physical_park_snapshot(manifests, before, after, self.parked_evidence())
        path = self.out / (label + '_physical_park.json')
        write(path, facts)
        if not facts['passed']:
            raise MemoryError('physical pause failed: ' + json.dumps(facts['rows'], sort_keys=True))
        return facts

    def make_plan(self, replicas, targets, workers):
        plan = restore_plan(replicas, targets, workers, PROFILE, 'nearest', quantum=GiB)
        plan.update(executor='serial', chunk_bytes=CHUNK, control_context=planning_context(workers, self.endpoints))
        return plan

    def native_transfer(self, replicas, targets, workers):
        plan = self.make_plan(replicas, targets, workers)
        return self.cluster.transfer(plan, verify_before_ready=True)

    def init(self):
        mesh = self.cluster.call_all('mesh', [{} for _ in self.roles])
        warm = self.cluster.call_all('warm', [{'pairs': [[0, 2], [1, 3]]} for _ in self.roles])
        self.command(self.plan['run_id'] + '_bootstrap', self.runtime / 'metrics/bootstrap')
        result = {'status': 'passed', 'session': self.plan['run_id'], 'mesh': mesh, 'warm': warm,
                  'resource_gate': self.gate(), 'plan_ref': file_ref(self.args.plan),
                  'source_code_sha256': self.plan.get('source_assets_sha256', {})}
        write(self.out / 'init.json', result)

    def setup(self):
        phase = self.plan['run_id'] + '_bootstrap'
        wait_for_exports(Path(self.plan['handoff']) / phase, ranks=2, timeout=1800)
        self.gate(allocation_by_worker={0: 35164859056, 1: 35164859056})
        # The experimental wrapper uses native D2H capture, then full SHA against
        # the still-blocked original engine before publishing captured markers.
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(lambda rank: request(self.endpoints[rank], 'capture_regions',
                        {'tag': phase, 'snap': self.plan['run_id'], 'block_bytes': GiB,
                         'timeout': 1800, 'numa_by_rank': {str(rank): [self.roles[rank]['numa']] * 33}}, timeout=1800), (0, 1)))
        manifests = [r[0]['manifest'] for r in rows]
        if [m['n'] for m in manifests] != [35164859056, 35164859056]:
            raise ValueError('actual complete processed Qwen shard sizes changed')
        captures = [r[0]['verification'] for r in rows]
        if not all(v['passed'] and v['checked_bytes'] == m['n'] for v, m in zip(captures, manifests)):
            raise ValueError('complete capture SHA failed')
        write(self.out / 'checkpoint_capture.json', {'session': self.plan['run_id'], 'capture': rows,
                                                    'manifests': manifests, 'full_sha_passed': True})
        deadline = time.monotonic() + 600
        while True:
            try:
                engine_call(self.engine_endpoint, 'get_model_info', timeout=3)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        reference = self.golden()
        write(self.out / 'checkpoint_reference.json', {'reference': reference, 'sampling': {'temperature': 0, 'max_new_tokens': 32}})
        self.cluster.call_all('release_live', [{'tag': phase} for _ in self.roles])
        before_park = self.cluster.inventory(compact=True)[0]
        self.park()
        workers, replicas = self.cluster.inventory(compact=True)
        parked = self.check_park(manifests, before_park, workers, 'bootstrap')
        write(self.out / 'checkpoint_parked.json', {'parked': True, 'physical_park_passed': True,
                                                   'evidence': parked, 'workers': workers, 'replicas': replicas})
        # Exactly one complete rank per helper; no cross-NUMA fallback.
        self.gate(workers, allocation_by_worker={2: manifests[0]['n'], 3: manifests[1]['n']})
        helper_targets = [target(m, self.plan['run_id'] + f'/remote/rank{r}', r + 2, mode='replica', numa=self.roles[r + 2]['numa'])
                          for r, m in enumerate(manifests)]
        for t in helper_targets:
            t['tier'] = 'dram'
        helpers = self.native_transfer(replicas, helper_targets, workers)
        write(self.out / 'checkpoint_helpers.json', helpers)
        workers, replicas = self.cluster.inventory(compact=True)
        write(self.out / 'checkpoint_helper_inventory.json', {'workers': workers, 'replicas': replicas})
        self.gate(workers)
        cache_targets = [dict(target(m, self.plan['run_id'] + f'/cache/rank{r}', r, mode='replica', numa=self.roles[r]['numa']), n=GiB)
                         for r, m in enumerate(manifests)]
        cache = self.native_transfer([r for r in replicas if r['worker'] in (0, 1) and r['tier'] == 'dram'], cache_targets, workers)
        write(self.out / 'checkpoint_cache.json', cache)
        workers, replicas = self.cluster.inventory(compact=True)
        write(self.out / 'checkpoint_cache_inventory.json', {'workers': workers, 'replicas': replicas})
        if any(r['leases'] for r in replicas):
            raise RuntimeError('bootstrap source leases leaked')
        write(self.out / 'checkpoint_before_final_gate.json', {'status': 'built_pending_resource_gate',
                     'session': self.plan['run_id'], 'manifests': manifests, 'capture': rows, 'reference': reference,
                     'helper_build': helpers, 'cache_build': cache, 'parked': True, 'physical_park_passed': True,
                     'workers': workers, 'replicas': replicas})
        result = {'status': 'passed', 'session': self.plan['run_id'], 'manifests': manifests,
                  'reference': reference, 'capture': rows, 'helper_build': helpers, 'cache_build': cache,
                  'parked': True, 'physical_park_passed': True, 'physical_park': parked,
                  'replicas': replicas, 'workers': workers, 'resource_gate': self.gate(workers),
                  'binding_ref': file_ref(self.args.binding), 'plan_ref': file_ref(self.args.plan),
                  'ownership_prefix': self.plan['run_id'] + '/'}
        write(self.out / 'bootstrap.json', result)

    def context(self, workers, replicas):
        preview = layout_plan(self.bootstrap['manifests'], replicas, workers, self.endpoints, 'C0_mixed', 'preview', 'preview')
        paths = {}
        for rank in (0, 1):
            for owner, tier in ((rank, 'dram'), (rank, 'hbm'), (rank + 2, 'dram')):
                numa = next(r['numa'] for r in replicas if r['worker'] == owner and r['tier'] == tier)
                paths[path_key(owner, tier, numa, rank)] = {'endpoints': [owner, rank], 'operation_kind': 'network' if owner != rank else 'local_' + tier,
                                                         'source_tier': tier, 'source_numa': numa}
        remote = [p for p, c in paths.items() if c['operation_kind'] == 'network']
        return {'executor': 'serial', 'chunk_bytes': CHUNK, 'control_mode': 'compact', 'load_state': 'warm',
                'clock_domain': self.clock, 'clock_scope': 'single_controller', 'worker_physical': physical(workers),
                'worker_BDF': {str(w['worker']): w['runtime_identity']['actual_bdf'] for w in workers},
                'path_context': paths, 'shared_resource_paths': {'node27/receiver/shca_0': remote},
                'operation_count_interval': [66, 66], 'full_model_plan_workload': plan_workload(preview)}

    def state(self, workers, replicas, context):
        return dict(new_state(workers), fresh_binding_verified=True, load_state='warm', config_sha256=digest(context),
                    warm_pairs=['0:2', '1:3'], worker_BDF=context['worker_BDF'], binding_source_refs=[self.bootstrap['binding_ref']],
                    source_lineage={r['id']: {'worker': r['worker'], 'tier': r['tier'], 'numa': r['numa'],
                                            'identity': r['identity'], 'generation': r['generation'], 'source_epoch': r['epoch']} for r in replicas})

    def path_calibration(self, context):
        samples, raw, run_ids = {}, [], []
        for index, (path, fields) in enumerate(context['path_context'].items()):
            samples[path] = []
            for size in (GiB // 2, GiB):
                workers, replicas = self.cluster.inventory(compact=True)
                self.gate(workers)
                owner, receiver = fields['endpoints']
                m = self.bootstrap['manifests'][receiver]
                source = next(r for r in replicas if r['worker'] == owner and r['tier'] == fields['source_tier'] and r['tp'] == receiver and r['off'] == 0)
                key = self.plan['run_id'] + f'/cal/{index}/{size}'
                t = dict(target(m, key, receiver, mode='replica', numa=self.roles[receiver]['numa']), n=size)
                flow = self.native_transfer([source], [t], workers)
                self.cluster.retire(key)
                path_file = self.out / f'path_{index}_{size}.json'
                write(path_file, {'flow': flow, 'resource_gate': self.gate(), 'target_retired': True})
                durations = [r['elapsed_s'] for w in flow['execution'] for r in w['records']]
                samples[path].append({'nbytes': size, 'service_s': max(durations), 'passed': True, 'verified_bytes': flow['verification']['checked_bytes'],
                                      'evidence_kind': 'actual_path_calibration', 'config_sha256': digest(context), 'session': self.plan['run_id']})
                raw.append(file_ref(path_file)); run_ids.append(key)
        workers, replicas = self.cluster.inventory(compact=True)
        binding = self.shared_binding(workers)
        binding_file = self.out / 'shared_resource_binding.json'
        write(binding_file, binding)
        remote_paths = context['shared_resource_paths']['node27/receiver/shca_0']
        sources, targets = [], []
        for rank, path in enumerate(remote_paths):
            owner = context['path_context'][path]['endpoints'][0]
            sources.append(next(r for r in replicas if r['worker'] == owner and r['tp'] == rank and r['off'] == 0 and r['tier'] == 'dram'))
            targets.append(dict(target(self.bootstrap['manifests'][rank], self.plan['run_id'] + f'/sharedcal/rank{rank}', rank, mode='replica', numa=self.roles[rank]['numa']), n=GiB))
        flow = self.native_transfer(sources, targets, workers)
        overlap = paired_overlap(flow['execution'], flow['plan'])
        for t in targets:
            self.cluster.retire(t['id'])
        if not overlap['confirmed']:
            raise RuntimeError('actual paired remote calibration did not overlap')
        shared_file = self.out / 'shared_calibration_raw.json'
        write(shared_file, {'flow': flow, 'overlap': overlap, 'resource_binding': binding, 'resource_gate': self.gate(), 'targets_retired': True})
        shared = {'node27/receiver/shca_0': [{'total_bytes': 2 * GiB, 'service_s': max(w['execute_s'] for w in flow['execution']),
                   'flow_count': 2, 'paths': remote_paths, 'path_bytes': {p: GiB for p in remote_paths},
                   'overlap_confirmed': True, 'resource_binding_confirmed': True, 'passed': True,
                   'evidence_kind': 'actual_shared_calibration', 'config_sha256': digest(context), 'session': self.plan['run_id']}]}
        return samples, shared, raw + [file_ref(shared_file), file_ref(binding_file)], run_ids + [self.plan['run_id'] + '/sharedcal']

    def shared_binding(self, workers):
        evidence = []
        roots = set()
        for rank in (0, 1):
            runtime = workers[rank]['runtime_identity']
            hca = runtime['selected_hca_metadata']
            if not hca['exists'] or runtime['communication_environment']['NCCL_IB_HCA'] != 'shca_0':
                raise ValueError('actual receiver HCA binding not configured')
            roots.add(hca['pci_path'])
            logfile = Path(self.roles[rank]['out']) / 'child.log'
            with logfile.open('rb') as stream:
                raw = stream.read((8 << 20) + 1)
            if len(raw) > 8 << 20:
                raise ValueError('transport log evidence exceeds bounded read')
            lines = [line for line in raw.decode(errors='replace').splitlines()
                     if 'NET/' in line and 'Using' in line and 'shca_0' in line]
            if not lines:
                raise ValueError('actual native RCCL receiver shca_0 selection absent from log')
            evidence.append({'worker': rank, 'pid': runtime['pid'], 'hca': hca, 'transport_selection_lines': lines,
                             'log_path': str(logfile), 'observed_prefix_sha256': hashlib.sha256(raw).hexdigest()})
        if len(roots) != 1:
            raise ValueError('receivers do not share the same actual HCA PCI root')
        return {'confirmed': True, 'resource': 'node27/receiver/shca_0', 'receivers': evidence,
                'scope': 'fresh receiver processes select same actual HCA in native RCCL logs; no capacity claim'}

    def online(self, layout, run_id, context):
        phase = self.plan['run_id'] + '_' + run_id
        metrics = self.runtime / 'metrics' / phase
        if metrics.exists() or (Path(self.plan['handoff']) / phase).exists():
            raise FileExistsError('online phase already exists')
        parked = self.parked_evidence()
        before_gate = self.gate()
        self.command(phase, metrics)
        begin_wall, begin = time.time_ns(), time.monotonic_ns()
        workers, replicas = self.cluster.inventory(compact=True)
        self.assert_sources(replicas)
        plan = layout_plan(self.bootstrap['manifests'], replicas, workers, self.endpoints, layout, phase, str(metrics))
        state = self.state(workers, replicas, context)
        future = Future()
        def resume():
            try:
                future.set_result(engine_call(self.engine_endpoint, 'resume_memory_occupation', {'tags': ['weights']}, timeout=600))
            except BaseException as exc:
                future.set_exception(exc)
        self.pending = future
        self.engine_parked = False
        threading.Thread(target=resume, daemon=True, name='online-model-resume').start()
        wait_for_exports(Path(self.plan['handoff']) / phase, ranks=2, timeout=600)
        flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
        future.result(timeout=600)
        ready = time.monotonic_ns()
        self.pending = None
        timing = summarize(metrics, 2)
        raw_file = self.out / (run_id + '_raw.json')
        # Persist the inclusive request clock before any SHA/generation work.
        write(raw_file, {'load_begin_ns': begin, 'all_weights_ready_ns': ready, 'load_begin_wall_ns': begin_wall,
                         'clock_domain': self.clock, 'flow': flow, 'rank_timing': timing, 'before_resource_gate': before_gate,
                         'before_parked_evidence': parked})
        verify_started = time.monotonic_ns()
        verification = self.cluster.verify(flow['tx'])
        verify_end = time.monotonic_ns()
        reply = self.golden()
        exact = reply['text'] == self.bootstrap['reference']['text']
        if not verification['passed'] or verification['checked_bytes'] != 70329718112 or not exact:
            raise RuntimeError('post-ready fullSHA or golden output failed')
        self.cluster.finish(flow['tx'])
        before_park = self.cluster.inventory(compact=True)[0]
        self.park()
        workers_after, replicas_after = self.cluster.inventory(compact=True)
        parked_after = self.check_park(self.bootstrap['manifests'], before_park, workers_after, run_id)
        self.assert_sources(replicas_after)
        if any(w['transactions'] for w in workers_after) or any(r['leases'] for r in replicas_after):
            raise RuntimeError('online request transaction/lease cleanup failed')
        overlap = paired_overlap(flow['execution'], plan)
        if layout in ('C0_mixed', 'Hremote') and not overlap['confirmed']:
            raise RuntimeError('complete-model remote phase did not actually overlap')
        result = {'scenario': layout, 'run_id': run_id, 'session': self.plan['run_id'], 'plan': plan, 'workers': workers, 'state': state,
                  'load_begin_ns': begin, 'all_weights_ready_ns': ready, 'load_begin_wall_ns': begin_wall,
                  'clock_domain': self.clock, 'clock_scope': 'single_controller', 'online_weights_ready_s': (ready - begin) / 1e9,
                  'all_target_weights_usable': True, 'exact_reference_output_passed': exact, 'reply': reply,
                  'full_sha_passed': True, 'verified_bytes': verification['checked_bytes'], 'verification_position': 'after_all_weights_ready',
                  'verification': verification, 'verification_begin_ns': verify_started, 'verification_end_ns': verify_end,
                  'fresh_binding_verified': True, 'incarnation_sha256': digest(state['incarnations']),
                  'resource_gate_passed': True, 'resource_gate': self.gate(workers_after), 'cleanup_passed': True, 'engine_parked': True,
                  'physical_park_passed': True, 'physical_park': parked_after,
                  'shared_phase_overlap_confirmed': overlap['confirmed'], 'paired_phase_evidence': overlap,
                  'source_refs': [file_ref(raw_file)], 'evidence_kind': 'actual_model_online'}
        write(self.out / (run_id + '.json'), result)
        return result

    def assert_sources(self, replicas):
        expected = {r['id']: r for r in self.bootstrap['replicas']}
        actual = {r['id']: r for r in replicas}
        if set(expected) != set(actual):
            raise ValueError('persistent source set changed or temporary replica leaked')
        for key, prior in expected.items():
            if any(actual[key].get(k) != prior.get(k) for k in ('identity', 'tp', 'off', 'n', 'worker', 'tier', 'numa', 'epoch', 'generation')):
                raise ValueError('persistent source lineage changed: ' + key)

    def calibrate(self):
        workers, replicas = self.cluster.inventory(compact=True)
        self.assert_sources(replicas)
        context = self.context(workers, replicas)
        write(self.out / 'registered_context.json', context)
        samples, shared, refs, run_ids = self.path_calibration(context)
        model = build_model(context, workers, samples, shared, refs)
        c0 = self.online('C0_mixed', 'C0_mixed', context)
        c0.update(evidence_kind='actual_model_online_calibration', config_sha256=digest(context), source_ref=file_ref(self.out / 'C0_mixed.json'))
        model = calibrate_outer(model, c0)
        write(self.out / 'model.json', model)
        workers, replicas = self.cluster.inventory(compact=True)
        state = self.state(workers, replicas, context)
        scenarios, definitions = {}, {}
        for name in ('Hnear', 'Hremote'):
            phase = self.plan['run_id'] + '_' + name
            plan = layout_plan(self.bootstrap['manifests'], replicas, workers, self.endpoints, name, phase, str(self.runtime / 'metrics' / phase))
            comparison = {'weight_identity': [m['identity'] for m in self.bootstrap['manifests']],
                          'target_signature': [{k: t[k] for k in ('worker', 'identity', 'tp', 'off', 'n')} for t in plan['targets']],
                          'allowed_budgets': self.plan['nodes'], 'comparison_group': self.plan['run_id'],
                          'clock_domain': self.clock, 'plan_workload': plan_workload(plan), 'layout': name,
                          'requires_shared_overlap': name == 'Hremote'}
            definitions[name] = {'logical_bytes': 70329718112, 'validation_samples': 1, 'context': comparison}
            scenarios[name] = {'plan': plan, 'workers': workers, 'state': state}
        protocol = {'schema': 'online_cost_protocol_v1', 'registered_wall_ns': time.time_ns(), 'max_relative_error': .10,
                    'verification_position': 'after_all_weights_ready', 'model_weights_ready_required': True,
                    'independence_mode': 'new_request_current_incarnation', 'scenarios': definitions}
        inputs = {'calibration_run_ids': run_ids + ['C0_mixed'], 'calibration_incarnation_digests': [digest(state['incarnations'])], 'scenarios': scenarios}
        write(self.out / 'protocol.json', protocol); write(self.out / 'inputs.json', inputs)
        frozen = freeze(self.out / 'protocol.json', self.out / 'model.json', self.out / 'inputs.json')
        write(self.out / 'frozen_prediction.json', frozen)
        write(self.out / 'calibration.json', {'status': 'passed', 'context': context, 'samples': samples, 'shared_samples': shared,
                                             'bootstrap_ref': file_ref(self.args.bootstrap), 'frozen_ref': file_ref(self.out / 'frozen_prediction.json'),
                                             'model_ref': file_ref(self.out / 'model.json'), 'protocol_ref': file_ref(self.out / 'protocol.json')})

    def validate(self):
        calibration = json.loads(self.args.calibration.read_text())
        frozen_path = Path(calibration['frozen_ref']['path'])
        frozen = json.loads(frozen_path.read_text())
        protocol = json.loads(Path(calibration['protocol_ref']['path']).read_text())
        if file_ref(frozen_path) != calibration['frozen_ref']:
            raise ValueError('frozen predictions changed')
        observations = {}
        for name in ('Hnear', 'Hremote'):
            record = self.online(name, name, calibration['context'])
            record['context_sha256'] = digest(protocol['scenarios'][name]['context'])
            observations[name] = [record]
        write(self.out / 'observations.json', observations)
        validation = evaluate(frozen, observations)
        write(self.out / 'independent_validation.json', validation)

    def cleanup(self):
        if not self.bootstrap.get('parked'):
            raise ValueError('cleanup requires a successfully parked bootstrap')
        parked = self.parked_evidence()
        workers, replicas = self.cluster.inventory(compact=True)
        if any(w['transactions'] for w in workers) or any(r['leases'] for r in replicas):
            raise ValueError('do not retire sources with pending consumers or leases')
        # End-of-run teardown of task-owned floors explicitly bypasses last-copy
        # retention policy; native worker/Catalog ownership checks still apply.
        for replica in replicas:
            if not replica['id'].startswith(self.bootstrap['ownership_prefix']):
                raise ValueError('refuse unrelated replica retirement')
            request(self.endpoints[replica['worker']], 'retire', {'id': replica['id']})
        workers, replicas = self.cluster.inventory(compact=True)
        if replicas or any(any(w['used_bytes'].values()) or w['transactions'] for w in workers):
            raise RuntimeError('task catalog cleanup incomplete')
        write(self.out / 'cleanup.json', {'passed': True, 'workers': workers, 'source_floors_retired': True,
                                         'parked_evidence': parked,
                                         'scope': 'Catalog cleanup only; main still stops own engine before worker supervisors'})


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--action', choices=['init', 'bootstrap', 'calibrate', 'validate', 'cleanup'], required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--bootstrap', type=Path)
    p.add_argument('--binding', type=Path)
    p.add_argument('--calibration', type=Path)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--deadline-s', type=int, default=1800)
    a = p.parse_args()
    if not 30 <= a.deadline_s <= 3600:
        p.error('intrinsic phase deadline must be 30..3600 seconds')
    if not a.execute:
        print(json.dumps({'status': 'not_executed', 'action': a.action, 'GPU_or_RPC_calls': 0}))
        return
    if a.action == 'bootstrap' and not a.binding:
        p.error('bootstrap requires actual init binding file')
    if a.action in ('calibrate', 'validate', 'cleanup') and not a.bootstrap:
        p.error('current immutable bootstrap file required')
    if a.action == 'validate' and not a.calibration:
        p.error('validate requires immutable calibration/freeze artifacts')
    run = Run(a)
    finished = threading.Event()
    def deadline():
        if not finished.wait(a.deadline_s):
            try:
                write(run.out / 'deadline.json', {'status': 'deadline_exceeded', 'action': a.action,
                                                  'deadline_s': a.deadline_s, 'owner_cleanup_required': True,
                                                  'pending_resume': run.pending is not None and not run.pending.done(),
                                                  'active_transactions': list(run.cluster.active)})
            finally:
                os._exit(124)
    threading.Thread(target=deadline, daemon=True, name='online-controller-deadline').start()
    try:
        {'init': run.init, 'bootstrap': run.setup, 'calibrate': run.calibrate, 'validate': run.validate, 'cleanup': run.cleanup}[a.action]()
        print(json.dumps({'status': 'completed', 'action': a.action, 'out': str(run.out)}))
    except BaseException as exc:
        write(run.out / 'failure.json', {'status': 'failed', 'error': repr(exc), 'action': a.action,
                                       'pending_resume': run.pending is not None and not run.pending.done(),
                                       'engine_parked': run.engine_parked, 'active_transactions': list(run.cluster.active),
                                       'resource_gate_failure_ref': getattr(run, 'latest_gate_failure_ref', None),
                                       'completed_checkpoint_files': sorted(p.name for p in run.out.glob('checkpoint_*.json')),
                                       'automatic_source_retirement': False})
        raise
    finally:
        finished.set()


if __name__ == '__main__':
    main()
