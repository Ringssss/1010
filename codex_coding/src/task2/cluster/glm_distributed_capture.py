"""Capture GLM in bounded stages, with independently committed remote backing blocks."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import pickle
import time

from fluidckpt.cluster.control import request
from fluidckpt.memory.bindings import choose_block_bindings
from fluidckpt.serving.replicas import identity


def spill_pairs(count):
    # One full shard per cache worker fits its 48 GiB budget.
    if not 0 <= count <= 3:
        raise ValueError('the six cache GPUs can hold at most three spills per compute host')
    return [(host*8+local, 16+host*count+local)
            for host in range(2) for local in range(count)]


def validate_spill_routes(runner, count):
    """Exercise both directions before any expensive model snapshot exists."""
    _, existing = runner.cluster.inventory(compact=True)
    if existing:
        raise RuntimeError('route validation requires an empty, isolated experiment mesh')
    runner.result['route_validation'] = []
    for source_worker, cache_worker in spill_pairs(count):
        snap = f'route_probe_{source_worker}_{cache_worker}'
        request(runner.endpoints[source_worker], 'synth',
                {'snap': snap, 'tp': 0, 'nbytes': (32 << 20)+17, 'seed': 1024+source_worker})
        source = request(runner.endpoints[source_worker], 'adopt',
                         {'snap': snap, 'tp': 0, 'id': snap+'/source'})
        outward = runner.replica_target(source, snap+'/remote', cache_worker, 'dram')
        flow_out = runner.cluster.transfer(runner.cluster.plan_restore(
            [outward], 'resource_lp', [source['id']]))
        inward = runner.replica_target(source, snap+'/return', source_worker, 'hbm')
        flow_in = runner.cluster.transfer(runner.cluster.plan_restore(
            [inward], 'resource_lp', [outward['id']]))
        runner.result['route_validation'].append({'source_worker': source_worker,
            'cache_worker': cache_worker, 'outward': flow_out['verification'],
            'return': flow_in['verification']})
        # These are deliberately disposable synthetic bytes, not model replicas.
        for worker, rid in [(source_worker, source['id']), (cache_worker, outward['id']),
                            (source_worker, inward['id'])]:
            request(runner.endpoints[worker], 'retire', {'id': rid})
        runner.event('spill_route_verified', source_worker=source_worker, cache_worker=cache_worker)


def capture_distributed(runner, phase, count):
    """Spill selected shards before admitting the remaining capture allocations."""
    block_bytes = runner.args.capture_block_mib << 20
    directory = runner.out/'distributed_capture'
    directory.mkdir(exist_ok=True)
    handoff = runner.out/'handoff'/phase
    # The controller and model processes run under different UIDs in this experiment.
    for path in [handoff.parent, handoff]:
        runner.remote('node27', ['chmod', '777', str(path)])
    sources = []
    for rank in range(16):
        with (handoff/f'rank{rank}.pkl').open('rb') as stream:
            data = pickle.load(stream)
        index, total = {}, 0
        for entry in data['entries']:
            index[entry['name']] = (total, entry['nbytes'])
            total += entry['nbytes']
        sources.append({'identity': identity(data['meta'], index), 'tp': rank, 'n': total,
                        'meta': data['meta'], 'index': index})
    runner.sources = sources
    runner.result['sources'] = sources
    runner.result['distributed_capture'] = {'spills': [], 'captures': [],
        'scope': 'fixed conservative spill layout for full-model correctness; not an optimized placement result'}

    def topology(node):
        path = directory/f'{node}_{time.time_ns()}.json'
        runner.remote(node, ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(path)])
        return json.loads(path.read_text())['topology']

    def binding(node, demands):
        return choose_block_bindings(topology(node), demands, block_bytes, guard_bytes=4 << 30)

    def acknowledge(rank):
        marker = handoff/f'rank{rank}.captured'
        temporary = marker.with_suffix('.captured.tmp')
        temporary.write_text(json.dumps({'bytes': sources[rank]['n'], 'distributed_capture': True}))
        os.replace(temporary, marker)

    def capture(rank, numas):
        captured = request(runner.endpoints[rank], 'capture_regions', {'tag': phase, 'snap': 'GLM',
            'block_bytes': block_bytes, 'publish_marker': False, 'numa_by_rank': {str(rank): numas}},
            timeout=runner.args.timeout)[0]
        (directory/f'rank{rank}.json').write_text(json.dumps(captured)+'\n')
        return captured

    spilled = set()
    for rank, destination in spill_pairs(count):
        source_node, source_gpu = runner.members[rank]
        cache_node, cache_gpu = runner.members[destination]
        layout = binding(source_node, [{'gpu': source_gpu, 'nbytes': sources[rank]['n']}])
        captured = capture(rank, [b['numa'] for b in layout['blocks'][source_gpu]])
        runner.event('staged_rank_captured', rank=rank, capture_s=captured['capture_s'])
        target_layout = binding(cache_node, [{'gpu': cache_gpu, 'nbytes': sources[rank]['n']}])
        targets = [dict(runner.replica_target(sources[rank], f'GLM/cache/rank{rank}/block{i}',
                       destination, 'dram', row['off'], row['n']), numa=allocation['numa'])
                   for i, (row, allocation) in enumerate(zip(captured['regions'], target_layout['blocks'][cache_gpu]))]
        flow = runner.cluster.transfer(runner.cluster.plan_restore(
            targets, 'resource_lp', [row['id'] for row in captured['regions']]))
        (directory/f'spill_rank{rank}.json').write_text(json.dumps(flow)+'\n')
        for row in captured['regions']:
            runner.cluster.retire(row['id'])
        spilled.add(rank)
        acknowledge(rank)
        runner.result['distributed_capture']['spills'].append({'rank': rank, 'worker': destination,
            'nbytes': sources[rank]['n'], 'prepare_s': flow['prepare_s'],
            'execute_wall_s': flow['execute_wall_s'], 'coordinator_wall_s': flow['coordinator_wall_s'],
            'checked_bytes': flow['verification']['checked_bytes']})
        runner.event('staged_rank_spilled', rank=rank, worker=destination,
                     checked_bytes=flow['verification']['checked_bytes'])

    numas = {}
    for node_index, node in enumerate(['node27', 'node29']):
        todo = [rank for rank in range(node_index*8, node_index*8+8) if rank not in spilled]
        placement = binding(node, [{'gpu': rank % 8, 'nbytes': sources[rank]['n']} for rank in todo])
        for rank in todo:
            numas[rank] = [b['numa'] for b in placement['blocks'][rank % 8]]
    with ThreadPoolExecutor(len(numas)) as pool:
        futures = {rank: pool.submit(capture, rank, layout) for rank, layout in numas.items()}
        for rank, future in futures.items():
            captured = future.result()
            acknowledge(rank)
            runner.result['distributed_capture']['captures'].append(
                {'rank': rank, 'nbytes': sources[rank]['n'], 'capture_s': captured['capture_s']})
            runner.event('staged_rank_captured', rank=rank, capture_s=captured['capture_s'])
    _, replicas = runner.cluster.inventory(compact=True)
    identities = {source['identity'] for source in sources}
    runner.regions = [row for row in replicas if row['identity'] in identities]
    for source in sources:
        offset = 0
        for row in sorted((r for r in runner.regions if r['identity'] == source['identity']), key=lambda r: r['off']):
            if row['off'] != offset:
                raise AssertionError('distributed capture must have contiguous, single-copy coverage')
            offset += row['n']
        if offset != source['n']:
            raise AssertionError('distributed capture is incomplete')
    runner.source_ids = [row['id'] for row in runner.regions]
    runner.event('distributed_capture_complete', checked_ranks=16, nbytes=sum(s['n'] for s in sources))
