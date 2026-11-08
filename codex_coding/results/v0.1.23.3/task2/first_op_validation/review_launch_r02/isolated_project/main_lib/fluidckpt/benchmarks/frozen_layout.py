"""Thin frozen-layout contracts and existing finite-candidate greedy controls."""
import copy
import hashlib
import itertools
import json
from pathlib import Path
import time

from .topology_cases import allocated, canonical_hash, interleave, validate_case
from fluidckpt.cluster.multi_joint import aggregate_cost, joint_scenarios
from fluidckpt.cluster.planning import placement_plan, restore_plan


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_frozen(manifest_path, case_id, root, session_path):
    manifest_path, root = Path(manifest_path).resolve(), Path(root).resolve()
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('schema_version') != 'topology_freeze.v1'
            or manifest.get('development') != 'v0.1.23.3/task2'
            or manifest.get('new_GPU_samples') != 0
            or manifest.get('GPU_or_SSH_mutations') is not False):
        raise ValueError('unexpected frozen manifest schema/development/measurement scope')
    payload = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    if canonical_hash(payload) != manifest['manifest_sha256']:
        raise ValueError('frozen manifest hash mismatch')
    for name, row in manifest['inputs'].items():
        if digest(row['path']) != row['sha256']:
            raise ValueError('frozen input hash mismatch: ' + row['path'])
        if digest(manifest_path.parent / 'inputs' / (name + '.json')) != row['sha256']:
            raise ValueError('frozen snapshot hash mismatch: ' + name)
    for path, expected in manifest['code_sha256'].items():
        if digest(root / path) != expected:
            raise ValueError('frozen code hash mismatch: ' + path)
    if digest(session_path) != manifest['inputs']['session']['sha256']:
        raise ValueError('session differs from the frozen owned overlay')
    registered = next(r for r in manifest['cases'] if r['case_id'] == case_id)
    if registered.get('profile_source') != 'functional_provisional_assumption':
        raise ValueError('this adapter requires the registered functional provisional profile')
    case = json.loads((manifest_path.parent / 'cases' / (case_id + '.json')).read_text())
    if canonical_hash(case) != registered['case_sha256']:
        raise ValueError('frozen case hash mismatch')
    validate_case(case)
    profile = json.loads(Path(manifest['inputs']['session']['path']).read_text())['profile']
    if not profile.get('serial_target_execution'):
        raise ValueError('serial target service model required')
    return case, profile, registered


def layout_ledger(case, selected):
    known = {c['id']: c for c in case['candidates']}
    if len({c['id'] for c in selected}) != len(selected):
        raise ValueError('duplicate allocation ID')
    for c in selected:
        if c['id'] not in known or c != known[c['id']]:
            raise ValueError('selection differs from frozen finite candidates')
    locations = {key: sum(allocated(c) for c in selected if f"{c['worker']}:{c['tier']}" == key)
                 for key in case['budget']['location_budgets']}
    total = sum(locations.values())
    if total > case['budget']['tier_budgets']['hbm'] or any(
            n > case['budget']['location_budgets'][key] for key, n in locations.items()):
        raise ValueError('selection exceeds frozen global/location caps')
    regions = {}
    for c in selected:
        regions.setdefault((c['identity'], c['tp']), []).append((c['off'], c['off'] + c['n']))
    unique = 0
    for spans in regions.values():
        end = -1
        for a, b in sorted(spans):
            unique += max(0, b - max(a, end))
            end = max(end, b)
    return dict(allocated_hbm_bytes=total, location_bytes=locations,
                cache_build_logical_bytes=sum(c['n'] for c in selected),
                unique_logical_bytes=unique,
                duplicated_logical_bytes=sum(c['n'] for c in selected) - unique,
                base_dram_bytes=case['budget']['base_dram_bytes'],
                target_allocation_bytes=case['budget']['target_allocation_bytes'],
                staging_bytes=case['budget']['staging_bytes'])


def evaluate(case, profile, selected, strategy='resource_lp'):
    layout_ledger(case, selected)
    plans = [interleave(restore_plan(case['base'] + selected, s['targets'], case['workers'],
                                   profile, strategy)) for s in case['restore_scenarios']]
    if any(p['strategy'] != strategy for p in plans):
        raise RuntimeError('routing fallback cannot be scored as the requested method')
    values = [p['estimated_transfer_s'] for p in plans]
    return dict(objective_s=aggregate_cost(values, case['restore_scenarios'], case['objective']),
                plans=dict(zip([s['name'] for s in case['restore_scenarios']], plans)))


