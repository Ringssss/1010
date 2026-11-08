"""Independently check recorded lifecycle, primitive, timing, and state contrasts."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

N = 8 << 20
HOST = ('source_slice', 'target_slice', 'copy_submit')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frozen-scale', type=int)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    data = json.loads(args.stage.read_text())
    checks = []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})

    rows = data['requests']
    order = {'A': [1, 4, 3, 2], 'B': [2, 3, 4, 1]}
    check('schema and fixed bytes', data['schema_version'] == 'first_op_validation.v1' and data['fixed_bytes'] == N)
    check('exact eight requests and frozen order', len(rows) == 8 and data['stage'] in order
          and [r['cell_id'] for r in rows] == [c for c in order.get(data['stage'], []) for _ in (1, 2)])
    check('request ordinals and unique IDs', [r['request_ordinal'] for r in rows] == [1, 2] * 4
          and len({r['request_id'] for r in rows}) == 8)
    check('execution claims qualified after cleanup', data['qualification']['passed'] and len(data['cleanup']) == 4)
    check('one held source binding throughout stage', len({json.dumps(r['diagnosis']['source_identity'],sort_keys=True) for r in rows}) == 1
          and len({r['ops'][0]['source_view']['data_ptr'] for r in rows}) == 1
          and all(r['diagnosis']['source_identity']['n'] == 2*N and r['diagnosis']['source_identity']['off'] == 0 for r in rows))
    check('observer runs in actual worker on independent CPU actor', data['observer_check']['worker'] == 4
          and data['observer_check']['CPU_ONLY'] and data['observer_check']['no_gpu_operations_in_observer_actor']
          and data['observer_check']['joined'] and not data['observer_check']['subtracted_from_measurements']
          and data['observer_check']['birth'] not in {r['executor_birth'] for r in rows})
    for row in rows:
        label = f"cell{row['cell_id']}.R{row['request_ordinal']}"
        executor, prepare = row['executor'], row['prepare']
        fixture = row['fixture']
        check(label + ': actual raw executor/prepare birth match', row['executor_birth'] == executor['birth']
              and row['prepare_birth'] == prepare['prepare_birth'] == fixture['prepare_birth']
              and executor['birth'] != prepare['prepare_birth'])
        check(label + ': prepare copy and sync evidence', prepare['intervention']['bytes'] == N
              and prepare['intervention']['birth'] == prepare['prepare_birth']
              and prepare['intervention']['kind'] == ('scratch' if row['target_state'] == 'fresh' else 'pretouch'))
        check(label + ': exact target write history and lease', prepare['target_writes_before_measurement']
              == row['target_writes_before_measurement'] == (0 if row['target_state'] == 'fresh' else 1)
              and prepare['target_lease_count'] == 1)
        ranges = fixture['ranges']
        check(label + ': three simultaneous distinct empty buffers', fixture['simultaneous_alive']
              and set(ranges) == {'A', 'B', 'scratch'}
              and all(b - a == N for a, b in ranges.values())
              and all(ranges[a][1] <= ranges[b][0] or ranges[b][1] <= ranges[a][0]
                      for a, b in (('A', 'B'), ('A', 'scratch'), ('B', 'scratch'))))
        check(label + ': queue ordered and live thread trace', executor['created_ns'] <= executor['entered_ns']
              <= executor['queue']['pop_ns'] <= executor['queue']['return_ns']
              and executor['queue']['enqueue_ns'] <= executor['queue']['pop_ns'])
        should_join = row['executor_mode'] == 'F' or row['request_ordinal'] == 2
        check(label + ': actual join policy', executor['joined'] == should_join
              and executor['alive_after_join'] == (not should_join))
        if should_join:
            check(label + ': exit before join return', executor['join_begin_ns'] <= executor['exit_ns']
                  <= executor['join_end_ns'])
        check(label + ': worker and full clocks close', sum(row['timing_components'].values()) == row['worker_execute_ns']
              and row['controller_execute_ns'] + sum(row['controller_components'].values()) == row['full_ns'])
        record = row['flow']['execution']['4' if '4' in row['flow']['execution'] else 4]['records'][0]
        check(label + ': record two copies and logical SHA distinct', record['actual_copy_bytes'] == 2*N
              and record['logical_SHA_bytes'] == N and row['flow']['verification']['passed']
              and row['flow']['verification']['checked_bytes'] == N)
        check(label + ': no events or between-round diagnostics', row['diagnosis']['CUDA_events'] == 0
              and row['diagnosis']['metadata_after_both_rounds'] and row['diagnosis']['no_metadata_between_rounds'])
        check(label + ': selected device is only pre-copy GPU operation',
              row['diagnosis']['current_request_pre_copy_gpu_operations'] == ['set_device']
              and row['diagnosis']['op_count_before'] == (2 if row['executor_mode'] == 'P' and row['request_ordinal'] == 2 else 0))
        for index, op in enumerate(row['ops']):
            clocks = op['raw_clocks']
            phases = list(op['phases'].values())
            check(label + f'.op{index+1}: raw phase clocks close', len(clocks) == 6
                  and all(clocks[j+1][k] >= clocks[j][k] for j in range(5) for k in (0, 1))
                  and sum(p['wall_ns'] for p in phases) == op['record_total_ns'] == clocks[-1][0]-clocks[0][0]
                  and sum(p['cpu_ns'] for p in phases) == op['record_cpu_ns'] == clocks[-1][1]-clocks[0][1])
            check(label + f'.op{index+1}: actual thread ordinal and fixed views', op['executor_birth'] == executor['birth']
                  and op['native_tid'] == executor['native_tid']
                  and op['thread_op_ordinal'] == index+1+(2 if row['executor_mode'] == 'P' and row['request_ordinal'] == 2 else 0)
                  and op['bytes'] == N)
            check(label + f'.op{index+1}: target is expected held allocation', op['target_view']['data_ptr']
                  == ranges['A' if row['request_ordinal'] == 1 else 'B'][0])
        check(label + ': both copies use same source/target address', row['ops'][0]['source_view']['data_ptr']
              == row['ops'][1]['source_view']['data_ptr'] and row['ops'][0]['target_view']['data_ptr']
              == row['ops'][1]['target_view']['data_ptr'])
    bycell = {c: [r for r in rows if r['cell_id'] == c] for c in range(1, 5)}
    for cell, pair in bycell.items():
        if len(pair) != 2:
            continue
        a, b = pair
        check(f'cell{cell}: genuine F/P birth and live continuity', (a['executor_birth'] == b['executor_birth'])
              == (a['executor_mode'] == 'P') and b['executor_alive_between_requests'] == (a['executor_mode'] == 'P'))
        check(f'cell{cell}: same prepare lifetime/fixture for both requests', a['prepare_birth'] == b['prepare_birth']
              and a['fixture'] == b['fixture'])
    for cleanup in data['cleanup']:
        check(cleanup['cell_key'] + ': exact fixture close', cleanup['all_released']
              and all(j['joined'] and not j['alive_after_join'] for j in cleanup['joins']))
        own = next(r for r in rows if r['request_id'] == cleanup['history'][0]['request_id'])
        check(cleanup['cell_key'] + ': final actual write ledger', cleanup['writes']
              == ({'A':2,'B':2,'scratch':2} if own['target_state'] == 'fresh' else {'A':3,'B':3,'scratch':0}))

    def host(op, kind):
        return sum(op['phases'][name][kind + '_ns'] for name in HOST)

    def mad(values):
        center = median(values)
        return median(abs(v-center) for v in values)

    s = max(5000, 4*data['observer_check']['empty_paired_clock_extra_p95_ns'],
            2*mad([host(r['ops'][1], 'wall') for r in rows]), 2*mad([host(r['ops'][1], 'cpu') for r in rows]))
    if args.frozen_scale is not None:
        s = args.frozen_scale
    indexed = {(r['cell_id'], r['request_ordinal']): r for r in rows}
    def delta(cell, ordinal, kind):
        r = indexed[cell, ordinal]
        return host(r['ops'][0], kind)-host(r['ops'][1], kind)
    common = all(delta(c, o, k) >= 2*s for c, o in [(c, 1) for c in range(1, 5)]+[(1, 2), (3, 2)] for k in ('wall', 'cpu'))
    common = common and all(abs(delta(c, o, 'wall')-delta(c, o, 'cpu')) <= max(s, .5*delta(c, o, 'wall'))
                            for c, o in [(c, 1) for c in range(1, 5)]+[(1, 2), (3, 2)])
    thread, request = common, common
    contrasts = []
    for f, p in ((1, 2), (3, 4)):
        for kind in ('wall', 'cpu'):
            contrast = host(indexed[f, 2]['ops'][0], kind)-host(indexed[p, 2]['ops'][0], kind)
            pd = delta(p, 2, kind)
            thread = thread and abs(pd) <= s and contrast >= 2*s
            request = request and pd >= 2*s and abs(contrast) <= s
            contrasts.append(dict(fresh_cell=f,persistent_cell=p,clock=kind,contrast_ns=contrast,persistent_delta_ns=pd))
        request = request and abs(delta(p, 2, 'wall')-delta(p, 2, 'cpu')) <= max(s,.5*delta(p,2,'wall'))
    def service_delta(cell, ordinal):
        ops = indexed[cell,ordinal]['ops']
        return ops[0]['record_total_ns']-ops[1]['record_total_ns']
    target_limit = max(2*s,.5*max(0,min(service_delta(c,o) for c,o in [(c,1) for c in range(1,5)]+[(1,2),(3,2)])))
    interactions = [service_delta(f,2)-service_delta(p,2) for f,p in ((1,3),(2,4))]
    target_ok = all(abs(v)<=target_limit for v in interactions)
    state = 'thread' if thread and target_ok else 'request' if request and target_ok else 'unidentified'
    result = dict(status='independent_stage_evidence_passed' if all(c['passed'] for c in checks) else 'independent_stage_evidence_failed',
                  checks=checks,stage_ref={'path':str(args.stage.resolve()),'sha256':hashlib.sha256(args.stage.read_bytes()).hexdigest()},
                  independent_state_gate=dict(state=state,passed=state!='unidentified',scale_ns=s,contrasts=contrasts,
                                              target_interactions_ns=interactions,target_limit_ns=target_limit),
                  GPU_launches=0,remote_calls=0,scope='Raw recorded lifecycle/clock evidence; external runtime source closure and asset protection reviewed separately.')
    (args.output/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks':len(checks),'failed':[c['name'] for c in checks if not c['passed']],
                      'state':state,'scale_ns':s}))
    if any(not c['passed'] for c in checks):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
