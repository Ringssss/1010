"""Independent enumeration and failure contracts for discovery, scenarios and feedback."""
import copy
import itertools
import math

import pytest

from test_managed import make_case, PROFILE, G, MemoryCluster
from fluidckpt.cluster.managed import rows, normalize_request, capacity_limits, generate_candidates, solve_joint_request
from fluidckpt.cluster.multi_joint import joint_scenarios, aggregate_cost
from fluidckpt.cluster.path_catalog import (new_catalog, discovery_tasks, record_probe,
    admitted_profile, refresh_tasks)
from fluidckpt.cluster.planning import restore_plan
from fluidckpt.cluster.feedback import migration_gate, update_feedback


def scenarios(q):
    return [{'name': 'remote', 'weight': .8, 'targets': q['targets']},
            {'name': 'origin', 'weight': .2, 'targets': [dict(t, worker=t['worker']-2) for t in q['targets']]}]


@pytest.mark.parametrize('objective', ['expected', 'worst'])
def test_scenario_milp_matches_exhaustive_placements(objective):
    ws, q = make_case(n=G)
    q = normalize_request(q, ws); limits = capacity_limits(q, ws)
    candidates, _ = generate_candidates(q, ws, PROFILE, limits)
    base = rows(ws, True); ss = scenarios(q)
    result = joint_scenarios(base, candidates, ss, ws, PROFILE, limits['tiers'], limits['locations'], objective=objective)
    best = math.inf
    for n in range(3):
        for subset in itertools.combinations(candidates, n):
            values = [restore_plan(base+list(subset), s['targets'], ws, PROFILE, 'resource_lp')['estimated_transfer_s'] for s in ss]
            best = min(best, aggregate_cost(values, ss, objective))
    assert result['objective_s'] == pytest.approx(best)
    assert result['rounded_plan_s'] == pytest.approx(best)
    assert sum(r['n'] for r in result['selected']) <= 2*G


def test_discovery_direction_tier_expiry_topology_and_failed_refresh():
    ws, _ = make_case(); cat = new_catalog(ws)
    tasks = discovery_tasks(ws, pairs=[(0, 2)], nbytes=32 << 20)
    assert len(tasks) == 2
    record_probe(cat, tasks[0], {'passed': True, 'verified_bytes': tasks[0]['nbytes'], 'execute_s': .1}, now=100)
    p, audit = admitted_profile(cat, ws, PROFILE, now=110, max_age_s=20)
    assert p['network_pairs'] == [[0, 2]] and len(p['verified_paths']) == 1
    assert p['directed_path_rates'] == {}
    assert refresh_tasks(cat, tasks, now=110, max_age_s=20) == tasks[1:]
    assert not admitted_profile(cat, ws, PROFILE, now=121, max_age_s=20)[0]['verified_paths']
    other = copy.deepcopy(ws); other[0]['numa'] = 8
    with pytest.raises(ValueError, match='topology'):
        admitted_profile(cat, other, PROFILE, now=110)
    record_probe(cat, tasks[0], {'error': 'timeout'}, now=115)
    assert not admitted_profile(cat, ws, PROFILE, now=116)[0]['verified_paths']


def policy(**kw):
    return dict(expected_restores=100, uncertainty_s=0, minimum_net_s=0,
                build_fixed_s=.01, retire_fixed_s=.001, verify_GBps=1,
                cooldown_s=0, since_last_change_s=100, provenance='synthetic test cost', **kw)


def test_migration_amortization_and_capacity_override():
    ws, q = make_case(); plan = solve_joint_request(ws, q, PROFILE)
    args = (plan['predicted_before_s'], plan['predicted_after_s'], plan['transition'], rows(ws, True), ws, PROFILE)
    good = migration_gate(*args, policy())
    assert good['allowed'] and good['break_even_restores'] > 0
    p = policy(); p['expected_restores'] = 0
    assert not migration_gate(*args, p)['allowed']
    assert migration_gate(*args, p, current_fits=False)['reason'] == 'capacity_required'
    p = policy(); p['cooldown_s'] = 101
    assert migration_gate(*args, p)['reason'] == 'cooldown'


