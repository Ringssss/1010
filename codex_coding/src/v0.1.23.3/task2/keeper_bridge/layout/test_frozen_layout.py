"""CPU behavior checks for copy fairness and runtime incarnation rebinding."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_frozen_layout as driver
from fluidckpt.benchmarks.frozen_layout import (bind_real_cache, compare_placement, layout_ledger,
                                               read_frozen, strong_simple)
from fluidckpt.benchmarks.topology_cases import allocated
from fluidckpt.cluster.planning import validate_plan


class FrozenTests(unittest.TestCase):
    def setUp(self):
        self.case, self.profile, self.registered = read_frozen(
            driver.DEFAULT_MANIFEST, 'T2_uneven_budget', driver.ROOT, driver.DEFAULT_SESSION)
        self.selected, _ = driver.selection(self.case, self.profile, 'split_rank_prefix')

    def actual(self):
        workers = copy.deepcopy(self.case['workers'])
        for w in workers:
            w['epoch'] = 'current/' + str(w['worker'])
        ids = ['actual/cache/' + str(i) for i in range(len(self.selected))]
        actual = [dict(c, id=rid, epoch='current/' + str(c['worker']), generation=7,
                       preview_only=False, allocated_bytes=allocated(c)) for c, rid in zip(self.selected, ids)]
        return workers, ids, actual

    def test_duplicate_content_charged_for_each_copy_and_each_build(self):
        row = layout_ledger(self.case, self.selected)
        self.assertEqual(row['allocated_hbm_bytes'], 8 << 30)
        self.assertEqual(row['unique_logical_bytes'], 6 << 30)
        self.assertEqual(row['duplicated_logical_bytes'], 2 << 30)
        self.assertEqual(row['cache_build_logical_bytes'], 8 << 30)
        self.assertEqual(row['location_bytes'], {'0:hbm': 6 << 30, '1:hbm': 2 << 30, '2:hbm': 0, '3:hbm': 0})

    def test_location_cap_checked_even_with_legal_global_bytes(self):
        selected = [c for c in self.case['candidates'] if c['worker'] == 0][:4]
        self.assertEqual(sum(allocated(c) for c in selected), 8 << 30)
        with self.assertRaises(ValueError):
            layout_ledger(self.case, selected)

    def test_unknown_or_duplicate_candidates_rejected(self):
        for selected in (self.selected + [self.selected[0]],
                         [dict(self.selected[0], n=4097)]):
            with self.assertRaises(ValueError):
                layout_ledger(self.case, selected)

    def test_runtime_binding_uses_current_actual_ids_epochs_generations(self):
        workers, ids, actual = self.actual()
        bound = bind_real_cache(self.case, self.selected, actual, workers, ids)
        self.assertEqual([r['id'] for r in bound], ids)
        self.assertTrue(all(r['generation'] == 7 and r['epoch'].startswith('current/') for r in bound))
        self.assertFalse(any(r['id'].startswith('preview/') for r in bound))

    def test_runtime_binding_rejects_stale_or_hypothetical_sources(self):
        workers, ids, actual = self.actual()
        for change in ({'preview_only': True}, {'epoch': 'old'}, {'generation': 0},
                       {'leases': 1}, {'allocated_bytes': (2 << 30) + 4096}, {'off': 4096}, {'state': 'RELEASED'}):
            with self.subTest(change=change):
                bad = copy.deepcopy(actual)
                bad[0].update(change)
                with self.assertRaises(ValueError):
                    bind_real_cache(self.case, self.selected, bad, workers, ids)
        with self.assertRaises(ValueError):
            bind_real_cache(self.case, self.selected, actual[:-1], workers, ids)

    def test_pairing_crosses_max_rank_zero_singleton_gain(self):
        case = copy.deepcopy(self.case)
        case['restore_scenarios'] = [case['restore_scenarios'][0]]
        for b in case['base']:
            b.update(n=2 << 30, allocated_bytes=2 << 30)
        for t in case['restore_scenarios'][0]['targets']:
            t['n'] = 2 << 30
        case['candidates'] = [next(c for c in case['candidates'] if c['tp'] == tp and c['worker'] == tp and c['off'] == 0)
                              for tp in (0, 1)]
        case['budget']['tier_budgets']['hbm'] = 4 << 30
        case['budget']['location_budgets'] = {f'{w}:hbm': (2 << 30) if w < 2 else 0 for w in range(4)}
        profile = copy.deepcopy(self.profile)
        for key in ('dram_GBps', 'gpu_ingress_GBps', 'gpu_egress_GBps', 'network_node_GBps'):
            profile[key] = 100000
        result = strong_simple(case, profile)
        self.assertEqual(result['controls']['marginal_resource_lp']['selected'], [])
        self.assertEqual(len(result['controls']['paired_resource_lp']['selected']), 2)
        self.assertEqual(len(result['selected']), 2)

    def test_simple_joint_same_candidate_budget_weights_and_range(self):
        result = compare_placement(self.case, self.profile)
        self.assertEqual(result['scenario_weights'], {'target_a': 0.5, 'target_b': 0.5})
        self.assertEqual(result['candidate_ids'], [c['id'] for c in self.case['candidates']])
        for policy in result['policies'].values():
            self.assertEqual(policy['ledger'], layout_ledger(self.case, policy['selected']))
            for plan in policy['evaluation']['plans'].values():
                validate_plan(plan)
                self.assertEqual(plan['shared_schedule'], 'rank_interleaved')
                self.assertEqual(sum(o['n'] for o in plan['ops']), sum(t['n'] for t in plan['targets']))
                self.assertEqual(sum(allocated(t) for t in plan['targets']), self.case['budget']['target_allocation_bytes'])
        self.assertTrue(result['decisions_equal'])
        self.assertEqual(result['new_GPU_samples'], 0)
        self.assertEqual(result['decision_aliases'][0]['action'], 'do_not_repeat_GPU')

    def test_build_creates_real_owner_targets_from_base_only(self):
        runner = driver.FrozenLayoutRun.__new__(driver.FrozenLayoutRun)
        runner.frozen, runner.selected = self.case, self.selected
        runner.ledger = layout_ledger(self.case, self.selected)
        runner.prefix, runner.owned = 'CPU-test-owned', []
        runner.model = driver.load_state(driver.DEFAULT_SESSION)['models']['B']
        runner.result = dict(builds=[], setup_ledger=copy.deepcopy(runner.ledger), memory_samples=[
            dict(workers=[dict(staging_bytes=4096) for _ in range(4)])])
        runner.save = lambda: None
        calls = []
        class Cluster:
            def plan_restore(self, targets, strategy, sources):
                calls.append((targets, strategy, sources))
                return dict(strategy=strategy, targets=targets)
            def transfer(self, plan):
                return dict(plan=plan, verification=dict(passed=True, checked_bytes=plan['targets'][0]['n']))
        runner.cluster = Cluster()
        def memory(label):
            workers, _, actual = self.actual()
            for r, rid in zip(actual, runner.owned):
                r['id'] = rid
            return workers, actual
        runner.memory = memory
        runner.build_cache()
        self.assertEqual(len(calls), 4)
        self.assertEqual(sum(c[0][0]['n'] for c in calls), 8 << 30)
        self.assertTrue(all(c[2] == runner.model['source_ids'] for c in calls))
        self.assertTrue(all(c[0][0]['id'].startswith('CPU-test-owned/') for c in calls))
        self.assertEqual(len(runner.result['builds'][0]['candidate_runtime_bindings']), 4)

    def test_registration_has_no_inventory_transfer_or_http(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'layout_cpu'
            out.mkdir()
            args = argparse.Namespace(out=out, session=driver.DEFAULT_SESSION, manifest=driver.DEFAULT_MANIFEST,
                protect_session=driver.DEFAULT_PROTECT, case='T2_uneven_budget', layout='split_rank_prefix',
                scene='S4', methods=['fastest'], source_slot='target_a', target_slot='target_b', repetitions=1,
                guard_mib=2048, allocation_tolerance_mib=32, physical_tolerance_mib=256)
            with (patch('run_scenarios.new_run', return_value=out),
                  patch('run_scenarios.Cluster') as cluster,
                  patch('run_scenarios.http') as http_call):
                cluster.return_value.profile = self.profile
                runner = driver.FrozenLayoutRun(args, self.case, self.profile, self.registered, self.selected)
            http_call.assert_not_called()
            cluster.return_value.inventory.assert_not_called()
            cluster.return_value.transfer.assert_not_called()
            self.assertEqual(runner.schedule, 'rank_interleaved')
            self.assertEqual(runner.result['mutually_exclusive_target'], 'target_b')
            self.assertIsNone(runner.source_slot)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = driver.new_out(args.out)
    with (out / 'unittest.log').open('w') as stream:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(FrozenTests)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    driver.write(out / 'summary.json', dict(status='passed' if result.wasSuccessful() else 'failed',
        tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
        new_GPU_samples=0, measurement_status='not_measured', log=str(out / 'unittest.log')))
    print(json.dumps(dict(status='passed' if result.wasSuccessful() else 'failed', tests_run=result.testsRun, out=str(out))))
    raise SystemExit(0 if result.wasSuccessful() else 1)
