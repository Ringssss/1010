"""Move real bytes between three hosts and recall them from a mixed committed layout."""
import argparse
import json
from pathlib import Path
import time

from run_system import ROOT
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.cluster.block_migration import migrate_blocks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.session.read_text())
    args.out.mkdir(parents=True, exist_ok=False)
    cluster = Cluster(state['endpoints'], state['profile'], planner_endpoint=state['endpoints'][0])
    tag = args.out.name
    request(cluster.endpoints[20], 'synth', {'snap': tag, 'tp': 0, 'nbytes': 64 << 20, 'seed': 428})
    primary = request(cluster.endpoints[20], 'adopt', {'snap': tag, 'tp': 0, 'id': tag+'/primary'})
    def target(rid, worker, off, n, tier='dram'):
        return dict(key=rid, id=rid, mode='replica', worker=worker, tier=tier, off=off, n=n,
                    identity=primary['identity'], tp=0, meta=primary['meta'], index=primary['index'])
    sources = [target(f'{tag}/block{i}', 20, i*(16 << 20), 16 << 20) for i in range(4)]
    cluster.transfer(cluster.plan_restore(sources, 'resource_lp', [primary['id']]))
    cluster.retire(primary['id'])
    actions = [{'source_id': row['id'], 'target': target(f'{tag}/moved{i}', 0, row['off'], row['n'])}
               for i, row in enumerate(sources)]
    migration = migrate_blocks(cluster, actions, args.out/'journal.json')
    commits = [next(migration), next(migration)]
    _, replicas = cluster.inventory(compact=True)
    mixed = [row for row in replicas if row['identity'] == primary['identity']]
    if {row['worker'] for row in mixed} != {0, 20}:
        raise AssertionError('fixture is not split between source and new location')
    started = time.monotonic()
    recall = cluster.transfer(cluster.plan_restore([target(tag+'/recall', 8, 0, 64 << 20, 'hbm')],
                                                    'resource_lp', [row['id'] for row in mixed]))
    elapsed = time.monotonic()-started
    cluster.retire(tag+'/recall')
    commits.extend(list(migration))
    _, replicas = cluster.inventory(compact=True)
    retained = [row for row in replicas if row['identity'] == primary['identity']]
    report = {'status': 'passed', 'scope': '64MiB synthetic data, real three-host transfers and full SHA256',
              'mixed_sources': [{'worker': row['worker'], 'off': row['off'], 'n': row['n']} for row in mixed],
              'recall_s_including_verification': elapsed, 'verification': recall['verification'],
              'commits': commits, 'final_workers': sorted({row['worker'] for row in retained})}
    (args.out/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'checked_bytes': recall['verification']['checked_bytes'],
                      'mixed_workers': [0, 20], 'destination_worker': 8, 'recall_s': elapsed}), flush=True)
    # Only this diagnostic fixture is discarded; no model snapshot is retired here.
    for row in retained:
        request(cluster.endpoints[row['worker']], 'retire', {'id': row['id']})


if __name__ == '__main__':
    main()