def test_feedback_cannot_infer_path_rate_from_aggregate_restore():
    p = dict(PROFILE, verified_paths=['0:dram:0->2'], feedback_context_id='sample', directed_path_rates={'0:dram:0->2': 10})
    sample = {'executor': 'serial', 'verified': True, 'profile_id': 'sample', 'kind': 'isolated_path',
              'path': '0:dram:0->2', 'nbytes': 2*10**9, 'execute_s': 1}
    updated = update_feedback(p, sample)
    assert updated['directed_path_rates'][sample['path']] == 8
    assert p['directed_path_rates'][sample['path']] == 10
    with pytest.raises(ValueError, match='duplicate'):
        update_feedback(updated, sample)
    for change in ({'kind': 'whole_restore'}, {'verified': False}, {'profile_id': 'other'}, {'nbytes': 32}):
        with pytest.raises(ValueError):
            update_feedback(p, dict(sample, **change))


def test_managed_multi_scenario_apply_and_demand_binding():
    ws, q = make_case(n=G)
    q['scenarios'] = scenarios(q); q['objective'] = 'worst'; q['scenario_mode'] = 'exclusive'
    cluster = MemoryCluster(ws)
    plan = cluster.plan_joint(q)
    assert plan['solver']['objective'] == 'worst'
    assert set(plan['scenario_costs']) == {'remote', 'origin'}
    cluster.apply_joint(plan)
    for s in q['scenarios']:
        restore = cluster.plan_managed_restore(plan, s['targets'], scenario=s['name'])
        assert restore['scenario'] == s['name']
    with pytest.raises(ValueError, match='scenario'):
        cluster.plan_managed_restore(plan, q['targets'], scenario='unknown')


@pytest.mark.parametrize('change', [
    {'scenario_mode': 'concurrent'}, {'objective': 'unknown'},
    {'scenarios': []}, {'scenarios': [{'name': 'bad', 'weight': -1, 'targets': []}]},
])
def test_reject_unsupported_scenario_contract(change):
    ws, q = make_case()
    q.update(scenarios=scenarios(q), scenario_mode='exclusive', objective='expected')
    q.update(change)
    with pytest.raises(ValueError):
        solve_joint_request(ws, q, PROFILE)


def test_integrated_gate_keeps_current_with_no_future_reuse():
    ws, q = make_case()
    p = policy(); p['expected_restores'] = 0
    q['migration_policy'] = p
    plan = solve_joint_request(ws, q, PROFILE)
    assert plan['decision'] == 'keep_current' and plan['transition']['actions'] == []
    assert plan['economics']['reason'] == 'insufficient_net_gain'
    p['expected_restores'] = 1000
    plan = solve_joint_request(ws, q, PROFILE)
    assert plan['decision'] == 'replace' and plan['economics']['allowed']


def test_strict_paths_reject_reverse_and_other_tier():
    import time
    from fluidckpt.cluster.planning import path_for
    ws, q = make_case()
    cat = new_catalog(ws); task = discovery_tasks(ws, pairs=[(0, 2)], tiers=['dram'], nbytes=G)[0]
    record_probe(cat, task, {'passed': True, 'verified_bytes': G, 'execute_s': 1})
    profile, _ = admitted_profile(cat, ws, PROFILE)
    wm = {w['worker']: w for w in ws}; base = rows(ws, True)[0]
    assert path_for(base, q['targets'][0], wm, profile)['GBps'] == pytest.approx(G/1e9)
    assert path_for(dict(base, tier='hbm'), q['targets'][0], wm, profile) is None
    assert path_for(dict(base, worker=2), dict(q['targets'][0], worker=0), wm, profile) is None
    profile['path_evidence_expires_at'] = time.time()-1
    with pytest.raises(ValueError, match='expired'):
        restore_plan(rows(ws, True), q['targets'], ws, profile)
