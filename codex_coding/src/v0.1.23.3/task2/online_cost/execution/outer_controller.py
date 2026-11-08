"""Four complete requests in one persistent controller, without path resampling."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'model')]
from evaluate import digest, evaluate, file_ref, require, verify_ref
from simple_contracts import RANK_BYTES, WORKFLOW, layout_plan
from simple_controller import SimpleRun, write
from simple_model import plan_workload
from outer_model import (DOMAIN, ORDER, calibrate_outer, freeze, inherit_model,
                         load_ref, validate_record, validate_registration)


class OuterRun(SimpleRun):
    def __init__(self, args):
        plan = json.loads(args.plan.read_text())
        outer = plan['outer_protocol']
        require(outer['outer_domain'] == DOMAIN and outer['complete_request_order'] == ORDER and
                outer['validation_order'] == ORDER[2:] and outer['max_complete_requests'] == 4,
                'finite post-prime outer protocol required')
        super().__init__(args)
        self.complete_requests = 0
        self.prime_ref = None
        self.registration_ref = None

    def path_calibration(self, context):
        raise ValueError('outer experiment inherits exact source service; path resampling prohibited')

    def state(self, workers, replicas, context):
        return dict(super().state(workers, replicas, context), outer_domain=DOMAIN,
            controller_pid=os.getpid(), current_session=self.plan['run_id'],
            prior_complete_restores=self.complete_requests, prime_ref=self.prime_ref)

    def online(self, layout, run_id, context):
        require(self.registration_ref is not None and self.complete_requests < 4 and
                run_id == ORDER[self.complete_requests], 'unregistered/replayed complete online request')
        registration = load_ref(self.registration_ref)
        require(registration['controller_pid'] == os.getpid() and
                registration['controller_clock_domain'] == self.clock, 'persistent controller changed')
        index = self.complete_requests + 1
        record = super().online(layout, run_id, context)
        # Preserve the original SimpleRun artifacts. New metadata has its own
        # immutable file and binds the exact pre-verification raw clock.
        record.update(controller_pid=os.getpid(), complete_request_index=index,
            prior_complete_restores=index-1, completed_wall_ns=time.time_ns(),
            outer_domain=DOMAIN, registration_ref=self.registration_ref,
            base_record_ref=file_ref(self.out / (run_id + '.json')),
            request_role='prime' if index == 1 else 'outer_calibration' if index == 2 else 'holdout')
        path = self.out / (run_id + '_record.json')
        write(path, record)
        self.complete_requests += 1
        if index == 1:
            self.prime_ref = file_ref(path)
        return record

    def calibrate(self):
        require(self.args.action == 'sequence' and self.complete_requests == 0,
                'calibration is only the beginning of the one persistent sequence')
        workers, replicas = self.cluster.inventory(compact=True)
        self.assert_sources(replicas)
        context = self.context(workers, replicas)
        write(self.out / 'registered_context.json', context)
        registration = dict(self.plan['outer_protocol'], registered_wall_ns=time.time_ns(),
            controller_pid=os.getpid(), controller_clock_domain=self.clock,
            current_session=self.plan['run_id'], bootstrap_ref=file_ref(self.args.bootstrap))
        validate_registration(registration)
        # Only the bootstrap phase may exist before this sequence is registered.
        for name in ORDER:
            phase = self.plan['run_id'] + '_' + name
            require(not (self.runtime / 'metrics' / phase).exists() and
                    not (Path(self.plan['handoff']) / phase).exists(), 'complete request already exists')
        write(self.out / 'outer_registration.json', registration)
        self.registration_ref = file_ref(self.out / 'outer_registration.json')
        initial_state = self.state(workers, replicas, context)
        model = inherit_model(registration['source_checkpoint_path_model_ref'], context,
                              workers, initial_state, self.registration_ref)
        write(self.out / 'checkpoint_path_model.json', model)
        prime = self.online('C0_mixed', 'C0_prime', context)
        validate_record(prime, model, 1)
        calibration = self.online('C0_mixed', 'C0_cal', context)
        calibration_ref = file_ref(self.out / 'C0_cal_record.json')
        model = calibrate_outer(model, self.prime_ref, calibration_ref)
        write(self.out / 'model.json', model)
        workers, replicas = self.cluster.inventory(compact=True)
        self.assert_sources(replicas)
        state = self.state(workers, replicas, context)
        scenarios, definitions = {}, {}
        for name in ORDER[2:]:
            phase = self.plan['run_id'] + '_' + name
            plan = layout_plan(self.bootstrap['manifests'], replicas, workers, self.endpoints,
                               name, phase, str(self.runtime / 'metrics' / phase))
            comparison = dict(weight_identity=[m['identity'] for m in self.bootstrap['manifests']],
                target_signature=[{k: t[k] for k in ('worker', 'identity', 'tp', 'off', 'n')} for t in plan['targets']],
                allowed_budgets=self.plan['nodes'], comparison_group=self.plan['run_id'], clock_domain=self.clock,
                plan_workload=plan_workload(plan), layout=name, requires_shared_overlap=False,
                requires_sender_serialization=name == 'Hremote', workflow=WORKFLOW, outer_domain=DOMAIN,
                controller_pid=os.getpid())
            definitions[name] = dict(logical_bytes=2 * RANK_BYTES, validation_samples=1, context=comparison)
            scenarios[name] = dict(plan=plan, workers=workers, state=state)
        protocol = dict(schema='online_cost_protocol_v1', registered_wall_ns=registration['registered_wall_ns'],
            max_relative_error=.10, verification_position='after_all_weights_ready', model_weights_ready_required=True,
            independence_mode='new_request_current_incarnation', scenarios=definitions,
            validation_order=ORDER[2:], outer_domain=DOMAIN, registration_ref=self.registration_ref)
        inputs = dict(calibration_run_ids=ORDER[:2],
            calibration_incarnation_digests=[calibration['incarnation_sha256']], scenarios=scenarios)
        write(self.out / 'protocol.json', protocol)
        write(self.out / 'inputs.json', inputs)
        frozen = freeze(self.out / 'protocol.json', self.out / 'model.json', self.out / 'inputs.json')
        write(self.out / 'frozen_prediction.json', frozen)
        write(self.out / 'calibration.json', dict(status='passed', workflow=WORKFLOW, outer_domain=DOMAIN,
            context=context, current_path_calibration_samples=0, current_path_warmup_samples=0,
            inherited_path_model_ref=registration['source_checkpoint_path_model_ref'],
            bootstrap_ref=file_ref(self.args.bootstrap), registration_ref=self.registration_ref,
            prime_ref=self.prime_ref, calibration_ref=calibration_ref,
            frozen_ref=file_ref(self.out / 'frozen_prediction.json'), model_ref=file_ref(self.out / 'model.json'),
            protocol_ref=file_ref(self.out / 'protocol.json')))

    def validate(self):
        require(self.args.action == 'sequence' and self.complete_requests == 2,
                'holdouts must continue the same controller after exactly two completed requests')
        calibration = load_ref(file_ref(self.out / 'calibration.json'))
        require(calibration['bootstrap_ref'] == file_ref(self.args.bootstrap), 'current bootstrap changed')
        frozen = load_ref(calibration['frozen_ref'])
        model = load_ref(calibration['model_ref'])
        protocol = load_ref(calibration['protocol_ref'])
        observations = {}
        previous = load_ref(calibration['calibration_ref'])
        for name in ORDER[2:]:
            verify_ref(calibration['frozen_ref'])
            verify_ref(calibration['model_ref'])
            record = self.online(name, name, calibration['context'])
            validate_record(record, model, self.complete_requests)
            require(previous['completed_wall_ns'] < record['load_begin_wall_ns'] and
                    previous['verification_end_ns'] < record['load_begin_ns'], 'complete requests overlap or order changed')
            require(record['load_begin_wall_ns'] > frozen['payload']['freeze_wall_ns'] and
                    digest(record['plan']) == frozen['payload']['predictions'][name]['plan_sha256'],
                    'heldout changed frozen plan or started before freeze')
            record['context_sha256'] = digest(protocol['scenarios'][name]['context'])
            observations[name] = [record]
            previous = record
        write(self.out / 'observations.json', observations)
        result = evaluate(frozen, observations)
        result.update(outer_domain=DOMAIN, persistent_controller=dict(pid=os.getpid(), clock_domain=self.clock),
            complete_request_order=ORDER, complete_requests=self.complete_requests,
            inherited_path_service=True, current_path_calibration_samples=0,
            prime_used_for_fitting=False, historical_holdouts_used_for_fitting=False)
        write(self.out / 'independent_validation.json', result)

    def sequence(self):
        self.calibrate()
        self.validate()
        require(self.complete_requests == 4, 'finite complete request count changed')
        write(self.out / 'sequence.json', dict(status='passed', complete_request_order=ORDER,
            complete_requests=4, controller_pid=os.getpid(), clock_domain=self.clock,
            outer_domain=DOMAIN, registration_ref=self.registration_ref,
            validation_ref=file_ref(self.out / 'independent_validation.json')))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--action', choices=['init', 'bootstrap', 'sequence', 'cleanup'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--bootstrap', type=Path)
    parser.add_argument('--binding', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--deadline-s', type=int, default=3600)
    args = parser.parse_args()
    if not 30 <= args.deadline_s <= 3600:
        parser.error('intrinsic phase deadline must be 30..3600 seconds')
    if not args.execute:
        print(json.dumps(dict(status='not_executed', action=args.action, GPU_or_RPC_calls=0)))
        return
    if args.action == 'bootstrap' and not args.binding:
        parser.error('bootstrap requires actual init binding file')
    if args.action in ('sequence', 'cleanup') and not args.bootstrap:
        parser.error('current immutable bootstrap required')
    run = OuterRun(args)
    finished = threading.Event()
    def deadline():
        if not finished.wait(args.deadline_s):
            try:
                write(run.out / 'deadline.json', dict(status='deadline_exceeded', action=args.action,
                    deadline_s=args.deadline_s, owner_cleanup_required=True,
                    pending_resume=run.pending is not None and not run.pending.done(),
                    active_transactions=list(run.cluster.active), complete_requests=run.complete_requests))
            finally:
                os._exit(124)
    threading.Thread(target=deadline, daemon=True, name='outer-controller-deadline').start()
    try:
        dict(init=run.init, bootstrap=run.setup, sequence=run.sequence, cleanup=run.cleanup)[args.action]()
        print(json.dumps(dict(status='completed', action=args.action, out=str(run.out))))
    except BaseException as exc:
        write(run.out / 'failure.json', dict(status='failed', error=repr(exc), action=args.action,
            pending_resume=run.pending is not None and not run.pending.done(), engine_parked=run.engine_parked,
            active_transactions=list(run.cluster.active), complete_requests=run.complete_requests,
            resource_gate_failure_ref=getattr(run, 'latest_gate_failure_ref', None),
            completed_checkpoint_files=sorted(p.name for p in run.out.glob('checkpoint_*.json')),
            automatic_source_retirement=False))
        raise
    finally:
        finished.set()


if __name__ == '__main__':
    main()
