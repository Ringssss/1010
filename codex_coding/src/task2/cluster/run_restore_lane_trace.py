"""Capture a complete GLM restore as per-rank task intervals for a Figure-4-style timeline."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import resource
import time

from run_recall_pressure_motivation import PressureRun, request


class LaneTrace(PressureRun):
    def calibrate_clocks(self):
        representatives = {}
        for i, member in enumerate(self.members):
            representatives.setdefault(member[0], i)

        def sample(pair):
            node, worker = pair
            samples = []
            for _ in range(5):
                before = time.monotonic_ns()
                reply = request(self.endpoints[worker], 'inventory?compact=1')
                after = time.monotonic_ns()
                remote = reply['headroom_sample_ns']
                samples.append({'controller_before_ns': before, 'controller_after_ns': after,
                    'remote_ns': remote, 'remote_minus_controller_ns': remote-(before+after)//2,
                    'half_rtt_ns': (after-before)//2})
            return node, {'worker': worker, 'samples': samples,
                          'best': min(samples, key=lambda r: r['half_rtt_ns'])}

        with ThreadPoolExecutor(len(representatives)) as pool:
            return dict(pool.map(sample, representatives.items()))

    def fill(self, name, phase):
        flow = super().fill(name, phase)
        self.trace_flow = flow
        return flow

    def run(self):
        self.result['protocol'] = {
            'purpose': 'per-rank readiness skew and source-path intervals during complete GLM recovery',
            'model': 'GLM-5.3 TP16', 'execution': 'serial operations per worker; resource LP',
            'x': 'seconds since controller restore request', 'y': 'TP rank / physical destination DCU',
            'colors': ['local DRAM operation', 'remote DRAM operation', 'SSD-backed operation',
                       'rank data complete while model waits'],
            'interpretation': 'operation spans include copy and synchronous waits; not GPU SM utilization',
            'clock_alignment': 'per-node monotonic offset from minimum-RTT RPC; uncertainty retained',
            'correctness': 'full SHA256 after generation and exact native-reference output',
        }
        self.result['model'] = self.args.model
        self.result['runtime_bytes'] = sum(s['n'] for s in self.models['A']['sources'])
        self.cluster.profile['serial_target_execution'] = True
        try:
            self.park('B'); self.park('A')
            self.checkpoint_lifecycle()
            self.result['clocks_before'] = self.calibrate_clocks()
            workers, replicas = self.cluster.inventory(compact=True)
            self.result['source_directory'] = [r for r in replicas if r['id'] in self.models['A']['source_ids']]
            self.result['members'] = self.members
            logs = {}
            root_state = json.loads((self.root/'session.json').read_text())
            for node, spec in root_state['processes']:
                if not Path(spec).name.startswith('worker_'): continue
                payload = json.loads(Path(spec).read_text())
                command = payload['command']; gpu = int(command[command.index('--gpu')+1])
                worker = self.members.index([node, gpu]) if [node, gpu] in self.members else self.members.index((node, gpu))
                path = Path(payload['log'])
                logs[worker] = {'path': str(path), 'start_byte': path.stat().st_size}
            if len(logs) != len(self.endpoints): raise RuntimeError('missing worker log provenance')
            calls = []
            original = self.cluster.call_all
            def timed(operation, *args, **kwargs):
                start = time.monotonic()
                result = original(operation, *args, **kwargs)
                calls.append({'operation': operation, 'begin': start, 'end': time.monotonic()})
                return result
            self.cluster.call_all = timed
            row = self.resume('A', 'lane_trace', verify=True)
            origin = row['reply']['first_token_time']-row['request_to_first_token_s']
            self.result['restore'] = row
            self.result['origin_controller_monotonic_s'] = origin
            self.result['control_intervals'] = [dict(r, begin=r['begin']-origin, end=r['end']-origin) for r in calls]
            self.result['clocks_after'] = self.calibrate_clocks()
            tx = self.trace_flow['tx']
            operations = []
            for worker, info in logs.items():
                with Path(info['path']).open('rb') as stream:
                    stream.seek(info['start_byte'])
                    lines = stream.read().decode().splitlines()
                for line in lines:
                    if not line.startswith('{'): continue
                    entry = json.loads(line)
                    if entry.get('tx') == tx and entry.get('ev') in ['cluster_op_begin', 'cluster_op_end']:
                        operations.append(dict(entry, worker=worker))
            self.result['log_provenance'] = logs
            events = {}
            for rank in range(16):
                path = self.root/'metrics'/row['phase']/f'rank{rank}.jsonl'
                events[str(rank)] = [json.loads(line) for line in path.read_text().splitlines()]
            (self.output/'rank_events.json').write_text(json.dumps(events, indent=2)+'\n')
            (self.output/'operation_events.json').write_text(json.dumps(operations)+'\n')
            (self.output/'flow.json').write_text(json.dumps(self.trace_flow)+'\n')
            source_files = [Path(__file__), Path(__file__).with_name('continue_glm_ab.py'),
                            Path(__file__).with_name('run_recall_pressure_motivation.py')]
            self.result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
            self.result['status'] = 'passed'
            self.event('lane_trace_verified', first_token_s=row['request_to_first_token_s'],
                checked_bytes=row['verification']['checked_bytes'], operation_events=len(operations))
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc))
            self.event('failed_resources_retained', error=repr(exc)); raise
        finally:
            self.checkpoint_lifecycle()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--route-result', type=Path, required=True)
    parser.add_argument('--resume-run', type=Path, required=True)
    parser.set_defaults(fractions=[1.], repeats=1, wait_timeout=10800, cpu_first_ssd=False,
                        resume_failed=False, free_pages_only=True)
    LaneTrace(parser.parse_args()).run()
