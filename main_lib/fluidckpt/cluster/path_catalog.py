"""Directed, expiring path evidence; probes must use disposable isolated workers."""
import copy
import hashlib
import json
import math
import time


def topology_id(workers):
    physical = [{k: w[k] for k in ('worker', 'node', 'gpu', 'numa')} for w in workers]
    return hashlib.sha256(json.dumps(physical, sort_keys=True).encode()).hexdigest()


def path_key(src, tier, numa, dst, dst_tier='hbm'):
    return f'{src}:{tier}:{numa}->{dst}'+(':'+dst_tier if dst_tier != 'hbm' else '')


def discovery_tasks(workers, tiers=('dram', 'hbm'), pairs=None, nbytes=32 << 20, target_tiers=('hbm',)):
    """Enumerate directed tasks without treating unknown paths as usable."""
    if type(nbytes) is not int or nbytes <= 0:
        raise ValueError('invalid probe size')
    ids = {w['worker'] for w in workers}
    pairs = list(pairs) if pairs is not None else [(s, d) for s in sorted(ids) for d in sorted(ids)]
    if any(s not in ids or d not in ids for s, d in pairs) or any(t not in ('dram', 'hbm') for t in (*tiers, *target_tiers)):
        raise ValueError('invalid probe endpoint or tier')
    wm = {w['worker']: w for w in workers}
    return [{'src': s, 'dst': d, 'tier': tier, 'dst_tier': dst_tier, 'numa': wm[s]['numa'], 'nbytes': nbytes,
             'path': path_key(s, tier, wm[s]['numa'], d, dst_tier)} for s, d in sorted(set(map(tuple, pairs)))
            for tier in tiers for dst_tier in target_tiers]


def new_catalog(workers):
    return {'schema_version': 1, 'topology_id': topology_id(workers), 'records': {}}


def record_probe(catalog, task, result, now=None):
    """A failed refresh replaces success so that a broken path cannot stay admitted."""
    now = time.time() if now is None else now
    row = dict(task, measured_at=now, status='failed', error=result.get('error'), samples=1)
    elapsed = result.get('execute_s')
    if result.get('verified_bytes') == task['nbytes'] and result.get('passed') is True:
        if elapsed is None or not math.isfinite(elapsed) or elapsed <= 0:
            raise ValueError('invalid probe duration')
        row.update(status='verified', execute_s=elapsed, GBps=task['nbytes']/1e9/elapsed,
                   verified_bytes=result['verified_bytes'], executor=result.get('executor', 'serial'))
    catalog['records'][task['path']] = row
    return row


def admitted_profile(catalog, workers, profile, max_age_s=3600, now=None, min_rate_bytes=1 << 30):
    """Availability and calibration have separate evidence-size requirements."""
    now = time.time() if now is None else now
    if not math.isfinite(max_age_s) or max_age_s <= 0:
        raise ValueError('invalid evidence lifetime')
    if catalog.get('schema_version') != 1 or catalog['topology_id'] != topology_id(workers):
        raise ValueError('path evidence topology changed')
    out = copy.deepcopy(profile)
    out.update(verified_paths=[], directed_path_rates={}, network_pairs=[], path_evidence_expires_at=now+max_age_s)
    rejected, admitted = {}, []
    wm = {w['worker']: w for w in workers}
    for key, row in catalog['records'].items():
        age = now-row['measured_at']
        reason = ('failed' if row['status'] != 'verified' else 'expired' if age < 0 or age > max_age_s
                  else 'executor' if row['executor'] != 'serial' else None)
        if reason:
            rejected[key] = reason
            continue
        if key != path_key(row['src'], row['tier'], row['numa'], row['dst'], row.get('dst_tier', 'hbm')):
            raise ValueError('path evidence key mismatch')
        out['verified_paths'].append(key)
        out['path_evidence_expires_at'] = min(out['path_evidence_expires_at'], row['measured_at']+max_age_s)
        if wm[row['src']]['node'] != wm[row['dst']]['node']:
            out['network_pairs'].append([row['src'], row['dst']])
        if row['nbytes'] >= min_rate_bytes:
            out['directed_path_rates'][key] = row['GBps']
        admitted.append(key)
    out['network_pairs'] = sorted(set(map(tuple, out['network_pairs'])))
    out['network_pairs'] = list(map(list, out['network_pairs']))
    out['path_catalog_id'] = hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest()
    out['path_topology_id'] = catalog['topology_id']
    out['feedback_context_id'] = out['path_catalog_id']
    return out, {'admitted': admitted, 'rejected': rejected, 'rate_paths': sorted(out['directed_path_rates']),
                 'scope': 'isolated one-sample evidence; shared-link concurrent capacity remains separately modeled'}


def refresh_tasks(catalog, tasks, now=None, max_age_s=3600):
    now = time.time() if now is None else now
    return [t for t in tasks if t['path'] not in catalog['records'] or
            catalog['records'][t['path']]['status'] != 'verified' or
            not 0 <= now-catalog['records'][t['path']]['measured_at'] <= max_age_s]


def check_profile_evidence(workers, profile):
    if 'path_topology_id' in profile and profile['path_topology_id'] != topology_id(workers):
        raise ValueError('path profile topology changed; refresh discovery')
    if 'path_evidence_expires_at' in profile and time.time() >= profile['path_evidence_expires_at']:
        raise ValueError('path profile expired; refresh discovery')
