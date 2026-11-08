"""Independently inspect preserved local-copy timings without Torch or remote calls."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[6]
OLD = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration/execution_actual_r02/measure'
NEW = ROOT / 'codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_actual_r03/measure'


def read(path):
    return json.loads(path.read_text())


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    names = ['p0_warm_small', 'p0_warm_large', 'p0_warm_batch',
             'p0_warm_batch_retry1', 'p0_warm_large_retry1', 'p0_warm_small_retry1']
    paths = [OLD / (n + '.json') for n in names]
    raw = [read(p) for p in paths]
    checks = []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        assert passed, name

    check('preserved chronological order', [r['request_timing']['request_begin_ns'] for r in raw]
          == sorted(r['request_timing']['request_begin_ns'] for r in raw))
    chronology = []
    for name, row in zip(names, raw):
        flow = row['flow']
        worker = next(w for w in flow['execution'] if w['worker'] == 4)
        plan = flow['plan']
        check(name + ': same local single-worker primitive', all(
            op['src_w'] == op['dst_w'] == 4 and op['kind'] == 'local_hbm'
            and op['replica_id'] == 'costcal_runtime_r02/source0/hbm'
            and op['path'] == '4:hbm:0->4' for op in plan['ops']))
        check(name + ': replica HBM targets', all(t['mode'] == 'replica' and t['tier'] == 'hbm'
              and t['worker'] == 4 for t in plan['targets']))
        check(name + ': full-byte verification', flow['verification']['passed']
              and flow['verification']['checked_bytes'] == row['nbytes'])
        check(name + ': one bounded chunk per op', all(op['n'] <= plan['chunk_bytes'] for op in plan['ops']))
        check(name + ': service is longest record, not batch sum', row['service_s']
              == max(r['elapsed_s'] for r in worker['records']))
        check(name + ': execute includes record intervals', worker['execute_s']
              >= sum(r['elapsed_s'] for r in worker['records']))
        chronology.append({'label': name, 'begin_ns': row['request_timing']['request_begin_ns'],
            'bytes': row['nbytes'], 'operations': row['operations'],
            'record_ms': [r['elapsed_s'] * 1000 for r in worker['records']],
            'worker_ms': worker['execute_s'] * 1000,
            'outside_record_ms': (worker['execute_s'] - sum(r['elapsed_s'] for r in worker['records'])) * 1000,
            'controller_execute_ms': flow['execute_wall_s'] * 1000,
            'source_offsets': [op['source_offset'] for op in plan['ops']],
            'target_bytes': [t['n'] for t in plan['targets']]})
    check('single-op lineage, offsets and manifest identical', len({
        json.dumps({k: r['flow']['plan']['ops'][0][k] for k in (
            'generation', 'identity', 'source_epoch', 'source_offset', 'replica_id')}, sort_keys=True)
        for r in raw if r['operations'] == 1}) == 1)
    contrasts = []
    for small_i, large_i in ((0, 1), (5, 4)):
        small, large = raw[small_i], raw[large_i]
        diff = large['service_s'] - small['service_s']
        check('negative service slope ' + names[small_i], diff < 0)
        contrasts.append({'small': names[small_i], 'large': names[large_i],
            'large_minus_small_ms': diff * 1000,
            'raw_service_slope_s_per_byte': diff / (12 * (1 << 20))})
    batch = []
    for i in (2, 3):
        records = chronology[i]['record_ms']
        check(names[i] + ': first record exceeds each subsequent record', records[0] > max(records[1:]))
        batch.append({'label': names[i], 'first_ms': records[0],
            'subsequent_mean_ms': statistics.mean(records[1:]),
            'ratio': records[0] / statistics.mean(records[1:]),
            'causality': 'Request position, handler state, target and source offset change together.'})
    newer = read(NEW / 'local_novel8.json')
    frozen_path = ROOT / 'codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/frozen_model.json'
    frozen = read(frozen_path)
    values = [r['service_s'] for r in raw if r['operations'] == 1]
    cost = frozen['model']['paths']['4:hbm:0->4']
    check('frozen local coefficient is all four preserved points mean',
          cost['operation_s'] == statistics.mean(values))
    check('frozen local bandwidth remains unidentified', cost['warm_GBps'] is None
          and cost['byte_slope_s_per_byte'] is None)
    paths += [NEW / 'local_novel8.json', frozen_path,
              ROOT / 'main_lib/fluidckpt/cluster/worker.py',
              ROOT / 'main_lib/fluidckpt/serving/keeper.py',
              ROOT / 'codex_coding/src/v0.1.23.3/task2/cost_calibration/execution/runner.py']
    result = {'status': 'prior_origin_review_passed_mechanism_unidentified',
              'checks': checks, 'chronology': chronology, 'contrasts': contrasts,
              'batch_first_record_evidence': batch,
              'frozen_constant_ms': cost['operation_s'] * 1000,
              'new_8mib_record_ms': newer['service_s'] * 1000,
              'source_refs': [ref(p) for p in paths]}
    (out / 'qualification.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'checks': len(checks), 'status': result['status'], 'output': str(out)}))


if __name__ == '__main__':
    main()
