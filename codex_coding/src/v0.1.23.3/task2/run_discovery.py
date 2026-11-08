"""Discover demand-relevant paths on disposable workers, without a loaded model."""
import argparse
import json
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from run_system import SystemRun
from fluidckpt.cluster.control import request
from fluidckpt.cluster.path_catalog import discovery_tasks, new_catalog, record_probe, admitted_profile, refresh_tasks, topology_id


class DiscoveryRun(SystemRun):
    def fixture(self):
        workers, _ = self.cluster.inventory(compact=True)
        catalog = new_catalog(workers)
        if self.args.previous_catalog:
            previous = json.loads(Path(self.args.previous_catalog).read_text())
            if previous['topology_id'] != topology_id(workers):
                raise ValueError('previous catalog topology changed; start a fresh discovery')
            catalog = previous
        # Scope is physical owners and alternative targets, never a hand-written usable-route list.
        owners = json.loads(self.args.owners)
        targets = json.loads(self.args.target_sets)
        if any(len(t) != len(owners) for t in targets):
            raise ValueError('target sets must have one destination per base rank')
        pairs = set()
        for rank, owner in enumerate(owners):
            destinations = {t[rank] for t in targets}
            for dst in destinations:
                pairs.add((owner, dst))
                for src in destinations:
                    pairs.add((src, dst))
        tasks = discovery_tasks(workers, pairs=pairs, nbytes=self.args.probe_bytes)
        tasks = refresh_tasks(catalog, tasks, max_age_s=self.args.max_age_s)
        self.result.update(path_catalog=catalog, discovery_tasks=tasks, path_samples=[])
        self.cluster.profile = dict(self.cluster.profile, serial_target_execution=True)
        for i, task in enumerate(tasks):
            prefix = f'probe{i}'
            try:
                request(self.endpoints[task['src']], 'synth', {'snap': prefix, 'tp': 0,
                        'nbytes': task['nbytes'], 'seed': 18000+i})
                base = request(self.endpoints[task['src']], 'adopt', {'snap': prefix, 'tp': 0, 'id': prefix+'/base'})
                source = base
                if task['tier'] == 'hbm':
                    cached = self.replica_target(base, prefix+'/hbm', task['src'], 'hbm')
                    self.cluster.transfer(self.cluster.plan_restore([cached], 'nearest', [base['id']]))
                    source = next(r for r in self.cluster.inventory()[1] if r['id'] == cached['id'])
                target = self.replica_target(source, prefix+'/out', task['dst'], task['dst_tier'])
                flow = self.cluster.transfer(self.cluster.plan_restore([target], 'nearest', [source['id']]))
                record_probe(catalog, task, {'passed': flow['verification']['passed'],
                    'verified_bytes': flow['verification']['checked_bytes'], 'execute_s': flow['execute_wall_s']})
                self.result['path_samples'].append({'task': task, 'flow': flow})
                for r in self.cluster.inventory()[1]:
                    request(self.endpoints[r['worker']], 'retire', {'id': r['id']})
                self.event('path_verified', path=task['path'], GBps=catalog['records'][task['path']]['GBps'])
            except Exception as exc:
                record_probe(catalog, task, {'error': repr(exc)})
                self.save()
                # Collective failure can poison the entire mesh. Stop and rebuild, never reuse it.
                raise
        profile, audit = admitted_profile(catalog, workers, self.cluster.profile, max_age_s=self.args.max_age_s)
        self.result.update(discovered_profile=profile, discovery_audit=audit)
        self.event('discovery_passed', paths=len(tasks), calibrated=len(audit['rate_paths']))

    def run(self):
        super().run()
        if self.result['status'] == 'passed' and not self.result['cleanup_errors']:
            (self.out/'path_catalog.json').write_text(json.dumps(self.result['path_catalog'], indent=2))


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--port-base', type=int, default=29500)
    parser.add_argument('--owners', default='[4,5]')
    parser.add_argument('--target-sets', default='[[2,3],[6,7]]')
    parser.add_argument('--probe-bytes', type=int, default=1 << 30)
    parser.add_argument('--previous-catalog')
    parser.add_argument('--max-age-s', type=float, default=3600)
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=True, development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'), source_directories=[str(Path(__file__).parent)])
    DiscoveryRun(parser.parse_args()).run()
