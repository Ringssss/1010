"""Offline finite-candidate placement comparison; never contacts GPU workers."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster.multi_joint import joint_scenarios
from fluidckpt.cluster.planning import covers, restore_plan


def signature(selected):
    return sorted((r['tp'], r['worker'], r['tier'], r['off'], r['n']) for r in selected)


def solve(case):
    base, candidates = case['base'], case['candidates']
    scenarios, workers, profile = case['scenarios'], case['workers'], case['profile']
    budget, limits = case['tier_budgets'], case['location_budgets']
    if not profile.get('serial_target_execution'):
        raise ValueError('serial executor requires an additive-target profile')
    for scenario in scenarios:
        for t in scenario['targets']:
            if not covers(base, t['identity'], t['tp'], t['off'], t['off']+t['n']):
                raise ValueError('complete charged base coverage is required')

    def legal(rs):
        return (all(sum((r['n']+4095)//4096*4096 for r in rs if r['tier'] == tier) <= cap
                    for tier, cap in budget.items())
                and all(sum((r['n']+4095)//4096*4096 for r in rs
                            if f"{r['worker']}:{r['tier']}" == loc) <= cap for loc, cap in limits.items()))

    def evaluate(selected, strategy):
        plans = [restore_plan(base+selected, s['targets'], workers, profile, strategy) for s in scenarios]
        if strategy == 'resource_lp' and any(p['strategy'] != strategy for p in plans):
            raise RuntimeError('LP fallback cannot be scored as LP')
        weights = [s.get('weight', 1) for s in scenarios]
        value = sum(p['estimated_transfer_s']*w for p, w in zip(plans, weights))/sum(weights)
        return {'predicted_expected_s': value, 'scenario_plans': {
            s['name']: p for s, p in zip(scenarios, plans)}}

    # Pairing ranks avoids a zero-marginal-gain trap in max-rank objectives.
    bundles = [[c] for c in candidates]
    paired = {}
    for c in candidates:
        paired.setdefault((c['placement_group'], c['block']), []).append(c)
    bundles += list(paired.values())
    selected, stamp = [], time.monotonic()
    current = evaluate(selected, 'fastest')['predicted_expected_s']
    greedy_steps = []
    while True:
        ids, choices = {r['id'] for r in selected}, []
        for bundle in bundles:
            addition = [r for r in bundle if r['id'] not in ids]
            if not addition or not legal(selected+addition):
                continue
            score = evaluate(selected+addition, 'fastest')['predicted_expected_s']
            gain = current-score
            if gain > 1e-9:
                size = sum((r['n']+4095)//4096*4096 for r in addition)
                # Normalize to GiB before rounding so 12 decimals do not erase byte-scale densities.
                density = gain/(size/(1 << 30))
                choices.append((-round(density, 12), round(score, 12),
                                tuple(r['id'] for r in addition), score, addition))
        if not choices:
            break
        _, _, _, current, addition = min(choices, key=lambda row: (row[0], row[1], row[2]))
        selected += addition
        greedy_steps.append({'ids': [r['id'] for r in addition], 'predicted_s': current})
    greedy = {'selected': selected, 'steps': greedy_steps, 'solve_wall_s': time.monotonic()-stamp,
              'tie_break': 'gain per allocated GiB rounded to 12 decimals; then lower candidate score rounded to 12 decimals; then candidate IDs',
              'scope': 'remote-aware marginal gain per allocated byte, singleton and paired-rank bundles; fastest-path scorer'}
    joint = joint_scenarios(base, candidates, scenarios, workers, profile, budget, limits,
                           solver_options={'time_limit': case.get('solver_time_limit_s', 20)})
    policies = {'greedy': greedy, 'joint': joint}
    for row in policies.values():
        if not legal(row['selected']):
            raise AssertionError('selected layout exceeds budget')
        row['routing'] = {strategy: evaluate(row['selected'], strategy)
                          for strategy in ('fastest', 'resource_lp')}
        row['allocated_hbm_bytes'] = sum((r['n']+4095)//4096*4096 for r in row['selected'])
    return {'schema_version': 1, 'status': 'planning_only', 'policies': policies,
            'layout_signatures_equal': signature(greedy['selected']) == signature(joint['selected']),
            'base_logical_bytes': sum(r['n'] for r in base),
            'base_allocated_bytes': sum(r.get('allocated_bytes', (r['n']+4095)//4096*4096) for r in base),
            'scope': 'finite candidate optimum under a provisional resource model; no measured recovery or global optimality claim'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    raw = args.input.read_bytes()
    result = solve(json.loads(raw))
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: result[k] for k in ('status', 'layout_signatures_equal', 'base_logical_bytes')}))
