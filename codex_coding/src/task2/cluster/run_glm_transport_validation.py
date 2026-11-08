"""Validate GLM spill routes and mid-migration recall on an isolated disposable mesh."""
import argparse
import json
import resource
import time

from run_glm_roundtrip import GlmRoundtripRun, ROOT
from glm_distributed_capture import validate_spill_routes
from fluidckpt.cluster.control import request
from fluidckpt.cluster.block_migration import migrate_blocks


class TransportRun(GlmRoundtripRun):
    def __init__(self, args):
        super().__init__(args)
        self.server_port = args.port_base+300
        if args.ib_hca:
            self.env['NCCL_IB_HCA'] = args.ib_hca
        self.result['ib_hca'] = args.ib_hca or 'automatic'

    def recall_probe(self):
        snap = 'mixed_recall'
        request(self.endpoints[16], 'synth', {'snap': snap, 'tp': 0, 'nbytes': 64 << 20, 'seed': 428})
        primary = request(self.endpoints[16], 'adopt', {'snap': snap, 'tp': 0, 'id': snap+'/primary'})
        sources = [self.replica_target(primary, f'{snap}/block{i}', 16, 'dram', i*(16 << 20), 16 << 20)
                   for i in range(4)]
        self.cluster.transfer(self.cluster.plan_restore(sources, 'resource_lp', [primary['id']]))
        self.cluster.retire(primary['id'])
        actions = [{'source_id': row['id'], 'target': self.replica_target(primary, f'{snap}/moved{i}',
                     0, 'dram', row['off'], row['n'])} for i, row in enumerate(sources)]
        migration = migrate_blocks(self.cluster, actions, self.out/'migration_journal.json')
        commits = []
        recalls = []
        for committed in migration:
            commits.append(committed)
            if len(commits) == 4:
                break
            _, replicas = self.cluster.inventory(compact=True)
            mixed = [row for row in replicas if row['identity'] == primary['identity']]
            if {row['worker'] for row in mixed} != {0, 16}:
                raise AssertionError('recall must read both committed destinations and remaining sources')
            target = self.replica_target(primary, f'{snap}/recall{len(commits)}', 8, 'hbm')
            started = time.monotonic()
            flow = self.cluster.transfer(self.cluster.plan_restore([target], 'resource_lp',
                                         [row['id'] for row in mixed]))
            recalls.append({'migration_fraction': len(commits)/4,
                'elapsed_s_with_sha256': time.monotonic()-started, 'verification': flow['verification'],
                'sources': [{'worker': row['worker'], 'off': row['off'], 'n': row['n']} for row in mixed]})
            self.cluster.retire(target['id'])
            self.event('mixed_recall_verified', migration_fraction=len(commits)/4,
                       checked_bytes=flow['verification']['checked_bytes'])
        self.result['recall'] = {'commits': commits, 'recalls': recalls,
            'scope': '64MiB synthetic bytes, actual DRAM/HBM/IB transfers; not full-model service latency'}

    def run(self):
        self.result['scope'] = 'disposable transport-only mesh; no model snapshots or model performance'
        try:
            self.isolated_containers()
            self.preflight()
            self.start_workers()
            validate_spill_routes(self, self.args.spill_ranks_per_node)
            self.recall_probe()
            self.result['status'] = 'passed'
            self.event('transport_validation_passed')
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc))
            self.event('failed', error=repr(exc))
            raise
        finally:
            self.cleanup()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--spill-ranks-per-node', type=int, default=2)
    parser.add_argument('--ib-hca', default='')
    parser.add_argument('--port-base', type=int, default=30200)
    parser.set_defaults(model='', host_memory_gib=440, capture_block_mib=256, shard_reserve_gib=47,
        worker_serialize=0, handoff_poll=.1, mesh='node27:0-7,node29:0-7,node28:2-7',
        result_root=str(ROOT/'codex_coding/results/transport_validation'), development='v0.1.23.2')
    TransportRun(parser.parse_args()).run()
