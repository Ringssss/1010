"""Explicit migration amortization and scoped, versioned measurement feedback."""
import copy
import math
import hashlib
import json
import time

from .planning import restore_plan


def validate_policy(policy):
    required = ('expected_restores', 'uncertainty_s', 'minimum_net_s', 'build_fixed_s',
                'retire_fixed_s', 'verify_GBps', 'cooldown_s', 'since_last_change_s')
    for key in required:
        value = policy.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f'invalid migration policy {key}')
    if policy['verify_GBps'] <= 0:
        raise ValueError('verification bandwidth must be positive')
    if not isinstance(policy.get('provenance'), str) or not policy['provenance']:
        raise ValueError('migration cost provenance required')


def migration_gate(before_s, after_s, transition, base, workers, profile, policy, current_fits=True, service_model=None):
    from .service_cost import estimate_execution, new_state, observe_success
    validate_policy(policy)
    state = new_state(workers)
    transfer_s, verify_s, fixed_s, built = 0., 0., 0., 0
    for action in transition['actions']:
        r = action['replica']
        if action['kind'] == 'retire':
            fixed_s += policy['retire_fixed_s']
            continue
        target = dict(r, key=r['id'])
        plan = restore_plan(base, [target], workers, profile, 'resource_lp')
        if plan['strategy'] != 'resource_lp':
            raise RuntimeError('migration cost LP fell back')
        if service_model is not None:
            transfer_s += estimate_execution(plan, workers, service_model, state)['estimated_execute_s']
            observe_success(plan, workers, state)
        else:
            transfer_s += plan['estimated_transfer_s']
        # Both source and target content are hashed before publication.
        verify_s += 2*r['n']/1e9/policy['verify_GBps']
        fixed_s += policy['build_fixed_s']; built += r['n']
    cost = transfer_s+verify_s+fixed_s
    saving = max(0., before_s-after_s-policy['uncertainty_s'])
    benefit = saving*policy['expected_restores']
    reason = ('capacity_required' if not current_fits else
              'cooldown' if policy['since_last_change_s'] < policy['cooldown_s'] else
              'amortized_gain' if benefit > cost+policy['minimum_net_s'] else 'insufficient_net_gain')
    return {'allowed': reason in ('capacity_required', 'amortized_gain'), 'reason': reason,
            'build_bytes': built, 'transfer_s': transfer_s, 'verification_s': verify_s, 'fixed_s': fixed_s,
            'migration_cost_s': cost, 'saving_per_restore_s': saving, 'expected_benefit_s': benefit,
            'net_benefit_s': benefit-cost, 'break_even_restores': cost/saving if saving > 0 else None,
            'policy': copy.deepcopy(policy), 'scope': 'amortized modeled transfer benefit; not an end-to-end latency guarantee'}


def update_feedback(profile, observation, alpha=.25):
    """Return a new profile; the caller must explicitly replan before applying it."""
    if not 0 < alpha <= 1:
        raise ValueError('invalid smoothing factor')
    if observation.get('executor') != 'serial' or observation.get('verified') is not True:
        raise ValueError('feedback requires a verified serial measurement')
    if not profile.get('feedback_context_id') or observation.get('profile_id') != profile['feedback_context_id']:
        raise ValueError('feedback context differs from active profile')
    if observation.get('kind') != 'isolated_path':
        raise ValueError('aggregate restore timing cannot identify individual path rates')
    path, n, elapsed = observation['path'], observation['nbytes'], observation['execute_s']
    if type(n) is not int or n <= 0 or not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError('invalid feedback sample')
    if path not in profile.get('verified_paths', []):
        raise ValueError('feedback cannot admit an unverified path')
    if n < profile.get('feedback_min_bytes', 1 << 30):
        raise ValueError('sample too small for bandwidth feedback')
    if 'measured_at' in observation and not 0 <= time.time()-observation['measured_at'] <= profile.get('feedback_max_age_s', 3600):
        raise ValueError('feedback sample expired or from the future')
    sample_id = hashlib.sha256(json.dumps(observation, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if any(r['sample_id'] == sample_id for r in profile.get('feedback_history', [])):
        raise ValueError('duplicate feedback sample')
    measured = n/1e9/elapsed
    out = copy.deepcopy(profile)
    prior = out.setdefault('directed_path_rates', {}).get(path, measured)
    out['directed_path_rates'][path] = (1-alpha)*prior+alpha*measured
    out['feedback_revision'] = out.get('feedback_revision', 0)+1
    out.setdefault('feedback_history', []).append(dict(observation, sample_id=sample_id, previous_GBps=prior,
        measured_GBps=measured, updated_GBps=out['directed_path_rates'][path], alpha=alpha))
    return out
