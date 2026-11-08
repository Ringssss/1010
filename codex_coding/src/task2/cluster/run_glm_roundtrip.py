"""Validate captured GLM FP8 TP16 weights through release and warm-engine restoration."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import pickle
from pathlib import Path
import resource
import time

from run_system import SystemRun, NODES, ROOT
from run_lifecycle import LifecycleRun
from fluidckpt.cluster.control import request
from fluidckpt.memory.bindings import choose_bindings, choose_block_bindings
from fluidckpt.serving.handoff import wait_for_exports


class GlmRoundtripRun(SystemRun):
    generate_stream = LifecycleRun.generate_stream

    def __init__(self, args):
        NODES.clear()
        NODES.update({f'node{node}': {'ip': f'12.11.3.{node}', 'gpus': list(range(8)),
                                    'container': f'zjn-keep-v02-n{node}'} for node in [27, 29]})
        # Keep the cache host in the initial mesh so later migration can reuse these snapshots.
        NODES['node28'] = {'ip': '12.11.3.28', 'gpus': list(range(2, 8)), 'container': 'zjn-keep-v02-n28'}
        super().__init__(args)
        self.server_port = 32253
        self.env.update(AMD_SERIALIZE_KERNEL='0', NCCL_SOCKET_IFNAME='ib0', GLOO_SOCKET_IFNAME='ib0',
                        SGLANG_ROCM_USE_AITER_MOE='0')
        self.env.pop('SGLANG_KV_LAYOUT_HCU_FA', None)
        self.env['NCCL_DEBUG'] = getattr(args, 'nccl_debug', 'WARN')
        if getattr(args, 'ib_hca', ''):
            self.env['NCCL_IB_HCA'] = args.ib_hca
        self.result['ib_hca'] = getattr(args, 'ib_hca', '') or 'automatic'
        self.owner_sockets = {}
        self.result.update(scope='GLM FP8 TP16 capture and local-DRAM warm restore across two compute hosts; '
                                 'not a three-node migration or performance comparison',
                           transitions=[], memory_samples=[])
        if getattr(args, 'spill_ranks_per_node', 0):
            self.result['scope'] = ('GLM FP8 TP16 capture, staged three-node DRAM backing, release and warm restore; '
                                    'not an A/B lifecycle or a same-budget policy comparison')
        self.result['model_path'] = args.model
        diagnostic = Path(args.model)/'diagnostic_manifest.json'
        if diagnostic.exists(): self.result['diagnostic_model'] = json.loads(diagnostic.read_text())

    def start(self, node, name, command, env=None):
        environment = dict(env or {})
        if name.startswith('worker_') and node in self.owner_sockets:
            environment['FLUIDCKPT_CPU_OWNER_SOCKET'] = self.owner_sockets[node]
            environment['TORCH_NCCL_ASYNC_ERROR_HANDLING'] = '0'
            command = command+['--hbm-budget', str(getattr(self.args, 'hbm_budget_gib', 48) << 30)]
        return super().start(node, name, command, environment)

    def start_workers(self):
        for node in NODES:
            if getattr(self.args, 'monitor_memory', False):
                self.start(node, f'monitor_{node}', ['python3', 'codex_coding/src/diag/monitor_cluster_memory.py',
                    'sample', '--out', str(self.out/f'{node}_hbm.jsonl')])
            path = f'/dev/shm/fluid-owner-{self.args.run}-{node}.sock'
            self.start(node, f'cpu_owner_{node}', ['python3', '-m', 'fluidckpt.memory.owner',
                '--socket', path, '--budget-bytes', str((self.args.host_memory_gib-64) << 30)])
            deadline = time.monotonic()+30
            while True:
                try:
                    self.remote(node, ['test', '-S', path])
                    break
                except Exception:
                    if time.monotonic() > deadline: raise
                    time.sleep(.1)
            self.owner_sockets[node] = path
        super().start_workers()

    def isolated_containers(self):
        for node in NODES:
            image = self.docker_host(node, ['inspect', NODES[node]['container'], '--format', '{{.Config.Image}}']).strip()
            name = 'zjn-fluid-glm-'+self.args.run
            command = ['run', '-d', '--name', name, '--network', 'host', '--ipc', 'host',
                       '--cap-add', 'SYS_NICE', '--ulimit', 'memlock=-1:-1', '--ulimit', 'core=0:0',
                       '--memory', f'{self.args.host_memory_gib}g', '--memory-swap', f'{self.args.host_memory_gib}g']
            for dev in ['/dev/kfd', '/dev/mkfd', '/dev/dri', '/dev/infiniband']:
                command += ['--device', dev]
            for mount in ['/opt/hyhal:/opt/hyhal:ro', '/home/zhujianian:/home/zhujianian',
                          '/data/node28/shared:/data/node28/shared:ro']:
                command += ['-v', mount]
            self.docker_host(node, command+[image, 'sleep', 'infinity'])
            self.owned_containers.append((node, name))
            NODES[node]['container'] = name
            self.event('isolated_container', node=node, container=name,
                       memory_limit_gib=self.args.host_memory_gib)

    def preflight(self):
        super().preflight()
        self.args.worker_numas = {}
        self.result['bindings'] = {}
        for node in NODES:
            bindings = choose_bindings(self.probes[node]['topology'],
                [{'gpu': gpu, 'nbytes': (self.args.capture_block_mib << 20) if self.args.capture_block_mib
                  else self.args.shard_reserve_gib << 30} for gpu in NODES[node]['gpus']])
            self.result['bindings'][node] = bindings
            for row in bindings['bindings']:
                self.args.worker_numas[f'{node}:{row["gpu"]}'] = row['numa']
        self.save()

    def memory(self, label):
        workers, replicas = self.cluster.inventory(compact=True)
        self.result['memory_samples'].append({'time': time.time(), 'label': label,
            'workers': workers, 'replicas': replicas})
        self.save()

    def wait_exports(self, phase):
        return wait_for_exports(self.out/'handoff'/phase, ranks=16, timeout=self.args.timeout, poll_s=.1)

    def boot_capture(self):
        phase = 'capture'
        model_path = Path(self.args.model)
        fingerprint = hashlib.sha256((model_path/'config.json').read_bytes()
                                     +(model_path/'model.safetensors.index.json').read_bytes())
        files = []
        for path in sorted(model_path.glob('*.safetensors')):
            stat = path.stat()
            row = {'name': path.name, 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
            fingerprint.update(json.dumps(row, sort_keys=True).encode())
            files.append(row)
        identity = fingerprint.hexdigest()
        self.result['checkpoint_revision'] = {'identity': identity, 'files': files,
            'scope': 'config/index and file metadata fingerprint; restored bytes are checked separately'}
        env = {'FLUIDCKPT_MODE': 'capture', 'FLUIDCKPT_TAG': phase,
            'FLUIDCKPT_HANDOFF': str(self.out/'handoff'), 'FLUIDCKPT_METRICS': str(self.out/'metrics'/phase),
            'FLUIDCKPT_CAPTURE_WAIT': '1', 'FLUIDCKPT_TIMEOUT': str(self.args.timeout),
            'FLUIDCKPT_TP_SIZE': '16', 'FLUIDCKPT_MODEL_ID': identity,
            'FLUIDCKPT_MANIFEST': str(self.out/'handoff'/phase/'derived.json'),
            'FLUIDCKPT_LIFECYCLE': str(self.out/'glm.command.json'),
            'HIP_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7'}
        for node, rank in [('node29', 1), ('node27', 0)]:
            argv = ['python3', 'codex_coding/src/serving/sglang_fluid_launch.py',
                '--model-path', self.args.model, '--tp-size', '16', '--nnodes', '2', '--node-rank', str(rank),
                '--dist-init-addr', f'12.11.3.27:{self.base+200}', '--host', '0.0.0.0',
                '--port', str(self.server_port), '--trust-remote-code', '--context-length', '16384',
                '--max-total-tokens', '32768', '--max-running-requests', '16', '--chunked-prefill-size', '4096',
                '--mem-fraction-static', '.85', '--disable-cuda-graph', '--disable-custom-all-reduce',
                '--moe-runner-backend', 'triton', '--fp8-gemm-backend', 'triton', '--kv-cache-dtype', 'bf16',
                '--watchdog-timeout', '3600', '--random-seed', '42']
            self.start(node, f'model_{node}', argv, env)
        self.wait_exports(phase)
        self.event('capture_exports_ready')
        if getattr(self.args, 'spill_ranks_per_node', 0):
            from glm_distributed_capture import capture_distributed
            capture_distributed(self, phase, self.args.spill_ranks_per_node)
        elif self.args.capture_block_mib:
            numa_plans = {}
            for node_index, node in enumerate(['node27', 'node29']):
                probe_path = self.out/f'{node}_capture_topology.json'
                self.remote(node, ['python3', 'codex_coding/src/task2/cluster/inspect_node.py', '--out', str(probe_path)])
                topology = json.loads(probe_path.read_text())['topology']
                demands = []
                for gpu in range(8):
                    rank = node_index*8+gpu
                    with (self.out/'handoff'/phase/f'rank{rank}.pkl').open('rb') as stream:
                        demands.append({'gpu': gpu, 'nbytes': pickle.load(stream)['meta']['bytes']})
                placement = choose_block_bindings(topology, demands, block_bytes=self.args.capture_block_mib << 20)
                self.result.setdefault('capture_block_bindings', {})[node] = placement
                for gpu, blocks in placement['blocks'].items():
                    numa_plans[str(node_index*8+gpu)] = [block['numa'] for block in blocks]
            with ThreadPoolExecutor(16) as pool:
                self.result['captures'] = list(pool.map(lambda endpoint: request(endpoint, 'capture_regions',
                    {'tag': phase, 'snap': 'GLM', 'block_bytes': self.args.capture_block_mib << 20,
                     'numa_by_rank': numa_plans}, timeout=1200),
                    self.endpoints[:16]))
            self.sources = [result[0]['manifest'] for result in self.result['captures']]
            self.regions = [region for result in self.result['captures'] for region in result[0]['regions']]
            self.source_ids = [row['id'] for row in self.regions]
        else:
            with ThreadPoolExecutor(16) as pool:
                self.result['captures'] = list(pool.map(lambda endpoint: request(endpoint, 'capture',
                    {'tag': phase, 'snap': 'GLM'}, timeout=1200), self.endpoints[:16]))
            for endpoint in self.endpoints[:16]:
                request(endpoint, 'release_live', {'tag': phase})
            self.sources = [request(endpoint, 'adopt', {'snap': 'GLM', 'tp': rank, 'id': f'GLM/base/{rank}'})
                            for rank, endpoint in enumerate(self.endpoints[:16])]
            self.regions = self.sources
            self.source_ids = [source['id'] for source in self.sources]
        self.result['sources'] = self.sources
        self.shell = {'endpoint': f'http://12.11.3.27:{self.server_port}'}
        self.wait_http(self.shell['endpoint'], 'health', timeout=600)
        self.reference = self.generate_stream(self.shell)
        self.result['reference'] = self.reference
        self.save()
        native = json.loads(Path(self.args.native_result).read_text())
        if self.reference['text'] != native['generations'][0]['answers'][0]['text']:
            raise AssertionError('captured model output differs from the native reference')
        self.result['reference'] = self.reference
        self.event('capture_reference', reply=self.reference, nbytes=sum(s['n'] for s in self.sources))
        self.memory('serving_before_release')

    def roundtrip(self):
        started = time.monotonic()
        request(self.shell['endpoint'], 'release_memory_occupation', {'tags': ['weights']}, timeout=300)
        self.result['release_s'] = time.monotonic()-started
        self.event('released', release_s=self.result['release_s'])
        self.memory('parked')
        phase = 'restore01'
        (self.out/'glm.command.json').write_text(json.dumps({'tag': phase,
            'metrics': str(self.out/'metrics'/phase), 'mode': 'restore'}))
        started = time.monotonic()
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(request, self.shell['endpoint'], 'resume_memory_occupation', {'tags': ['weights']}, 1200)
            try:
                self.wait_exports(phase)
                targets = [dict(self.replica_target(s, f'{phase}/{rank}', rank), mode='live', tag=phase,
                                metrics_directory=str(self.out/'metrics'/phase)) for rank, s in enumerate(self.sources)]
                plan = self.cluster.plan_restore(targets, 'resource_lp', self.source_ids)
                flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
                future.result()
            except BaseException as exc:
                self.result['restore_failure'] = repr(exc)
                self.save()
                # An unfilled model waits for a marker, so close its RPC before joining the pool.
                # CPU owners and captured bytes remain available with --keep-on-failure.
                for handle in list(self.processes):
                    if handle[1].name.startswith('model_'):
                        self.stop(handle)
                raise
        reply = self.generate_stream(self.shell)
        self.result['restore'] = {'request_to_first_token_s': reply['first_token_time']-started,
                                  'reply': reply, 'correct': reply['text'] == self.reference['text'],
                                  'flow': flow}
        if not self.result['restore']['correct']:
            raise AssertionError('GLM output changed after parking and restoring captured weights')
        self.result['verification'] = self.cluster.verify(flow['tx'])
        self.cluster.finish(flow['tx'])
        self.memory('serving_after_restore')
        self.event('roundtrip_verified', request_to_first_token_s=self.result['restore']['request_to_first_token_s'],
                   checked_bytes=self.result['verification']['checked_bytes'])

    def cleanup(self):
        for handle in list(reversed(self.processes)):
            try:
                self.stop(handle)
            except Exception as exc:
                self.result.setdefault('cleanup_errors', []).append(repr(exc))
        for node, container in self.owned_containers:
            try:
                self.docker_host(node, ['rm', '-f', container])
            except Exception as exc:
                self.result.setdefault('cleanup_errors', []).append(repr(exc))
        self.save()

    def checkpoint(self):
        state = {'args': vars(self.args), 'nodes': NODES, 'profile': self.cluster.profile,
                 'endpoints': self.endpoints, 'members': self.members, 'env': self.env,
                 'processes': [(node, str(path)) for node, path in self.processes],
                 'owned_containers': self.owned_containers,
                 'owner_sockets': self.owner_sockets,
                 'sources': getattr(self, 'sources', None), 'regions': getattr(self, 'regions', None),
                 'source_ids': getattr(self, 'source_ids', None),
                 'shell': getattr(self, 'shell', None), 'reference': getattr(self, 'reference', None)}
        (self.out/'session.json').write_text(json.dumps(state, indent=2)+'\n')

    def run(self):
        try:
            native = json.loads(Path(self.args.native_result).read_text())
            if native['status'] != 'generated_repeatably':
                raise RuntimeError('native GLM generation must pass before testing FluidCKPT')
            if native['args']['model'] != self.args.model:
                raise ValueError('native reference must come from the same complete checkpoint')
            if native['args'].get('ib_hca', '') != self.args.ib_hca:
                raise ValueError('native reference must use the same HCA selection as the experiment')
            self.result['native_reference'] = self.args.native_result
            self.isolated_containers()
            self.preflight()
            self.start_workers()
            if self.args.spill_ranks_per_node:
                from glm_distributed_capture import validate_spill_routes
                validate_spill_routes(self, self.args.spill_ranks_per_node)
            self.boot_capture()
            self.roundtrip()
            if self.args.ipc_baseline:
                from glm_ipc_baseline import resident_ipc
                resident_ipc(self)
            self.result['status'] = 'passed'
            self.checkpoint()
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc))
            self.event('failed', error=repr(exc))
            raise
        finally:
            if self.args.keep_running and self.result['status'] == 'passed':
                self.event('retained_for_next_stage')
            elif self.args.keep_on_failure and self.result['status'] == 'failed':
                self.checkpoint()
                self.event('retained_for_debugging')
            else:
                self.cleanup()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--native-result', required=True)
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/GLM-5.3')
    parser.add_argument('--timeout', type=int, default=3600)
    parser.add_argument('--port-base', type=int, default=29700)
    parser.add_argument('--host-memory-gib', type=int, default=440)
    parser.add_argument('--shard-reserve-gib', type=int, default=47)
    parser.add_argument('--capture-block-mib', type=int, default=256,
                        help='Independent backing allocation size; zero uses the original packed capture')
    parser.add_argument('--spill-ranks-per-node', type=int, default=0)
    parser.add_argument('--nccl-debug', default='WARN')
    parser.add_argument('--ib-hca', default='')
    parser.add_argument('--monitor-memory', action='store_true')
    parser.add_argument('--ipc-baseline', action='store_true')
    parser.add_argument('--hbm-budget-gib', type=int, default=48)
    parser.add_argument('--keep-running', action='store_true')
    parser.add_argument('--keep-on-failure', action='store_true')
    parser.set_defaults(worker_serialize=0, handoff_poll=.1, mesh='node27:0-7,node29:0-7,node28:2-7',
                        result_root=str(ROOT/'codex_coding/results/glm_roundtrip'), development='v0.1.23.2')
    GlmRoundtripRun(parser.parse_args()).run()
