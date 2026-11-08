"""Same complete model and routes; compare in-window planning against a one-shot ticket."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import time

from run_managed import ManagedRun, ROOT, GIB
from fluidckpt.serving.measure import summarize


class PreplanRun(ManagedRun):
    def fixture(self):
        # Already verified managed failure recovery and transport in earlier versioned runs.
        self.cluster.profile = dict(self.cluster.profile, serial_target_execution=True)
        self.event('acceptance_scope', main_metric='max(weights_ready)-min(load_begin)', variants=['baseline', 'preplanned'])

    def real_model(self):
        sources, reference = self.prepare_model()
        self.calibrate(sources)
        _, replicas = self.cluster.inventory(compact=True)
        base = [r['id'] for r in replicas if r['id'].startswith('model/remote/')]
        q = self.demand(sources, 'preplan_gain', base, [r['id'] for r in replicas if r['id'] not in base], GIB, 8*GIB, 2*GIB, reserve=40*GIB)
        placement = self.cluster.plan_joint(q)
        assert not placement['fallback_reason']
        self.result['placement'] = placement
        self.result['placement_apply'] = self.cluster.apply_joint(placement, journal_path=self.out/'placement_journal.json')
        layout = self.layout(); self.result['comparison_layout'] = layout
        signatures = []
        for variant in ('baseline', 'preplanned'):
            phase = 'load_'+variant
            assert self.layout() == layout
            targets = [dict(self.replica_target(s, f'{phase}/{g}', self.target_workers[g]), mode='live', tag=phase,
                metrics_directory=str(self.out/'metrics'/phase)) for g, s in enumerate(sources)]
            ticket = None
            if variant == 'preplanned':
                ticket = self.cluster.preplan_managed_restore(placement, targets)
                self.result['preplan_ticket'] = ticket
                self.event('restore_preplanned', planning_s=ticket['planning_s'])
            server = self.server(self.target_node, phase, 'restore')
            self.wait_exports(phase)
            began = time.monotonic()
            if ticket:
                flow = self.cluster.transfer_preplanned_restore(ticket['token'], verify_before_ready=False, retain=True)
            else:
                plan = self.cluster.plan_managed_restore(placement, targets)
                flow = self.cluster.transfer(plan, verify_before_ready=False, retain=True)
            elapsed = time.monotonic()-began
            plan = flow['plan']
            assert plan['strategy'] == 'resource_lp' and not plan['fallback_reason']
            fields = ('src_w', 'dst_w', 'tp', 'identity', 'off', 'n', 'replica_id', 'generation', 'source_offset', 'kind')
            signature = hashlib.sha256(json.dumps([[o[k] for k in fields] for o in plan['ops']]).encode()).hexdigest()
            signatures.append(signature)
            assert len(set(signatures)) == 1, 'routes changed'
            self.event('weights_transferred', phase=phase, execute_s=flow['execute_wall_s'], dispatch_to_ready_s=elapsed)
            replies = self.generate(self.target_node)
            assert [r['text'] for r in replies] == [r['text'] for r in reference]
            verified = self.cluster.verify(flow['tx'])
            timing = summarize(self.out/'metrics'/phase, 2)
            self.cluster.finish(flow['tx']); self.stop(server)
            assert self.layout() == layout
            self.result['restores'].append({'phase': phase, 'node': self.target_node, 'strategy': 'resource_lp',
                'variant': variant, 'chunk_bytes': 32 << 20, 'byte_plan_sha256': signature, 'flow': flow,
                'verification': verified, 'timing': timing, 'replies': replies, 'passed': True,
                'preplanning_s': ticket['planning_s'] if ticket else 0, 'dispatch_to_ready_s': elapsed})
            self.event('load_variant_passed', variant=variant, load_s=timing['load_s'])
        self.result['scope'] = 'same Qwen TP2 model, layout, routes, 32MiB chunks, warm pairs and memory budget; one run per planning placement in time'


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument('--run', required=True)
    parser.add_argument('--port-base', type=int, default=19600)
    parser.add_argument('--network-pairs-file', required=True)
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=False, development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'), source_directories=[str(Path(__file__).parent)])
    PreplanRun(parser.parse_args()).run()
