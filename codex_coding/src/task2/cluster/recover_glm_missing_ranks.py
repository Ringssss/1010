"""Recreate an engine from surviving rank snapshots and cold-load only missing ranks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pickle
import time

from run_glm_roundtrip import GlmRoundtripRun, NODES
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.memory.bindings import choose_block_bindings
from fluidckpt.serving.replicas import identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--phase', required=True)
    args = parser.parse_args()
    state = json.loads(args.session.read_text())
    run = GlmRoundtripRun.__new__(GlmRoundtripRun)
    run.args = argparse.Namespace(**state['args'])
    run.out = args.session.parent
    run.base = run.args.port_base
    run.server_port = 32253
    NODES.clear(); NODES.update(state['nodes'])
    for key in ['env', 'endpoints', 'members', 'owned_containers']:
        setattr(run, key, state[key])
    run.processes = [(node, Path(path)) for node, path in state['processes']]
    run.result = json.loads((run.out/'summary.json').read_text())
    run.result.setdefault('prior_failures', []).append(run.result.get('error'))
    run.result.update(status='running', error=None)
    run.cluster = Cluster(run.endpoints, state['profile'], planner_endpoint=run.endpoints[0])
    capture = run.out/'handoff/capture'
    run.sources = []
    for rank in range(16):
        with (capture/f'rank{rank}.pkl').open('rb') as stream:
            data = pickle.load(stream)
        index, size = {}, 0
        for entry in data['entries']:
            index[entry['name']] = (size, entry['nbytes']); size += entry['nbytes']
        run.sources.append({'identity': identity(data['meta'], index), 'tp': rank, 'n': size,
                            'meta': data['meta'], 'index': index})
    _, available = run.cluster.inventory(compact=True)
    surviving = []
    for source in run.sources:
        rows = sorted((row for row in available if row['identity'] == source['identity']), key=lambda row: row['off'])
        cursor = 0
        for row in rows:
            if row['off'] > cursor:
                break
            cursor = max(cursor, row['off']+row['n'])
        if cursor == source['n']:
            surviving.append(source['tp'])
    missing = [rank for rank in range(16) if rank not in surviving]
    phase_dir = run.out/'handoff'/args.phase
    phase_dir.mkdir(parents=True, exist_ok=False)
    phase_dir.chmod(0o777)
    run.result['hybrid_recovery'] = {'phase': args.phase, 'surviving_ranks': surviving, 'cold_ranks': missing}
    run.event('hybrid_recovery_begin', surviving_ranks=surviving, cold_ranks=missing)
    try:
        old_specs = {node: json.loads((run.out/f'model_{node}.process.json').read_text())
                     for node in ['node27', 'node29']}
        for handle in list(run.processes):
            if handle[1].name.startswith('model_'):
                run.stop(handle)
        for node in ['node29', 'node27']:
            spec = old_specs[node]
            env = dict(spec['env'], FLUIDCKPT_MODE='restore', FLUIDCKPT_TAG=args.phase,
                FLUIDCKPT_COLD_RANKS=','.join(map(str, missing)), FLUIDCKPT_TIMEOUT='7200',
                FLUIDCKPT_METRICS=str(run.out/'metrics'/args.phase))
            run.start(node, f'model_{node}_{args.phase}', spec['command']+['--load-format', 'dummy'], env)
        deadline = time.monotonic()+3600
        while not all((phase_dir/f'rank{rank}.pkl').exists() for rank in surviving):
            if time.monotonic() > deadline:
                raise TimeoutError('surviving-rank skeletons were not exported')
            for node in ['node27', 'node29']:
                log = run.out/f'model_{node}_{args.phase}.log'
                if log.exists() and any(token in log.read_text(errors='replace') for token in [
                        'Scheduler hit an exception', 'KERNEL VMFault', 'Fatal Python error:']):
                    raise RuntimeError(f'hybrid engine failed; inspect {log}')
            time.sleep(1)
        targets = [dict(run.replica_target(run.sources[rank], f'{args.phase}/warm/{rank}', rank),
                        mode='live', tag=args.phase, metrics_directory=str(run.out/'metrics'/args.phase))
                   for rank in surviving]
        source_ids = [row['id'] for row in available if row['tp'] in surviving
                      and row['identity'] == run.sources[row['tp']]['identity']]
        restored = run.cluster.transfer(run.cluster.plan_restore(targets, 'resource_lp', source_ids))
        run.result['hybrid_recovery']['restore'] = {key: value for key, value in restored.items() if key not in ['plan', 'rpc']}
        run.event('surviving_ranks_restored', ranks=surviving)
        while not all((phase_dir/f'rank{rank}.pkl').exists() for rank in missing):
            if time.monotonic() > deadline:
                raise TimeoutError('cold fallback ranks did not finish loading')
            time.sleep(1)
        plans = {}
        for node, ranks in [('node27', [r for r in missing if r < 8]),
                            ('node29', [r for r in missing if r >= 8])]:
            if not ranks:
                continue
            probe = run.out/f'{node}_{args.phase}_topology.json'
            run.remote(node, ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(probe)])
            topo = json.loads(probe.read_text())['topology']
            plan = choose_block_bindings(topo, [{'gpu': rank % 8, 'nbytes': run.sources[rank]['n']}
                                                for rank in ranks], guard_bytes=4 << 30)
            for rank in ranks:
                plans[str(rank)] = [block['numa'] for block in plan['blocks'][rank % 8]]
        def capture_missing(rank):
            result = request(run.endpoints[rank], 'capture_regions', {'tag': args.phase, 'snap': 'GLM',
                'block_bytes': 256 << 20, 'numa_by_rank': plans}, timeout=1200)
            (run.out/'capture_recovery'/f'rank{rank}.json').write_text(json.dumps(result)+'\n')
            return result[0]['manifest']
        with ThreadPoolExecutor(max(1, len(missing))) as pool:
            captured = list(pool.map(capture_missing, missing))
        for source in captured:
            run.sources[source['tp']] = source
        _, rows = run.cluster.inventory(compact=True)
        identities = {source['identity'] for source in run.sources}
        run.regions = [row for row in rows if row['identity'] in identities]
        run.source_ids = [row['id'] for row in run.regions]
        run.shell = {'endpoint': 'http://12.11.3.27:32253'}
        run.wait_http(run.shell['endpoint'], 'health', timeout=600)
        run.reference = run.generate_stream(run.shell)
        native = json.loads(Path(run.args.native_result).read_text())
        if run.reference['text'] != native['generations'][0]['answers'][0]['text']:
            raise AssertionError('hybrid-recovered model differs from native generation')
        run.result['reference'] = run.reference
        run.checkpoint()
        run.memory('hybrid_capture_ready')
        run.roundtrip()
        run.result['status'] = 'passed_after_hybrid_recovery'
        run.checkpoint()
        run.event('retained_for_next_stage')
    except BaseException as exc:
        run.result.update(status='failed', error=repr(exc))
        run.checkpoint()
        run.event('hybrid_recovery_failed_resources_retained', error=repr(exc))
        raise


if __name__ == '__main__':
    main()
