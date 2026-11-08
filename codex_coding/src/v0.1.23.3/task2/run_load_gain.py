"""Gate a fixed-budget chunk change on a microcheck, then measure real load windows."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import time

from run_managed import ManagedRun, ROOT, GIB
from fluidckpt.cluster.control import request
from fluidckpt.serving.measure import summarize


class LoadGainRun(ManagedRun):
    def fixture(self):
        self.cluster.profile = dict(self.cluster.profile, serial_target_execution=True)
        sources = []
        size = (2 << 30)+17 if self.args.fixture_only else (96 << 20)+17
        for g, owner in enumerate(self.source_workers):
            request(self.endpoints[owner], 'synth', {'snap': 'chunk', 'tp': g, 'nbytes': size, 'seed': 30000+g})
            sources.append(request(self.endpoints[owner], 'adopt', {'snap': 'chunk', 'tp': g, 'id': f'chunk/base/{g}'}))
        # Both variants use warm pair communicators, identical data and physical owners.
        warm = [self.replica_target(s, f'warm/{g}', self.target_workers[g], n=32 << 20) for g, s in enumerate(sources)]
        self.cluster.transfer(self.cluster.plan_restore(warm, 'resource_lp', [s['id'] for s in sources]))
        for t in warm: self.cluster.retire(t['id'])
        self.result['chunk_fixture'] = []
        for chunk in (32 << 20, 64 << 20):
            targets = [self.replica_target(s, f'chunk/{chunk}/{g}', self.target_workers[g]) for g, s in enumerate(sources)]
            plan = self.cluster.plan_restore(targets, 'resource_lp', [s['id'] for s in sources])
            plan['chunk_bytes'] = chunk
            flow = self.cluster.transfer(plan)
            assert flow['verification']['passed']
            assert all(w['staging_bytes'] == 64 << 20 for w in flow['execution'])
            self.result['chunk_fixture'].append({'chunk_bytes': chunk, 'flow': flow})
            self.event('chunk_fixture_passed', chunk_bytes=chunk, execute_s=flow['execute_wall_s'])
            for t in targets: self.cluster.retire(t['id'])
        for s in sources: request(self.endpoints[s['worker']], 'retire', {'id': s['id']})

    def real_model(self):
        gate = json.loads(Path(self.args.gate_file).read_text())
        if gate['status'] != 'passed' or gate['cleanup_errors']:
            raise ValueError('microcheck has not passed')
        times = [r['flow']['execute_wall_s'] for r in gate['chunk_fixture']]
        if times[1] >= times[0]:
            raise ValueError('no microcheck gain; do not spend full-model runs')
        self.result['gate'] = {'file': self.args.gate_file, 'serial32_s': times[0], 'serial64_s': times[1]}
        sources, reference = self.prepare_model()
        self.calibrate(sources)
        _, replicas = self.cluster.inventory(compact=True)
        base = [r['id'] for r in replicas if r['id'].startswith('model/remote/')]
        q = self.demand(sources, 'load_gain', base, [r['id'] for r in replicas if r['id'] not in base], GIB, 8*GIB, 2*GIB, reserve=40*GIB)
        placement = self.cluster.plan_joint(q)
        if placement['fallback_reason']: raise AssertionError('placement fallback')
        self.result['placement'] = placement
        self.result['placement_apply'] = self.cluster.apply_joint(placement, journal_path=self.out/'placement_journal.json')
        original_layout = self.layout()
        self.result['comparison_layout'] = original_layout
        signatures = []
        for chunk in (32 << 20, 64 << 20):
            phase = f'load_chunk{chunk >> 20}'
            assert self.layout() == original_layout
            server = self.server(self.target_node, phase, 'restore')
            self.wait_exports(phase)
            targets = [dict(self.replica_target(s, f'{phase}/{g}', self.target_workers[g]), mode='live', tag=phase,
                metrics_directory=str(self.out/'metrics'/phase)) for g, s in enumerate(sources)]
            plan = self.cluster.plan_managed_restore(placement, targets)
            if plan['strategy'] != 'resource_lp' or plan['fallback_reason']: raise AssertionError('restore LP fallback')
            plan['chunk_bytes'] = chunk
            fields = ('src_w', 'dst_w', 'tp', 'identity', 'off', 'n', 'replica_id', 'generation', 'source_offset', 'kind')
            signature = hashlib.sha256(json.dumps([[o[k] for k in fields] for o in plan['ops']]).encode()).hexdigest()
            signatures.append(signature)
            if len(set(signatures)) != 1: raise AssertionError('byte routes changed')
            flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
            self.event('weights_transferred', phase=phase, execute_s=flow['execute_wall_s'])
            replies = self.generate(self.target_node)
            assert [r['text'] for r in replies] == [r['text'] for r in reference]
            verified = self.cluster.verify(flow['tx'])
            timing = summarize(self.out/'metrics'/phase, 2)
            self.cluster.finish(flow['tx']); self.stop(server)
            assert self.layout() == original_layout
            self.result['restores'].append({'phase': phase, 'node': self.target_node, 'strategy': 'resource_lp',
                'chunk_bytes': chunk, 'byte_plan_sha256': signature, 'flow': flow, 'verification': verified,
                'timing': timing, 'replies': replies, 'passed': True})
            self.event('load_variant_passed', chunk_bytes=chunk, load_s=timing['load_s'])
        self.result['scope'] = 'one real TP2 restore per chunk size; same byte routes, warm communication, 8GiB HBM+2GiB DRAM cache and 64MiB staging per worker'


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument('--run', required=True)
    parser.add_argument('--fixture-only', action='store_true'); parser.add_argument('--gate-file')
    parser.add_argument('--port-base', type=int, default=19400)
    parser.add_argument('--network-pairs-file', required=True)
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'), source_directories=[str(Path(__file__).parent)])
    LoadGainRun(parser.parse_args()).run()
