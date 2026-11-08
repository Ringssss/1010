"""Build a read-only Qwen finite-candidate case from recorded inventory snapshots."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[6]
GIB = 1 << 30


def main(args):
    args.out.mkdir(parents=True, exist_ok=False)
    evidence = json.loads(args.inventory.read_text())
    state = json.loads(args.session.read_text())
    workers = evidence['workers']
    model = state['models']['B']
    replicas = {r['id']: r for w in workers for r in w['replicas'] if r['state'] == 'VALID'}
    base = [replicas[rid] for rid in model['source_ids']]
    budget, block = 8*GIB, 4*GIB
    slots = {'target_a': [0, 1], 'target_b': [8, 9]}
    scenarios, candidates = [], []
    wm = {w['worker']: w for w in workers}
    for gi, (name, targets) in enumerate(slots.items()):
        scenario = {'name': name, 'weight': 1, 'targets': []}
        for rank, source in enumerate(model['sources']):
            worker = targets[rank]
            scenario['targets'].append(dict(key=f'future/{name}/{rank}', worker=worker, tier='hbm',
                identity=source['identity'], tp=rank, off=0, n=source['n']))
            for bi, off in enumerate(range(0, min(source['n'], budget), block)):
                candidates.append(dict(id=f'cand/{gi}/{rank}/{bi}', identity=source['identity'], tp=rank,
                    worker=worker, node=wm[worker]['node'], numa=wm[worker]['numa'], tier='hbm',
                    off=off, n=min(block, source['n']-off), generation=1, state='VALID',
                    placement_group=gi, block=bi))
        scenarios.append(scenario)
    locations = {f"{c['worker']}:hbm": min(budget, wm[c['worker']]['budget_bytes']['hbm']
                 -wm[c['worker']]['used_bytes']['hbm']) for c in candidates}
    case = dict(base=base, candidates=candidates, workers=workers, scenarios=scenarios,
                profile=state['profile'], tier_budgets={'hbm': budget}, location_budgets=locations)
    case_path, solution_path = args.out/'case.json', args.out/'solutions.json'
    case_path.write_text(json.dumps(case, indent=2)+'\n')
    solver = Path(__file__).parent.parent/'placement/solve_matrix.py'
    subprocess.run([sys.executable, str(solver), '--input', str(case_path), '--output', str(solution_path)], check=True)
    solution = json.loads(solution_path.read_text())
    report = {'status': 'planning_only', 'layout_signatures_equal': solution['layout_signatures_equal'],
        'base_logical_bytes': sum(r['n'] for r in base), 'extra_hbm_budget_bytes': budget,
        'candidate_block_bytes': block, 'candidate_count': len(candidates), 'scenarios': scenarios,
        'inputs': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.inventory, args.session, solver)},
        'scope': 'recorded inventory and provisional bandwidth profile; no GPU operation, current admission, or performance evidence',
        'policies': {}}
    for name, policy in solution['policies'].items():
        report['policies'][name] = {'selected': [{k: r[k] for k in ('tp', 'worker', 'tier', 'off', 'n')}
            for r in policy['selected']], 'allocated_hbm_bytes': policy['allocated_hbm_bytes'],
            'predicted_expected_s': {route: row['predicted_expected_s'] for route, row in policy['routing'].items()},
            'scenario_predicted_s': {route: {s: p['estimated_transfer_s'] for s, p in row['scenario_plans'].items()}
                                      for route, row in policy['routing'].items()},
            'solve_wall_s': policy['solve_wall_s'], 'solver_status': policy.get('status'), 'mip_gap': policy.get('mip_gap')}
    (args.out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    main(parser.parse_args())
