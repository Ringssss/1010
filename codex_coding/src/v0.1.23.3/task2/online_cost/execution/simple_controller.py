"""Three-worker adapter for the finite native complete-Qwen online workflow."""
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import controller as native
from contracts import CHUNK, GiB, target
from fluidckpt.cluster.control import request
from fluidckpt.cluster.path_catalog import path_key
from fluidckpt.cluster.service_cost import new_state
from fluidckpt.serving.handoff import wait_for_exports
from fluidckpt.serving.measure import summarize
from evaluate import digest, file_ref, verify_ref
from simple_contracts import (RANK_BYTES, SOURCE_NUMA, WARM_PAIRS, WORKFLOW,
                              layout_plan, serialized_sender_evidence, validate_roles)
from simple_model import (CALIBRATION_SIZES, POLICY, build_model, calibrate_outer,
                          freeze, physical, plan_workload)
from simple_resources import resource_snapshot

write = native.write


class SimpleRun(native.Run):
    def __init__(self, args):
        plan = json.loads(args.plan.read_text())
        validate_roles(plan)
        super().__init__(args)
        if self.bootstrap is not None:
            if self.bootstrap.get('session') != self.plan['run_id'] or self.bootstrap.get('workflow') != WORKFLOW:
                raise ValueError('bootstrap belongs to another workflow/session')
            if self.bootstrap.get('status') != 'passed' or not self.bootstrap.get('physical_park_passed'):
                raise ValueError('complete verified parked bootstrap required')
            if self.bootstrap['plan_ref'] != file_ref(args.plan):
                raise ValueError('bootstrap launch plan changed')
            verify_ref(self.bootstrap['binding_ref'])

    def gate(self, workers=None, allocation_by_worker=None, allocation_by_numa=None):
        workers = self.cluster.inventory(compact=True)[0] if workers is None else workers
        snapshot = resource_snapshot(workers, self.roles, self.plan['nodes'], self.clock,
                                     allocation_by_worker, allocation_by_numa)
        snapshot['observed_controller_ns'] = time.monotonic_ns()
        self.gate_count = getattr(self, 'gate_count', 0) + 1
        self.last_gate_facts = snapshot
        if not snapshot['passed']:
            path = self.out / f'resource_gate_{self.gate_count:03d}_failed.json'
            write(path, snapshot)
            self.latest_gate_failure_ref = file_ref(path)
            raise MemoryError('resource gate failed: ' + json.dumps(snapshot['failures'][0], sort_keys=True))
        return snapshot

    def init(self):
        mesh = self.cluster.call_all('mesh', [{} for _ in self.roles])
        warm = self.cluster.call_all('warm', [{'pairs': WARM_PAIRS} for _ in self.roles])
        self.command(self.plan['run_id'] + '_bootstrap', self.runtime / 'metrics/bootstrap')
        write(self.out / 'init.json', dict(status='passed', session=self.plan['run_id'], workflow=WORKFLOW,
              mesh=mesh, warm=warm, resource_gate=self.gate(), plan_ref=file_ref(self.args.plan),
              source_code_sha256=self.plan.get('source_assets_sha256', {})))

    def setup(self):
        phase = self.plan['run_id'] + '_bootstrap'
        binding = json.loads(self.args.binding.read_text())
        if binding.get('session') != self.plan['run_id'] or binding.get('workflow') != WORKFLOW or binding.get('status') != 'passed':
            raise ValueError('current simple init binding required')
        if binding['plan_ref'] != file_ref(self.args.plan):
            raise ValueError('init binding launch plan changed')
        wait_for_exports(Path(self.plan['handoff']) / phase, ranks=2, timeout=1800)
        self.gate(allocation_by_worker={0: RANK_BYTES, 1: RANK_BYTES},
                  allocation_by_numa={0: {4: RANK_BYTES}, 1: {7: RANK_BYTES}})
        # Reuse the proved single native IPC mapping for full D2H capture/SHA.
        def capture_rank(rank):
            try:
                row = request(self.endpoints[rank], 'capture_regions',
                              dict(tag=phase, snap=self.plan['run_id'], block_bytes=GiB, timeout=1800,
                                   numa_by_rank={str(rank): [SOURCE_NUMA[rank]] * 33}), timeout=1800)
            except BaseException as exc:
                write(self.out / f'checkpoint_capture_rank{rank}_failed.json',
                      dict(session=self.plan['run_id'], rank=rank, error=repr(exc), status='failed'))
                raise
            write(self.out / f'checkpoint_capture_rank{rank}.json',
                  dict(session=self.plan['run_id'], rank=rank, capture=row))
            return row
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(capture_rank, (0, 1)))
        manifests = [row[0]['manifest'] for row in rows]
        if [m['n'] for m in manifests] != [RANK_BYTES, RANK_BYTES]:
            raise ValueError('actual complete processed Qwen shard sizes changed')
        if not all(row[0]['verification']['passed'] and row[0]['verification']['checked_bytes'] == m['n']
                   for row, m in zip(rows, manifests)):
            raise ValueError('complete capture SHA failed')
        write(self.out / 'checkpoint_capture.json', dict(session=self.plan['run_id'], capture=rows,
                                                       manifests=manifests, full_sha_passed=True))
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
        write(self.out / 'checkpoint_reference.json', dict(reference=reference, sampling=dict(temperature=0, max_new_tokens=32)))
        self.cluster.call_all('release_live', [{'tag': phase} for _ in self.roles])
        before_park = self.cluster.inventory(compact=True)[0]
        self.park()
        workers, replicas = self.cluster.inventory(compact=True)
        parked = self.check_park(manifests, before_park, workers, 'bootstrap')
        write(self.out / 'checkpoint_parked.json', dict(parked=True, physical_park_passed=True,
                                                      evidence=parked, workers=workers, replicas=replicas))
        self.gate(workers, allocation_by_worker={2: 2 * RANK_BYTES},
                  allocation_by_numa={2: {4: RANK_BYTES, 7: RANK_BYTES}})
        helper_targets = [dict(target(m, self.plan['run_id'] + f'/remote/rank{rank}', 2,
                                mode='replica', numa=SOURCE_NUMA[rank]), tier='dram')
                          for rank, m in enumerate(manifests)]
        helpers = self.native_transfer(replicas, helper_targets, workers)
        write(self.out / 'checkpoint_helpers.json', helpers)
        workers, replicas = self.cluster.inventory(compact=True)
        write(self.out / 'checkpoint_helper_inventory.json', dict(workers=workers, replicas=replicas))
        self.gate(workers)
        cache_targets = [dict(target(m, self.plan['run_id'] + f'/cache/rank{rank}', rank,
                                     mode='replica', numa=SOURCE_NUMA[rank]), n=GiB)
                         for rank, m in enumerate(manifests)]
        cache = self.native_transfer([r for r in replicas if r['worker'] in (0, 1) and r['tier'] == 'dram'], cache_targets, workers)
        write(self.out / 'checkpoint_cache.json', cache)
        workers, replicas = self.cluster.inventory(compact=True)
        write(self.out / 'checkpoint_cache_inventory.json', dict(workers=workers, replicas=replicas))
        if any(r['leases'] for r in replicas) or any(w['transactions'] for w in workers):
            raise RuntimeError('bootstrap source leases/transactions leaked')
        remote = [r for r in replicas if r['worker'] == 2]
        if len(remote) != 2 or sorted((r['tp'], r['numa'], r['n'], r['tier'], r['state']) for r in remote) != [
                (0, 4, RANK_BYTES, 'dram', 'VALID'), (1, 7, RANK_BYTES, 'dram', 'VALID')]:
            raise ValueError('both complete helper sources and rank NUMA placement must be proven')
        pending = dict(status='built_pending_resource_gate', session=self.plan['run_id'], workflow=WORKFLOW,
                       manifests=manifests, reference=reference, capture=rows, helper_build=helpers,
                       cache_build=cache, parked=True, physical_park_passed=True, physical_park=parked,
                       replicas=replicas, workers=workers, binding_ref=file_ref(self.args.binding),
                       plan_ref=file_ref(self.args.plan), ownership_prefix=self.plan['run_id'] + '/')
        write(self.out / 'checkpoint_before_final_gate.json', pending)
        write(self.out / 'bootstrap.json', dict(pending, status='passed', resource_gate=self.gate(workers)))

    def context(self, workers, replicas):
        preview = layout_plan(self.bootstrap['manifests'], replicas, workers, self.endpoints, 'C0_mixed', 'preview', 'preview')
        paths = {}
        for rank in (0, 1):
            for owner, tier in ((rank, 'dram'), (rank, 'hbm'), (2, 'dram')):
                sources = [r for r in replicas if r['worker'] == owner and r['tier'] == tier and r['tp'] == rank]
                numas = {r['numa'] for r in sources}
                if numas != {SOURCE_NUMA[rank]}:
                    raise ValueError('rank-specific source NUMA missing or ambiguous')
                numa = numas.pop()
                paths[path_key(owner, tier, numa, rank)] = dict(endpoints=[owner, rank],
                    operation_kind='network' if owner != rank else 'local_' + tier,
                    source_tier=tier, source_numa=numa, tp_rank=rank)
        return dict(executor='serial', chunk_bytes=CHUNK, control_mode='compact', load_state='warm', workflow=WORKFLOW,
                    clock_domain=self.clock, clock_scope='single_controller', worker_physical=physical(workers),
                    worker_BDF={str(w['worker']): w['runtime_identity']['actual_bdf'] for w in workers},
                    path_context=paths, shared_resource_paths={}, operation_count_interval=[66, 66],
                    path_cost_policy=POLICY, calibration_sizes=CALIBRATION_SIZES, path_warmup_bytes=GiB,
                    full_model_plan_workload=plan_workload(preview))

    def state(self, workers, replicas, context):
        return dict(new_state(workers), fresh_binding_verified=True, load_state='warm', config_sha256=digest(context),
                    warm_pairs=['0:2', '1:2'], worker_BDF=context['worker_BDF'], binding_source_refs=[self.bootstrap['binding_ref']],
                    source_lineage={r['id']: dict(worker=r['worker'], tier=r['tier'], numa=r['numa'],
                                                identity=r['identity'], generation=r['generation'], source_epoch=r['epoch']) for r in replicas})

    def path_calibration(self, context):
        samples, refs, run_ids = {}, [], []
        for index, (path, fields) in enumerate(context['path_context'].items()):
            samples[path] = []
            warmup_ref = None
            for label, size in [('warmup', GiB)] + [('cal', n) for n in CALIBRATION_SIZES]:
                workers, replicas = self.cluster.inventory(compact=True)
                self.assert_sources(replicas)
                self.gate(workers)
                owner, receiver = fields['endpoints']
                m = self.bootstrap['manifests'][receiver]
                source = next(r for r in replicas if r['worker'] == owner and r['tier'] == fields['source_tier']
                              and r['tp'] == receiver and r['numa'] == fields['source_numa'] and r['off'] == 0)
                key = self.plan['run_id'] + f'/{label}/{index}/{size}'
                t = dict(target(m, key, receiver, mode='replica', numa=SOURCE_NUMA[receiver]), n=size)
                flow = self.native_transfer([source], [t], workers)
                sender = serialized_sender_evidence(flow['execution'], flow['plan'])
                self.cluster.retire(key)
                path_file = self.out / f'path_{label}_{index}_{size}.json'
                write(path_file, dict(flow=flow, sender_serialization=sender, resource_gate=self.gate(),
                                     target_retired=True, sample_role=label, path=path, config_sha256=digest(context), session=self.plan['run_id']))
                durations = [r['elapsed_s'] for w in flow['execution'] for r in w['records']]
                refs.append(file_ref(path_file)); run_ids.append(key)
                if label == 'warmup':
                    warmup_ref = file_ref(path_file)
                    continue
                samples[path].append(dict(nbytes=size, service_s=max(durations), passed=True,
                    verified_bytes=flow['verification']['checked_bytes'], evidence_kind='actual_path_calibration',
                    config_sha256=digest(context), session=self.plan['run_id'], warmup_ref=warmup_ref, source_ref=file_ref(path_file)))
        return samples, {}, refs, run_ids

    def online(self, layout, run_id, context):
        phase = self.plan['run_id'] + '_' + run_id
        metrics = self.runtime / 'metrics' / phase
        if metrics.exists() or (Path(self.plan['handoff']) / phase).exists():
            raise FileExistsError('online phase already exists')
        parked, before_gate = self.parked_evidence(), self.gate()
        self.command(phase, metrics)
        begin_wall, begin = time.time_ns(), time.monotonic_ns()
        workers, replicas = self.cluster.inventory(compact=True)
        self.assert_sources(replicas)
        plan = layout_plan(self.bootstrap['manifests'], replicas, workers, self.endpoints, layout, phase, str(metrics))
        state = self.state(workers, replicas, context)
        future = Future()
        def resume():
            try:
                future.set_result(native.engine_call(self.engine_endpoint, 'resume_memory_occupation', {'tags': ['weights']}, timeout=600))
            except BaseException as exc:
                future.set_exception(exc)
        self.pending, self.engine_parked = future, False
        threading.Thread(target=resume, daemon=True, name='simple-model-resume').start()
        wait_for_exports(Path(self.plan['handoff']) / phase, ranks=2, timeout=600)
        flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
        future.result(timeout=600)
        ready = time.monotonic_ns()
        self.pending = None
        timing = summarize(metrics, 2)
        raw_file = self.out / (run_id + '_raw.json')
        # Preserve the measured inclusive clock before full SHA and generation.
        write(raw_file, dict(load_begin_ns=begin, all_weights_ready_ns=ready, load_begin_wall_ns=begin_wall,
              clock_domain=self.clock, flow=flow, rank_timing=timing, before_resource_gate=before_gate,
              before_parked_evidence=parked))
        ready_gate = self.gate()
        write(self.out / (run_id + '_ready_resource_gate.json'), ready_gate)
        sender = serialized_sender_evidence(flow['execution'], plan)
        verify_started = time.monotonic_ns()
        verification = self.cluster.verify(flow['tx'])
        verify_end = time.monotonic_ns()
        reply = self.golden()
        exact = reply['text'] == self.bootstrap['reference']['text']
        if not verification['passed'] or verification['checked_bytes'] != 2 * RANK_BYTES or not exact:
            raise RuntimeError('post-ready full SHA or golden output failed')
        self.cluster.finish(flow['tx'])
        before_park = self.cluster.inventory(compact=True)[0]
        self.park()
        workers_after, replicas_after = self.cluster.inventory(compact=True)
        parked_after = self.check_park(self.bootstrap['manifests'], before_park, workers_after, run_id)
        self.assert_sources(replicas_after)
        if any(w['transactions'] for w in workers_after) or any(r['leases'] for r in replicas_after):
            raise RuntimeError('online request transaction/lease cleanup failed')
        result = dict(scenario=layout, run_id=run_id, session=self.plan['run_id'], workflow=WORKFLOW,
                      plan=plan, workers=workers, state=state, load_begin_ns=begin,
                      all_weights_ready_ns=ready, load_begin_wall_ns=begin_wall,
                      clock_domain=self.clock, clock_scope='single_controller', online_weights_ready_s=(ready-begin)/1e9,
                      all_target_weights_usable=True, exact_reference_output_passed=exact, reply=reply,
                      full_sha_passed=True, verified_bytes=verification['checked_bytes'], verification_position='after_all_weights_ready',
                      verification=verification, verification_begin_ns=verify_started, verification_end_ns=verify_end,
                      fresh_binding_verified=True, incarnation_sha256=digest(state['incarnations']),
                      resource_gate_passed=True, resource_gate=self.gate(workers_after), ready_resource_gate=ready_gate, cleanup_passed=True,
                      engine_parked=True, physical_park_passed=True, physical_park=parked_after,
                      sender_serialization=sender, shared_phase_overlap_confirmed=False,
                      source_refs=[file_ref(raw_file)], evidence_kind='actual_model_online')
        write(self.out / (run_id + '.json'), result)
        return result

    def calibrate(self):
        workers, replicas = self.cluster.inventory(compact=True)
        self.assert_sources(replicas)
        context = self.context(workers, replicas)
        write(self.out / 'registered_context.json', context)
        samples, shared, refs, run_ids = self.path_calibration(context)
        model = build_model(context, workers, samples, shared, refs)
        write(self.out / 'checkpoint_path_model.json', model)
        c0 = self.online('C0_mixed', 'C0_mixed', context)
        c0.update(evidence_kind='actual_model_online_calibration', config_sha256=digest(context), source_ref=file_ref(self.out / 'C0_mixed.json'))
        model = calibrate_outer(model, c0)
        write(self.out / 'model.json', model)
        workers, replicas = self.cluster.inventory(compact=True)
        self.assert_sources(replicas)
        state = self.state(workers, replicas, context)
        scenarios, definitions = {}, {}
        for name in ('Hnear', 'Hremote'):
            phase = self.plan['run_id'] + '_' + name
            plan = layout_plan(self.bootstrap['manifests'], replicas, workers, self.endpoints, name, phase, str(self.runtime / 'metrics' / phase))
            comparison = dict(weight_identity=[m['identity'] for m in self.bootstrap['manifests']],
                target_signature=[{k: t[k] for k in ('worker', 'identity', 'tp', 'off', 'n')} for t in plan['targets']],
                allowed_budgets=self.plan['nodes'], comparison_group=self.plan['run_id'], clock_domain=self.clock,
                plan_workload=plan_workload(plan), layout=name, requires_shared_overlap=False,
                requires_sender_serialization=name == 'Hremote', workflow=WORKFLOW)
            definitions[name] = dict(logical_bytes=2 * RANK_BYTES, validation_samples=1, context=comparison)
            scenarios[name] = dict(plan=plan, workers=workers, state=state)
        protocol = dict(schema='online_cost_protocol_v1', registered_wall_ns=time.time_ns(), max_relative_error=.10,
                        verification_position='after_all_weights_ready', model_weights_ready_required=True,
                        independence_mode='new_request_current_incarnation', scenarios=definitions)
        inputs = dict(calibration_run_ids=run_ids + ['C0_mixed'], calibration_incarnation_digests=[digest(state['incarnations'])], scenarios=scenarios)
        write(self.out / 'protocol.json', protocol); write(self.out / 'inputs.json', inputs)
        frozen = freeze(self.out / 'protocol.json', self.out / 'model.json', self.out / 'inputs.json')
        write(self.out / 'frozen_prediction.json', frozen)
        write(self.out / 'calibration.json', dict(status='passed', workflow=WORKFLOW, context=context,
              samples=samples, shared_samples={}, bootstrap_ref=file_ref(self.args.bootstrap),
              frozen_ref=file_ref(self.out / 'frozen_prediction.json'), model_ref=file_ref(self.out / 'model.json'),
              protocol_ref=file_ref(self.out / 'protocol.json')))

    def validate(self):
        calibration = json.loads(self.args.calibration.read_text())
        if calibration.get('workflow') != WORKFLOW or calibration['bootstrap_ref'] != file_ref(self.args.bootstrap):
            raise ValueError('calibration must belong to the current simple bootstrap')
        verify_ref(calibration['model_ref']); verify_ref(calibration['protocol_ref'])
        # Native evaluation retains per-case 10% checks and freeze chronology.
        return super().validate()


def main():
    native.Run = SimpleRun
    native.main()


if __name__ == '__main__':
    main()
