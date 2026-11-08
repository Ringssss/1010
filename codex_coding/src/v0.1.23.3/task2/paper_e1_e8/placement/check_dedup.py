"""CPU-only mocked execution tests; no timing in these outputs is a measurement."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def main(args):
    args.out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('matrix_under_test', Path(__file__).with_name('run_matrix.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case = json.loads((args.preview/'case.json').read_text())
    solution = json.loads((args.preview/'solutions.json').read_text())
    if not solution['layout_signatures_equal']:
        raise ValueError('test requires a preview with identical selected layouts')
    for name in ('target_a', 'target_b'):
        fast = solution['policies']['greedy']['routing']['fastest']['scenario_plans'][name]
        lp = solution['policies']['joint']['routing']['resource_lp']['scenario_plans'][name]
        assert module.route_signature(fast) == module.route_signature(lp)
        assert module.route_signature(fast) != module.route_signature(dict(fast, ops=list(reversed(fast['ops']))))
        renumbered = copy.deepcopy(fast)
        for op in renumbered['ops']:
            op['op_id'] += 1000
        assert module.route_signature(fast) == module.route_signature(renumbered)
        renumbered['ops'][0]['source_offset'] += 1
        assert module.route_signature(fast) != module.route_signature(renumbered)
    sources = [dict(t, meta={}, index={}) for t in case['scenarios'][0]['targets']]
    slots = {s['name']: {'endpoint': 'http://unused-'+s['name'], 'workers': [t['worker'] for t in s['targets']],
                         'parked': True} for s in case['scenarios']}
    session = args.out/'mock_session.json'
    session.write_text(json.dumps({'models': {'B': {'sources': sources, 'source_ids': [r['id'] for r in case['base']],
        'reference': {'text': 'mock'}, 'tp': 2}}, 'slots': slots, 'endpoints': [], 'profile': case['profile'],
        'handoff': str(args.out/'unused_handoff')}))

    def runner(stage, comparison=None):
        options = SimpleNamespace(session=session, run_dir=args.out/f'mock_{stage}', stage=stage,
            budget_gib=8, budgets_gib='8', block_gib=4, extra_layouts=False,
            baseline_routing='fastest', candidate_routing='resource_lp', comparison_run=comparison)
        run = module.MatrixRun(options)
        run.result['test_scope'] = 'CPU mock; zero GPU operations; all timings fabricated for control-flow validation'
        run.inventory = lambda: (case['workers'], case['base'])
        run.cluster.inventory = lambda **kw: (case['workers'], case['base'])

        def solve(budget):
            (run.out/f'budget_{budget}_case.json').write_text(json.dumps(case))
            (run.out/f'budget_{budget}_solutions.json').write_text(json.dumps(solution))
            return solution

        run.solve = solve
        run.prewarm = lambda: run.result.update(prewarm={'status': 'mocked'})
        run.build = lambda selected, label, budget: run.result['builds'].append({'label': label}) or ['mock_source']
        run.clear = lambda: None

        def restore(label, scenario, routing, source_ids):
            run.result['cases'].append({'label': label, 'scenario': scenario, 'routing': routing,
                'phase': f'mock_{label}_{scenario}_{routing}', 'status': 'passed', 'correct': True,
                'verification': {'passed': True}, 'timing': {'load_s': 1.0}})

        run.restore = restore
        return run

    e4 = runner('e4')
    e4.run()
    assert len(e4.result['cases']) == 2
    assert len(e4.result['route_aliases']) == 6
    assert len(e4.result['builds']) == 1
    e5 = runner('e5', e4.out)
    e5.run()
    assert e5.result['status'] == 'decision_only'
    assert not e5.result['cases'] and not e5.result['builds'] and 'prewarm' not in e5.result
    assert len(e5.result['comparison']['samples']) == 4
    changed = copy.deepcopy(case)
    changed['profile']['network_node_GBps'] += 1
    (e5.out/f'budget_{8 << 30}_case.json').write_text(json.dumps(changed))
    try:
        e5.comparison_evidence(8 << 30, solution)
    except ValueError as exc:
        assert 'profile differs' in str(exc)
    else:
        raise AssertionError('changed profile was accepted')
    report = {'status': 'passed', 'scope': 'CPU mocks only; zero GPU activity',
              'checks': ['ordered ops preserved', 'op_id ignored', 'source offset checked',
                         'eight logical combinations become two observations and six aliases',
                         'one cache build', 'unchanged e5 makes no prewarm/build/restore', 'profile drift rejected']}
    (args.out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    main(parser.parse_args())
