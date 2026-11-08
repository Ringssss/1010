"""Read original plans and timings; preserve exact sources and static path assertions."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[6]


def reference(path):
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    old = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration/execution_actual_r02/measure'
    worker_source = ROOT / 'main_lib/fluidckpt/cluster/worker.py'
    worker_tree = ast.parse(worker_source.read_text())
    worker = next(node for node in worker_tree.body if isinstance(node, ast.ClassDef) and node.name=='ClusterWorker')
    methods = {node.name:node for node in worker.body if isinstance(node, ast.FunctionDef)}
    assert '_execute' in methods and 'land' in methods
    for name in ('_execute', 'land'):
        assert not any(isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and node.slice.value=='index' for node in ast.walk(methods[name]))
    refs = [reference(ROOT / path) for path in (
        'main_lib/fluidckpt/cluster/worker.py', 'main_lib/fluidckpt/cluster/catalog.py',
        'main_lib/fluidckpt/serving/keeper.py', 'main_lib/fluidckpt/serving/measure.py',
        'codex_coding/src/v0.1.23.3/task2/cost_calibration/execution/runner.py',
        'codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/worker.py',
        'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/runtime_worker.py')]
    cases = []
    for label in ('warm_small', 'warm_large', 'warm_batch', 'warm_batch_retry1', 'warm_large_retry1', 'warm_small_retry1'):
        pending, observed = old / ('p0_'+label+'.pending.json'), old / ('p0_'+label+'.json')
        plan = json.loads(pending.read_text())['plan']
        row = json.loads(observed.read_text())
        local = next(item for item in row['flow']['execution'] if item['worker']==4)
        refs.extend([reference(pending), reference(observed)])
        assert plan['executor']=='serial' and plan['chunk_bytes']==32 << 20
        assert all(target['mode']=='replica' and target['tier']=='hbm' for target in plan['targets'])
        assert all(op['src_w']==op['dst_w']==4 and op['kind']=='local_hbm' for op in plan['ops'])
        assert all(op['n']<=plan['chunk_bytes'] for op in plan['ops'])
        assert len(local['records'])==len(plan['ops'])
        assert all(record['role']=='both' for record in local['records'])
        times = [item['elapsed_s']*1e6 for item in local['records']]
        cases.append(dict(label=label, plan=plan, old_record_us=times,
            old_execute_us=local['execute_s']*1e6,
            old_controller_execute_us=row['flow']['execute_wall_s']*1e6,
            worker_outside_record_sum_us=(local['execute_s']-sum(r['elapsed_s'] for r in local['records']))*1e6,
            first_vs_later_mean_ratio=times[0]/statistics.mean(times[1:]) if len(times)>1 else None,
            source_level_expected_copy_count=len(plan['ops']),
            source_level_expected_synchronization_count=len(plan['ops']),
            actual_gpu_dispatch_observed=False))
    ids = {op['replica_id'] for case in cases for op in case['plan']['ops']}
    epochs = {op['source_epoch'] for case in cases for op in case['plan']['ops']}
    assert len(ids)==len(epochs)==1
    body = dict(status='original_plan_and_source_audit_passed', cases=cases, evidence_refs=refs,
        source_identity_stable=True, source_index_unused_in_copy_methods=True,
        cuda_pointer_alignment_historical=None, cuda_runtime_dispatch_historical=None,
        scope='static code and original timing; no generated tensor, GPU execution, or fitted model')
    target=out/'audit.json'
    assert not target.exists(), 'original audit output must not be overwritten'
    target.write_text(json.dumps(body, indent=2)+'\n')
    print(json.dumps(dict(status=body['status'], cases=len(cases), output=str(target))))


if __name__=='__main__':
    main()
