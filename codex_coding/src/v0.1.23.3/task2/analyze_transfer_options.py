"""Offline sensitivity from measured bytes/timings; counterfactuals are not speedup evidence."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
GIB = 1 << 30


def main(out):
    root = ROOT/'codex_coding/results/v0.1.23.3/task2'
    data = json.loads((root/'preplan01/summary.json').read_text())
    primary = json.loads((root/'preplan01/load_gain.json').read_text())['cases'][1]
    r = data['restores'][1]; flow = r['flow']; plan = flow['plan']
    total = sum(t['n'] for t in plan['targets'])
    grouped = {}
    for op in plan['ops']:
        grouped[op['kind']] = grouped.get(op['kind'], 0)+op['n']
    assert sum(grouped.values()) == total == 70329718112
    targets = {t['worker'] for t in plan['targets']}
    ranks = []
    for w in flow['execution']:
        if w['worker'] not in targets:
            continue
        by = {}
        for op in w['records']:
            row = by.setdefault(op['kind'], {'bytes': 0, 'observed_s': 0.})
            row['bytes'] += op['bytes']; row['observed_s'] += op['elapsed_s']
        for row in by.values():
            row['effective_GBps'] = row['bytes']/1e9/row['observed_s']
        ranks.append({'worker': w['worker'], 'execute_s': w['execute_s'], 'paths': by})
    base_bytes = sum(s['n'] for s in data['capture']['sources'])
    hbm = grouped['local_hbm']; rank_count = len(targets)
    fixed = primary['load_s']-flow['execute_wall_s']
    hbm_tail = max(w['paths']['local_hbm']['observed_s'] for w in ranks)
    scenarios = []
    for rate in (25, 35, plan['profile']['path_rates']['local_dram:dram']):
        transfer = (total-hbm)/rank_count/1e9/rate+hbm_tail
        load = fixed+transfer
        scenarios.append({'assumed_local_dram_GBps_per_rank': rate, 'modeled_transfer_s': transfer,
                          'modeled_load_s': load, 'modeled_saved_s': primary['load_s']-load,
                          'modeled_reduction_pct': 100*(1-load/primary['load_s'])})
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'source': 'preplan01 measured serial32, preplanned restore',
        'measured': {'load_s': primary['load_s'], 'execute_s': flow['execute_wall_s'],
                     'total_GiB': total/GIB, 'path_bytes': grouped,
                     'path_GiB': {k: v/GIB for k, v in grouped.items()},
                     'path_percent': {k: 100*v/total for k, v in grouped.items()}, 'target_rank_records': ranks},
        'budget': {'current_complete_base_dram_GiB': base_bytes/GIB,
                   'current_extra_dram_GiB': data['placement']['limits']['tiers']['dram']/GIB,
                   'current_extra_hbm_GiB': data['placement']['limits']['tiers']['hbm']/GIB,
                   'current_global_persistent_dram_GiB': (base_bytes+2*GIB)/GIB,
                   'local_base_counterfactual_global_dram_GiB': base_bytes/GIB,
                   'local_base_counterfactual_hbm_GiB': hbm/GIB,
                   'note': 'retire the redundant 2GiB local cache; total persistent memory does not increase, but per-node budgets and immutable-base policy must change'},
        'local_base_counterfactual': scenarios,
        'assumptions': ['two ranks progress concurrently with independent feasible NUMA domains',
                       'local H2D sustained rates must be validated on the full manifest',
                       'non-execute loading overhead and HBM-copy tail held at observed values',
                       'target known in advance; other possible restore nodes can become slower',
                       'existing transport observations do not prove the NIC is saturated'],
        'migration_constraints': ['current bases are full-rank allocations and retirement releases whole owners',
            'partial logical coverage does not free physical pages from an existing complete allocation',
            'same steady-state global budget does not imply a feasible same-budget migration peak',
            'first test initial base placement in isolated cases, with setup bytes/time/peak accounted separately',
            'bounded online relocation requires independently releasable blocks plus verified coverage and failure-safe transitions'],
        'recommended_order': ['same global budget, remote-base versus target-local-base complete-model A/B',
                              'only if the primary metric improves: add primary-location selection and scenario-aware global budgets',
                              'then implement bounded base migration if runtime movement is needed',
                              'defer further chunk/pipeline tuning absent evidence of a useful main-metric opportunity']}
    out.mkdir(parents=True, exist_ok=False)
    (out/'analysis.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--out', required=True, type=Path)
    main(p.parse_args().out)
