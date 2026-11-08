"""Continue retained GLM exporters, spilling one captured rank before filling local DRAM."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import pickle
import time

from run_glm_roundtrip import GlmRoundtripRun, NODES, ROOT
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.memory.bindings import choose_block_bindings
from fluidckpt.serving.replicas import identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.session.read_text())
    runner = GlmRoundtripRun.__new__(GlmRoundtripRun)
    runner.args = argparse.Namespace(**state['args'])
    runner.out = args.session.parent
    NODES.clear(); NODES.update(state['nodes'])
    for key in ['env', 'endpoints', 'members', 'owned_containers']:
        setattr(runner, key, state[key])
    runner.processes = [(node, Path(path)) for node, path in state['processes']]
    runner.base = runner.args.port_base
    runner.server_port = 32253
    runner.result = json.loads((runner.out/'summary.json').read_text())
    runner.result.setdefault('prior_failures', []).append(runner.result.get('error'))
    runner.result.update(status='running', error=None)
    runner.cluster = Cluster(runner.endpoints, state['profile'], planner_endpoint=runner.endpoints[0])
    handoff = runner.out/'handoff/capture'
    recovery = runner.out/'capture_recovery'
    recovery.mkdir(exist_ok=True)
    runner.sources = []
    for rank in range(16):
        with (handoff/f'rank{rank}.pkl').open('rb') as stream:
            data = pickle.load(stream)
        index, total = {}, 0
        for entry in data['entries']:
            index[entry['name']] = (total, entry['nbytes'])
            total += entry['nbytes']
        runner.sources.append({'identity': identity(data['meta'], index), 'tp': rank,
                               'n': total, 'meta': data['meta'], 'index': index})
    runner.result['sources'] = runner.sources
    def topology(node):
        path = recovery/f'{node}_{time.time_ns()}.json'
        runner.remote(node, ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(path)])
        return json.loads(path.read_text())['topology']
    def capture(rank, numas):
        result = request(runner.endpoints[rank], 'capture_regions', {'tag': 'capture', 'snap': 'GLM',
            'block_bytes': runner.args.capture_block_mib << 20, 'publish_marker': False,
            'numa_by_rank': {str(rank): numas}}, timeout=1200)
        (recovery/f'rank{rank}.json').write_text(json.dumps(result)+'\n')
        return result
    def acknowledge(rank):
        marker = handoff/f'rank{rank}.captured'
        temporary = marker.with_suffix('.captured.tmp')
        temporary.write_text(json.dumps({'bytes': runner.sources[rank]['n'], 'distributed_capture': True}))
        temporary.replace(marker)
    try:
        runner.event('capture_recovery_begin', source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        if not (recovery/'rank0.json').exists():
            bindings = choose_block_bindings(topology('node27'), [{'gpu': 0, 'nbytes': runner.sources[0]['n']}])
            capture(0, [block['numa'] for block in bindings['blocks'][0]])
            runner.event('rank0_captured_for_spill')
        rank0 = json.loads((recovery/'rank0.json').read_text())[0]
        targets = [runner.replica_target(runner.sources[0], f'GLM/node28/rank0/block{i}', 16, 'dram',
                    region['off'], region['n']) for i, region in enumerate(rank0['regions'])]
        _, replicas = runner.cluster.inventory(compact=True)
        valid = {row['id'] for row in replicas}
        missing = [target for target in targets if target['id'] not in valid]
        if missing:
            flow = runner.cluster.transfer(runner.cluster.plan_restore(missing, 'resource_lp',
                [region['id'] for region in rank0['regions']]))
            runner.result['initial_rank0_spill'] = {key: value for key, value in flow.items()
                                                  if key not in ['plan', 'rpc']}
            runner.event('rank0_spill_verified', nbytes=runner.sources[0]['n'])
        _, replicas = runner.cluster.inventory(compact=True)
        valid = {row['id'] for row in replicas}
        for region in rank0['regions']:
            if region['id'] in valid:
                runner.cluster.retire(region['id'])
        runner.event('rank0_local_dram_released')
        # Each finished rank can continue while the rest of the tensor-parallel group is captured.
        for rank in range(16):
            if (recovery/f'rank{rank}.json').exists():
                acknowledge(rank)
        bindings_by_rank = {}
        for node, ranks in [('node27', list(range(1, 8))), ('node29', list(range(8, 16)))]:
            todo = [rank for rank in ranks if not (recovery/f'rank{rank}.json').exists()]
            demands = [{'gpu': rank % 8, 'nbytes': runner.sources[rank]['n']} for rank in todo]
            if not demands:
                continue
            placement = choose_block_bindings(topology(node), demands, guard_bytes=4 << 30)
            for rank in todo:
                bindings_by_rank[rank] = [block['numa'] for block in placement['blocks'][rank % 8]]
        with ThreadPoolExecutor(15) as pool:
            futures = {rank: pool.submit(capture, rank, numas) for rank, numas in bindings_by_rank.items()}
            for rank, future in futures.items():
                future.result()
                acknowledge(rank)
                runner.event('rank_capture_complete', rank=rank)
        _, replicas = runner.cluster.inventory(compact=True)
        identities = {source['identity'] for source in runner.sources}
        runner.regions = [row for row in replicas if row['identity'] in identities]
        runner.source_ids = [row['id'] for row in runner.regions]
        for source in runner.sources:
            cursor = 0
            for region in sorted((row for row in runner.regions if row['identity'] == source['identity']), key=lambda row: row['off']):
                if region['off'] != cursor:
                    raise AssertionError('captured coverage has a gap or duplicate')
                cursor += region['n']
            if cursor != source['n']:
                raise AssertionError('captured coverage is incomplete')
        for rank, source in enumerate(runner.sources):
            marker = handoff/f'rank{rank}.captured'
            temporary = marker.with_suffix('.captured.tmp')
            temporary.write_text(json.dumps({'bytes': source['n'], 'distributed_capture': True}))
            temporary.replace(marker)
        runner.shell = {'endpoint': 'http://12.11.3.27:32253'}
        runner.wait_http(runner.shell['endpoint'], 'health', timeout=600)
        runner.reference = runner.generate_stream(runner.shell)
        native = json.loads(Path(runner.args.native_result).read_text())
        if runner.reference['text'] != native['generations'][0]['answers'][0]['text']:
            raise AssertionError('captured GLM differs from native generation')
        runner.result['reference'] = runner.reference
        runner.checkpoint()
        runner.memory('distributed_capture_before_release')
        runner.roundtrip()
        runner.result['status'] = 'passed_after_capture_recovery'
        runner.checkpoint()
        runner.event('retained_for_next_stage')
    except BaseException as exc:
        runner.result.update(status='failed', error=repr(exc))
        runner.checkpoint()
        runner.event('recovery_failed_resources_retained', error=repr(exc))
        raise


if __name__ == '__main__':
    main()
