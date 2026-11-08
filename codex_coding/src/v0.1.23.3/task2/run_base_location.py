"""Matched complete-model restores with remote versus target-local immutable bases.

The controlled setup copies one full rank before retiring its old base. Its
temporary memory and preparation time are accounted outside the load window.
This is not a bounded online base relocation implementation.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import resource
import time

from run_managed import ManagedRun, ROOT, GIB
from fluidckpt.cluster.control import request
from fluidckpt.memory.bindings import choose_bindings
from fluidckpt.serving.measure import summarize


class BaseLocationRun(ManagedRun):
    def preflight(self):
        super().preflight()
        rank_bytes = 35164859056
        demands = [{'gpu': self.members[w][1], 'nbytes': rank_bytes+GIB} for w in self.target_workers]
        decision = choose_bindings(self.probes[self.target_node]['topology'], demands)
        if any(r['numa'] != r['preferred_numa'] for r in decision['bindings']):
            raise MemoryError('target-local experiment requires sufficient memory on each nearest NUMA node')
        for row in decision['bindings']:
            self.args.worker_numas[f"{self.target_node}:{row['gpu']}"] = row['numa']
        self.result['target_base_bindings'] = decision
        self.event('target_local_capacity_passed', bindings=decision['bindings'])

    def fixture(self):
        self.cluster.profile = dict(self.cluster.profile, serial_target_execution=True)
        self.result.update(memory_samples=[], base_preparation=[], layouts={}, fixed_cache_ids=[])
        self.event('acceptance_scope', variants=['remote_base', 'local_base'],
            main_metric='max(weights_ready)-min(load_begin)',
            budget='one complete DRAM base + 2GiB local DRAM + 8GiB local HBM; 64MiB staging per worker',
            setup_policy='full-rank copy then verified retirement, with temporary peak reported')

    def memory_sample(self, label, steady=False):
        workers, replicas = self.cluster.inventory(compact=True)
        totals = {tier: sum(w['used_bytes'][tier] for w in workers) for tier in ('dram', 'hbm')}
        row = {'label': label, 'totals': totals,
               'workers': [{k: w[k] for k in ('worker', 'node', 'gpu', 'numa', 'used_bytes', 'headroom_bytes', 'hbm_allocated_bytes', 'staging_bytes')} for w in workers],
               'scope': 'catalog allocation accounting, excludes live model allocations; allocator telemetry also recorded'}
        if steady:
            expected = {'dram': sum((s['n']+4095)//4096*4096 for s in self.sources)+2*GIB, 'hbm': 8*GIB}
            assert totals == expected, (label, totals, expected)
            assert all(w['staging_bytes'] == 64 << 20 for w in workers)
        self.result['memory_samples'].append(row)
        self.save()
        return row

    def capture(self):
        digest = hashlib.sha256(); model = Path(self.args.model)
        for name in ('config.json', 'model.safetensors.index.json'):
            digest.update((model/name).read_bytes())
        for path in sorted(model.glob('*.safetensors')):
            digest.update(f'{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}'.encode())
        self.model_identity = digest.hexdigest()
        server = self.server('node27', 'capture', 'capture'); self.wait_exports('capture')
        with ThreadPoolExecutor(2) as pool:
            captures = list(pool.map(lambda g: request(self.endpoints[g], 'capture', {'tag': 'capture', 'snap': 'model'}), range(2)))
        reference = self.generate('node27')
        checks = [request(self.endpoints[g], 'verify_capture', {'tag': 'capture', 'snap': 'model', 'tp': g}) for g in range(2)]
        assert all(c['passed'] for c in checks)
        for g in range(2): request(self.endpoints[g], 'release_live', {'tag': 'capture'})
        self.stop(server)
        self.sources = self.adopt('model', 'model/base')
        self.result['capture'] = {'workers': captures, 'verification': checks, 'replies': reference, 'sources': self.sources}
        self.event('model_captured', bytes=sum(s['n'] for s in self.sources))
        self.memory_sample('captured')
        return reference

    def relocate(self, old_ids, owners, prefix):
        new_ids = []
        for rank, old_id in enumerate(old_ids):
            _, replicas = self.cluster.inventory()
            src = next(r for r in replicas if r['id'] == old_id)
            target = self.replica_target(src, f'{prefix}/{rank}', owners[rank], 'dram')
            stamp = time.monotonic()
            # Use only the complete old base so setup accounting is explicit and reproducible.
            plan = self.cluster.plan_restore([target], 'resource_lp', [old_id])
            flow = self.cluster.transfer(plan)
            assert flow['verification']['passed'] and flow['verification']['checked_bytes'] == src['n']
            peak = self.memory_sample(f'{prefix}/rank{rank}/before_retire')
            self.cluster.retire(old_id)
            elapsed = time.monotonic()-stamp
            self.result['base_preparation'].append({'prefix': prefix, 'rank': rank, 'wall_s': elapsed,
                'flow': flow, 'peak_catalog_bytes': peak['totals'], 'retired': old_id})
            new_ids.append(target['id'])
            self.event('base_rank_placed', destination=owners[rank], rank=rank, wall_s=elapsed,
                       execute_s=flow['execute_wall_s'], verified_bytes=src['n'])
        return new_ids

    def caches(self, base_ids):
        _, replicas = self.cluster.inventory()
        bases = [next(r for r in replicas if r['id'] == rid) for rid in base_ids]
        stamp = time.monotonic()
        for tier, offset, n in (('hbm', 0, 4*GIB), ('dram', 4*GIB, GIB)):
            targets = [self.replica_target(s, f'fixed/{tier}/{g}', self.target_workers[g], tier, offset, n) for g, s in enumerate(bases)]
            flow = self.cluster.transfer(self.cluster.plan_restore(targets, 'resource_lp', base_ids))
            assert flow['verification']['passed']
            self.result.setdefault('cache_build', []).append(flow)
            self.result['fixed_cache_ids'].extend(t['id'] for t in targets)
        self.result['cache_build_wall_s'] = time.monotonic()-stamp
        self.event('fixed_caches_ready', hbm_bytes=8*GIB, dram_bytes=2*GIB)

    def warm(self, base_ids, variant):
        _, replicas = self.cluster.inventory()
        bases = [next(r for r in replicas if r['id'] == rid) for rid in base_ids]
        targets = [self.replica_target(s, f'warm/{variant}/{g}', self.target_workers[g], n=32 << 20) for g, s in enumerate(bases)]
        stamp = time.monotonic()
        flow = self.cluster.transfer(self.cluster.plan_restore(targets, 'resource_lp', base_ids))
        for t in targets: self.cluster.retire(t['id'])
        self.result.setdefault('warmups', []).append({'variant': variant, 'flow': flow, 'wall_s': time.monotonic()-stamp})

    def restore(self, variant, base_ids, reference):
        self.warm(base_ids, variant)
        before = self.memory_sample(variant+'/steady', steady=True)
        layout = self.layout(); self.result['layouts'][variant] = layout
        phase = 'load_'+variant
        targets = [dict(self.replica_target(s, f'{phase}/{g}', self.target_workers[g]), mode='live', tag=phase,
                       metrics_directory=str(self.out/'metrics'/phase)) for g, s in enumerate(self.sources)]
        # This A/B fixes all physical replicas. No cache optimization is performed between variants.
        q = {'schema_version': 1, 'scope': variant, 'executor': 'serial', 'base_ids': base_ids,
             'managed_ids': [], 'targets': targets,
             'budget': {'tier_bytes': {'hbm': 0, 'dram': 0}, 'locations': []}}
        placement = self.cluster.plan_joint(q)
        assert not placement['transition']['actions'] and not placement['fallback_reason']
        self.cluster.apply_joint(placement)
        ticket = self.cluster.preplan_managed_restore(placement, targets)
        self.result.setdefault('preplans', {})[variant] = {'ticket': ticket, 'placement': placement}
        self.event('restore_preplanned', variant=variant, planning_s=ticket['planning_s'])
        server = self.server(self.target_node, phase, 'restore'); self.wait_exports(phase)
        flow = self.cluster.transfer_preplanned_restore(ticket['token'], verify_before_ready=False, retain=True)
        path_bytes = {}
        for op in flow['plan']['ops']: path_bytes[op['kind']] = path_bytes.get(op['kind'], 0)+op['n']
        assert path_bytes.get('local_hbm') == 8*GIB
        if variant == 'remote_base':
            assert path_bytes.get('network') == sum(s['n'] for s in self.sources)-10*GIB
        else:
            assert path_bytes.get('network', 0) == 0
        self.event('weights_transferred', variant=variant, execute_s=flow['execute_wall_s'], path_bytes=path_bytes)
        replies = self.generate(self.target_node)
        assert [r['text'] for r in replies] == [r['text'] for r in reference]
        verified = self.cluster.verify(flow['tx'])
        timing = summarize(self.out/'metrics'/phase, 2)
        self.cluster.finish(flow['tx']); self.stop(server)
        assert self.layout() == layout
        self.memory_sample(variant+'/after_restore', steady=True)
        self.result['restores'].append({'phase': phase, 'variant': variant, 'node': self.target_node,
            'strategy': 'resource_lp', 'flow': flow, 'verification': verified, 'timing': timing,
            'replies': replies, 'passed': True, 'steady_bytes': before['totals'],
            'path_bytes': path_bytes, 'preplanning_s': ticket['planning_s']})
        self.event('load_variant_passed', variant=variant, load_s=timing['load_s'])

    def real_model(self):
        reference = self.capture()
        remote = self.relocate([s['id'] for s in self.sources], self.source_workers, 'base/remote')
        self.caches(remote)
        self.restore('remote_base', remote, reference)
        local = self.relocate(remote, self.target_workers, 'base/local')
        self.restore('local_base', local, reference)
        self.result['scope'] = 'same 67.5GiB DRAM + 8GiB HBM steady allocation; same model, targets, caches and serial32 executor; only full-base owner changes'


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument('--run', required=True)
    parser.add_argument('--port-base', type=int, default=19800)
    parser.add_argument('--network-pairs-file', required=True)
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=False, development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'), source_directories=[str(Path(__file__).parent)])
    BaseLocationRun(parser.parse_args()).run()
