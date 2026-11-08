"""CPU contract tests; fixture bandwidths are not performance evidence."""
import argparse
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.benchmarks.basic_scenarios import (
    GIB, IPC_DRIVER, assess_case, build_method_plan, method_catalog, scenario_contract)
from fluidckpt.cluster.path_catalog import path_key, topology_id


class ContractTests(unittest.TestCase):
    planner_checks = {}
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fluidckpt_contract_fixture_')
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name)/'test_fixture.json'
        self.evidence.write_text(json.dumps({'test_fixture': True, 'scope': 'CPU schema validation only'}))

    def case(self, scenario='S2', local_host=True):
        workers = [dict(worker=i, node='source' if i < 2 and not local_host else 'target', gpu=i, numa=0,
                        capabilities=['local_dram', 'local_hbm', 'peer', 'network']) for i in range(4)]
        replicas = [dict(id=f'host/{rank}', identity='contract-fixture', tp=rank, off=0, n=4096,
                         tier='dram', worker=rank, numa=0, generation=1, state='VALID',
                         source_role='base', allocation_id=f'host-physical/{rank}') for rank in range(2)]
        targets = [dict(key=f'target/{rank}', identity='contract-fixture', tp=rank, off=0,
                        n=4096, tier='hbm', worker=rank+2) for rank in range(2)]
        return dict(scenario_id=scenario, workers=workers, replicas=replicas, targets=targets,
                    profile=dict(rates=dict(local_dram=1, local_hbm=1, peer=1, network=1),
                                 gpu_ingress_GBps=1, gpu_egress_GBps=1, dram_GBps=1,
                                 network_node_GBps=1, serial_target_execution=True),
                    format_contract=dict(model_version='contract-fixture', dtype='float16',
                                         quantization='none', tp=2, processed_snapshot_id='fixture'),
                    budget=dict(base_dram_bytes=8192, extra_hbm_bytes=8*GIB,
                                location_extra_hbm_bytes={'0:hbm':4*GIB, '1:hbm':4*GIB,
                                                          '2:hbm':4*GIB, '3:hbm':4*GIB},
                                target_allocation_bytes=8192, staging_bytes=0))

    def add_active(self, case, logical_id='active/A', epdt=10, complete=True, existing=True):
        rows = [dict(id=f'{logical_id}/{rank}', identity='contract-fixture', tp=rank, off=0, n=4096,
                     tier='hbm', worker=rank, numa=0, generation=1, state='VALID', source_role='active')
                for rank in range(2 if complete else 1)]
        case['replicas'] += rows
        case.setdefault('active_instances', []).append(dict(
            logical_id=logical_id, replica_ids=[r['id'] for r in rows], epdt=epdt,
            survival_evidence=dict(path=str(self.evidence), alive_before=True,
                                   protected_during_copy=True, external_existing=existing,
                                   additional_lifetime_s=1, additional_hbm_byte_seconds=8192)))
        return rows

    def test_catalog_freezes_identity_and_not_measurement(self):
        catalog = method_catalog()
        self.assertEqual(catalog['janus_d2d']['upstream']['commit'], 'ec1beca2dbef5628b38e266fdc9afa0dba451678')
        self.assertEqual(catalog['sglang_ipc_hip']['identity'], 'hip_adaptation')
        self.assertEqual(catalog['janus_original']['identity'], 'original_system')
        self.assertEqual(assess_case({'scenario_id':'S2'}, 'janus_original')['status'], 'pending')
        self.assertEqual(assess_case({'scenario_id':'S4'}, 'blitzscale_distribution')['status'], 'unsupported')
        self.assertEqual(scenario_contract('S4')['experiment_namespace'], 'e1-e8')

    def test_host_and_existing_planners(self):
        case = self.case()
        for method in ('janus_host', 'fastest', 'resource_lp'):
            with self.subTest(method=method):
                result = build_method_plan(case, method)
                self.planner_checks[method] = dict(status=result['status'],
                    actual_strategy=result['plan']['strategy'] if result['plan'] else None,
                    reasons=result['reasons'])
                if method == 'resource_lp' and result['status'] == 'pending':
                    self.assertIn('planner_dependency_unavailable:', result['reasons'][0])
                    self.assertIsNone(result['plan'])
                    continue
                self.assertEqual(result['status'], 'applicable', result)
                self.assertEqual({o['replica_id'] for o in result['plan']['ops']}, {'host/0','host/1'})
                self.assertEqual(result['measurement_status'], 'not_measured')

    def test_no_host_reconstruction_from_partial_cross_worker_sources(self):
        case = self.case()
        first = case['replicas'][0]
        first['n'] = 2048
        tail = dict(first, id='host/tail', off=2048, worker=1, allocation_id='host-tail')
        case['replicas'].append(tail)
        # Three page allocations still fit the deliberately declared fixture budget.
        case['budget']['base_dram_bytes'] = 12288
        self.assertEqual(assess_case(case, 'janus_host')['status'], 'infeasible')
        self.assertEqual(build_method_plan(case, 'fastest')['status'], 'applicable')

    def test_remote_only_host_is_not_janus_warm_or_fallback(self):
        for scenario, method in [('S2','janus_host'), ('S3','janus_d2d')]:
            with self.subTest(scenario=scenario):
                case = self.case(scenario, local_host=False)
                result = assess_case(case, method)
                self.assertEqual(result['status'], 'infeasible')
                self.assertEqual(result['reasons'], ['complete_target_host_cache_missing'])
                self.assertEqual(result['rejections']['host/0'], 'remote_host_is_not_Janus_warm_cache')
                self.assertEqual(build_method_plan(case,'fastest')['status'], 'applicable')

    def test_janus_uses_local_host_even_when_remote_is_faster(self):
        case = self.case(local_host=False)
        local = [dict(row, id='local/'+str(row['tp']), worker=row['tp']+2,
                      allocation_id='local-physical/'+str(row['tp'])) for row in case['replicas']]
        case['replicas'] += local
        case['budget']['base_dram_bytes'] = 16384
        case['profile']['rates']['network'] = 100
        result = build_method_plan(case,'janus_host')
        self.assertEqual(result['status'], 'applicable')
        self.assertEqual({o['replica_id'] for o in result['plan']['ops']}, {'local/0','local/1'})
        fluid = build_method_plan(case,'fastest')
        self.assertEqual({o['replica_id'] for o in fluid['plan']['ops']}, {'host/0','host/1'})

    def test_incomplete_host_is_infeasible(self):
        case = self.case()
        case['replicas'][1]['n'] = 2048
        self.assertEqual(assess_case(case, 'janus_host')['reasons'], ['complete_host_base_missing'])

    def test_active_not_allowed_in_host_scenario(self):
        case = self.case()
        rows = self.add_active(case)
        result = assess_case(case, 'fastest')
        self.assertEqual(result['source_ids'], ['host/0','host/1'])
        for row in rows:
            self.assertEqual(result['rejections'][row['id']], 'role_not_allowed_in_scenario')

    def test_single_logical_instance_uses_highest_epdt(self):
        case = self.case('S3')
        self.add_active(case, 'active/A', 10)
        self.add_active(case, 'active/B', 20)
        result = build_method_plan(case, 'janus_d2d')
        self.assertEqual(result['selected_logical_instance'], 'active/B')
        self.assertEqual({o['replica_id'] for o in result['plan']['ops']}, {'active/B/0','active/B/1'})
        self.assertEqual(result['active_source_evidence'][0]['survival_status'], 'post_copy_pending')

    def test_janus_d2d_rejects_target_workers(self):
        case = self.case('S3')
        rows = self.add_active(case)
        for row in rows:
            row['worker'] = row['tp']+2
        result = assess_case(case,'janus_d2d')
        self.assertEqual(result['source_mode'], 'host_fallback')
        self.assertEqual(result['rejections']['active:active/A'], 'Janus_D2D_source_is_the_target')
        # A common executor can still use this physical source through its own mechanism.
        self.assertIn('active/A/0', assess_case(case,'fastest')['source_ids'])

    def test_janus_d2d_rejects_target_logical_instance(self):
        case = self.case('S3')
        self.add_active(case)
        case['target_logical_id'] = 'active/A'
        result = assess_case(case,'janus_d2d')
        self.assertEqual(result['source_mode'], 'host_fallback')
        self.assertEqual(result['rejections']['active:active/A'], 'Janus_D2D_source_is_the_target')

    def test_partial_logical_instances_are_not_combined(self):
        case = self.case('S3')
        self.add_active(case, 'active/A', complete=False)
        self.add_active(case, 'active/B', complete=False)
        case['replicas'][-1]['tp'] = 1
        result = assess_case(case, 'janus_d2d')
        self.assertEqual(result['source_mode'], 'host_fallback')
        self.assertEqual(result['source_ids'], ['host/0','host/1'])

    def test_survival_gate_falls_back_to_host(self):
        case = self.case('S3')
        self.add_active(case)
        case['active_instances'][0]['survival_evidence']['protected_during_copy'] = False
        self.assertEqual(assess_case(case, 'janus_d2d')['source_mode'], 'host_fallback')

    def test_missing_active_artifact_is_not_live_evidence(self):
        case = self.case('S3')
        self.add_active(case)
        case['active_instances'][0]['survival_evidence']['path'] = str(self.evidence)+'missing'
        self.assertEqual(assess_case(case, 'janus_d2d')['source_mode'], 'host_fallback')

    def test_no_active_and_no_host_is_infeasible(self):
        case = self.case('S3')
        case['replicas'] = []
        self.assertEqual(assess_case(case, 'janus_d2d')['status'], 'infeasible')

    def test_new_active_source_is_incremental_cost(self):
        case = self.case('S3')
        self.add_active(case, existing=False)
        ledger = assess_case(case, 'janus_d2d')['resource_ledger']
        self.assertEqual(ledger['existing_active_hbm_bytes'], 0)
        self.assertEqual(ledger['incremental_active_hbm_bytes'], 8192)
        self.assertEqual(ledger['active_source_lifetime'][0]['additional_hbm_byte_seconds'], 8192)

    def test_physical_allocation_alias_charged_once(self):
        case = self.case()
        case['replicas'].append(dict(case['replicas'][0], id='host/alias'))
        result = assess_case(case, 'fastest')
        self.assertEqual(result['resource_ledger']['full_base_dram_bytes'], 8192)
        case['replicas'][-1]['allocated_bytes'] = 8192
        with self.assertRaisesRegex(ValueError, 'aliases disagree'):
            assess_case(case, 'fastest')

    def test_budget_and_distributed_s4_gate(self):
        case = self.case('S4')
        self.assertEqual(assess_case(case, 'janus_host')['status'], 'applicable')
        case['budget']['extra_hbm_bytes'] = 0
        self.assertEqual(assess_case(case, 'fastest')['status'], 'infeasible')
        case['budget']['extra_hbm_bytes'] = 8*GIB
        case['budget']['location_extra_hbm_bytes'] = {'0:hbm':8*GIB}
        self.assertEqual(assess_case(case, 'fastest')['reasons'], ['S4_requires_distributed_8GiB_budget'])

    def test_extra_cache_budget_is_page_rounded(self):
        case = self.case('S4')
        case['replicas'].append(dict(case['replicas'][0], id='extra/0', tier='hbm',
                                    source_role='extra', allocation_id='extra-physical', n=1))
        case['budget']['location_extra_hbm_bytes']['0:hbm'] = 1
        self.assertEqual(assess_case(case, 'fastest')['reasons'], ['physical_allocation_budget_exceeded'])

    def test_s1_never_uses_copy_planner(self):
        case = self.case('S1')
        for row in case['replicas']:
            row.update(tier='hbm', source_role='ipc', worker=row['tp']+2)
        case['budget']['target_allocation_bytes'] = 0
        case['ipc_evidence'] = dict(driver=IPC_DRIVER, compatible=True, owner_alive=True,
                                    evidence_path=str(self.evidence))
        for method in ('sglang_ipc_hip','fastest','resource_lp'):
            result = build_method_plan(case, method)
            self.assertEqual(result['driver'], 'ipc')
            self.assertIsNone(result['plan'])
        self.assertEqual(assess_case(case,'janus_host')['status'], 'unsupported')
        case['ipc_evidence']['driver'] = 'fake-copy-driver'
        self.assertEqual(assess_case(case,'sglang_ipc_hip')['status'], 'pending')

    def test_directed_path_reachability_is_respected(self):
        case = self.case()
        case['profile']['verified_paths'] = [path_key(0,'dram',0,2)]
        result = assess_case(case, 'fastest')
        self.assertEqual(result['status'], 'infeasible')
        self.assertEqual(result['rejections']['host/1'], 'no_executable_path')

    def test_identity_and_format_sources_filtered(self):
        case = self.case()
        case['replicas'][1]['identity'] = 'wrong-version'
        result = assess_case(case, 'fastest')
        self.assertEqual(result['rejections']['host/1'], 'identity_or_tp_mismatch')
        case['replicas'][1]['identity'] = 'contract-fixture'
        case['replicas'][1]['format_contract'] = dict(case['format_contract'], dtype='float32')
        self.assertEqual(assess_case(case,'fastest')['rejections']['host/1'], 'format_mismatch')

    def test_s5_requires_artifact_and_concurrent_samples(self):
        case = self.case('S5', local_host=False)
        self.assertEqual(assess_case(case,'fastest')['status'], 'pending')
        case['shared_resource_evidence'] = dict(measured=True, evidence_path=str(self.evidence), resources=['net_out:source'])
        self.assertEqual(assess_case(case,'fastest')['status'], 'pending')
        artifact = dict(measurement_kind='shared_resource_contention', source='runtime',
                        topology_id=topology_id(case['workers']), executor='serial',
                        samples=[dict(resource='net_out:source', elapsed_s=1, bytes=4096, concurrent_flows=1)])
        self.evidence.write_text(json.dumps(artifact))
        self.assertEqual(assess_case(case,'fastest')['reasons'], ['shared_resource_concurrent_sample_missing'])
        artifact['samples'][0]['concurrent_flows'] = 2
        self.evidence.write_text(json.dumps(artifact))
        result = assess_case(case,'fastest')
        self.assertEqual(result['status'], 'applicable')
        self.assertEqual(result['measurement_status'], 'not_measured')
        artifact['mock'] = True
        self.evidence.write_text(json.dumps(artifact))
        self.assertEqual(assess_case(case,'fastest')['status'], 'pending')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (args.run_dir/'tests.log').write_text(stream.getvalue())
    summary = dict(schema_version=1, status='passed' if result.wasSuccessful() else 'failed',
                   tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   scope='CPU contracts only; synthetic schema/path fixtures; no GPU or performance evidence',
                   gpu_experiments_run=False, performance_measurement_completed=False)
    summary['planner_checks'] = ContractTests.planner_checks
    (args.run_dir/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
