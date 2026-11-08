"""Measure first-token recovery as real Qwen backing leaves local DRAM."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import resource
import sys
import time

from continue_glm_ab import GlmAB, NODES, ROOT
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.cluster.block_migration import migrate_blocks
from fluidckpt.memory.bindings import choose_block_bindings


class PressureRun(GlmAB):
    def __init__(self, args):
        self.root = args.run_dir.resolve()
        self.output = ROOT/'codex_coding/results/motivation'/args.run
        self.output.mkdir(parents=True, exist_ok=False)
        self.result = {'status': 'waiting_for_lifecycle', 'development': 'v0.1.23.2',
            'run': args.run, 'events': [], 'memory_samples': [], 'samples': [],
            'lifecycle': {'recalls': []},
            'axes': {'x': 'Measured local DRAM bytes / complete Qwen runtime weight bytes',
                     'y': 'Restore request to first correct token, seconds'},
            'protocol': {'fractions': args.fractions,
                         'cpu_first_ssd': args.cpu_first_ssd,
                         'policies': ['local_dram_ssd', 'cluster_dram'],
                         'local_dram_ssd': 'same warm engine and processed format; local DRAM plus local SSD fallback',
                         'cluster_dram': 'same warm engine and processed format; local and remote DRAM',
                         'comparison': 'same allocated backing objects and physical environment; policy changes eligible sources',
                         'ssd_policy': 'fsync and file-specific FADV_DONTNEED; record actual kernel read_bytes',
                         'repetitions': args.repeats,
                         'role': 'capacity-locality motivation; placement/LP benefit is evaluated separately'}}
        self.save()
        deadline = time.monotonic()+args.wait_timeout
        while True:
            current = json.loads((self.root/'summary.json').read_text())
            if current['status'] == 'lifecycle_passed': break
            if current['status'] == 'lifecycle_failed':
                self.result.update(status='prerequisite_failed', error=current['lifecycle'].get('error'))
                self.save()
                raise RuntimeError('lifecycle prerequisite failed; snapshots remain in their owners')
            if time.monotonic() >= deadline: raise TimeoutError('lifecycle prerequisite deadline')
            time.sleep(2)
        route = json.loads(args.route_result.read_text())
        if route['status'] != 'passed': raise RuntimeError('motivation transport paths must pass validation')
        self.result['route_evidence'] = str(args.route_result)
        if args.resume_run:
            self.result['status'] = 'waiting_for_previous'
            self.save()
            while True:
                previous = json.loads((args.resume_run/'summary.json').read_text())
                if previous['status'] == 'passed': break
                if args.resume_failed and previous['status'] == 'failed': break
                if previous['status'] in ['failed', 'prerequisite_failed']:
                    self.result.update(status='prerequisite_failed', error=previous.get('error'))
                    self.save()
                    raise RuntimeError('preceding pressure run failed')
                if time.monotonic() >= deadline: raise TimeoutError('preceding pressure run deadline')
                time.sleep(2)
        state = json.loads((self.root/'session.json').read_text())
        lifecycle = json.loads(((args.resume_run/'session.json') if args.resume_run
                                else self.root/'lifecycle_session.json').read_text())
        for key in ['env', 'endpoints', 'members', 'owned_containers', 'owner_sockets']:
            setattr(self, key, state[key])
        self.args = argparse.Namespace(**state['args'])
        self.models, self.slots = lifecycle['models'], lifecycle['slots']
        self.processes = [(node, Path(path)) for node, path in lifecycle['processes']]
        self.processes = [(node, path) for node, path in self.processes if not path.name.startswith('cpu_owner_')]
        self.processes += [(node, Path(path)) for node, path in state['processes']
                           if Path(path).name.startswith('cpu_owner_')]
        self.out = self.root
        self.base = state['args']['port_base']
        self.attempt = 'motivation_'+args.run
        self.sequence = 0
        self.ssd_process = None
        NODES.clear(); NODES.update(state['nodes'])
        self.cluster = Cluster(state['endpoints'], lifecycle['profile'], planner_endpoint=state['endpoints'][0])
        self.cluster.profile['rates']['local_ssd'] = .5
        self.cluster.profile.update(ssd_GBps=.5, store_network_GBps=1.)
        self.result['status'] = 'running'
        self.result['model'] = '/data/node28/shared/zhujianian/Qwen3.6-35B-A3B'
        self.result['runtime_bytes'] = sum(r['n'] for r in self.models['B']['sources'])
        self.fractions, self.repeats = args.fractions, args.repeats
        self.cpu_first_ssd = args.cpu_first_ssd
        self.free_pages_only = args.free_pages_only
        self.result['protocol']['free_pages_only'] = self.free_pages_only
        self.allowed_ids = None
        self.ssd_ids = lifecycle.get('ssd_ids', []) if args.resume_run else []
        if self.ssd_ids:
            self.ssd_endpoint = lifecycle['ssd_endpoint']
            self.result['reused_backing_from'] = str(args.resume_run)
        self.save()
        if args.resume_failed:
            _, replicas = self.cluster.inventory(compact=True)
            available = {r['id'] for r in replicas}
            if any(not set(model['source_ids']) <= available for model in self.models.values()):
                raise RuntimeError('failed-run recovery requires every recorded source to be published')

    def save(self):
        path = self.output/'summary.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.result, ensure_ascii=False, indent=2)+'\n')
        temporary.replace(path)

    def checkpoint_lifecycle(self):
        state = {'models': self.models, 'slots': self.slots,
                 'processes': [(node, str(path)) for node, path in self.processes],
                 'profile': self.cluster.profile, 'sequence': self.sequence,
                 'root_session': str(self.root/'session.json'), 'ssd_ids': self.ssd_ids,
                 'ssd_endpoint': getattr(self, 'ssd_endpoint', None)}
        (self.output/'session.json').write_text(json.dumps(state, indent=2)+'\n')
        self.save()

    def fill(self, name, phase):
        model, slot = self.models[name], self.slots[name]
        targets = [dict(self.replica_target(source, f'{phase}/rank{rank}', slot['workers'][rank]),
                        mode='live', tag=phase, metrics_directory=str(self.out/'metrics'/phase))
                   for rank, source in enumerate(model['sources'])]
        ids = self.allowed_ids if name == 'B' and self.allowed_ids is not None else model['source_ids']
        plan = self.cluster.plan_restore(targets, 'resource_lp', ids)
        flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
        self.last_source_bytes = flow['source_bytes']
        return flow

    def prepare_ssd(self):
        if self.ssd_ids:
            _, replicas = self.cluster.inventory(compact=True)
            rows = [r for r in replicas if r['id'] in self.ssd_ids]
            if sum(r['n'] for r in rows) != self.result['runtime_bytes']:
                raise RuntimeError('existing SSD backing no longer covers the model')
            self.result['ssd_backing_status'] = request(self.ssd_endpoint, 'status')
            self.event('ssd_backing_reused', nbytes=sum(r['n'] for r in rows))
            return
        self.ssd_endpoint = 'http://12.11.3.27:29791'
        directory = self.output/'ssd_data'
        self.ssd_handle = self.start('node27', 'motivation_ssd_'+self.result['run'],
            ['python3', '-m', 'fluidckpt.serving.ssd_store', '--directory', str(directory), '--port', '29791'])
        self.wait_http(self.ssd_endpoint)
        self.result['ssd_filesystem'] = self.remote('node27', ['df', '-hT', str(directory)])
        _, replicas = self.cluster.inventory(compact=True)
        ids = set(self.models['B']['source_ids'])
        targets = []
        for row in replicas:
            if row['id'] not in ids: continue
            target = self.replica_target(self.models['B']['sources'][row['tp']],
                f'motivation/{self.result["run"]}/ssd/{row["tp"]}/{row["off"]}',
                row['tp'], 'ssd', row['off'], row['n'])
            target.update(storage_node='node27', store_endpoint=self.ssd_endpoint)
            targets.append(target)
        started = time.monotonic()
        flow = self.cluster.transfer(self.cluster.plan_restore(targets, 'resource_lp', list(ids)))
        self.ssd_ids = [t['id'] for t in targets]
        self.result['ssd_build'] = {'wall_s': time.monotonic()-started, 'verification': flow['verification'],
                                    'bytes': sum(t['n'] for t in targets)}
        self.event('ssd_backing_ready', nbytes=self.result['ssd_build']['bytes'])
        self.checkpoint_lifecycle()

    def move_to_fraction(self, fraction):
        _, replicas = self.cluster.inventory(compact=True)
        ids = set(self.models['B']['source_ids'])
        current = [r for r in replicas if r['id'] in ids]
        moves = []
        for rank, source in enumerate(self.models['B']['sources']):
            desired_blocks = math.ceil(source['n']*fraction/(256 << 20))
            rows = sorted((r for r in current if r['tp'] == rank), key=lambda r: r['off'])
            for row in rows:
                destination = 'node27' if row['off']//(256 << 20) < desired_blocks else 'node29'
                if self.members[row['worker']][0] != destination:
                    moves.append((row, destination))
        if not moves: return
        actions = []
        for node in sorted({node for _, node in moves}):
            selected = [row for row, destination in moves if destination == node]
            topology = self.output/f'{node}_before_{fraction}.json'
            self.remote(node, ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(topology)])
            demands = [{'gpu': rank, 'nbytes': sum(r['n'] for r in selected if r['tp'] == rank)} for rank in range(2)]
            hardware = json.loads(topology.read_text())['topology']
            if self.free_pages_only:
                for row in hardware['nodes']:
                    row['memory_bytes']['FilePages'] = row['memory_bytes']['Shmem'] = 0
            placement = choose_block_bindings(hardware, demands,
                                              block_bytes=256 << 20, guard_bytes=4 << 30)
            counts = {0: 0, 1: 0}
            for source in selected:
                rank = source['tp']
                target = self.replica_target(self.models['B']['sources'][rank],
                    f'motivation/{self.result["run"]}/{node}/{rank}/{source["off"]}',
                    rank+(8 if node == 'node29' else 0), 'dram', source['off'], source['n'])
                target['numa'] = placement['blocks'][rank][counts[rank]]['numa']
                counts[rank] += 1
                actions.append({'source_id': source['id'], 'target': target})
        started = time.monotonic()
        count = 0
        for committed in migrate_blocks(self.cluster, actions, self.output/f'migration_{fraction}.json'):
            self.models['B']['source_ids'].remove(committed['source_id'])
            self.models['B']['source_ids'].append(committed['target_id'])
            count += 1
            if count % 16 == 0: self.checkpoint_lifecycle()
        self.result.setdefault('migrations', []).append({'target_fraction': fraction,
            'bytes': sum(a['target']['n'] for a in actions), 'blocks': count,
            'wall_s': time.monotonic()-started})
        self.event('local_pressure_applied', fraction=fraction, blocks=count)
        self.checkpoint_lifecycle()

    def run(self):
        try:
            self.park('A'); self.park('B')
            self.prepare_ssd()
            for index, fraction in enumerate(self.fractions):
                self.move_to_fraction(fraction)
                workers, replicas = self.cluster.inventory(compact=True)
                ids = set(self.models['B']['source_ids'])
                current = [r for r in replicas if r['id'] in ids]
                local = [r for r in current if self.members[r['worker']][0] == 'node27']
                actual_fraction = sum(r['n'] for r in local)/self.result['runtime_bytes']
                if sum(r['n'] for r in current) != self.result['runtime_bytes']:
                    raise AssertionError('backing byte budget changed')
                for repetition in range(self.repeats):
                    policies = ['local_dram_ssd', 'cluster_dram']
                    if (index+repetition) % 2: policies.reverse()
                    for policy in policies:
                        covered = {(r['tp'], r['off'], r['n']) for r in local}
                        ssd_ids = [r['id'] for r in replicas if r['id'] in self.ssd_ids and
                                   (not self.cpu_first_ssd or (r['tp'], r['off'], r['n']) not in covered)]
                        self.allowed_ids = [r['id'] for r in local]+ssd_ids if policy == 'local_dram_ssd' else list(ids)
                        before = request(self.ssd_endpoint, 'status')
                        row = self.resume('B', f'{policy}_{index}_{repetition}', verify=False)
                        after = request(self.ssd_endpoint, 'status')
                        self.result['samples'].append({'policy': policy, 'fraction': actual_fraction,
                            'requested_fraction': fraction, 'repetition': repetition,
                            'first_token_s': row['request_to_first_token_s'], 'correct': row['correct'],
                            'flow': row['flow'], 'source_bytes': self.last_source_bytes,
                            'ssd_logical_read_bytes': after['read_bytes']-before['read_bytes'],
                            'ssd_kernel_read_bytes': after['kernel_io']['read_bytes']-before['kernel_io']['read_bytes'],
                            'dram_bytes': sum(r['n'] for r in current)})
                        self.park('B')
                        self.checkpoint_lifecycle()
                        self.event('motivation_sample', policy=policy, fraction=actual_fraction,
                                   first_token_s=row['request_to_first_token_s'])
            self.result['status'] = 'passed'
            self.event('motivation_complete')
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc)); self.event('failed_resources_retained', error=repr(exc))
            raise
        finally:
            self.checkpoint_lifecycle()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--route-result', type=Path, required=True)
    parser.add_argument('--resume-run', type=Path)
    parser.add_argument('--cpu-first-ssd', action='store_true')
    parser.add_argument('--resume-failed', action='store_true')
    parser.add_argument('--free-pages-only', action='store_true')
    parser.add_argument('--fractions', type=float, nargs='+', default=[1., .5, 0.])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--wait-timeout', type=int, default=10800)
    args = parser.parse_args()
    if any(not 0 <= f <= 1 for f in args.fractions):
        parser.error('fractions must be within [0,1]')
    run = PressureRun(args)
    run.run()
