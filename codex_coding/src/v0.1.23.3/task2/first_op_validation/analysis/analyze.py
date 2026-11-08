"""CPU-only first-operation analysis; raw observations are never rewritten."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[6]
PROTOCOL = ROOT / 'codex_coding/results/v0.1.23.3/task2/first_op_validation/analysis_protocol_r01/protocol.json'
PHASES = ('source_slice', 'target_slice', 'copy_submit')
WORKER_COMPONENTS = ('record_gap_ns', 'set_device_ns', 'postdiagnosis_ns',
                     'dispatch_create_join_ns', 'worker_other_ns')
SHARED_COMPONENTS = WORKER_COMPONENTS + ('controller_residual_ns', 'query_ns', 'prepare_ns', 'full_other_ns')
FIXED_BYTES = 8 * 1024 * 1024


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')


def mad(values):
    center = median(values)
    return median([abs(x - center) for x in values])


def nnls(features, targets):
    """Enumerate active sets for the one/two-column nonnegative least squares."""
    width = len(features[0])
    if width not in (1, 2) or len(features) != len(targets):
        raise ValueError('Expected equal one/two-column design and target rows')
    candidates = [[0.0] * width]
    for col in range(width):
        denom = sum(row[col] ** 2 for row in features)
        if denom:
            coef = [0.0] * width
            coef[col] = max(0.0, sum(row[col] * y for row, y in zip(features, targets)) / denom)
            candidates.append(coef)
    if width == 2:
        aa = sum(row[0] ** 2 for row in features)
        ab = sum(row[0] * row[1] for row in features)
        bb = sum(row[1] ** 2 for row in features)
        ay = sum(row[0] * y for row, y in zip(features, targets))
        by = sum(row[1] * y for row, y in zip(features, targets))
        determinant = aa * bb - ab * ab
        if determinant > 0:
            coef = [(ay * bb - by * ab) / determinant, (by * aa - ay * ab) / determinant]
            if min(coef) >= 0:
                candidates.append(coef)
    def squared_error(coef):
        return sum((sum(x * c for x, c in zip(row, coef)) - y) ** 2 for row, y in zip(features, targets))
    answer = min(candidates, key=lambda c: (squared_error(c), sum(c)))
    return {'coefficients_ns': answer, 'sse_ns2': squared_error(answer), 'sample_count': len(targets)}


def host(op, clock):
    return sum(op['phases'][phase][clock + '_ns'] for phase in PHASES)


def components(request):
    result = {name: request['timing_components'][name] for name in WORKER_COMPONENTS}
    result['controller_residual_ns'] = request['controller_execute_ns'] - request['worker_execute_ns']
    controller = request.get('controller_components', request)
    result.update({name: controller[name] for name in ('query_ns', 'prepare_ns', 'full_other_ns')})
    return result


def validate(stage):
    errors = []
    requests = stage.get('requests', [])
    if stage.get('schema_version') != 'first_op_validation.v1':
        errors.append('schema')
    if stage.get('stage') not in ('A', 'B'):
        errors.append('stage')
    if stage.get('fixed_bytes') != FIXED_BYTES or len(requests) != 8:
        errors.append('fixed_domain')
    expected_order = [1, 4, 3, 2] if stage.get('stage') == 'A' else [2, 3, 4, 1]
    if [r.get('cell_id') for r in requests] != [x for cell in expected_order for x in (cell, cell)]:
        errors.append('stage_order')
    if [r.get('request_ordinal') for r in requests] != [1, 2] * 4:
        errors.append('request_ordinal_order')
    if stage.get('observer_check', {}).get('empty_paired_clock_extra_p95_ns') is None:
        errors.append('missing_cpu_clock_scale')
    births = {}
    for r in requests:
        label = f"cell{r.get('cell_id')}.R{r.get('request_ordinal')}"
        try:
            mode = 'F' if r['cell_id'] in (1, 3) else 'P'
            target = 'fresh' if r['cell_id'] in (1, 2) else 'pretouched'
            assert r['executor_mode'] == mode and r['target_state'] == target
            assert r['request_ordinal'] in (1, 2) and len(r['ops']) == 2
            assert r['executor_birth'] != r['prepare_birth']
            assert r['target_writes_before_measurement'] == (0 if target == 'fresh' else 1)
            if mode == 'P' and r['request_ordinal'] == 2:
                assert r['executor_alive_between_requests'] is True
            for i, op in enumerate(r['ops']):
                assert op['ordinal'] == i + 1
                expected_thread_op = i + 1 + (2 if mode == 'P' and r['request_ordinal'] == 2 else 0)
                assert op['thread_op_ordinal'] == expected_thread_op
                assert op['executor_birth'] == r['executor_birth']
                assert op['prepare_birth'] == r['prepare_birth']
                assert op['record_total_ns'] >= 0
                assert sum(p['wall_ns'] for p in op['phases'].values()) == op['record_total_ns']
                for p in op['phases'].values():
                    assert p['wall_ns'] >= 0 and p['cpu_ns'] >= 0
            c = components(r)
            assert all(math.isfinite(v) and v >= 0 for v in c.values())
            records = sum(op['record_total_ns'] for op in r['ops'])
            assert r['timing_components']['record_total_ns'] == records
            assert records + sum(c[k] for k in WORKER_COMPONENTS) == r['worker_execute_ns']
            assert r['controller_execute_ns'] + sum(c[k] for k in ('query_ns', 'prepare_ns', 'full_other_ns')) == r['full_ns']
            births.setdefault(r['cell_id'], []).append(r['executor_birth'])
        except (KeyError, AssertionError, TypeError):
            errors.append(label + ':state_or_timing_contract')
    for cell, values in births.items():
        if len(values) != 2 or ((values[0] == values[1]) != (cell in (2, 4))):
            errors.append(f'cell{cell}:birth_lifecycle')
    return {'passed': not errors, 'errors': errors,
            'scope': 'Canonical state/timing schema only; runtime lineage, primitive, SHA and cleanup require independent review.'}


def state_gate(stage, frozen_scale=None):
    qualification = validate(stage)
    if not qualification['passed']:
        return {'passed': False, 'state': 'unidentified', 'qualification': qualification}
    requests = {(r['cell_id'], r['request_ordinal']): r for r in stage['requests']}
    warm_wall = [host(r['ops'][1], 'wall') for r in stage['requests']]
    warm_cpu = [host(r['ops'][1], 'cpu') for r in stage['requests']]
    scale_parts = {'floor_ns': 5000, 'clock_ns': 4 * stage['observer_check']['empty_paired_clock_extra_p95_ns'],
                   'warm_wall_mad_ns': 2 * mad(warm_wall), 'warm_cpu_mad_ns': 2 * mad(warm_cpu)}
    scale = max(scale_parts.values()) if frozen_scale is None else frozen_scale
    operands = []
    def delta(cell, ordinal, clock):
        ops = requests[cell, ordinal]['ops']
        return host(ops[0], clock) - host(ops[1], clock)
    def supported(cell, ordinal):
        wall, cpu = delta(cell, ordinal, 'wall'), delta(cell, ordinal, 'cpu')
        passed = wall >= 2 * scale and cpu >= 2 * scale and abs(wall - cpu) <= max(scale, .5 * wall)
        operands.append({'test': 'state_delta', 'cell': cell, 'request_ordinal': ordinal, 'wall_ns': wall, 'cpu_ns': cpu, 'passed': passed})
        return passed
    common = all([supported(cell, 1) for cell in range(1, 5)] + [supported(cell, 2) for cell in (1, 3)])
    thread_checks, request_checks = [], []
    for fresh, persistent in ((1, 2), (3, 4)):
        p_supported = supported(persistent, 2)
        for clock in ('wall', 'cpu'):
            p_delta = delta(persistent, 2, clock)
            contrast = host(requests[fresh, 2]['ops'][0], clock) - host(requests[persistent, 2]['ops'][0], clock)
            thread_checks.append(abs(p_delta) <= scale and contrast >= 2 * scale)
            request_checks.append(p_supported and abs(contrast) <= scale)
            operands.append({'test': 'R2_F_minus_P', 'target': requests[fresh, 2]['target_state'], 'clock': clock,
                             'persistent_delta_ns': p_delta, 'contrast_ns': contrast})
    state = 'thread' if common and all(thread_checks) else 'request' if common and all(request_checks) else 'unidentified'
    service_deltas = [requests[c, o]['ops'][0]['record_total_ns'] - requests[c, o]['ops'][1]['record_total_ns']
                      for c, o in [(c, 1) for c in range(1, 5)] + [(1, 2), (3, 2)]]
    minimum_state_service = min(service_deltas)
    target_limit = max(2 * scale, .5 * max(0, minimum_state_service))
    target_checks = []
    for fresh, pretouched in ((1, 3), (2, 4)):
        def service_delta(cell):
            op = requests[cell, 2]['ops']
            return op[0]['record_total_ns'] - op[1]['record_total_ns']
        interaction = service_delta(fresh) - service_delta(pretouched)
        target_checks.append(abs(interaction) <= target_limit)
        operands.append({'test': 'target_service_delta_interaction', 'mode': requests[fresh, 2]['executor_mode'],
                         'interaction_ns': interaction, 'limit_ns': target_limit, 'passed': target_checks[-1]})
    if not all(target_checks):
        state = 'unidentified'
    return {'passed': state != 'unidentified', 'state': state, 'scale_ns': scale,
            'scale_parts_ns': scale_parts, 'scale_source': 'A_frozen' if frozen_scale is not None else 'this_A',
            'qualification': qualification, 'operands': operands, 'observer_check': stage['observer_check'],
            'minimum_state_service_delta_ns': minimum_state_service,
            'interpretation': 'Exploratory gate; no statistical confidence or future error bound.'}


def fit(stage, gate):
    targets, request_features, state_features = [], [], []
    state = gate['state']
    for r in stage['requests']:
        for op in r['ops']:
            targets.append(op['record_total_ns'])
            request_features.append([1, int(op['ordinal'] == 1)])
            q = op['ordinal'] == 1 if state == 'request' else op['thread_op_ordinal'] == 1
            state_features.append([1, int(q)])
    result = {'C0': nnls([[1]] * len(targets), targets), 'C1': nnls(request_features, targets),
              'C2': nnls(state_features, targets)}
    result['C2']['state_rule'] = state if state != 'unidentified' else 'thread_diagnostic_only_not_admitted'
    buckets = {}
    for mode, ordinal in itertools.product(('F', 'P'), (1, 2)):
        rows = [components(r) for r in stage['requests'] if (r['executor_mode'], r['request_ordinal']) == (mode, ordinal)]
        buckets[f'{mode}.R{ordinal}'] = {k: mean([row[k] for row in rows]) for k in SHARED_COMPONENTS}
    return {'candidates': result, 'shared_costs_ns': buckets, 'bandwidth_bytes_per_s': None,
            'byte_slope_ns_per_byte': None, 'fit_observations': len(targets)}


def factors(stage):
    requests = {(r['cell_id'], r['request_ordinal']): r for r in stage['requests']}
    names = tuple(requests[1, 1]['ops'][0]['phases']) + ('nonlog_host', 'service')
    def value(cell, ordinal, op_index, phase, clock):
        op = requests[cell, ordinal]['ops'][op_index]
        if phase == 'nonlog_host':
            return host(op, clock)
        if phase == 'service':
            return op['record_total_ns'] if clock == 'wall' else sum(p['cpu_ns'] for p in op['phases'].values())
        return op['phases'][phase][clock + '_ns']
    result = {}
    for name, clock in itertools.product(names, ('wall', 'cpu')):
        get = lambda c, o, i: value(c, o, i, name, clock)
        result[f'{name}.{clock}_ns'] = {
            'R2_thread_fresh_F_minus_P': get(1, 2, 0) - get(2, 2, 0),
            'R2_thread_pretouched_F_minus_P': get(3, 2, 0) - get(4, 2, 0),
            'R2_target_F_fresh_minus_pretouched': get(1, 2, 0) - get(3, 2, 0),
            'R2_target_P_fresh_minus_pretouched': get(2, 2, 0) - get(4, 2, 0),
            'R2_thread_target_interaction': (get(1, 2, 0) - get(2, 2, 0)) - (get(3, 2, 0) - get(4, 2, 0)),
            'R1_op1_minus_op2_by_cell': {str(c): get(c, 1, 0) - get(c, 1, 1) for c in range(1, 5)},
            'R2_op1_minus_op2_by_cell': {str(c): get(c, 2, 0) - get(c, 2, 1) for c in range(1, 5)}}
    return result


def predict(model, case):
    if case.get('bytes') != FIXED_BYTES or case.get('copies') != 2 or case.get('stage') != 'B':
        return {'accepted': False, 'reason': 'outside_frozen_diagnostic_domain', 'old_bounded_prediction': None}
    if not model['gate']['passed']:
        return {'accepted': False, 'reason': 'A_state_unidentified', 'old_bounded_prediction': None}
    cell, ordinal = case['cell_id'], case['request_ordinal']
    if cell not in (1, 2, 3, 4) or ordinal not in (1, 2):
        return {'accepted': False, 'reason': 'unknown_state', 'old_bounded_prediction': None}
    mode = 'F' if cell in (1, 3) else 'P'
    expected = {'executor_mode': mode, 'target_state': 'fresh' if cell in (1, 2) else 'pretouched',
                'thread_first': mode == 'F' or ordinal == 1}
    if any(key in case and case[key] != value for key, value in expected.items()):
        return {'accepted': False, 'reason': 'contradictory_preknown_state', 'old_bounded_prediction': None}
    costs = model['fit']['shared_costs_ns'][f'{mode}.R{ordinal}']
    q = 1 if model['gate']['state'] == 'request' or mode == 'F' or ordinal == 1 else 0
    predictions = {}
    for name, candidate in model['fit']['candidates'].items():
        coef = candidate['coefficients_ns']
        op_service = 2 * coef[0] + (coef[1] if name == 'C1' else coef[1] * q if name == 'C2' else 0)
        record = op_service + costs['record_gap_ns']
        worker = op_service + sum(costs[k] for k in WORKER_COMPONENTS)
        controller = worker + costs['controller_residual_ns']
        full = controller + sum(costs[k] for k in ('query_ns', 'prepare_ns', 'full_other_ns'))
        predictions[name] = {'op_service_ns': op_service, 'record_service_ns': record, 'worker_execute_ns': worker,
                             'controller_execute_ns': controller, 'full_ns': full, 'published_ns': None}
    return {'accepted': True, 'prediction_key': f'B.cell{cell}.{mode}.R{ordinal}', 'case': case, 'executor_mode': mode, 'state_count': q,
            'shared_costs_ns': costs, 'predictions': predictions, 'old_bounded_prediction': None}


def build_model(stage, source_path):
    gate = state_gate(stage)
    if not gate['qualification']['passed']:
        return {'schema_version': 'first_op_model.v1', 'gate': gate, 'fit': None, 'predictions': [], 'frozen': False}
    model = {'schema_version': 'first_op_model.v1', 'gate': gate, 'fit': fit(stage, gate),
             'A_path': str(Path(source_path).resolve()), 'A_sha256': digest(source_path),
             'A_session_id': stage['session_id'], 'protocol_sha256': digest(PROTOCOL),
             'analysis_source_sha256': digest(__file__), 'A_executor_births': sorted({r['executor_birth'] for r in stage['requests']}),
             'factors': factors(stage),
             'domain': {'fixed_bytes': FIXED_BYTES, 'copies': 2, 'path': 'localHBM-localHBM',
                        'single_worker': 'w4', 'chunk_bytes': 32 * 1024 * 1024, 'events': False,
                        'requires_independent_runtime_lineage_and_cleanup_qualification': True},
             'evidence_kind': 'cpu_replay_only' if stage.get('CPU_REPLAY_ONLY') else 'gpu_measurement_requires_independent_review',
             'frozen': gate['passed'] and not stage.get('CPU_REPLAY_ONLY', False), 'product_admission': False, 'published_prediction': None}
    model['predictions'] = [predict(model, {'stage': 'B', 'cell_id': cell, 'request_ordinal': ordinal,
                                          'bytes': FIXED_BYTES, 'copies': 2}) for cell in (2, 3, 4, 1) for ordinal in (1, 2)] if model['frozen'] else []
    model['expected_prediction_keys'] = [row['prediction_key'] for row in model['predictions']]
    return model


def score(model, stage):
    gate = state_gate(stage, model['gate']['scale_ns'])
    metrics = ('op_service_ns', 'record_service_ns', 'worker_execute_ns', 'controller_execute_ns', 'full_ns')
    errors = {name: {metric: [] for metric in metrics} for name in ('C0', 'C1', 'C2')}
    rows, drifts = [], []
    for r, predicted in zip(stage['requests'], model['predictions']):
        case = predicted['case']
        if (r['cell_id'], r['request_ordinal']) != (case['cell_id'], case['request_ordinal']):
            raise ValueError('B order differs from frozen prediction')
        actual = {k: r[k] for k in ('worker_execute_ns', 'controller_execute_ns', 'full_ns')}
        actual['op_service_ns'] = sum(op['record_total_ns'] for op in r['ops'])
        actual['record_service_ns'] = actual['op_service_ns'] + r['timing_components']['record_gap_ns']
        per_case = {'cell_id': r['cell_id'], 'request_ordinal': r['request_ordinal'], 'actual_ns': actual, 'candidates': {}}
        shared = components(r)
        drift = sum(shared.values()) - sum(predicted['shared_costs_ns'].values())
        drifts.append(abs(drift))
        per_case['shared_total_drift_ns'] = drift
        per_case['shared_component_drift_ns'] = {k: shared[k] - predicted['shared_costs_ns'][k] for k in SHARED_COMPONENTS}
        for name in errors:
            per_case['candidates'][name] = {}
            for metric in metrics:
                error = predicted['predictions'][name][metric] - actual[metric]
                errors[name][metric].append(error)
                per_case['candidates'][name][metric] = {'predicted_ns': predicted['predictions'][name][metric], 'error_ns': error,
                                                       'relative_error': error / actual[metric] if actual[metric] else None}
        rows.append(per_case)
    summaries = {name: {metric: {'mae_ns': mean([abs(x) for x in values]), 'max_abs_error_ns': max(abs(x) for x in values)}
                        for metric, values in group.items()} for name, group in errors.items()}
    observer_scale = max(model['gate']['observer_check'].get('dummy_log_fluctuation_p95_ns', 0),
                         stage['observer_check'].get('dummy_log_fluctuation_p95_ns', 0))
    admission_scale = max(model['gate']['scale_ns'], observer_scale, max(drifts))
    reductions = []
    for competitor in ('C0', 'C1'):
        for metric in ('worker_execute_ns', 'controller_execute_ns', 'full_ns'):
            for stat in ('mae_ns', 'max_abs_error_ns'):
                reduction = summaries[competitor][metric][stat] - summaries['C2'][metric][stat]
                limit = admission_scale if metric in ('worker_execute_ns', 'full_ns') else 0
                reductions.append({'against': competitor, 'metric': metric, 'stat': stat,
                                   'reduction_ns': reduction, 'required_above_ns': limit, 'passed': reduction > limit})
    independent_births = not set(model['A_executor_births']).intersection(r['executor_birth'] for r in stage['requests'])
    maintained = gate['passed'] and gate['state'] == model['gate']['state'] and stage['session_id'] != model['A_session_id'] and independent_births
    simple_passed = all(r['passed'] for r in reductions if r['against'] == 'C0')
    selected = 'C1' if maintained and gate['state'] == 'request' and simple_passed else 'C2' if maintained and all(r['passed'] for r in reductions) else None
    return {'B_gate': gate, 'state_transferred': maintained, 'factors': factors(stage), 'rows': rows, 'metrics': summaries,
            'admission_scale_ns': admission_scale, 'reductions': reductions, 'selected_candidate': selected,
            'preferred_equivalent_candidate': 'C1' if gate['state'] == 'request' else None,
            'state_model_admitted': selected == 'C2', 'product_admission': False,
            'conclusion': 'request_equivalent_choose_simple' if selected == 'C1' else 'state_model_candidate_passed' if selected == 'C2' else 'state_model_not_admitted',
            'published_prediction': None, 'old_bounded_prediction': None}


def write_report(path, result, stage):
    lines = ['# 首操作状态分析', '', '所有时间保留原始ns；以下门槛是探索停止规则，不提供统计置信或未来误差界。',
             '完整runtime/source/SHA/生命周期/资源清理仍需独立审查，本文schema闭合检查不替代该资格。', '']
    gate = result.get('gate', result.get('B_gate', {}))
    lines += [f"阶段：{stage['stage']}；session：{stage['session_id']}。", f"状态门：{gate.get('state')}；通过：{gate.get('passed', False)}。",
              f"冻结/使用噪声尺度：{gate.get('scale_ns')}ns。", '']
    if stage.get('CPU_REPLAY_ONLY'):
        lines += ['本文件输入为CPU协议回放，所有时间与状态因素仅用于schema解析检查；不能作为GPU性能、规律或预测资格证据。', '']
    if stage['stage'] == 'A':
        lines += ['A全部16个op同集拟合；C0常数、C1请求首项、C2实际执行状态首项。两copy与record外成本全部保留。',
                  'record外组件按F/P与R1/R2分组冻结，R1 allocation/prepare、create与R2存活线程差异进入各自组。',
                  '固定8MiB，带宽与字节斜率未识别；旧bounded域外预测和published预测均为null。', '']
        if result.get('fit'):
            lines += ['| 候选 | 系数(ns) | 同集样本数 |', '|---|---|---:|']
            for name, value in result['fit']['candidates'].items():
                lines.append(f"| {name} | {value['coefficients_ns']} | {value['sample_count']} |")
            lines += ['', f"冻结候选预测：{len(result.get('predictions', []))}请求；B是否启动由主agent独立资格审查决定。"]
        if not gate.get('passed'):
            lines += ['', 'A没有通过预登记状态门，止于A；不能据此否定所有首操作现象，也不新增参数挽救本次模型。']
    else:
        lines += [f"B状态迁移成立：{result.get('state_transferred', False)}。", f"主指标选择：{result.get('selected_candidate')}；新增state模型准入：{result.get('state_model_admitted', False)}。",
                  f"主指标收益必须超过的控制/观察尺度：{result.get('admission_scale_ns')}ns。", '',
                  '状态结构是否复现与是否值得进入完整成本模型是两个独立判断。只改善service不能升级规划成本。', '']
        if result.get('metrics'):
            lines += ['| 候选 | 指标 | MAE(us) | 最大绝对误差(us) |', '|---|---|---:|---:|']
            for name, metrics in result['metrics'].items():
                for metric, values in metrics.items():
                    lines.append(f"| {name} | {metric} | {values['mae_ns']/1000:.3f} | {values['max_abs_error_ns']/1000:.3f} |")
    with Path(path).open('x') as handle:
        handle.write('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('A', 'B'))
    parser.add_argument('--stage', required=True)
    parser.add_argument('--model')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    stage = load(args.stage)
    if stage['stage'] != args.command:
        raise ValueError('Wrong stage label')
    if args.command == 'A':
        value = build_model(stage, args.stage)
    else:
        if not args.model:
            parser.error('B requires --model frozen before B')
        model = load(args.model)
        if not model['frozen'] or digest(PROTOCOL) != model['protocol_sha256'] or digest(__file__) != model['analysis_source_sha256']:
            raise ValueError('Unfrozen model or changed protocol')
        if digest(model['A_path']) != model['A_sha256']:
            raise ValueError('A original changed')
        if stage.get('CPU_REPLAY_ONLY'):
            raise ValueError('CPU replay cannot be a GPU holdout')
        qualification = validate(stage)
        if not qualification['passed']:
            value = {'conclusion': 'B_schema_qualification_failed', 'qualification': qualification}
        else:
            value = score(model, stage)
            value['B_sha256'] = digest(args.stage)
            value['model_sha256'] = digest(args.model)
    save_new(args.out, value)
    write_report(Path(args.out).with_suffix('.md'), value, stage)
    print(json.dumps({'output': args.out, 'state': value.get('gate', value.get('B_gate', {})).get('state'),
                      'conclusion': value.get('conclusion'), 'frozen': value.get('frozen')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