def strong_simple(case, profile):
    """Reuse singleton/paired-rank marginal greedy and existing joint_greedy.

    Every candidate copy remains a separate legal addition. Pairing avoids the
    zero-singleton-gain trap of a maximum-rank objective. No new solver is used.
    """
    started = time.monotonic()
    candidates = case['candidates']
    bundles = [[c] for c in candidates]
    bundles += [[a, b] for a, b in itertools.combinations(candidates, 2)
                if a['tp'] != b['tp'] and (a['off'], a['n']) == (b['off'], b['n'])]
    controls = {}
    for strategy in ('fastest', 'resource_lp'):
        selected, steps = [], []
        current = evaluate(case, profile, selected, strategy)['objective_s']
        while True:
            ids, choices = {c['id'] for c in selected}, []
            for bundle in bundles:
                addition = [c for c in bundle if c['id'] not in ids]
                if not addition:
                    continue
                try:
                    layout_ledger(case, selected + addition)
                except ValueError:
                    continue
                score = evaluate(case, profile, selected + addition, strategy)['objective_s']
                gain = current - score
                if gain > 1e-9:
                    density = gain / (sum(allocated(c) for c in addition) / (1 << 30))
                    choices.append((-round(density, 12), round(score, 12),
                                    tuple(c['id'] for c in addition), score, addition))
            if not choices:
                break
            _, _, _, current, addition = min(choices, key=lambda r: r[:3])
            selected += addition
            steps.append(dict(ids=[c['id'] for c in addition], objective_s=current))
        controls['paired_' + strategy] = dict(selected=selected, steps=steps)
    workers = copy.deepcopy(case['workers'])
    for w in workers:
        w['budget_bytes']['hbm'] = w['used_bytes']['hbm'] + case['budget']['location_budgets'][f"{w['worker']}:hbm"]
    requests = [dict(c, locations=[dict(worker=c['worker'], tier='hbm')]) for c in candidates]
    if case['objective'] == 'expected':
        marginal = placement_plan(case['base'], requests, workers, profile, 'joint_greedy',
                                  scenarios=case['restore_scenarios'],
                                  extra_budget=case['budget']['tier_budgets']['hbm'])
        if marginal['evaluation_fallback_reasons']:
            raise RuntimeError('marginal greedy evaluation fell back')
        ids = {d['replica']['id'] for d in marginal['decisions']}
        controls['marginal_resource_lp'] = dict(selected=[c for c in candidates if c['id'] in ids], details=marginal)
    for row in controls.values():
        row['evaluation'] = evaluate(case, profile, row['selected'])
        row['ledger'] = layout_ledger(case, row['selected'])
    winner = min(controls, key=lambda name: (round(controls[name]['evaluation']['objective_s'], 12),
                                            controls[name]['ledger']['allocated_hbm_bytes'], name))
    return dict(selected=controls[winner]['selected'], controls=controls, winner=winner,
                solve_wall_s=time.monotonic() - started,
                scope='existing marginal/paired greedy; identical candidates and copies; common LP final scorer')


def decision_signature(selected, evaluation):
    layout = sorted((c['identity'], c['tp'], c['worker'], c['tier'], c['off'], c['n']) for c in selected)
    ops = {name: [{k: op[k] for k in ('target', 'identity', 'tp', 'off', 'n', 'src_w', 'dst_w',
                                     'replica_id', 'kind', 'resources')} for op in plan['ops']]
           for name, plan in evaluation['plans'].items()}
    return canonical_hash(dict(layout=layout, ops=ops))


def compare_placement(case, profile):
    validate_case(case)
    simple = strong_simple(case, profile)
    b = case['budget']
    joint = joint_scenarios(case['base'], case['candidates'], case['restore_scenarios'],
                            case['workers'], profile, b['tier_budgets'], b['location_budgets'],
                            objective=case['objective'], solver_options={'time_limit': 5})
    rows = {'strong_simple': simple, 'joint': joint}
    for row in rows.values():
        row['ledger'] = layout_ledger(case, row['selected'])
        row['evaluation'] = evaluate(case, profile, row['selected'])
        row['decision_signature'] = decision_signature(row['selected'], row['evaluation'])
    same = simple['decision_signature'] == joint['decision_signature']
    return dict(status='CPU_verified_only', case_id=case['case_id'], policies=rows,
                candidate_ids=[c['id'] for c in case['candidates']],
                scenario_weights={s['name']: s['weight'] for s in case['restore_scenarios']},
                objective=case['objective'], budget=b, schedule='rank_interleaved',
                decision_aliases=[dict(method='joint', same_decision_as='strong_simple',
                                       action='do_not_repeat_GPU')] if same else [],
                decisions_equal=same, profile_source='functional_provisional_assumption',
                prediction_ready=False, physical_contention_claim_ready=False,
                measurement_status='not_measured', new_GPU_samples=0)


def bind_real_cache(case, selected, actual, workers, owner_ids):
    """Only successfully built actual catalog rows can become runtime sources."""
    ledger = layout_ledger(case, selected)
    if len(owner_ids) != len(selected) or len(set(owner_ids)) != len(owner_ids):
        raise ValueError('missing or duplicate runtime owner IDs')
    wm, rows = {w['worker']: w for w in workers}, {r['id']: r for r in actual}
    if len(rows) != len(actual) or set(rows) != set(owner_ids):
        raise ValueError('actual catalog does not exactly match this run cache owners')
    bound = []
    for c, rid in zip(selected, owner_ids):
        r = rows[rid]
        if r.get('preview_only') or rid.startswith('preview/') or r['state'] != 'VALID' or r.get('leases', 0):
            raise ValueError('cache source is hypothetical, invalid or leased')
        if any(r.get(k) != c[k] for k in ('identity', 'tp', 'off', 'n', 'worker', 'tier', 'node', 'gpu', 'numa')):
            raise ValueError('built extent differs from frozen selected candidate')
        if r.get('epoch') != wm[r['worker']]['epoch'] or not isinstance(r.get('generation'), int) or r['generation'] < 1:
            raise ValueError('cache source lacks current worker incarnation/generation')
        if r.get('allocated_bytes') != allocated(c):
            raise ValueError('actual allocation differs from page-rounded frozen ledger')
        bound.append(copy.deepcopy(r))
    if sum(r['allocated_bytes'] for r in bound) != ledger['allocated_hbm_bytes']:
        raise ValueError('actual physical copy accounting mismatch')
    return bound
