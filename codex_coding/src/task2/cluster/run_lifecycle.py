"""Run real model parking, cross-node weight movement and warm-engine recovery."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import threading
import time
import urllib.request

from run_system import SystemRun, NODES, ROOT, PROMPTS
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.memory.bindings import choose_bindings
from fluidckpt.serving.measure import summarize

GIB = 1 << 30


class LifecycleRun(SystemRun):
    def __init__(self, args):
        if args.resume_stage:
            self.restore_session(args)
            return
        NODES.clear()
        NODES.update({n: {'ip': f'12.11.3.{i}', 'gpus': gs,
                         'container': f'zjn-keep-v02-n{i}'}
                     for n, i, gs in [('node27', 27, [0, 1]), ('node28', 28, [2, 3]),
                                      ('node29', 29, [0, 1])]})
        super().__init__(args)
        self.server_port = 32040
        self.shells = {}
        self.models = {}
        self.result.update(scope='real two-model lifecycle on Hygon DCU; HIP IPC is an adapted baseline',
                           transitions=[], memory_samples=[])
        self.cluster.profile['rates']['local_ssd'] = .5
        self.cluster.profile.update(ssd_GBps=.5, store_network_GBps=1.0, serial_target_execution=True)
        self.ssd_process = None
        self.write_lock = threading.RLock()

    def save(self):
        with self.write_lock:
            super().save()

    def checkpoint(self, stage):
        names = ['models', 'shells', 'processes', 'owned_containers', 'members', 'endpoints',
                 'env', 'base', 'server_port', 'ssd_process']
        state = {'stage': stage, 'args': vars(self.args), 'nodes': NODES,
                 'runtime': {name: getattr(self, name) for name in names}, 'profile': self.cluster.profile}
        if hasattr(self, 'ssd_endpoint'): state['runtime']['ssd_endpoint'] = self.ssd_endpoint
        (self.out/'session.json').write_text(json.dumps(state, default=str))
        self.event('checkpoint', stage=stage)

    def restore_session(self, args):
        self.out = Path(args.result_root)/args.run
        state = json.loads((self.out/'session.json').read_text())
        saved = state['args']; saved.update(resume_stage=args.resume_stage, keep_on_failure=args.keep_on_failure,
                                           local_dram_gib=args.local_dram_gib)
        # Historical sessions placed SSD extents on node30.
        saved.setdefault('ssd_node', 'node30')
        saved.setdefault('ssd_directory', f'/tmp/fluidckpt-{args.run}')
        self.args = argparse.Namespace(**saved)
        self.__dict__.update(state['runtime'])
        self.processes = [(n, Path(p)) for n, p in self.processes]
        self.members = [tuple(m) for m in self.members]
        self.owned_containers = [tuple(m) for m in self.owned_containers]
        if self.ssd_process: self.ssd_process = Path(self.ssd_process)
        for shell in self.shells.values(): shell['handle'] = (shell['handle'][0], Path(shell['handle'][1]))
        NODES.clear(); NODES.update(state['nodes'])
        self.result = json.loads((self.out/'summary.json').read_text())
        self.result.update(status='running', error=None)
        self.write_lock = threading.RLock()
        self.cluster = Cluster(self.endpoints, state['profile'], planner_endpoint=self.endpoints[0])
        self.resume_stage = state['stage'] if args.resume_stage == 'auto' else args.resume_stage
        self.event('continuing', stage=self.resume_stage)

    def normalize_shells(self):
        restart = []
        for shell in list(self.shells.values()):
            try:
                request(shell['endpoint'], 'get_model_info', timeout=5)
                self.park(shell)
            except Exception:
                restart.append(shell)
        for shell in restart:
            if shell['handle'] in self.processes: self.stop(shell['handle'])
            fresh = self.boot(shell['model'], shell['node'], restore=True)
            self.park(fresh)

    def isolated_containers(self):
        for node in NODES:
            image = self.docker_host(node, ['inspect', NODES[node]['container'], '--format', '{{.Config.Image}}']).strip()
            name = 'zjn-fluid-life-' + self.args.run.replace('/', '-')
            command = ['run', '-d', '--name', name, '--network', 'host', '--ipc', 'host',
                       '--cap-add', 'SYS_NICE', '--ulimit', 'memlock=-1:-1', '--ulimit', 'core=0:0']
            for dev in ['/dev/kfd', '/dev/mkfd', '/dev/dri', '/dev/infiniband']:
                command += ['--device', dev]
            for mount in ['/opt/hyhal:/opt/hyhal:ro', '/home/zhujianian:/home/zhujianian',
                          '/data/node28/shared:/data/node28/shared:ro']:
                command += ['-v', mount]
            self.docker_host(node, command + [image, 'sleep', 'infinity'])
            self.owned_containers.append((node, name))
            NODES[node]['container'] = name

    def preflight(self):
        super().preflight()
        decisions = {node: choose_bindings(self.probes[node]['topology'],
                     [{'gpu': g, 'nbytes': 35164859056 + (8252825600+8*GIB if node == 'node27' else 0)}
                      for g in NODES[node]['gpus']])
                     for node in ['node27', 'node28']}
        self.args.worker_numas = {f'{node}:{r["gpu"]}': r['numa']
                                 for node, d in decisions.items() for r in d['bindings']}
        self.result['initial_memory_bindings'] = decisions

    def memory(self, label):
        rows, replicas = self.cluster.inventory(compact=True)
        self.result['memory_samples'].append({'time': time.time(), 'label': label,
            'workers': [{k: w[k] for k in ['node', 'gpu', 'used_bytes', 'hbm_allocated_bytes',
                         'headroom_bytes', 'hbm_total_bytes']} for w in rows], 'replicas': replicas})
        self.save()

    def boot(self, model, node='node27', restore=False):
        label = f'{model}_{node}'
        phase = label + '_boot'
        if label in self.shells: phase += '_' + str(time.time_ns())
        path = self.args.model if model == 'A' else self.args.model_b
        identity = hashlib.sha256((Path(path)/'config.json').read_bytes() + str(path).encode()).hexdigest()
        port = self.server_port + (model == 'B')
        env = {'HIP_VISIBLE_DEVICES': '0,1', 'FLUIDCKPT_MODE': 'restore' if restore else 'capture',
            'FLUIDCKPT_TAG': phase, 'FLUIDCKPT_HANDOFF': str(self.out/'handoff'),
            'FLUIDCKPT_METRICS': str(self.out/'metrics'/phase), 'FLUIDCKPT_CAPTURE_WAIT': '1',
            'FLUIDCKPT_TIMEOUT': '600', 'FLUIDCKPT_TP_SIZE': '2', 'FLUIDCKPT_MODEL_ID': identity,
            'FLUIDCKPT_MANIFEST': str(self.out/'handoff'/f'{model}_node27_boot'/'derived.json'),
            'FLUIDCKPT_LIFECYCLE': str(self.out/f'{label}.command.json')}
        argv = ['python3', 'codex_coding/src/serving/sglang_fluid_launch.py', '--model-path', path,
            '--host', '0.0.0.0', '--port', str(port), '--tp-size', '2', '--mem-fraction-static', '0.70',
            '--context-length', '4096', '--max-running-requests', '4', '--disable-cuda-graph',
            '--attention-backend', 'triton', '--page-size', '1', '--moe-runner-backend', 'triton',
            '--max-total-tokens', '8192', '--disable-custom-all-reduce', '--random-seed', '42']
        if model == 'A':
            argv += ['--mamba-scheduler-strategy', 'extra_buffer', '--max-mamba-cache-size', '32']
        if restore: argv += ['--load-format', 'dummy']
        started = time.monotonic()
        handle = self.start(node, phase, argv, env)
        shell = {'label': label, 'phase': phase, 'node': node, 'port': port, 'model': model,
                 'handle': handle, 'endpoint': f'http://{NODES[node]["ip"]}:{port}',
                 'workers': [0, 1] if node == 'node27' else [4, 5]}
        self.shells[label] = shell
        self.wait_exports(phase)
        if not restore:
            with ThreadPoolExecutor(2) as pool:
                captures = list(pool.map(lambda pair: request(self.endpoints[pair[1]], 'capture',
                    {'tag': phase, 'snap': model}, timeout=300), enumerate(shell['workers'])))
            for w in shell['workers']:
                request(self.endpoints[w], 'release_live', {'tag': phase})
            sources = [request(self.endpoints[w], 'adopt', {'snap': model, 'tp': rank,
                       'id': f'{model}/base/{rank}'}) for rank, w in enumerate(shell['workers'])]
            self.models[model] = {'sources': sources, 'source_ids': [s['id'] for s in sources], 'captures': captures}
        else:
            flow = self.fill(shell, phase)
        self.wait_http(shell['endpoint'], 'health', timeout=600)
        reply = self.generate_stream(shell)
        if restore:
            assert reply['text'] == self.models[model]['reference']['text']
            self.cluster.finish(flow['tx'])
        else:
            self.models[model]['reference'] = reply
        self.event('shell_ready', label=label, startup_s=time.monotonic()-started, reply=reply)
        shell['parked'] = False
        self.memory(phase)
        return shell

    def generate_stream(self, shell):
        payload = {'text': PROMPTS[0], 'sampling_params': {'temperature': 0, 'max_new_tokens': 32}, 'stream': True}
        started = time.monotonic()
        req = urllib.request.Request(shell['endpoint']+'/generate', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        first = None
        last = {}
        with urllib.request.urlopen(req, timeout=180) as response:
            for raw in response:
                if not raw.startswith(b'data: '): continue
                data = raw[6:].strip()
                if data == b'[DONE]': break
                last = json.loads(data)
                if first is None and (last.get('text') or last.get('meta_info', {}).get('completion_tokens', 0)):
                    first = time.monotonic()
        if first is None: raise RuntimeError('generation produced no token')
        return {'text': last['text'], 'ttft_s': first-started, 'first_token_time': first,
                'generation_s': time.monotonic()-started, 'tokens': last.get('meta_info', {}).get('completion_tokens')}

    def park(self, shell):
        started = time.monotonic()
        request(shell['endpoint'], 'release_memory_occupation', {'tags': ['weights']}, timeout=180)
        elapsed = time.monotonic()-started
        self.event('parked', label=shell['label'], release_s=elapsed)
        shell['parked'] = True
        self.memory(shell['label']+'_parked')
        return elapsed

    def fill(self, shell, phase):
        model = self.models[shell['model']]
        targets = [dict(self.replica_target(s, f'{phase}/{rank}', shell['workers'][rank]),
                    mode='live', tag=phase, metrics_directory=str(self.out/'metrics'/phase))
                   for rank, s in enumerate(model['sources'])]
        plan = self.cluster.plan_restore(targets, 'resource_lp', model['source_ids'])
        flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
        return flow

    def resume(self, shell, phase, mode='restore', ipc_directory=None, verify=False):
        if (self.out/'metrics'/phase).exists(): phase += '_' + str(time.time_ns())
        command = {'tag': phase, 'metrics': str(self.out/'metrics'/phase), 'mode': mode,
                   'ipc_directory': ipc_directory}
        path = self.out/f'{shell["label"]}.command.json'
        path.write_text(json.dumps(command))
        started = time.monotonic()
        flow = None
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(request, shell['endpoint'], 'resume_memory_occupation', {'tags': ['weights']}, 600)
            if mode == 'restore':
                self.wait_exports(phase)
                flow = self.fill(shell, phase)
            future.result()
        reply = self.generate_stream(shell)
        correct = reply['text'] == self.models[shell['model']]['reference']['text']
        if not correct: raise AssertionError('resumed generation differs from original')
        row = {'phase': phase, 'model': shell['model'], 'node': shell['node'], 'mode': mode,
               'first_correct_token_s': reply['first_token_time']-started, 'reply': reply,
               'timing': summarize(self.out/'metrics'/phase, 2), 'correct': correct}
        if flow:
            row['flow'] = {k: v for k, v in flow.items() if k not in ['plan', 'rpc']}
            row['routes'] = [{k: o[k] for k in ['src_w', 'dst_w', 'kind', 'n', 'replica_id']} for o in flow['plan']['ops']]
            if verify: row['verification'] = self.cluster.verify(flow['tx'])
            self.cluster.finish(flow['tx'])
        self.result['transitions'].append(row)
        shell['parked'] = False
        self.event('resumed', phase=phase, first_correct_token_s=row['first_correct_token_s'], load_s=row['timing']['load_s'])
        self.memory(phase)
        return row

    def start_ssd(self):
        ssd_node = self.args.ssd_node
        ssd_ip = '12.11.3.' + ssd_node.removeprefix('node')
        self.ssd_endpoint = f'http://{ssd_ip}:{self.base+90}'
        if self.ssd_process:
            request(self.ssd_endpoint, 'status')
            return
        if not self.args.ssd_directory:
            parent = '/data/node28/shared/zhujianian' if ssd_node == 'node28' else '/tmp'
            self.args.ssd_directory = f'{parent}/fluidckpt-{self.args.run}'
        spec = self.out/'ssd.process.json'
        spec.write_text(json.dumps({'command': ['python3', '-m', 'fluidckpt.serving.ssd_store',
            '--directory', self.args.ssd_directory, '--port', str(self.base+90)],
            'env': {'PYTHONPATH': str(ROOT/'main_lib')}, 'cwd': str(ROOT), 'log': str(self.out/'ssd.log')}))
        command = ['python3', str(ROOT/'codex_coding/src/task2/cluster/process.py'), 'start', '--spec', str(spec)]
        subprocess.check_output(['ssh', ssd_node, shlex.join(command)], text=True)
        self.ssd_process = spec
        self.wait_http(self.ssd_endpoint)
        self.result['ssd_backend'] = {'endpoint': self.ssd_endpoint, 'node': ssd_node,
            'path': self.args.ssd_directory, 'transport': 'CPU HTTP proxy with bounded chunks',
            'filesystem': subprocess.check_output(['ssh', ssd_node,
                shlex.join(['df', '-T', self.args.ssd_directory])], text=True)}

    def switch(self, previous, following, phase, mode='restore', **kw):
        started = time.monotonic()
        released = self.park(previous)
        row = self.resume(following, phase, mode, **kw)
        row['switch_to_first_token_s'] = row['reply']['first_token_time']-started
        row['previous_model'] = previous['model']
        row['release_s'] = released
        self.save()
        return row

    def migrate_a(self):
        sources = self.models['A']['sources']
        local_bytes = self.args.local_dram_gib*GIB
        locations = []
        for rank, source in enumerate(sources):
            locations += [
                {'tp': rank, 'worker': rank, 'tier': 'dram', 'node': 'node27', 'capacity_bytes': local_bytes},
                {'tp': rank, 'worker': rank+2, 'tier': 'dram', 'node': 'node28',
                 'capacity_bytes': source['n']-local_bytes-(512 << 20)},
                {'tp': rank, 'worker': rank, 'tier': 'ssd', 'node': self.args.ssd_node, 'capacity_bytes': 2*GIB,
                 'storage_node': self.args.ssd_node, 'store_endpoint': self.ssd_endpoint}]
        scenarios = [{'node': n, 'ssd_GBps': .5, 'local_GBps': 35, 'remote_GBps': 20}
                     for n in ['node27', 'node29']]
        placement = request(self.endpoints[0], 'plan_eviction',
            {'sources': [{k: s[k] for k in ['identity', 'tp', 'n']} for s in sources],
             'locations': locations, 'scenarios': scenarios})
        self.result['eviction_plan'] = placement
        self.event('eviction_planned', solve_s=placement['solve_s'], extents=len(placement['extents']))
        targets = []
        for i, extent in enumerate(placement['extents']):
            source = sources[extent['tp']]
            target = self.replica_target(source, f'A/placed/{i}', extent['worker'], extent['tier'], extent['off'], extent['n'])
            target.update({k: extent[k] for k in ['storage_node', 'store_endpoint'] if k in extent})
            targets.append(target)
        started = time.monotonic()
        flow = self.cluster.transfer(self.cluster.plan_restore(targets, 'resource_lp', self.models['A']['source_ids']))
        self.result['migration'] = {k: v for k, v in flow.items() if k not in ['plan', 'rpc']}
        for rid in self.models['A']['source_ids']: self.cluster.retire(rid)
        self.models['A']['source_ids'] = [t['id'] for t in targets]
        self.event('A_evacuated', migration_and_verification_s=time.monotonic()-started,
                   logical_bytes=sum(s['n'] for s in sources), cold_source_reads=0)
        self.memory('A_distributed_B_serving')

    def resident_ipc(self, a):
        model = self.models['A']
        targets = [self.replica_target(s, f'A/ipc/{i}', i) for i, s in enumerate(model['sources'])]
        self.cluster.transfer(self.cluster.plan_restore(targets, 'resource_lp', model['source_ids']))
        directory = str(self.out/'ipc')
        for i, target in enumerate(targets):
            request(self.endpoints[i], 'export_ipc', {'id': target['id'], 'directory': directory})
        self.resume(a, 'baseline_ipc_hit', 'ipc', directory)
        started = time.monotonic()
        self.park(a)
        for target in targets: self.cluster.retire(target['id'])
        self.event('ipc_owner_evicted', release_and_owner_retire_s=time.monotonic()-started)

    def full_trace(self, a):
        self.park(a)
        b = self.boot('B')
        self.park(b)
        remote_a = self.boot('A', 'node29', restore=True)
        self.park(remote_a)
        for i in [0, 1]:
            request(self.endpoints[i], 'reserve_service_memory', {'nbytes': self.args.reserve_gib*GIB})
        self.result['service_reserve_bytes_per_gpu'] = self.args.reserve_gib*GIB
        self.memory('bootstrap_complete')
        self.checkpoint('baseline')
        self.after_bootstrap('baseline')

    def after_bootstrap(self, stage):
        a, b, remote_a = [self.shells[label] for label in ['A_node27', 'B_node27', 'A_node29']]
        if stage == 'baseline':
            self.resident_ipc(a)
            self.resume(a, 'baseline_A_disk', 'disk')
            self.switch(a, b, 'baseline_A_to_B_disk', 'disk')
            self.switch(b, a, 'baseline_B_to_A_disk', 'disk')
            self.resume(remote_a, 'baseline_remote_A_disk', 'disk')
            self.park(remote_a)
            self.checkpoint('fluid')
        if stage in ['baseline', 'fluid']:
            if a.get('parked'): self.resume(a, 'setup_A_before_fluid')
            self.switch(a, b, 'fluid_A_to_B')
            self.result['B_idle_samples'] = [self.generate_stream(b) for _ in range(5)]
        elif b.get('parked'):
            self.resume(b, 'setup_B_before_migration')
        self.start_ssd()
        self.checkpoint('return' if stage == 'return' else 'migration')
        stopped = threading.Event()
        samples = []
        def pulse():
            while not stopped.is_set():
                try: samples.append(self.generate_stream(b))
                except Exception as exc: samples.append({'error': repr(exc)})
                stopped.wait(2)
        thread = threading.Thread(target=pulse)
        thread.start()
        try:
            if stage != 'return': self.migrate_a()
            self.checkpoint('return')
            self.resume(remote_a, 'fluid_remote_A', verify=True)
        finally:
            stopped.set(); thread.join()
            self.result['B_while_A_moves'] = samples
        self.park(remote_a)
        self.switch(b, a, 'fluid_B_to_A')
        self.switch(a, b, 'fluid_repeat_A_to_B')
        self.switch(b, a, 'fluid_repeat_B_to_A')
        self.result['ssd_statistics'] = request(self.ssd_endpoint, 'status')

    def run(self):
        try:
            if self.args.resume_stage:
                self.normalize_shells()
                self.after_bootstrap(self.resume_stage)
            else:
                self.isolated_containers()
                self.preflight()
                self.start_workers()
                for node in ['node27', 'node29']:
                    self.start(node, f'{node}_memory_monitor', ['python3',
                        'codex_coding/src/task2/cluster/monitor_memory.py', '--out', str(self.out/f'{node}_hbm.jsonl')])
                a = self.boot('A')
                self.park(a)
                self.resume(a, 'A_local_smoke', verify=self.args.smoke)
                if not self.args.smoke: self.full_trace(a)
            self.result['status'] = 'passed'
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc))
            self.event('failed', error=repr(exc))
            raise
        finally:
            self.save()
            if self.result['status'] == 'failed' and self.args.keep_on_failure:
                self.event('processes_retained_for_continuation')
            else:
                self.cleanup()

    def cleanup(self):
            for handle in list(reversed(self.processes)):
                try: self.stop(handle)
                except Exception as exc: self.result.setdefault('cleanup_errors', []).append(repr(exc))
            for node, container in self.owned_containers:
                try: self.docker_host(node, ['rm', '-f', container])
                except Exception as exc: self.result.setdefault('cleanup_errors', []).append(repr(exc))
            if self.ssd_process:
                command = ['python3', str(ROOT/'codex_coding/src/task2/cluster/process.py'), 'stop', '--spec', str(self.ssd_process)]
                subprocess.run(['ssh', self.args.ssd_node, shlex.join(command)], check=False)
            self.save()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--port-base', type=int, default=29100)
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.add_argument('--model-b', default='/data/node28/shared/models/9g_8b_thinking_llama')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--reserve-gib', type=int, default=22)
    parser.add_argument('--local-dram-gib', type=int, default=0)
    parser.add_argument('--ssd-node', choices=['node27', 'node28', 'node29', 'node30'], default='node28')
    parser.add_argument('--ssd-directory', help='An extent directory on the SSD host filesystem')
    parser.add_argument('--keep-on-failure', action='store_true')
    parser.add_argument('--resume-stage', choices=['auto', 'baseline', 'fluid', 'migration', 'return'])
    parser.set_defaults(worker_serialize=0, handoff_poll=.05, mesh='node27:0-1,node28:2-3,node29:0-1',
                        result_root=str(ROOT/'codex_coding/results/lifecycle'), development='v0.1.23.2')
    LifecycleRun(parser.parse_args()).run()
