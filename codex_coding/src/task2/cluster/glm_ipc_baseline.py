"""Measure a real resident HIP IPC hit using the same GLM warm engine and request."""
from concurrent.futures import ThreadPoolExecutor
import json
import time

from fluidckpt.cluster.control import request


def resident_ipc(runner, repeats=3):
    endpoint = runner.shell['endpoint']
    request(endpoint, 'release_memory_occupation', {'tags': ['weights']}, timeout=300)
    before, _ = runner.cluster.inventory(compact=True)
    targets = [runner.replica_target(source, f'GLM/ipc/{rank}', rank)
               for rank, source in enumerate(runner.sources)]
    flow = runner.cluster.transfer(runner.cluster.plan_restore(targets, 'resource_lp', runner.source_ids))
    runner.result['ipc_baseline'] = {'scope': 'adapted HIP IPC HBM-resident hit; '
        'owner setup is measured separately; not a capacity-pressure policy or upstream daemon implementation',
        'owner_setup_s': flow['coordinator_wall_s'], 'owner_bytes': sum(s['n'] for s in runner.sources),
        'owner_verification': flow['verification'], 'hits': []}
    directory = str(runner.out/'ipc')
    runner.memory('ipc_resident_owner_engine_parked')
    for i in range(repeats):
        phase = f'ipc_hit{i}'
        (runner.out/'glm.command.json').write_text(json.dumps({'tag': phase, 'mode': 'ipc',
            'ipc_directory': directory, 'metrics': str(runner.out/'metrics'/phase)}))
        started = time.monotonic()
        # Each import needs a fresh producer reference, including repeat hits.
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(lambda pair: request(runner.endpoints[pair[0]], 'export_ipc',
                 {'id': pair[1]['id'], 'directory': directory}), enumerate(targets)))
        export_s = time.monotonic()-started
        request(endpoint, 'resume_memory_occupation', {'tags': ['weights']}, timeout=300)
        reply = runner.generate_stream(runner.shell)
        if reply['text'] != runner.reference['text']:
            raise AssertionError('HIP IPC baseline must match the native and captured reference')
        hit = {'request_to_first_token_s': reply['first_token_time']-started,
               'export_s': export_s, 'reply': reply, 'correct': True}
        runner.result['ipc_baseline']['hits'].append(hit)
        runner.event('ipc_hit_verified', repetition=i, request_to_first_token_s=hit['request_to_first_token_s'])
        request(endpoint, 'release_memory_occupation', {'tags': ['weights']}, timeout=300)
    for target in targets:
        runner.cluster.retire(target['id'])
    runner.memory('ipc_owner_evicted')
    after, _ = runner.cluster.inventory(compact=True)
    released = [{'worker': i, 'allocated_before': before[i]['hbm_allocated_bytes'],
                 'allocated_after': after[i]['hbm_allocated_bytes'],
                 'physical_free_before': before[i]['headroom_bytes']['hbm'],
                 'physical_free_after': after[i]['headroom_bytes']['hbm']}
                for i in range(len(targets))]
    runner.result['ipc_baseline']['owner_retirement'] = released
    if any(r['allocated_after'] > r['allocated_before']+(32 << 20) for r in released):
        raise RuntimeError('IPC owner retirement retained physical weight allocations')
    runner.result['ipc_baseline']['final_engine_state'] = 'parked; CPU snapshots remain valid'
