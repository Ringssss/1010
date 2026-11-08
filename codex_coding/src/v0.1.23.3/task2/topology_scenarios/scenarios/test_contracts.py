#!/usr/bin/env python3
"""CPU-only contract rejection tests using the exact protected input snapshots."""
import argparse
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).parent
ROOT = HERE.parents[5]
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.benchmarks.topology_cases import calibration_gate, preview_case, validate_case
from fluidckpt.cluster.planning import validate_plan

spec = importlib.util.spec_from_file_location('freeze_case', HERE/'freeze_case.py')
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = json.loads(freeze.SESSION.read_text())
        cls.preflight = json.loads(freeze.PREFLIGHT.read_text())
        cls.session_sha = freeze.digest(freeze.SESSION)
        cls.cases = freeze.make_cases(cls.session, cls.preflight, cls.session_sha,
                                     json.loads(freeze.FORMAT.read_text())['format_contract'])

    def case(self, index=0):
        return copy.deepcopy(self.cases[index])

    def test_three_classes_share_complete_base_and_global_budget(self):
        self.assertEqual(len(self.cases), 3)
        for c in self.cases:
            self.assertTrue(validate_case(c)['passed'])
            self.assertEqual(c['base'], self.cases[0]['base'])
            self.assertEqual(c['format_contract'], self.cases[0]['format_contract'])
            self.assertEqual(c['budget']['tier_budgets']['hbm'], 8 << 30)
            self.assertTrue(all(x['preview_only'] for x in c['candidates']))

    def test_mutually_exclusive_not_concurrent(self):
        c = self.case(1)
        self.assertEqual([s['weight'] for s in c['restore_scenarios']], [.5,.5])
        c['scenario_semantics'] = 'concurrent_restores'
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            validate_case(c)

    def test_every_future_target_requires_full_TP(self):
        c = self.case(1)
        c['restore_scenarios'][1]['targets'].pop()
        with self.assertRaisesRegex(ValueError, 'all TP ranks'):
            validate_case(c)

    def test_rank_identity_or_extent_cannot_change(self):
        for field, value in [('identity', 'wrong-model'), ('n', 4096)]:
            c = self.case()
            c['restore_scenarios'][0]['targets'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'identity/layout'):
                validate_case(c)

    def test_full_base_charge_cannot_hide_or_double_count(self):
        for delta in (-4096,4096):
            c = self.case()
            c['budget']['base_dram_bytes'] += delta
            with self.assertRaisesRegex(ValueError, 'exactly once'):
                validate_case(c)

    def test_location_cap_including_zero_required(self):
        c = self.case()
        del c['budget']['location_budgets']['3:hbm']
        with self.assertRaisesRegex(ValueError, 'explicit extra HBM'):
            validate_case(c)

    def test_global_and_location_caps_are_independent(self):
        c = self.case()
        c['budget']['tier_budgets']['hbm'] = 6 << 30
        with self.assertRaisesRegex(ValueError, 'global extra'):
            validate_case(c)
        c = self.case()
        c['budget']['location_budgets']['0:hbm'] = 2 << 30
        with self.assertRaisesRegex(ValueError, 'per-location'):
            validate_case(c)

    def test_candidate_must_be_buildable_hypothesis(self):
        for change in ({'preview_only':False}, {'identity':'other'}, {'off':1 << 50}):
            c = self.case()
            c['candidates'][0].update(change)
            with self.assertRaisesRegex(ValueError, 'hypothetical'):
                validate_case(c)

    def test_unequal_caps_never_report_physical_scarcity(self):
        c = self.case(1)
        self.assertEqual(c['budget']['location_budgets'],
                         {'0:hbm':6 << 30,'1:hbm':2 << 30,'2:hbm':0,'3:hbm':0})
        self.assertFalse(c['budget']['physical_free_memory_is_budget'])
        c['budget']['budget_origin'] = 'physical_capacity'
        with self.assertRaisesRegex(ValueError, 'budget origin'):
            validate_case(c)

    def test_common_executor_and_verification_contract(self):
        for field, value in [('schedule','target_major'), ('verification_position','inside_restore')]:
            c = self.case()
            c['execution_contract'][field] = value
            with self.assertRaises(ValueError):
                validate_case(c)

    def test_preflight_rejects_old_session_and_endpoint_mapping(self):
        with self.assertRaisesRegex(ValueError, 'exact owned overlay'):
            freeze.make_cases(self.session, self.preflight, 'old', self.cases[0]['format_contract'])
        p = copy.deepcopy(self.preflight)
        p['endpoint_probes'][0]['inventory']['gpu'] = 3
        with self.assertRaisesRegex(ValueError, 'mapping'):
            freeze.make_cases(self.session, p, self.session_sha, self.cases[0]['format_contract'])

    def test_new_readonly_inventory_is_bound_to_owned_session(self):
        inventory=json.loads((freeze.RESULTS/'topology_scenarios/calibration_inventory_r01/summary.json').read_text())
        adapted=freeze.inventory_preflight(inventory,self.session,self.session_sha)
        cases=freeze.make_cases(self.session,adapted,self.session_sha,self.cases[0]['format_contract'])
        self.assertEqual(cases[0]['base'],self.cases[0]['base'])
        self.assertEqual([w['numa'] for w in cases[0]['workers']],[0,3,0,3])
        inventory['session_overlay_sha256']='old'
        with self.assertRaisesRegex(ValueError,'admission/session/protection'):
            freeze.inventory_preflight(inventory,self.session,self.session_sha)

    def test_latest_raw_postflight_requires_passed_calibration_binding(self):
        directory=freeze.RESULTS/'topology_scenarios/calibration_probe_r01'
        after=json.loads((directory/'inventory_after.json').read_text())
        before=json.loads((directory/'inventory_before.json').read_text())
        calibration=json.loads((directory/'summary.json').read_text())
        adapted=freeze.inventory_preflight(after,self.session,self.session_sha,calibration,before)
        self.assertEqual(adapted['session_sha256'],self.session_sha)
        with self.assertRaisesRegex(ValueError,'admission/session/protection'):
            freeze.inventory_preflight(after,self.session,self.session_sha)
        calibration['status']='failed'
        with self.assertRaisesRegex(ValueError,'admission/session/protection'):
            freeze.inventory_preflight(after,self.session,self.session_sha,calibration,before)

    def test_calibration_stale_or_microprobe_never_qualifies_serial_contention(self):
        c = self.case(2)
        self.assertFalse(calibration_gate(None,c,self.session_sha)['physical_shared_case_qualified'])
        calibration = dict(schema_version='topology_calibration.v1', topology_id=c['topology_id'],
                           session_overlay_sha256=self.session_sha, capacity_is_physical_upper_bound=False,
                           status='passed',resource_admission={'status':'passed'},
                           before_after_preservation={'status':'passed'},cleanup={'status':'passed'},
                           code_preservation={'status':'passed'},input_preservation={'status':'passed'},
                           shared_resource=dict(gate_status='qualified', application_executor='rccl_microprobe'))
        gate = calibration_gate(calibration,c,self.session_sha)
        self.assertFalse(gate['physical_shared_case_qualified'])
        self.assertIn('serial_application_shared_contention_unverified', gate['reasons'])
        calibration['session_overlay_sha256'] = 'old'
        self.assertIn('calibration_session_mismatch', calibration_gate(calibration,c,self.session_sha)['reasons'])

    def test_incomplete_profile_cannot_silently_use_provisional_rates(self):
        c = self.case()
        calibration = dict(schema_version='topology_calibration.v1', topology_id=c['topology_id'],
                           session_overlay_sha256=self.session_sha, capacity_is_physical_upper_bound=False,
                           status='passed',resource_admission={'status':'passed'},
                           before_after_preservation={'status':'passed'},cleanup={'status':'passed'},
                           code_preservation={'status':'passed'},input_preservation={'status':'passed'},
                           application_executor_compatible=True,
                           application_profile=dict(executor='serial',measurement_status='verified',profile={}))
        gate = calibration_gate(calibration,c,self.session_sha)
        self.assertFalse(gate['application_profile_qualified'])
        self.assertIn('application_profile_missing_explicit_path_evidence',gate['reasons'])

    def test_failed_calibration_or_preservation_is_fail_closed(self):
        c = self.case(2)
        calibration = dict(schema_version='topology_calibration.v1',topology_id=c['topology_id'],
                           session_overlay_sha256=self.session_sha,capacity_is_physical_upper_bound=False,
                           status='failed', resource_admission={'status':'passed'},
                           before_after_preservation={'status':'passed'},cleanup={'status':'passed'},
                           code_preservation={'status':'passed'},input_preservation={'status':'passed'},
                           shared_resource=dict(gate_status='qualified',application_executor='serial',
                                                application_executor_contention_verified=True,
                                                application_evidence_refs=['fixture'],application_executor_code_sha256='fixture'))
        self.assertFalse(calibration_gate(calibration,c,self.session_sha)['physical_shared_case_qualified'])
        calibration['status']='passed'
        calibration['before_after_preservation']['status']='failed'
        gate=calibration_gate(calibration,c,self.session_sha)
        self.assertFalse(gate['physical_shared_case_qualified'])
        self.assertIn('calibration_before_after_preservation_not_passed',gate['reasons'])
        calibration['before_after_preservation']={'status':'passed','passed':False}
        self.assertFalse(calibration_gate(calibration,c,self.session_sha)['physical_shared_case_qualified'])

    def test_physical_locations_and_worker_incarnations_are_frozen(self):
        for field in ('node','gpu','numa','epoch'):
            c=self.case()
            c['base'][0][field]='wrong'
            with self.assertRaisesRegex(ValueError,'incarnation'):
                validate_case(c)
        c=self.case()
        c['candidates'][0]['gpu']=7
        with self.assertRaisesRegex(ValueError,'physical location'):
            validate_case(c)

    def test_CPU_plans_keep_exact_coverage_and_common_interleaving(self):
        result = preview_case(self.case(),self.session['profile'])
        plans = [r['plan'] for r in result['rows'] if r['plan']]
        self.assertGreaterEqual(len(plans),2)
        for p in plans:
            validate_plan(p)
            self.assertEqual(p['shared_schedule'],'rank_interleaved')
            self.assertEqual(sum(o['n'] for o in p['ops']),sum(b['n'] for b in self.cases[0]['base']))
        lp = next(r for r in result['rows'] if r['method']=='resource_lp')
        if lp['status'] == 'applicable':
            self.assertTrue(any(a['method']=='resource_lp' and 'fastest' in a['same_decision_as']
                                for a in result['decision_aliases']))
        self.assertEqual(result['new_GPU_samples'],0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('new output directory required')
    args.output.mkdir(parents=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests))
    (args.output/'tests.log').write_text(stream.getvalue())
    freeze.write_json(args.output/'summary.json',dict(status='passed' if result.wasSuccessful() else 'failed',
                       tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                       new_GPU_samples=0, session_sha256=freeze.digest(freeze.SESSION),
                       code_sha256={str(x.relative_to(ROOT)):freeze.digest(x) for x in
                                    (Path(__file__),HERE/'freeze_case.py',ROOT/'main_lib/fluidckpt/benchmarks/topology_cases.py')}))
    print(stream.getvalue(),end='')
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())
