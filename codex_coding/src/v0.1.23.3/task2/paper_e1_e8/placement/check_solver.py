"""Exercise finite-candidate CLI against an independently enumerable small case."""
import argparse
import itertools
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.cluster.planning import restore_plan


def main(out):
    out.mkdir(parents=True, exist_ok=False)
    size = 4 << 20
    profile = {'rates': {'local_dram': 2, 'local_hbm': 20, 'peer': 5, 'network': 1},
               'gpu_ingress_GBps': 20, 'gpu_egress_GBps': 20, 'dram_GBps': 20,
               'network_node_GBps': 2, 'serial_target_execution': True}
    workers = [{'worker': w, 'node': 'abx'[w//2], 'numa': w%2,
                'capabilities': list(profile['rates'])} for w in range(6)]
    base = [dict(id=f'base/{r}', identity='tiny', tp=r, worker=r+4, node='x', numa=r,
                 tier='dram', off=0, n=size, state='VALID', generation=1) for r in range(2)]
    candidates = [dict(base[r], id=f'cand/{group}/{r}', worker=2*group+r, node='ab'[group],
                       tier='hbm', placement_group=group, block=0) for group in range(2) for r in range(2)]
    scenarios = [{'name': f's{group}', 'weight': 1, 'targets': [
        dict(key=f't/{group}/{r}', identity='tiny', tp=r, worker=group*2+r, off=0, n=size)
        for r in range(2)]} for group in range(2)]
    report = []
    for budget in (0, 2*size, 4*size):
        case = dict(base=base, candidates=candidates, scenarios=scenarios, workers=workers, profile=profile,
                    tier_budgets={'hbm': budget}, location_budgets={f'{w}:hbm': size for w in range(4)})
        src, dst = out/f'{budget}_input.json', out/f'{budget}_output.json'
        src.write_text(json.dumps(case))
        subprocess.run([sys.executable, str(Path(__file__).with_name('solve_matrix.py')),
                        '--input', str(src), '--output', str(dst)], check=True)
        result = json.loads(dst.read_text())
        scores = []
        for mask in itertools.product((0, 1), repeat=len(candidates)):
            selected = [c for c, bit in zip(candidates, mask) if bit]
            if sum(c['n'] for c in selected) > budget:
                continue
            score = sum(restore_plan(base+selected, s['targets'], workers, profile,
                                     'resource_lp')['estimated_transfer_s'] for s in scenarios)/2
            scores.append(score)
        got = result['policies']['joint']['rounded_plan_s']
        if abs(got-min(scores)) > 1e-8:
            raise AssertionError((budget, got, min(scores)))
        if result['base_logical_bytes'] != 2*size:
            raise AssertionError('base not fully charged')
        for policy in result['policies'].values():
            if policy['allocated_hbm_bytes'] > budget:
                raise AssertionError('budget not enforced')
        report.append({'budget_bytes': budget, 'enumerated_optimum_s': min(scores), 'milp_s': got,
                       'layout_signatures_equal': result['layout_signatures_equal']})
    (out/'summary.json').write_text(json.dumps({'status': 'passed', 'cases': report}, indent=2)+'\n')
    print(json.dumps({'status': 'passed', 'cases': len(report)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    main(parser.parse_args().out)
