"""One calibration session followed by a fresh held-out cold/warm validation session."""
import argparse
import json
from pathlib import Path
import resource
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from run_system import SystemRun
from fluidckpt.cluster.control import request
from fluidckpt.cluster.service_cost import fit_model, new_state, observe_success, estimate_execution

MIB = 1 << 20


class CostRun(SystemRun):
    def fixture(self):
        workers, _ = self.cluster.inventory(compact=True)
        state = new_state(workers)
        self.cluster.profile = dict(self.cluster.profile, serial_target_execution=True)
        self.result.update(samples=[], validation=[])
        sources = []
        # Independent identities allow explicit operation chains on the same target worker.
        for owner, tier in ((4, 'dram'), (2, 'hbm')):
            tag = f'cost{owner}'
            request(self.endpoints[owner], 'synth', {'snap': tag, 'tp': 0, 'nbytes': 256*MIB, 'seed': 25000+owner})
            src = request(self.endpoints[owner], 'adopt', {'snap': tag, 'tp': 0, 'id': tag+'/base'})
            if tier == 'hbm':
                target = self.replica_target(src, tag+'/hbm', owner)
                self.cluster.transfer(self.cluster.plan_restore([target], 'nearest', [src['id']]))
                src = next(r for r in self.cluster.inventory()[1] if r['id'] == target['id'])
            sources.append(src)

        def measure(label, specs, model=None):
            targets = [self.replica_target(sources[i], f'{label}/{k}', 6, n=size) for k, (i, size) in enumerate(specs)]
            plan = self.cluster.plan_restore(targets, 'nearest', [s['id'] for s in sources])
            predicted = estimate_execution(plan, workers, model, state) if model else None
            # Persist the prediction before execution; held-out data must not fit its own model.
            if predicted:
                self.result['pending_prediction'] = {'label': label, 'prediction': predicted}; self.save()
            flow = self.cluster.transfer(plan)
            assert flow['verification']['passed']
            observe_success(plan, workers, state)
            maximum = max(w['execute_s'] for w in flow['execution'])
            row = {'label': label, 'flow': flow, 'prediction': predicted,
                   'dispatch_s': max(0., flow['execute_wall_s']-maximum)}
            if len(specs) == 1:
                row.update(path=plan['ops'][0]['path'], nbytes=specs[0][1],
                    service_s=max(r['elapsed_s'] for w in flow['execution'] for r in w['records']))
            self.result['samples' if model is None else 'validation'].append(row)
            self.result.pop('pending_prediction', None)
            self.event('cost_sample', label=label, actual_s=flow['execute_wall_s'],
                       predicted_s=predicted['estimated_execute_s'] if predicted else None)
            for target in targets: self.cluster.retire(target['id'])
            return row

        if not self.args.model_file:
            for i in range(2):
                for label, size in [('cold_small', 32*MIB), ('warm_small', 32*MIB), ('warm_large', 256*MIB)]:
                    row = measure(f'{i}_{label}', [(i, size)])
                    row['label'] = label
            model = fit_model(workers, self.result['samples'])
            self.result['service_model'] = model
            # Unseen size, same warm session; these rows are excluded from fitting.
            for i in range(2): measure(f'holdout_warm128_{i}', [(i, 128*MIB)], model)
        else:
            model = json.loads(Path(self.args.model_file).read_text())
            self.result['service_model'] = model
            # New worker epochs, unseen size and a two-source dependency chain.
            measure('holdout_cold_chain64', [(0, 64*MIB), (1, 64*MIB), (0, 64*MIB)], model)
            measure('holdout_warm_chain64', [(0, 64*MIB), (1, 64*MIB), (0, 64*MIB)], model)
        self.event('cost_validation_passed', cases=len(self.result['validation']))

    def run(self):
        super().run()
        if self.result['status'] == 'passed' and not self.result['cleanup_errors']:
            (self.out/'service_model.json').write_text(json.dumps(self.result['service_model'], indent=2))


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument('--run', required=True)
    parser.add_argument('--port-base', type=int, default=18200)
    parser.add_argument('--model-file')
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=True, development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'), source_directories=[str(Path(__file__).parent)])
    CostRun(parser.parse_args()).run()
