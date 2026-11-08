"""CPU-only validation against extracted upstream functions, without Ray imports."""
import ast
import copy
import logging
from pathlib import Path
import sys
from typing import Mapping, Optional, Tuple
import unittest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.benchmarks.baseline_scope import (
    SLLM_POLICY, algorithm_adaptations, baseline_scopes,
    serverlessllm_loading_time, serverlessllm_migration_target)


def upstream_methods():
    """Compile the two actual frozen functions rather than a handwritten oracle."""
    source = ast.parse((ROOT/SLLM_POLICY).read_text())
    names = {'_get_model_loading_time', '_get_migration_target'}
    functions = [copy.deepcopy(node) for node in ast.walk(source)
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in functions} != names or len(functions) != 2:
        raise RuntimeError('frozen upstream symbols missing or ambiguous')
    module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    context = dict(Mapping=Mapping, Optional=Optional, Tuple=Tuple, logger=logging.getLogger('upstream_oracle'))
    exec(compile(module, str(ROOT/SLLM_POLICY), 'exec'), context)
    cls = type('FrozenUpstreamPolicy', (), {name: context[name] for name in names})
    return cls()


class BaselineScopeTests(unittest.TestCase):
    def test_loading_estimate_matches_actual_frozen_source(self):
        upstream = upstream_methods()
        for size in (0, 4096, 70329718112):
            for waiting in (0, 3, 17.125):
                for hardware in ({'disk_bandwidth': 100, 'pcie_bandwidth': 200},
                                 {'disk_bandwidth': 4e9, 'pcie_bandwidth': 21e9}):
                    for pool in ({}, {'model': 0}, {'unrelated': 0}):
                        with self.subTest(size=size, waiting=waiting, hardware=hardware, pool=pool):
                            expected = upstream._get_model_loading_time('model', size, hardware, waiting, pool)
                            self.assertEqual(serverlessllm_loading_time('model', size, hardware, waiting, pool), expected)

    def test_node_choice_matches_actual_upstream_with_ties_and_gpu_filters(self):
        upstream = upstream_methods()
        for ordering in ((3, 1, 2), (2, 1, 3)):
            for free in (0, 1, 2, 8):
                for pools in ({}, {'model': 0}):
                    workers = {node: {'free_gpu': free if node == 1 else 2} for node in ordering}
                    store = {node: [{'model': 4096}, pools if node == 1 else {}, 0] for node in ordering}
                    hardware = {node: {'disk_bandwidth': 100, 'pcie_bandwidth': 200} for node in ordering}
                    for source in (1, 99):
                        args = ('model', 2, source, workers, {'model':4096}, store, hardware)
                        self.assertEqual(serverlessllm_migration_target(*args), upstream._get_migration_target(*args))

    def test_fixed_target_is_one_candidate_and_empty_is_no_target(self):
        args = ('model', 2, 'source', {'target': {'free_gpu': 2}}, {'model':1000},
                {'target': [{'model':1000}, {'model':0}, 99]},
                {'target': {'disk_bandwidth':10, 'pcie_bandwidth':100}})
        self.assertEqual(serverlessllm_migration_target(*args), ('target', 10))
        args[3]['target']['free_gpu'] = 1
        self.assertIsNone(serverlessllm_migration_target(*args))

    def test_validation_rejects_nonphysical_inputs(self):
        for size, bandwidth, waiting in ((-1, 1, 0), (1, 0, 0), (1, float('nan'), 0), (1, 1, -2)):
            with self.assertRaises(ValueError):
                serverlessllm_loading_time('model', size, {'disk_bandwidth':bandwidth}, waiting, {})

    def test_complete_table_retains_system_identity_and_legal_fallback(self):
        rows = baseline_scopes()
        self.assertEqual(len(rows), 55)
        lookup = {(row['method_id'], row['scenario_id']):row for row in rows}
        for row in rows:
            self.assertIn(row['applicability'], ('applicable','conditional','not_adapted','infeasible'))
            self.assertTrue(row['source_files'])
            self.assertEqual(row['measurement_status'], 'not_measured')
        self.assertEqual(lookup['sglang_original','S4']['applicability'], 'conditional')
        self.assertEqual(lookup['janus_original','S4']['applicability'], 'not_adapted')
        self.assertEqual(lookup['sglang_ipc_hip','S4']['applicability'], 'infeasible')
        self.assertEqual(lookup['janus_d2d','S2']['applicability'], 'applicable')
        self.assertIn('后备', lookup['janus_d2d','S2']['reason'])
        self.assertEqual(lookup['janus_d2d','S2']['source_mode'], 'host_fallback')
        for method in ('janus_host', 'janus_d2d'):
            self.assertEqual(lookup[method,'S1']['applicability'], 'conditional')
            self.assertEqual(lookup[method,'S1']['measurement_status'], 'not_measured')
            self.assertIn('不排除', lookup[method,'S1']['reason'])
        self.assertEqual(lookup['resource_lp','S5']['applicability'], 'conditional')
        for name in ('blitzscale_distribution', 'serverlessllm_locality'):
            for scene in ('S1','S2','S3','S4','S5'):
                self.assertEqual(lookup[name,scene]['implementation'], 'not_adapted')
        self.assertTrue(all(not row['executable_restore_plan'] for row in algorithm_adaptations()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
