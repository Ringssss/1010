"""Read existing cost-model and runtime artifacts without invoking GPU code."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
ACTUAL = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_runtime/operations_actual_r01'


def read(path):
    return json.loads(path.read_text())


def path_key(src, tier, numa, dst, dst_tier='hbm'):
    return f'{src}:{tier}:{numa}->{dst}' + (':' + dst_tier if dst_tier != 'hbm' else '')


def topology_id(workers):
    physical = [{k: w[k] for k in ('worker', 'node', 'gpu', 'numa')} for w in workers]
    return hashlib.sha256(json.dumps(physical, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    qualification = read(ACTUAL / 'qualification/qualification.json')
    checks = []
    refs = []

    def source(path):
        refs.append(dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

    def check(name, passed):
        checks.append(dict(name=name, passed=bool(passed)))

    source(ACTUAL / 'qualification/qualification.json')
    routes = []
    for key, proof in qualification['paths'].items():
        expected = path_key(proof['source_worker'], proof['source_tier'], proof['source_numa'],
                            proof['target_worker'], proof['target_tier'])
        check('qualification_exact_key:' + key, key == expected)
        check('qualification_full_SHA:' + key, proof['status'] == 'verified' and
              proof['transfer']['verification']['passed'] and
              proof['transfer']['verification']['checked_bytes'] == proof['nbytes'] == 4 << 20)
        routes.append(dict(path=key, src_tier=proof['source_tier'], dst_tier=proof['target_tier'],
                           nbytes=proof['nbytes'], executor=proof['executor']))
    check('qualification_23_routes', len(routes) == 23)
    check('destination_tier_separate_key', path_key(0, 'ssd', 0, 0, 'dram') != path_key(0, 'ssd', 0, 0, 'hbm'))
    phases = []
    for phase in ('SSD_only', 'C0', 'C1', 'C3'):
        path = ACTUAL / f'cases/{phase}.json'
        source(path)
        data = read(path)
        for stage in ('save', 'restore'):
            plan = data[stage + '_plan']
            check(phase + ':' + stage + ':frozen_and_unpredicted',
                  plan['strategy'] == 'frozen_witness' and plan['estimated_transfer_s'] is None and
                  plan['bandwidth_lower_bound_s'] is None and all(op['resources'] == [] for op in plan['ops']))
            targets = {t['key']: t for t in plan['targets']}
            for op in plan['ops']:
                proof = qualification['paths'][op['path']]
                check(phase + ':' + stage + ':qualified_exact_tier_size:' + str(op['op_id']),
                      proof['target_tier'] == targets[op['target']]['tier'] and proof['nbytes'] == op['n'])
        phases.append(dict(phase=phase, strategy=data['restore_plan']['strategy'],
                           bandwidth_lower_bound_s=None, estimated_transfer_s=None,
                           service_prediction_s=None, restore_ops=len(data['restore_plan']['ops'])))
    old_models = []
    current_topology = topology_id(qualification['workers'])
    for name in ('costcal01', 'costval01', 'requestcal01', 'requestselected01', 'requestcurrent01'):
        path = ROOT / 'codex_coding/results/v0.1.23.3/task2' / name / 'service_model.json'
        source(path)
        model = read(path)
        check(name + ':incompatible_topology', model['topology_id'] != current_topology)
        old_models.append(dict(run=name, schema_version=model['schema_version'], topology_id=model['topology_id'],
                               current_topology_matches=False, paths=sorted(model['paths']),
                               fit_bytes={k: v['fit_bytes'] for k, v in model['paths'].items()}))
    for relative in ('main_lib/fluidckpt/cluster/service_cost.py', 'main_lib/fluidckpt/cluster/planning.py',
                     'main_lib/fluidckpt/cluster/path_catalog.py', 'main_lib/fluidckpt/cluster/multi_joint.py',
                     'main_lib/fluidckpt/cluster/managed.py', 'main_lib/fluidckpt/cluster/control.py',
                     'main_lib/fluidckpt/cluster/worker.py',
                     'codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/adapter.py',
                     'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/run.py'):
        source(ROOT / relative)
    runner = ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/run.py'
    tree = ast.parse(runner.read_text())
    calls = sorted({node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                    for node in ast.walk(tree) if isinstance(node, ast.Call) and
                    isinstance(node.func, (ast.Name, ast.Attribute))})
    check('runner_no_cost_prediction_call', not set(calls) & {'estimate_execution', 'restore_plan', 'solve_joint_request', 'fit_model', 'fit_request_model'})
    result = dict(schema_version=1, status='passed' if all(c['passed'] for c in checks) else 'failed',
                  scope='local read-only artifact audit; no fitted or invented predictions',
                  current_topology_id=current_topology, current_service_model=None,
                  current_capacity_profile=None, absolute_model_accuracy=None,
                  qualification_routes=routes, phases=phases, old_models=old_models,
                  runner_call_names=calls, checks=checks, sources=refs)
    (out / 'audit.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(status=result['status'], checks=len(checks), failed=sum(not c['passed'] for c in checks), out=str(out))))
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
