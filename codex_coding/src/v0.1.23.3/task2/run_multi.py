"""Exercise one shared layout, alternative GPU targets, amortization and explicit feedback."""
import argparse
import copy
import json
from pathlib import Path
import resource
import time

from run_managed import ManagedRun, ROOT, GIB
from fluidckpt.cluster.control import request
from fluidckpt.cluster.path_catalog import admitted_profile
from fluidckpt.cluster.managed import state_hash


class MultiRun(ManagedRun):
    def fixture(self):
        workers, _ = self.cluster.inventory(compact=True)
        catalog = json.loads(Path(self.args.path_catalog).read_text())
        self.cluster.profile, audit = admitted_profile(catalog, workers,
            dict(self.cluster.profile, serial_target_execution=True), max_age_s=3600)
        self.result['path_admission'] = audit
        sources = []
        for rank, owner in enumerate(self.source_workers):
            request(self.endpoints[owner], 'synth', {'snap': 'multi', 'tp': rank, 'nbytes': (96 << 20)+17, 'seed': 21000+rank})
            sources.append(request(self.endpoints[owner], 'adopt', {'snap': 'multi', 'tp': rank, 'id': f'multi/base/{rank}'}))
        q = self.demand(sources, 'multi', [s['id'] for s in sources], [], 32 << 20, 64 << 20, 0)
        q['minimum_gain_s'] = 1e-7
        q['scenarios'] = [{'name': name, 'weight': weight, 'targets': [dict(t, worker=dst[g]) for g, t in enumerate(q['targets'])]}
                          for name, weight, dst in [('node29', .7, [6, 7]), ('node27', .3, [2, 3])]]
        q['scenario_mode'] = 'exclusive'; q['objective'] = 'expected'
        q['migration_policy'] = {'expected_restores': 0, 'uncertainty_s': 0., 'minimum_net_s': 0.,
            'build_fixed_s': .01, 'retire_fixed_s': .001, 'verify_GBps': 1., 'cooldown_s': 0.,
            'since_last_change_s': 100., 'provenance': 'conservative fixture cost assumptions; not a production calibration'}
        low = self.cluster.plan_joint(q)
        assert low['decision'] == 'keep_current' and not low['transition']['actions']
        q['migration_policy']['expected_restores'] = 10000
        plan = self.cluster.plan_joint(q)
        assert plan['decision'] == 'replace' and plan['economics']['allowed'] and not plan['fallback_reason']
        initial_plan = copy.deepcopy(plan)
        applied = self.cluster.apply_joint(plan, journal_path=self.out/'multi_journal.json')
        restored = []
        for scenario in q['scenarios']:
            targets = [self.replica_target(s, f"out/{scenario['name']}/{g}", scenario['targets'][g]['worker']) for g, s in enumerate(sources)]
            restore = self.cluster.plan_managed_restore(plan, targets, scenario=scenario['name'])
            flow = self.cluster.transfer(restore)
            assert flow['verification']['passed']
            restored.append({'scenario': scenario['name'], 'flow': flow})
            # Test-only outputs are not caches; remove their tombstones by replanning before the next request.
            for target in targets:
                self.cluster.retire(target['id'])
            q['managed_ids'] = [r['id'] for r in plan['selected']]
            plan = self.cluster.plan_joint(q)
            assert plan['decision'] == 'keep_current'
            self.cluster.apply_joint(plan)
        prior_plan = plan
        request(self.endpoints[4], 'synth', {'snap': 'feedback', 'tp': 0, 'nbytes': GIB, 'seed': 22000})
        probe = request(self.endpoints[4], 'adopt', {'snap': 'feedback', 'tp': 0, 'id': 'feedback/base'})
        target = self.replica_target(probe, 'feedback/out', 6)
        measured = self.cluster.transfer(self.cluster.plan_restore([target], 'resource_lp', [probe['id']]))
        assert measured['verification']['passed']
        self.cluster.retire(target['id'])
        request(self.endpoints[4], 'retire', {'id': probe['id']})
        observation = {'kind': 'isolated_path', 'path': measured['plan']['ops'][0]['path'], 'nbytes': GIB,
            'execute_s': measured['execute_wall_s'], 'verified': True, 'executor': 'serial', 'measured_at': time.time(),
            'profile_id': self.cluster.profile['feedback_context_id']}
        feedback = self.cluster.record_path_feedback(observation)
        rejected = False
        try:
            self.cluster.apply_joint(prior_plan)
        except ValueError:
            rejected = True
        assert rejected
        replanned = self.cluster.plan_joint(q)
        self.cluster.apply_joint(replanned)
        targets = [self.replica_target(s, f'feedback/restored/{g}', self.target_workers[g]) for g, s in enumerate(sources)]
        feedback_restore = self.cluster.transfer(self.cluster.plan_managed_restore(replanned, targets, scenario='node29'))
        assert feedback_restore['verification']['passed']
        self.result['multi_acceptance'] = {'low_reuse_plan': low, 'initial_plan': initial_plan,
            'applied': applied, 'restores': restored, 'feedback': feedback, 'feedback_measurement': measured,
            'feedback_restore': feedback_restore, 'old_profile_rejected': rejected, 'replanned': replanned}
        self.event('multi_passed', scenarios=len(restored), verified_bytes=sum(r['flow']['verification']['checked_bytes'] for r in restored))


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--path-catalog', required=True)
    parser.add_argument('--port-base', type=int, default=29700)
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=True, development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2'), source_directories=[str(Path(__file__).parent)])
    MultiRun(parser.parse_args()).run()
