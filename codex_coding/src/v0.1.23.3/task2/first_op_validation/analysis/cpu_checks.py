"""Manufactured CPU fixtures check arithmetic and refusal, never GPU evidence."""
import argparse
import copy
import tempfile
from pathlib import Path
from analyze import build_model, load, nnls, predict, save_new, score, state_gate, validate


def fixture(stage='A', state='thread'):
    result = {'schema_version': 'first_op_validation.v1', 'stage': stage,
              'session_id': 'manufactured_cpu_' + stage, 'fixed_bytes': 8388608,
              'observer_check': {'empty_paired_clock_extra_p95_ns': 100,
                                 'dummy_log_p95_ns': 20000, 'dummy_log_fluctuation_p95_ns': 1000}, 'requests': []}
    order = (1, 4, 3, 2) if stage == 'A' else (2, 3, 4, 1)
    for cell in order:
        mode = 'F' if cell in (1, 3) else 'P'
        target = 'fresh' if cell in (1, 2) else 'pretouched'
        for ordinal in (1, 2):
            birth = f'{stage}.cell{cell}.executor{ordinal if mode == "F" else 1}'
            prepare = f'{stage}.cell{cell}.prepare{ordinal}'
            request = {'cell_id': cell, 'request_ordinal': ordinal, 'executor_mode': mode, 'target_state': target,
                       'executor_birth': birth, 'prepare_birth': prepare, 'request_id': f'{stage}.{cell}.{ordinal}',
                       'target_writes_before_measurement': int(target == 'pretouched'),
                       'executor_alive_between_requests': mode == 'P', 'ops': []}
            for op_number in (1, 2):
                thread_ordinal = op_number + (2 if mode == 'P' and ordinal == 2 else 0)
                cold = op_number == 1 and (state == 'request' or thread_ordinal == 1)
                times = {'begin_log': 20000 + 30000 * cold, 'source_slice': 10000 + 60000 * cold,
                         'target_slice': 5000, 'copy_submit': 10000 + 30000 * cold, 'sync': 25000}
                request['ops'].append({'ordinal': op_number, 'thread_op_ordinal': thread_ordinal,
                                       'executor_birth': birth, 'prepare_birth': prepare,
                                       'phases': {name: {'wall_ns': value, 'cpu_ns': value} for name, value in times.items()},
                                       'record_total_ns': sum(times.values())})
            record = sum(op['record_total_ns'] for op in request['ops'])
            request['timing_components'] = {'record_total_ns': record, 'record_gap_ns': 20000, 'set_device_ns': 5000,
                                           'postdiagnosis_ns': 10000, 'dispatch_create_join_ns': 50000 if ordinal == 1 else 30000,
                                           'worker_other_ns': 5000}
            request['worker_execute_ns'] = sum(request['timing_components'].values())
            request['controller_execute_ns'] = request['worker_execute_ns'] + 2000000
            request['controller_components'] = {'query_ns': 5000000, 'prepare_ns': 3000000 if ordinal == 1 else 2000000,
                                                'full_other_ns': 500000}
            request['full_ns'] = request['controller_execute_ns'] + sum(request['controller_components'].values())
            result['requests'].append(request)
    return result


def change_phase(request, phase, amount):
    request['ops'][0]['phases'][phase]['wall_ns'] += amount
    request['ops'][0]['phases'][phase]['cpu_ns'] += amount
    request['ops'][0]['record_total_ns'] += amount
    request['timing_components']['record_total_ns'] += amount
    for key in ('worker_execute_ns', 'controller_execute_ns', 'full_ns'):
        request[key] += amount


