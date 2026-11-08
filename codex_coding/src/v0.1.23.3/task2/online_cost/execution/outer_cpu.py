"""CPU sequence and negative controls; these artifacts cannot qualify GPU work."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'model')]
from cpu_check import fixtures
from evaluate import digest, evaluate, file_ref
from simple_contracts import GiB, RANK_BYTES, WORKFLOW, layout_plan
from simple_controller import SimpleRun
from simple_model import CALIBRATION_SIZES, build_model, transfer_service
import outer_controller
from outer_controller import OuterRun, write
from outer_model import (DOMAIN, ORDER, calibrate_outer, freeze, inherit_model,
                         load_ref, predict, validate_inheritance, validate_record)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, passed):
        checks.append(dict(name=name, passed=bool(passed)))
        if not passed:
            raise AssertionError(name)
    def rejects(name, fn):
        try:
            fn()
        except (ValueError, KeyError, FileExistsError):
            check(name, True)
        else:
            check(name, False)
    workers, manifests, replicas = fixtures()
    workers = workers[:3]
    for worker in workers:
        worker['runtime_identity'] = dict(actual_bdf='CPU_BDF' + str(worker['worker']))
        worker['transactions'] = []
    for replica in replicas:
        if replica['worker'] == 3:
            replica['worker'], replica['epoch'] = 2, workers[2]['epoch']
    old = SimpleRun.__new__(SimpleRun)
    old.bootstrap, old.endpoints, old.clock = dict(manifests=manifests), ['CPU']*3, 'CPU_old_clock'
    old_context = old.context(workers, replicas)
    source_dir = args.out / 'CPU_old_source'
    source_dir.mkdir()
    refs, samples = [], {}
    for index, (path, fields) in enumerate(old_context['path_context'].items()):
        samples[path] = []
        warmup_ref = None
        for role, n in [('warmup', GiB)] + [('cal', n) for n in CALIBRATION_SIZES]:
            service = (.073129 if n != GiB else .066855) if fields['operation_kind'] == 'network' else n/1e9/20
            raw_path = source_dir / f'CPU_path_{index}_{role}_{n}.json'
            write(raw_path, dict(CPU_fixture=True, path=path, sample_role=role, session='CPU_old_session',
                config_sha256=digest(old_context), resource_gate=dict(passed=True), target_retired=True,
                flow=dict(verification=dict(passed=True, checked_bytes=n),
                          execution=[dict(records=[dict(elapsed_s=service)])])))
            ref = file_ref(raw_path)
            refs.append(ref)
            if role == 'warmup':
                warmup_ref = ref
            else:
                samples[path].append(dict(nbytes=n, service_s=service, passed=True, verified_bytes=n,
                    evidence_kind='actual_path_calibration', config_sha256=digest(old_context),
                    session='CPU_old_session', warmup_ref=warmup_ref, source_ref=ref))
    source = build_model(old_context, workers, samples, {}, refs)
    source_path = source_dir / 'CPU_checkpoint_path_model.json'
    write(source_path, source)
    source_before = {p: p.read_bytes() for p in source_dir.glob('*.json')}
    binding_path = args.out / 'CPU_current_binding.json'
    write(binding_path, dict(CPU_fixture=True, GPU_calls=0))
    bootstrap_path = args.out / 'CPU_bootstrap.json'
    write(bootstrap_path, dict(CPU_fixture=True, GPU_calls=0))
    runner = OuterRun.__new__(OuterRun)
    runner.args = SimpleNamespace(action='sequence', bootstrap=bootstrap_path)
    runner.out = args.out / 'CPU_sequence'
    runner.out.mkdir()
    runner.runtime = runner.out / 'runtime'
    runner.clock, runner.endpoints = 'CPU_current_clock', ['CPU']*3
    runner.complete_requests, runner.prime_ref, runner.registration_ref = 0, None, None
    runner.bootstrap = dict(manifests=manifests, replicas=replicas, binding_ref=file_ref(binding_path))
    runner.plan = dict(run_id='CPU_current_session', workflow=WORKFLOW, nodes=dict(CPU=True),
        handoff=str(runner.runtime / 'handoff'), outer_protocol=dict(schema='post_prime_outer_protocol_v1',
        source_checkpoint_path_model_ref=file_ref(source_path), outer_domain=DOMAIN,
        complete_request_order=ORDER, validation_order=ORDER[2:], max_complete_requests=4,
        prime_run_id=ORDER[0], outer_calibration_run_id=ORDER[1]))
    runner.cluster = SimpleNamespace(inventory=lambda compact=True: (workers, replicas))
    runner.cpu_next_ns = 10000
    observed_order, pids, boundaries = [], [], []
    def cpu_online(self, layout, run_id, context):
        observed_order.append(run_id)
        pids.append(os.getpid())
        plan = layout_plan(manifests, replicas, workers, self.endpoints, layout,
            self.plan['run_id']+'_'+run_id, str(self.runtime / 'metrics' / (self.plan['run_id']+'_'+run_id)))
        state = self.state(workers, replicas, context)
        inherited = load_ref(file_ref(self.out / 'checkpoint_path_model.json'))
        service = transfer_service(plan, workers, inherited, state)['worker_dag_s']
        residual = 1.5 if run_id == 'C0_prime' else .5
        begin = self.cpu_next_ns
        ready = begin + round((service + residual)*1e9)
        self.cpu_next_ns = ready + 1000
        wall = time.time_ns()
        raw_path = self.out / (run_id + '_raw.json')
        write(raw_path, dict(CPU_fixture=True, load_begin_ns=begin, all_weights_ready_ns=ready,
            load_begin_wall_ns=wall, clock_domain=self.clock, rank_timing=dict(ranks={str(r):
                dict(host='CPU_engine_host', begin_ns=begin+1, end_ns=ready-1,
                    events=[dict(event=event, rank=r, pid=100+r, host='CPU_engine_host', monotonic_ns=stamp)
                            for event, stamp in [('load_begin', begin+1), ('weights_ready', ready-1)]])
                for r in range(2)})))
        record = dict(CPU_fixture=True, scenario=layout, run_id=run_id, session=self.plan['run_id'], workflow=WORKFLOW,
            plan=plan, workers=workers, state=state, load_begin_ns=begin, all_weights_ready_ns=ready,
            load_begin_wall_ns=wall, clock_domain=self.clock, clock_scope='single_controller',
            online_weights_ready_s=(ready-begin)/1e9, all_target_weights_usable=True,
            exact_reference_output_passed=True, full_sha_passed=True, verified_bytes=2*RANK_BYTES,
            verification_position='after_all_weights_ready', verification_begin_ns=ready,
            verification_end_ns=ready+1, fresh_binding_verified=True,
            incarnation_sha256=digest(state['incarnations']), resource_gate_passed=True,
            ready_resource_gate=dict(passed=True), cleanup_passed=True, engine_parked=True,
            physical_park_passed=True, physical_park=dict(passed=True), source_refs=[file_ref(raw_path)],
            evidence_kind='actual_model_online')
        write(self.out / (run_id+'.json'), record)
        boundaries.append((run_id, state['prior_complete_restores']))
        return record
    def cpu_evaluate(frozen, observations):
        observations = copy.deepcopy(observations)
        for rows in observations.values():
            for row in rows:
                row['evidence_kind'] = 'CPU_fixture'
        return evaluate(frozen, observations)
    with patch.object(SimpleRun, 'online', cpu_online), patch.object(outer_controller, 'evaluate', cpu_evaluate):
        runner.sequence()
    model = load_ref(file_ref(runner.out / 'model.json'))
    checkpoint = load_ref(file_ref(runner.out / 'checkpoint_path_model.json'))
    registration_ref = file_ref(runner.out / 'outer_registration.json')
    registration = load_ref(registration_ref)
    prime_ref, cal_ref = file_ref(runner.out / 'C0_prime_record.json'), file_ref(runner.out / 'C0_cal_record.json')
    frozen = load_ref(file_ref(runner.out / 'frozen_prediction.json'))
    observations = load_ref(file_ref(runner.out / 'observations.json'))
    validation = load_ref(file_ref(runner.out / 'independent_validation.json'))
    check('one_persistent_controller_four_requests_exact_order', observed_order == ORDER and len(set(pids)) == 1)
    check('first_prime_and_complete_restore_state_progression', boundaries == list(zip(ORDER, range(4))))
    check('original19files_exact_bytes_unchanged', all(p.read_bytes() == raw for p, raw in source_before.items()))
    check('inherit_exact_points_context_refs_and_old_sessions', model['point_costs'] == source['point_costs'] and
        model['path_cost_inheritance']['source_context'] == old_context and
        model['path_cost_inheritance']['source_raw_refs'] == refs and
        model['path_cost_inheritance']['source_calibration_sessions'] == ['CPU_old_session'])
    check('new_context_changes_only_clock', dict(model['context'], clock_domain=old.clock) == old_context)
    check('new_calibration_session_does_not_relabel_old_service', model['calibration_sessions'] == ['CPU_current_session'])
    check('calibration_uses_only_C0_cal_not_expensive_prime', abs(model['online_outer_s']-.5) < 1e-9 and
        model['current_outer_calibration']['prime_used_for_fitting'] is False and
        model['current_outer_calibration']['prime_online_ns'] > model['current_outer_calibration']['calibration_online_ns'])
    check('both_holdouts_frozen_after_cal_before_any_holdout', set(frozen['payload']['predictions']) == set(ORDER[2:]) and
        load_ref(cal_ref)['completed_wall_ns'] < frozen['payload']['freeze_wall_ns'] <
        min(rows[0]['load_begin_wall_ns'] for rows in observations.values()))
    check('CPU_can_pass_numeric_gate_but_never_GPU_qualification', validation['all_scenario_error_gates_passed'] and
        not validation['actual_model_online_qualified'])
    check('no_current_path_samples', load_ref(file_ref(runner.out / 'calibration.json'))['current_path_calibration_samples'] == 0)
    check('all_four_original_and_decorated_raw_records_retained', all((runner.out/(name+suffix)).is_file()
        for name in ORDER for suffix in ('.json', '_raw.json', '_record.json')))
    check('prime_calibration_and_holdout_engine_binding_from_raw_events',
          model['current_outer_calibration']['engine_incarnations'] ==
          {'0': dict(host='CPU_engine_host', pid=100), '1': dict(host='CPU_engine_host', pid=101)})
    rejects('no_path_resampling', lambda: runner.path_calibration(model['context']))
    rejects('fifth_request_rejected', lambda: runner.online('Hnear', 'Hnear', model['context']))
    rejects('cannot_recalibrate_after_holdouts', runner.calibrate)
    rejects('cannot_replay_validation', runner.validate)
    bad = copy.deepcopy(model); bad['point_costs'][next(iter(bad['point_costs']))][str(GiB)]['service_s'] += .01
    rejects('reject_exact_service_parameter_mutation', lambda: validate_inheritance(bad))
    bad = copy.deepcopy(model); bad['path_cost_inheritance']['source_context']['clock_domain'] = runner.clock
    rejects('reject_historical_clock_relabeling', lambda: validate_inheritance(bad))
    bad = copy.deepcopy(model); bad['current_controller']['pid'] += 1
    rejects('reject_controller_PID_switch', lambda: validate_inheritance(bad))
    bad = copy.deepcopy(model); bad['context']['worker_BDF']['2'] = 'foreign'
    rejects('reject_physical_BDF_change', lambda: validate_inheritance(bad))
    bad_context = copy.deepcopy(model['context']); bad_context['path_context']['2:dram:7->1']['source_numa'] = 4
    rejects('reject_path_source_NUMA_migration', lambda: inherit_model(file_ref(source_path), bad_context, workers,
        runner.state(workers, replicas, bad_context), registration_ref))
    held = observations['Hremote'][0]
    rejects('reject_holdout_as_outer_calibration', lambda: calibrate_outer(checkpoint, prime_ref,
        file_ref(runner.out / 'Hremote_record.json')))
    rejects('reject_prime_as_outer_calibration', lambda: calibrate_outer(checkpoint, prime_ref, prime_ref))
    rejects('reject_second_fit_once_model_is_bound', lambda: calibrate_outer(model, prime_ref, cal_ref))
    bad = copy.deepcopy(held['state']); bad['prime_ref'] = None
    rejects('reject_prediction_without_persisted_prime', lambda: predict(held['plan'], workers, model, bad))
    bad = copy.deepcopy(held['state']); bad['prior_complete_restores'] = 0
    rejects('reject_prediction_cold_state', lambda: predict(held['plan'], workers, model, bad))
    bad = copy.deepcopy(held['state']); bad['incarnations'][0][1] = 'foreign'
    rejects('reject_prediction_new_worker_incarnation', lambda: predict(held['plan'], workers, model, bad))
    bad = copy.deepcopy(model); bad['online_outer_s'] += .01
    rejects('reject_holdout_residual_refit', lambda: predict(held['plan'], workers, bad, held['state']))
    bad = load_ref(prime_ref); bad['complete_request_index'] = 2
    rejects('reject_prime_not_first_request', lambda: validate_record(bad, model, 1))
    bad = load_ref(prime_ref); bad['full_sha_passed'] = False
    rejects('reject_unverified_prime', lambda: validate_record(bad, model, 1))
    bad = load_ref(prime_ref); bad['physical_park']['passed'] = False
    rejects('reject_prime_not_physically_parked', lambda: validate_record(bad, model, 1))
    bad = load_ref(prime_ref); bad['online_weights_ready_s'] += 1
    rejects('reject_persisted_prime_cost_change', lambda: validate_record(bad, model, 1))
    bad = copy.deepcopy(model); bad['current_outer_calibration']['prime_online_ns'] += 1
    rejects('reject_model_prime_cost_change', lambda: predict(held['plan'], workers, bad, held['state']))
    def changed_raw_record(key, mutate):
        record = copy.deepcopy(held)
        raw = load_ref(record['source_refs'][0])
        mutate(raw)
        path = args.out / ('CPU_changed_raw_' + key + '.json')
        write(path, raw)
        record['source_refs'] = [file_ref(path)]
        return record
    changed_engine = changed_raw_record('enginePID', lambda raw:
        [event.update(pid=999) for event in raw['rank_timing']['ranks']['0']['events']])
    rejects('reject_engine_PID_switch_after_calibration', lambda: validate_record(changed_engine, model, 3))
    changed_ready = changed_raw_record('engineACK', lambda raw:
        raw['rank_timing']['ranks']['0'].update(end_ns=held['all_weights_ready_ns']+1))
    rejects('reject_engine_weights_ready_outside_ACK', lambda: validate_record(changed_ready, model, 3))
    bad = copy.deepcopy(observations); bad['Hremote'][0]['load_begin_wall_ns'] = frozen['payload']['freeze_wall_ns']
    rejects('reject_holdout_before_freeze', lambda: evaluate(frozen, bad))
    bad = copy.deepcopy(frozen); bad['payload']['predictions']['Hnear']['estimated_online_ns'] += 1
    rejects('reject_mutated_frozen_payload', lambda: evaluate(bad, observations))
    rejects('reject_freeze_aliases_source', lambda: freeze(runner.out / 'protocol.json', source_path, runner.out / 'inputs.json'))
    rejects('reject_freeze_model_inputs_alias', lambda: freeze(runner.out / 'protocol.json',
        runner.out / 'model.json', runner.out / 'model.json'))
    bad = copy.deepcopy(source); bad['online_outer_s'] = .808030006
    contaminated = args.out / 'CPU_old_outer_contaminated.json'
    write(contaminated, bad)
    rejects('reject_historical_outer_model_source', lambda: inherit_model(file_ref(contaminated), model['context'],
        workers, held['state'], registration_ref))
    check('old_simple_default_never_modified', SimpleRun.path_calibration is not OuterRun.path_calibration)
    result = dict(passed=all(row['passed'] for row in checks), checks=checks, check_count=len(checks),
        GPU_actions=0, SSH_actions=0, Docker_actions=0, actual_model_online_qualified=False,
        scope='Synthetic CPU protocol qualification only; all original and current sources explicitly CPU fixtures.')
    write(args.out / 'qualification.json', result)
    print(json.dumps(dict(passed=result['passed'], check_count=len(checks), GPU_actions=0)))


if __name__ == '__main__':
    main()
