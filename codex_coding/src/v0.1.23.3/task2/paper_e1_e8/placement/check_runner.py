"""CPU checks for overlay selection and cache admission before any RPC mutation."""
import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def main(out):
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('matrix_under_test', Path(__file__).with_name('run_matrix.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    size = 32 << 30
    sources = [dict(identity='test', tp=r, n=size, meta={}, index={}) for r in range(2)]
    slots = {'A': {'parked': False}, 'B': {'parked': False},
             'target_a': {'endpoint': 'http://unused-a', 'workers': [0, 1], 'parked': True},
             'target_b': {'endpoint': 'http://unused-b', 'workers': [2, 3], 'parked': True}}
    session = out/'session.json'
    session.write_text(json.dumps(dict(models={'B': {'sources': sources, 'source_ids': ['b0', 'b1'],
                 'reference': {'text': 'unused'}, 'tp': 2}}, slots=slots, endpoints=[],
                 profile={}, handoff=str(out/'handoff'))))
    args = SimpleNamespace(session=session, run_dir=out/'run', stage='e3', budget_gib=8,
                           budgets_gib='0,8,16', block_gib=4, extra_layouts=False)
    runner = module.MatrixRun(args)
    if set(runner.slots) != {'target_a', 'target_b'}:
        raise AssertionError('inherited aliases were selected')
    target = runner.target(sources[0], 'target', 0, mode='live')
    if target['mode'] != 'live':
        raise AssertionError('live override failed')
    workers = [dict(worker=w, node='ab'[w//2], numa=w%2,
                    budget_bytes={'hbm': 64 << 30}, used_bytes={'hbm': 0},
                    headroom_bytes={'hbm': 40 << 30}) for w in range(4)]
    base = [dict(s, id=f'b{r}', state='VALID', off=0, tier='dram', worker=r) for r, s in enumerate(sources)]
    runner.inventory = lambda: (workers, base)
    selected = runner.manual('concentrated', 8 << 30, workers)
    try:
        runner.build(selected, 'must_reject', 8 << 30)
    except MemoryError:
        pass
    else:
        raise AssertionError('cache admission ignored future weight allocation')
    if runner.owned or runner.result['builds']:
        raise AssertionError('admission failure mutated build state')
    layouts = {}
    for name in ('concentrated', 'distributed', 'replicated', 'hybrid'):
        selected = runner.manual(name, 8 << 30, workers)
        layouts[name] = sum((r['n']+4095)//4096*4096 for r in selected)
        if layouts[name] != 8 << 30:
            raise AssertionError('manual layout changed physical cache budget')
    (out/'summary.json').write_text(json.dumps({'status': 'passed',
        'checks': ['explicit slots', 'live mode override', 'future output admission', 'equal cache budget'],
        'layout_allocated_bytes': layouts}, indent=2)+'\n')
    print(json.dumps({'status': 'passed', 'checks': 4}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    main(parser.parse_args().out)
