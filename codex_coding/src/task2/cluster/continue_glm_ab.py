"""Use a verified GLM session for real Qwen switching and recall during block migration."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import pickle
import resource
import time

from run_glm_roundtrip import GlmRoundtripRun, NODES, ROOT
from run_lifecycle import LifecycleRun
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.cluster.block_migration import migrate_blocks
from fluidckpt.memory.bindings import choose_block_bindings
from fluidckpt.serving.handoff import wait_for_exports


class GlmAB(GlmRoundtripRun):
    start_ssd = LifecycleRun.start_ssd
    def __init__(self, args):
        state = json.loads(args.session.read_text())
        self.args = argparse.Namespace(**state['args'])
        self.args.model_b = args.model_b
        self.args.ssd_node = 'node28'
        self.args.ssd_directory = str(args.ssd_directory) if args.ssd_directory else None
        self.ssd_process = None
        self.out = args.session.parent
        self.result = json.loads((self.out/'summary.json').read_text())
        self.attempt = args.attempt
        self.resume_export = args.resume_export
        self.pre_spill_rank = args.pre_spill_rank
        retry = self.result['status'] == 'lifecycle_failed' and args.attempt != 'initial'
        if retry:
            previous = self.result['lifecycle']
            if previous['moves'] or previous['recalls'] or previous.get('model_b_reference'):
                raise ValueError('this retry entry resumes failures before B capture only')
            archive = self.out/f'lifecycle_before_{args.attempt}.json'
            if archive.exists(): raise ValueError('attempt already exists')
            archive.write_text(json.dumps(self.result, indent=2)+'\n')
        elif self.result['status'] != 'passed':
            raise ValueError('a completed full-model roundtrip is required')
        if 'diagnostic_model' in self.result:
            raise ValueError('the lifecycle must use the full GLM checkpoint')
        if 'lifecycle' in self.result and not retry:
            raise ValueError('this session already has a lifecycle attempt')
        if not retry:
            (self.out/'roundtrip_result.json').write_text(json.dumps(self.result, indent=2)+'\n')
        for key in ['env', 'endpoints', 'members', 'owned_containers', 'owner_sockets',
                    'sources', 'source_ids', 'regions', 'reference']:
            setattr(self, key, state[key])
        self.processes = [(node, Path(path)) for node, path in state['processes']]
        self.base = self.args.port_base
        NODES.clear(); NODES.update(state['nodes'])
        if retry and not self.resume_export:
            failed = json.loads((self.out/'lifecycle_session.json').read_text())
            for node, path in failed['processes']:
                if Path(path).name.startswith('model_B_'):
                    self.remote(node, ['python3', 'codex_coding/src/task2/cluster/process.py',
                                      'stop', '--spec', path], timeout=60)
        elif retry:
            failed = json.loads((self.out/'lifecycle_session.json').read_text())
            self.processes += [(node, Path(path)) for node, path in failed['processes']
                               if Path(path).name.startswith('model_B_') and (node, Path(path)) not in self.processes]
        self.cluster = Cluster(self.endpoints, state['profile'], planner_endpoint=self.endpoints[0])
        self.cluster.profile['rates']['local_ssd'] = .5
        self.cluster.profile.update(ssd_GBps=.5, store_network_GBps=1.)
        self.models = {'A': {'sources': self.sources, 'source_ids': self.source_ids, 'reference': self.reference, 'tp': 16}}
        parked = self.result.get('ipc_baseline', {}).get('final_engine_state', '').startswith('parked')
        self.slots = {'A': dict(state['shell'], model='A', label='GLM_local', workers=list(range(16)),
                               command=str(self.out/'glm.command.json'), parked=parked)}
        self.result['lifecycle'] = {'status': 'running', 'attempt': self.attempt,
            'switches': [], 'recalls': [], 'moves': [],
            'scope': 'full GLM and real Qwen share node27 GPU0/1; DRAM and SSD bytes migrate to node28; '
                     'local GLM recalls interrupt migration. No different-compute-placement claim.'}
        self.result['status'] = 'running_lifecycle'
        self.sequence = 0

    def checkpoint_lifecycle(self):
        state = {'models': self.models, 'slots': self.slots,
                 'processes': [(node, str(path)) for node, path in self.processes],
                 'sequence': self.sequence, 'profile': self.cluster.profile,
                 'ssd_process': str(self.ssd_process) if self.ssd_process else None,
                 'ssd_endpoint': getattr(self, 'ssd_endpoint', None)}
        (self.out/'lifecycle_session.json').write_text(json.dumps(state, indent=2)+'\n')
        self.save()

    def park(self, name):
        slot = self.slots[name]
        if slot['parked']: return 0.
        started = time.monotonic()
        request(slot['endpoint'], 'release_memory_occupation', {'tags': ['weights']}, timeout=300)
        elapsed = time.monotonic()-started
        slot['parked'] = True
        self.event('lifecycle_parked', model=name, release_s=elapsed)
        self.memory('lifecycle_'+name+'_parked')
        return elapsed

    def fill(self, name, phase):
        model, slot = self.models[name], self.slots[name]
        targets = [dict(self.replica_target(source, f'{phase}/rank{rank}', slot['workers'][rank]),
                        mode='live', tag=phase, metrics_directory=str(self.out/'metrics'/phase))
                   for rank, source in enumerate(model['sources'])]
        return self.cluster.transfer(self.cluster.plan_restore(targets, 'resource_lp', model['source_ids']),
                                     verify_before_ready=False, retain=True)

    def resume(self, name, reason, verify=False):
        self.sequence += 1
        phase = f'life_{self.attempt}_{self.sequence:03d}_{name}_{reason}'
        slot, model = self.slots[name], self.models[name]
        Path(slot['command']).write_text(json.dumps({'tag': phase, 'mode': 'restore',
            'metrics': str(self.out/'metrics'/phase)}))
        started = time.monotonic()
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(request, slot['endpoint'], 'resume_memory_occupation', {'tags': ['weights']}, 1200)
            try:
                wait_for_exports(self.out/'handoff'/phase, ranks=model['tp'], timeout=1200)
                flow = self.fill(name, phase)
                future.result()
            except BaseException:
                for handle in list(self.processes):
                    if handle[1].name.startswith('model_'):
                        self.stop(handle)
                raise
        reply = self.generate_stream(slot)
        row = {'model': name, 'reason': reason, 'phase': phase,
               'request_to_first_token_s': reply['first_token_time']-started,
               'correct': reply['text'] == model['reference']['text'], 'reply': reply,
               'flow': {k: flow[k] for k in ['tx', 'prepare_s', 'execute_wall_s', 'verify_and_publish_s']}}
        if verify: row['verification'] = self.cluster.verify(flow['tx'])
        self.cluster.finish(flow['tx'])
        slot['parked'] = False
        self.result['lifecycle']['recalls'].append(row)
        self.event('lifecycle_resumed', model=name, reason=reason,
                   request_to_first_token_s=row['request_to_first_token_s'], correct=row['correct'])
        if not row['correct']: raise AssertionError('lifecycle generation changed')
        self.memory(phase)
        return row

    def switch(self, previous, following, reason, verify=False):
        started = time.monotonic()
        release_s = self.park(previous)
        row = self.resume(following, reason, verify=verify)
        transition = {'from': previous, 'to': following, 'reason': reason, 'release_s': release_s,
                      'switch_to_first_token_s': row['reply']['first_token_time']-started}
        self.result['lifecycle']['switches'].append(transition)
        self.checkpoint_lifecycle()
        return transition

    def boot_b(self):
        model_path = Path(self.args.model_b)
        phase = self.resume_export or ('life_B_capture_'+self.attempt)
        identity = hashlib.sha256((model_path/'config.json').read_bytes()+str(model_path).encode()).hexdigest()
        env = {'FLUIDCKPT_MODE': 'capture', 'FLUIDCKPT_TAG': phase, 'FLUIDCKPT_MODEL_ID': identity,
            'FLUIDCKPT_HANDOFF': str(self.out/'handoff'), 'FLUIDCKPT_CAPTURE_WAIT': '1',
            'FLUIDCKPT_TIMEOUT': '1800', 'FLUIDCKPT_TP_SIZE': '2',
            'FLUIDCKPT_METRICS': str(self.out/'metrics'/phase),
            'FLUIDCKPT_MANIFEST': str(self.out/'handoff'/phase/'derived.json'),
            'FLUIDCKPT_LIFECYCLE': str(self.out/'B.command.json'), 'HIP_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7',
            'SGLANG_KV_LAYOUT_HCU_FA': 'false', 'AMD_SERIALIZE_KERNEL': '3'}
        command = ['python3', 'codex_coding/src/serving/sglang_fluid_launch.py', '--model-path', str(model_path),
            '--host', '0.0.0.0', '--port', '32540', '--nccl-port', '32640', '--tp-size', '2',
            '--mem-fraction-static', '.70', '--context-length', '4096', '--max-running-requests', '4',
            '--disable-cuda-graph', '--attention-backend', 'triton', '--page-size', '1',
            '--moe-runner-backend', 'triton', '--max-total-tokens', '8192', '--disable-custom-all-reduce',
            '--mamba-scheduler-strategy', 'extra_buffer', '--max-mamba-cache-size', '32', '--random-seed', '42']
        name = 'model_B_node27_'+self.attempt
        if not self.resume_export:
            self.start('node27', name, command, env)
        deadline = time.monotonic()+1800
        while not all((self.out/'handoff'/phase/f'rank{r}.pkl').exists() for r in range(2)):
            log = self.out/(name+'.log')
            tail = log.read_text(errors='replace')[-16384:] if log.exists() else ''
            if 'torch.OutOfMemoryError' in tail or 'Received sigquit from a child process' in tail:
                raise RuntimeError(f'B startup failed; see {log}')
            if time.monotonic() >= deadline: raise TimeoutError('B capture export deadline')
            time.sleep(1)
        topology_path = self.out/'life_B_capture_topology.json'
        self.remote('node27', ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(topology_path)])
        demands = []
        for rank in range(2):
            with (self.out/'handoff'/phase/f'rank{rank}.pkl').open('rb') as stream:
                demands.append({'gpu': rank, 'nbytes': pickle.load(stream)['meta']['bytes']})
        layout = choose_block_bindings(json.loads(topology_path.read_text())['topology'], demands,
                                       block_bytes=256 << 20, guard_bytes=4 << 30)
        numas = {str(rank): [row['numa'] for row in layout['blocks'][rank]] for rank in range(2)}
        with ThreadPoolExecutor(2) as pool:
            captures = list(pool.map(lambda rank: request(self.endpoints[rank], 'capture_regions',
                {'tag': phase, 'snap': 'B', 'block_bytes': 256 << 20, 'numa_by_rank': numas}, timeout=1200), range(2)))
        sources = [rows[0]['manifest'] for rows in captures]
        self.slots['B'] = {'model': 'B', 'label': 'Qwen_local', 'endpoint': 'http://12.11.3.27:32540',
                           'workers': [0, 1], 'command': str(self.out/'B.command.json'), 'parked': False}
        self.wait_http(self.slots['B']['endpoint'], 'health', timeout=600)
        reference = self.generate_stream(self.slots['B'])
        self.models['B'] = {'sources': sources, 'source_ids': [r['id'] for rows in captures for r in rows[0]['regions']],
                            'reference': reference, 'tp': 2}
        self.result['lifecycle']['model_b_reference'] = reference
        self.event('lifecycle_B_ready', reference=reference)

    def pre_spill(self):
        rank = self.pre_spill_rank
        if rank not in [2, 3]: raise ValueError('pre-spill uses the validated rank2/3 routes')
        _, replicas = self.cluster.inventory(compact=True)
        ids = self.models['A']['source_ids']
        selected = sorted((r for r in replicas if r['id'] in ids and r['tp'] == rank
                           and self.members[r['worker']][0] == 'node27'), key=lambda r: r['off'])
        topology = self.out/f'pre_spill_{self.attempt}.json'
        self.remote('node28', ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(topology)])
        gpu = 6+rank-2
        layout = choose_block_bindings(json.loads(topology.read_text())['topology'],
            [{'gpu': gpu, 'nbytes': sum(r['n'] for r in selected)}], block_bytes=256 << 20, guard_bytes=4 << 30)
        actions = []
        for i, source in enumerate(selected):
            target = self.replica_target(self.models['A']['sources'][rank],
                f'GLM/pre_{self.attempt}/rank{rank}/block{i}', 20+rank-2, 'dram', source['off'], source['n'])
            target['numa'] = layout['blocks'][gpu][i]['numa']
            actions.append({'source_id': source['id'], 'target': target})
        self.event('pre_spill_begin', rank=rank, bytes=sum(r['n'] for r in selected))
        self.result['lifecycle']['pre_spill'] = []
        for committed in migrate_blocks(self.cluster, actions, self.out/f'pre_spill_{self.attempt}_journal.json'):
            ids.remove(committed['source_id']); ids.append(committed['target_id'])
            self.result['lifecycle']['pre_spill'].append(committed)
            if len(self.result['lifecycle']['pre_spill']) % 16 == 0:
                self.checkpoint_lifecycle()
        self.checkpoint_lifecycle()
        self.event('pre_spill_complete', rank=rank, blocks=len(actions))

    def migrate_with_recalls(self):
        _, replicas = self.cluster.inventory(compact=True)
        local_before = sum(row['n'] for row in replicas if row['id'] in self.models['A']['source_ids']
                           and row['tier'] == 'dram' and self.members[row['worker']][0] == 'node27')
        selected = [row for row in replicas if row['id'] in self.models['A']['source_ids'] and
                    row['tier'] == 'dram' and self.members[row['worker']][0] == 'node27' and
                    (row['tp'] in [2, 3] or (row['tp'] == 4 and row['off'] < 1 << 30))]
        topology_path = self.out/'life_cache_topology.json'
        self.remote('node28', ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(topology_path)])
        layout = choose_block_bindings(json.loads(topology_path.read_text())['topology'],
            [{'gpu': 6+i, 'nbytes': self.models['A']['sources'][2+i]['n']} for i in range(2)
             if any(row['tp'] == 2+i for row in selected)],
            block_bytes=256 << 20, guard_bytes=4 << 30)
        actions = []
        for source in sorted(selected, key=lambda row: (row['tp'], row['off'])):
            rank = source['tp']; ordinal = source['off']//(256 << 20)
            if rank == 4:
                target = self.replica_target(self.models['A']['sources'][rank],
                    f'GLM/life_ssd/rank{rank}/block{ordinal}', 4, 'ssd', source['off'], source['n'])
                target.update(storage_node='node28', store_endpoint=self.ssd_endpoint)
            else:
                target = self.replica_target(self.models['A']['sources'][rank],
                    f'GLM/life_moved/rank{rank}/block{ordinal}', 20+rank-2, 'dram', source['off'], source['n'])
                target['numa'] = layout['blocks'][6+rank-2][ordinal]['numa']
            actions.append({'source_id': source['id'], 'target': target})
        checkpoints = {max(1, len(actions)*i//4) for i in [1, 2, 3]}
        self.result['lifecycle']['migration_bytes'] = sum(a['target']['n'] for a in actions)
        self.result['lifecycle']['pressure_event'] = {
            'node': 'node27', 'GLM_dram_before_bytes': local_before,
            'requested_GLM_dram_budget_bytes': local_before-sum(a['target']['n'] for a in actions),
            'scope': 'synthetic budget reduction; actual weight migration and source allocation release',
            'placement_policy': 'fixed trace for lifecycle acceptance, not MILP optimality evidence'}
        for completed in migrate_blocks(self.cluster, actions, self.out/'life_migration_journal.json'):
            ids = self.models['A']['source_ids']
            ids.remove(completed['source_id']); ids.append(completed['target_id'])
            self.result['lifecycle']['moves'].append(completed)
            count = len(self.result['lifecycle']['moves'])
            if count % 16 == 0:
                self.event('lifecycle_migration_progress', committed=count, total=len(actions))
                self.result['lifecycle'].setdefault('B_during_migration', []).append(self.generate_stream(self.slots['B']))
            if count in checkpoints:
                self.switch('B', 'A', f'mid_migration_{count}')
                self.switch('A', 'B', f'continue_after_{count}')
        self.checkpoint_lifecycle()
        _, current = self.cluster.inventory(compact=True)
        local_after = sum(row['n'] for row in current if row['id'] in self.models['A']['source_ids']
                          and row['tier'] == 'dram' and self.members[row['worker']][0] == 'node27')
        self.result['lifecycle']['pressure_event']['GLM_dram_after_bytes'] = local_after
        if local_after > self.result['lifecycle']['pressure_event']['requested_GLM_dram_budget_bytes']:
            raise AssertionError('migration did not satisfy the requested local snapshot budget')

    def run(self):
        try:
            self.park('A')
            if self.pre_spill_rank is not None: self.pre_spill()
            self.boot_b()
            self.park('B')
            self.resume('A', 'bootstrap', verify=True)
            self.switch('A', 'B', 'first_switch', verify=True)
            self.start_ssd()
            self.migrate_with_recalls()
            self.switch('B', 'A', 'return_after_migration', verify=True)
            self.result['lifecycle']['status'] = 'passed'
            self.result['status'] = 'lifecycle_passed'
            self.event('lifecycle_complete')
        except BaseException as exc:
            self.result['lifecycle'].update(status='failed', error=repr(exc))
            self.result['status'] = 'lifecycle_failed'
            self.event('lifecycle_failed_resources_retained', error=repr(exc))
            raise
        finally:
            self.checkpoint_lifecycle()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--model-b', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.add_argument('--ssd-directory', type=Path)
    parser.add_argument('--attempt', default='initial')
    parser.add_argument('--resume-export')
    parser.add_argument('--pre-spill-rank', type=int)
    GlmAB(parser.parse_args()).run()
