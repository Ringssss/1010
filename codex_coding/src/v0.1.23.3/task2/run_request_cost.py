"""Fit equal-byte batching and validate unseen sizes plus actual layout decisions."""
import argparse
import json
from pathlib import Path
import resource
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from run_system import SystemRun
from fluidckpt.cluster.control import request
from fluidckpt.cluster.service_cost import fit_request_model, new_state, observe_success, estimate_execution

MIB = 1 << 20


class RequestCostRun(SystemRun):
    def fixture(self):
        workers, _ = self.cluster.inventory(compact=True)
        state = new_state(workers)
        self.cluster.profile = dict(self.cluster.profile, serial_target_execution=True)
        self.result.update(samples=[], validation=[])
        self.counter = 0
        def create(owner, name, size):
            request(self.endpoints[owner], 'synth', {'snap': name, 'tp': 0, 'nbytes': size, 'seed': 28000+owner})
            return request(self.endpoints[owner], 'adopt', {'snap': name, 'tp': 0, 'id': name+'/base'})
        def copy_hbm(source, owner, name, n):
            target = self.replica_target(source, name, owner, n=n)
            self.cluster.transfer(self.cluster.plan_restore([target], 'nearest', [source['id']]))
            return next(r for r in self.cluster.inventory()[1] if r['id'] == name)
        def execute(label, plan, model=None, sample_path=None):
            prediction = estimate_execution(plan, workers, model, state) if model else None
            self.result['pending'] = {'label': label, 'prediction': prediction}
            self.save()
            flow = self.cluster.transfer(plan)
            assert flow['verification']['passed']
            observe_success(plan, workers, state)
            target_rows = [w for w in flow['execution'] if w['worker'] == 6]
            row = {'label': label, 'flow': flow, 'prediction': prediction,
                   'target_s': target_rows[0]['execute_s'],
                   'dispatch_s': max(0., flow['execute_wall_s']-target_rows[0]['execute_s'])}
            if sample_path:
                row.update(path=sample_path, nbytes=sum(o['n'] for o in plan['ops']), operations=len(plan['ops']))
            self.result['samples' if model is None else 'validation'].append(row)
            self.result.pop('pending')
            self.event('request_cost_sample', name=label, actual_s=flow['execute_wall_s'],
                       predicted_s=prediction['estimated_execute_s'] if prediction else None)
            for target in plan['targets']: self.cluster.retire(target['id'])
            return row
        def measure(source, label, size, count, model=None):
            self.counter += 1
            targets = [self.replica_target(source, f'out/{self.counter}/{i}', 6, n=size//count) for i in range(count)]
            plan = self.cluster.plan_restore(targets, 'nearest', [source['id']])
            return execute(label, plan, model, plan['ops'][0]['path'])

        if self.args.mode == 'calibrate':
            sources = [create(4, 'dram', 256*MIB)]
            sources.append(copy_hbm(create(2, 'hbm', 256*MIB), 2, 'hbm/cache', 256*MIB))
            for src in sources:
                for label, size, count in [('cold_small', 128*MIB, 1), ('warm_small', 128*MIB, 1),
                                           ('warm_batch', 128*MIB, 4), ('warm_large', 256*MIB, 1)]:
                    measure(src, label, size, count)
            model = fit_request_model(workers, self.result['samples'])
            self.result['service_model'] = model
            for i, src in enumerate(sources):
                measure(src, f'holdout192_three_{i}', 192*MIB, 3, model)
            # This mixed-source chain is not used to fit any coefficients.
            targets = [self.replica_target(sources[i], f'mixed/{k}', 6, n=64*MIB) for k, i in enumerate((0, 1, 0))]
            plan = self.cluster.plan_restore(targets, 'nearest', [s['id'] for s in sources])
            execute('holdout_mixed_three', plan, model)
        else:
            model = json.loads(Path(self.args.model_file).read_text())
            self.result['service_model'] = model
            base = create(4, 'layout', 192*MIB)
            cache = copy_hbm(base, 2, 'layout/cache', 64*MIB)
            # The build warms 4<->2, not either of the future restore pairs to worker6.
            self.cluster.profile.update(network_pairs=[[4, 2], [4, 6], [2, 6]],
                directed_path_rates={k: c['warm_GBps'] for k, c in model['paths'].items()})
            q = {'schema_version': 1, 'scope': 'request_cost_layout', 'executor': 'serial',
                 'base_ids': [base['id']], 'managed_ids': [cache['id']], 'block_bytes': 64*MIB,
                 'targets': [{'key': 'future', 'worker': 6, 'identity': base['identity'], 'tp': 0, 'off': 0, 'n': base['n']}],
                 'budget': {'tier_bytes': {'hbm': 64*MIB, 'dram': 0},
                            'locations': [{'worker': 2, 'tier': 'hbm', 'max_bytes': 64*MIB, 'reserve_free_bytes': 2 << 30}]},
                 'service_model': model, 'service_pair_policy': 'cold'}
            placement = self.cluster.plan_joint(q)
            self.result['placement'] = placement
            assert placement['solver']['status'] == 'service_reranked' and not placement['fallback_reason']
            if self.args.mode == 'selected':
                applied = self.cluster.apply_joint(placement)
                self.result['applied'] = applied
                source_ids = placement['protected_source_ids']+[r['id'] for r in placement['selected']]
            else:
                source_ids = [base['id'], cache['id']]
            for phase in ('cold', 'warm'):
                targets = [self.replica_target(base, f'{phase}/out', 6)]
                plan = self.cluster.plan_restore(targets, 'resource_lp', source_ids)
                execute(f'{self.args.mode}_{phase}', plan, model)
        self.event('request_cost_passed', cases=len(self.result['validation']))

    def run(self):
        super().run()
        if self.result['status'] == 'passed' and not self.result['cleanup_errors']:
            (self.out/'service_model.json').write_text(json.dumps(self.result['service_model'], indent=2))


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument('--run', required=True)
    parser.add_argument('--mode', choices=['calibrate', 'current', 'selected'], required=True)
    parser.add_argument('--port-base', type=int, default=18800); parser.add_argument('--model-file')
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=True, development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'), source_directories=[str(Path(__file__).parent)])
    RequestCostRun(parser.parse_args()).run()
