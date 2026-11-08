"""Commit independently allocated blocks and yield to recalls between transfers."""
import hashlib
import json
from pathlib import Path
import time


def schedule_block_moves(replicas, actions, capacity_bytes, resource_keys=None):
    """Order exact allocation moves under copy-before-release peak limits.

    This is a feasibility scheduler for an already chosen destination layout, not
    an optimal placement solver. The caller can charge each descriptor to several
    shared resources (worker, NUMA and host) using resource_keys. Without that
    callback, limits describe individual (worker, tier) locations only.
    """
    keys = resource_keys or (lambda row: [(row['worker'], row['tier'])])
    active = {row['id']: row for row in replicas}
    pending = list(actions)
    used = {key: 0 for key in capacity_bytes}
    def size(row):
        return row.get('allocated_bytes', (row['n']+4095)//4096*4096)
    def charge(row, sign):
        for key in keys(row):
            used[key] += sign*size(row)
    for row in active.values():
        charge(row, 1)
    if any(used[key] > cap for key, cap in capacity_bytes.items()):
        raise ValueError('initial replicas exceed the transition budget')
    ordered = []
    peaks = dict(used)
    while pending:
        chosen = None
        for i, action in enumerate(pending):
            source = active.get(action['source_id'])
            target = action['target']
            if source is None:
                continue
            if target['id'] == source['id'] or target['id'] in active:
                raise ValueError('move destination must have a fresh identity')
            if any(source[k] != target[k] for k in ('identity', 'tp', 'off', 'n')):
                raise ValueError('moves must match independently releasable source allocations')
            if all(used[key]+size(target) <= capacity_bytes[key] for key in keys(target)):
                chosen = i
                break
        if chosen is None:
            raise ValueError('no move fits copy-before-release capacity; staging space or a different layout is needed')
        action = pending.pop(chosen)
        source, target = active[action['source_id']], action['target']
        charge(target, 1)
        for key in used:
            peaks[key] = max(peaks[key], used[key])
        active[target['id']] = target
        charge(source, -1)
        del active[source['id']]
        ordered.append(action)
    return {'actions': ordered, 'peak_bytes': peaks, 'final_bytes': dict(used),
            'scope': 'greedy feasible ordering of fixed exact-block moves; no minimum-latency claim'}


def migrate_blocks(cluster, actions, journal_path):
    """Yield after each verified move and source retirement.

    The caller can serve an urgent restore at every yield using the current exact
    replica directory. An in-flight collective is completed before yielding.
    Restarting the coordinator reuses VALID destinations after checking identity
    and interval ownership. This does not recover lost worker memory.
    """
    actions = list(actions)
    compact = [{'source_id': action['source_id'], 'target': {
        key: action['target'][key] for key in ['id', 'identity', 'tp', 'off', 'n', 'worker', 'tier']}}
        for action in actions]
    digest = hashlib.sha256(json.dumps(compact, sort_keys=True).encode()).hexdigest()
    path = Path(journal_path)
    state = json.loads(path.read_text()) if path.exists() else {
        'plan_sha256': digest, 'actions': compact, 'committed': []}
    if state['plan_sha256'] != digest:
        raise ValueError('journal belongs to a different block plan')
    for ordinal, action in enumerate(actions):
        target = action['target']
        if target['mode'] != 'replica' or action['source_id'] == target['id']:
            raise ValueError('block migration requires a distinct persistent destination')
        _, replicas = cluster.inventory(compact=True)
        available = {row['id']: row for row in replicas}
        destination = available.get(target['id'])
        if destination:
            if any(destination[k] != target[k] for k in ['identity', 'tp', 'off', 'n', 'worker', 'tier']):
                raise ValueError('published destination differs from the intended block')
        source = available.get(action['source_id'])
        if source and any(source[k] != target[k] for k in ['identity', 'tp', 'off', 'n']):
            raise ValueError('source allocation does not exactly match the migrating block')
        started = time.monotonic()
        moved = 0
        if destination is None:
            if source is None:
                raise RuntimeError('neither the source nor destination block is available')
            plan = cluster.plan_restore([target], 'resource_lp', [action['source_id']])
            cluster.transfer(plan, verify_before_ready=True)
            moved = target['n']
        if source is not None:
            cluster.retire(action['source_id'])
        row = {'ordinal': ordinal, 'source_id': action['source_id'], 'target_id': target['id'],
               'logical_bytes': target['n'], 'transferred_bytes_this_attempt': moved,
               'elapsed_s': time.monotonic()-started, 'committed_at': time.time()}
        if len(state['committed']) <= ordinal:
            state['committed'].append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(json.dumps(state, indent=2)+'\n')
        temporary.replace(path)
        yield row
