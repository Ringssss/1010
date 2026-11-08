"""Run the same base-placement A/B on the available node27 GPU4/5 and node28 GPU2/3."""
import argparse
import resource
from pathlib import Path

from run_base_location import BaseLocationRun, ROOT
from run_system import NODES
from fluidckpt.cluster.control import request


class IdleBaseLocationRun(BaseLocationRun):
    source_workers = [2, 3]
    target_workers = [0, 1]
    replica_workers = [0, 1]
    target_node = 'node27'

    def __init__(self, args):
        super().__init__(args)
        self.server_port = args.server_port

    def start(self, node, name, command, env=None):
        if name.startswith('worker_') and self.args.match_hca:
            gpu = int(command[command.index('--gpu')+1])
            rank = self.members.index((node, gpu))
            env = dict(env or {}, NCCL_IB_HCA=f'=shca_{rank % 2}')
        if env and env.get('FLUIDCKPT_MODE') in ('capture', 'restore'):
            if node != 'node27': raise ValueError('unexpected inference node')
            command = list(command)+['--base-gpu-id', str(getattr(self.args, 'inference_base_gpu', 4))]
            # Keep physical ordinals identical in IPC exporters and keeper importers.
            env = dict(env, HIP_VISIBLE_DEVICES='0,1,2,3,4,5,6,7')
        return super().start(node, name, command, env)

    def fixture(self):
        super().fixture()
        self.result['validation_topology'] = {'members': self.members, 'target_gpus': [self.members[w][1] for w in self.target_workers],
            'target_node': 'node27', 'matched_hca': self.args.match_hca,
            'reason': 'original GPUs unavailable; use configured idle pairs',
            'scope': 'physical members and optional HCA binding define the comparison scope'}
        sources = []
        for rank in range(2):
            request(self.endpoints[rank], 'synth', {'snap': 'base_probe', 'tp': rank, 'nbytes': (32 << 20)+17, 'seed': 33000+rank})
            sources.append(request(self.endpoints[rank], 'adopt', {'snap': 'base_probe', 'tp': rank, 'id': f'probe/base/{rank}'}))
        admitted, flows = [], []
        for label, owners, tier in [('outbound', self.source_workers, 'dram'), ('inbound', self.target_workers, 'hbm')]:
            targets = [self.replica_target(s, f'probe/{label}/{g}', owners[g], tier) for g, s in enumerate(sources)]
            flow = self.cluster.transfer(self.cluster.plan_restore(targets, 'resource_lp', [s['id'] for s in sources]))
            assert flow['verification']['passed']
            admitted.extend([[o['src_w'], o['dst_w']] for o in flow['plan']['ops'] if o['kind'] == 'network'])
            flows.append(flow)
            _, replicas = self.cluster.inventory()
            sources = [next(r for r in replicas if r['id'] == t['id']) for t in targets]
        _, replicas = self.cluster.inventory()
        for r in replicas: request(self.endpoints[r['worker']], 'retire', {'id': r['id']})
        self.cluster.profile.update(network_pairs=admitted)
        self.result['network_path_probe'] = {'members': self.members, 'network_pairs': admitted, 'flows': flows}
        self.event('idle_routes_verified', pairs=admitted, checked_bytes=sum(f['verification']['checked_bytes'] for f in flows))


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    keep = {'node27': dict(NODES['node27'], gpus=[4, 5]), 'node28': dict(NODES['node28'], gpus=[2, 3])}
    NODES.clear(); NODES.update(keep)
    parser = argparse.ArgumentParser(); parser.add_argument('--run', required=True)
    parser.add_argument('--port-base', type=int, default=20200)
    parser.add_argument('--server-port', type=int, default=31944)
    parser.add_argument('--match-hca', action='store_true')
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=False, development='v0.1.23.3',
        mesh='node27:4-5,node28:2-3', result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'),
        source_directories=[str(Path(__file__).parent)])
    IdleBaseLocationRun(parser.parse_args()).run()