def run(out):
    checks = []
    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)
    a = fixture()
    check('manufactured_thread_structure', state_gate(a)['state'] == 'thread')
    request_a = fixture(state='request')
    check('manufactured_request_structure', state_gate(request_a)['state'] == 'request')
    check('nonnegative_fit_rejects_negative_first_excess', nnls([[1, 1], [1, 0]], [1, 2])['coefficients_ns'] == [1.5, 0.0])
    target = copy.deepcopy(a)
    for r in target['requests']:
        if r['target_state'] == 'fresh' and r['request_ordinal'] == 2:
            change_phase(r, 'sync', 200000)
    check('target_interaction_stops_A', not state_gate(target)['passed'])
    logger = fixture()
    for r in logger['requests']:
        if r['ops'][0]['thread_op_ordinal'] == 1:
            change_phase(r, 'source_slice', -60000)
            change_phase(r, 'copy_submit', -30000)
            change_phase(r, 'begin_log', 90000)
    check('observer_only_stops_A', not state_gate(logger)['passed'])
    broken = copy.deepcopy(a)
    broken['requests'][0]['worker_execute_ns'] += 1
    check('one_ns_nonclosure_rejected', not validate(broken)['passed'])
    wrong_birth = copy.deepcopy(a)
    wrong_birth['requests'][0]['prepare_birth'] = wrong_birth['requests'][0]['executor_birth']
    check('prepare_executor_not_distinct_rejected', not validate(wrong_birth)['passed'])
    with tempfile.TemporaryDirectory(prefix='first_op_analysis_cpu_') as directory:
        path = Path(directory) / 'A.json'
        save_new(path, a)
        model = build_model(a, path)
        check('same_A_training_rows_all_candidates', all(c['sample_count'] == 16 for c in model['fit']['candidates'].values()))
        check('known_thread_p_h_exact', model['fit']['candidates']['C2']['coefficients_ns'] == [70000.0, 120000.0])
        check('fixed_size_bandwidth_remains_null', model['fit']['bandwidth_bytes_per_s'] is None)
        check('B_P_R2_has_zero_state_charge', model['predictions'][1]['state_count'] == 0)
        check('old_bounded_prediction_null', all(p['old_bounded_prediction'] is None for p in model['predictions']))
        bad = {'stage': 'B', 'cell_id': 2, 'request_ordinal': 2, 'bytes': 4194304, 'copies': 2}
        check('wrong_size_domain_rejected', not predict(model, bad)['accepted'])
        bad.update(bytes=8388608, request_ordinal=3)
        check('unknown_state_rejected', not predict(model, bad)['accepted'])
        contradictory = {'stage': 'B', 'cell_id': 2, 'request_ordinal': 2, 'bytes': 8388608, 'copies': 2,
                         'executor_mode': 'F'}
        check('contradictory_mode_rejected', not predict(model, contradictory)['accepted'])
        contradictory.update(executor_mode='P', thread_first=True)
        check('contradictory_thread_first_rejected', not predict(model, contradictory)['accepted'])
        check('R1_fixture_cost_not_averaged_into_R2', model['predictions'][0]['shared_costs_ns']['prepare_ns'] == 3000000 and
              model['predictions'][1]['shared_costs_ns']['prepare_ns'] == 2000000)
        b = fixture('B')
        measured = score(model, b)
        check('deterministic_frozen_predictions_exact_C2', measured['metrics']['C2']['full_ns']['max_abs_error_ns'] == 0)
        check('manufactured_clear_main_metric_improvement', measured['selected_candidate'] == 'C2')
        drift_b = copy.deepcopy(b)
        for r in drift_b['requests']:
            r['controller_components']['full_other_ns'] += 2000000
            r['full_ns'] += 2000000
        check('large_shared_control_drift_blocks_admission', score(model, drift_b)['selected_candidate'] is None)
        path2 = Path(directory) / 'request_A.json'
        save_new(path2, request_a)
        request_model = build_model(request_a, path2)
        check('request_state_identical_C1_C2', request_model['fit']['candidates']['C1']['coefficients_ns'] == request_model['fit']['candidates']['C2']['coefficients_ns'])
        check('request_equivalence_prefers_simple', score(request_model, fixture('B', 'request'))['preferred_equivalent_candidate'] == 'C1')
        check('request_structure_alone_no_full_metric_gain', score(request_model, fixture('B', 'request'))['selected_candidate'] is None)
        duplicate = copy.deepcopy(a)
        duplicate['requests'][1]['request_ordinal'] = 1
        check('duplicate_request_ordinal_rejected', not validate(duplicate)['passed'])
        check('duplicate_output_refused', path.exists())
        try:
            save_new(path, a)
        except FileExistsError:
            pass
        else:
            raise AssertionError('duplicate_output_write')
    save_new(out, {'status': 'cpu_arithmetic_and_refusal_checks_passed', 'checks': checks,
                   'fixture_type': 'manufactured CPU arithmetic; not measurements or GPU qualification',
                   'count': len(checks)})
    print(f'{len(checks)} CPU checks passed; {out}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    run(parser.parse_args().out)
