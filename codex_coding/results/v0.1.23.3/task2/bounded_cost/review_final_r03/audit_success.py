"""Independent audit of two frozen holdouts and owned-session protection."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[6]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--actual', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, references, metrics = [], {}, []

    def check(name, value):
        checks.append(dict(name=name, passed=bool(value)))

    def reference(path):
        path = Path(path).resolve()
        result = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        references[str(path)] = result
        return result

    def read(relative):
        path = args.actual / relative
        reference(path)
        return json.loads(path.read_text())

    def verify(ref):
        return reference(ref['path']) == ref

    summary = read('summary.json')
    check('parent_completed_and_all_collection_passed', summary['status'] == 'completed_pending_independent_review' and all(r['passed'] for r in summary['collection'].values()))
    check('launch_plan_unchanged', verify(summary['plan_ref']))
    plan = json.loads(Path(summary['plan_ref']['path']).read_text())
    check('frozen_source_model_unchanged', verify(plan['frozen_model_ref']))
    frozen = json.loads(Path(plan['frozen_model_ref']['path']).read_text())
    for name, ref in frozen['source_refs'].items():
        check('training_provenance_' + name, verify(ref))
    check('training_only_no_holdout_fit', frozen['decision_ready'] is False and frozen['independent_validation_complete'] is False)
    own_summary = read('measure/summary.json')
    journal = read('measure/journal.json')
    check('two_holdouts_no_training_or_AB', own_summary['status'] == 'complete' and own_summary['validation_requests'] == 2 and own_summary['training_requests'] == 0 and own_summary['query_AB_requests'] == 0 and journal['status'] == 'complete')
    attestation = read('measure/migration_attestation.json')
    bound = read('measure/bound_model.json')
    freeze = read('measure/prediction_freeze.json')
    model = bound['model']
    check('bound_model_coefficients_equal_original', {k:v for k,v in model.items() if k != 'binding'} == frozen['model'])
    check('bound_original_refs_unchanged', verify(bound['source_frozen_model_ref']) and verify(bound['attestation_ref']) and verify(freeze['bound_model_ref']) and verify(freeze['source_frozen_model_ref']))
    check('both_predictions_frozen_before_measurements', freeze['measurement_requests_executed'] == 0 and set(freeze['predictions']) == {'local_novel8','mixed_novel8'})
    workers = attestation['fresh_workers']
    mesh = read('mesh/mesh.json')
    check('six_fresh_ranks_and_exact_warm_pairs', [w['worker'] for w in workers] == list(range(6)) and mesh['necessary_pairs'] == [[2,4],[4,5]] and len(mesh['warm']) == 6 and all(w['warm_s'] >= 0 for w in mesh['warm']))
    check('fresh_incarnations_match_mesh_and_binding', attestation['fresh_incarnations'] == mesh['incarnations'] == model['binding']['incarnations'] and attestation['fresh_incarnations'] != attestation['old_calibration_incarnations'])
    check('migration_gates_and_current_sources_verified', all(attestation[k] is True for k in ('physical_gate_passed','config_verified','source_lineage_verified')) and attestation['worker_incarnations'] == model['binding']['incarnations'])
    old_workers = json.loads(Path(attestation['prior_inventory_ref']['path']).read_text())
    check('old_inventory_reference_unchanged', verify(attestation['prior_inventory_ref']))
    for current, old in zip(workers, old_workers):
        check('w' + str(current['worker']) + '_same_hardware_and_communication_config', all(current[k] == old[k] for k in ('worker','node','gpu','numa','staging_bytes')) and all(current['runtime_identity'][k] == old['runtime_identity'][k] for k in ('actual_bdf','pci_numa_node','selected_hca_metadata','communication_environment')))
        check('w' + str(current['worker']) + '_current_session_and_cached_fresh', current['session'] == plan['session'] and current['inventory_diagnosis']['mode'] == 'cached' and bool(current['inventory_diagnosis']['fresh_fields']))
    lineage = read('measure/source_content_lineage.json')
    check('content_lineage_references_unchanged', verify(attestation['source_content_lineage_ref']))
    check('DRAM_source_limit_explicit', lineage['source1_full_old_SHA_comparison'] is False and all(s['same_source_schema'] for s in lineage['sources']))
    bootstrap_bytes = 0
    for index in (0,2):
        flow = read('measure/bootstrap' + str(index) + '.json')
        old_ref = lineage['sources'][index]['old_bootstrap_ref']
        check('bootstrap' + str(index) + '_old_reference_SHA', verify(old_ref))
        old = json.loads(Path(old_ref['path']).read_text())
        check('bootstrap' + str(index) + '_full16MiB_old_content_equal', flow['verification']['passed'] and flow['verification']['checked_bytes'] == 16 << 20 and list(flow['verification']['operations'].values()) == list(old['verification']['operations'].values()))
        bootstrap_bytes += flow['verification']['checked_bytes']
    predictor_path = ROOT / 'codex_coding/src/v0.1.23.3/task2/bounded_cost/model/predictor.py'
    spec = importlib.util.spec_from_file_location('independent_success_predictor', predictor_path)
    predictor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(predictor)
    rows = read('measure/validation.json')
    check('exact_two_preregistered_observations', [r['label'] for r in rows] == ['local_novel8','mixed_novel8'] and [r['nbytes'] for r in rows] == [8 << 20,24 << 20])
    for row in rows:
        label = row['label']; prediction = row['prediction']; flow = row['flow']; timing = row['request_timing']
        preref = freeze['predictions'][label]
        check(label + '_prediction_ref_immutable', preref == row['preregistered_prediction_ref'] and verify(preref) and verify(row['prediction_freeze_ref']) and verify(row['bound_model_ref']) and verify(row['source_frozen_model_ref']))
        prereg = json.loads(Path(preref['path']).read_text())
        check(label + '_frozen_before_request', prereg['saved_ns'] <= freeze['freeze_saved_ns'] < timing['request_begin_ns'])
        check(label + '_same_frozen_prediction_and_plan', prereg['prediction'] == prediction and all(prereg['plan'][key] == flow['plan'][key] for key in ('targets','ops','executor','chunk_bytes')))
        state = dict(incarnations=model['binding']['incarnations'], warm_pairs=['2:4','4:5'])
        check(label + '_independent_prediction_recompute', predictor.predict(prereg['plan'],workers,model,state) == prediction)
        check(label + '_single8MiB_per_calibrated_path', len(flow['plan']['ops']) == row['operations'] and all(op['n'] == 8 << 20 for op in flow['plan']['ops']) and len({op['path'] for op in flow['plan']['ops']}) == row['operations'])
        check(label + '_same_controller_clock', timing['clock_domain'] == flow['controller_timing']['clock_domain'])
        check(label + '_full_window_arithmetic', abs(timing['request_to_filled_s'] - (timing['filled_ns'] - timing['request_begin_ns']) / 1e9) < 1e-15 and abs(timing['request_to_published_s'] - (timing['published_ns'] - timing['request_begin_ns']) / 1e9) < 1e-15)
        check(label + '_online_work_inside_window', timing['request_begin_ns'] < timing['inventory_end_ns'] <= timing['gate_end_ns'] <= timing['plan_end_ns'] <= timing['filled_ns'] < timing['published_ns'])
        check(label + '_all_byte_SHA', flow['verification']['passed'] and flow['verification']['checked_bytes'] == row['nbytes'] and sum(r['n'] for r in flow['verification']['operations'].values()) == row['nbytes'] and all(r['source_sha256'] == r['target_sha256'] and len(r['source_sha256']) == 64 for r in flow['verification']['operations'].values()))
        check(label + '_published_prediction_null_decision_not_certified', prediction['estimated_full_published_s'] is None and prediction['decision_ready'] is False)
        target = max(w['execute_s'] for w in flow['execution'] if w['worker'] in {op['dst_w'] for op in flow['plan']['ops']})
        error = lambda predicted, actual: (predicted-actual) / actual
        metric = dict(label=label, bytes=row['nbytes'], actual_execute_s=flow['execute_wall_s'], predicted_execute_s=prediction['estimated_execute_s'], execute_relative_error=error(prediction['estimated_execute_s'],flow['execute_wall_s']), actual_worker_s=target, predicted_worker_s=prediction['estimated_worker_s'], worker_relative_error=error(prediction['estimated_worker_s'],target), actual_filled_s=timing['request_to_filled_s'], predicted_filled_s=prediction['estimated_full_filled_s'], filled_relative_error=error(prediction['estimated_full_filled_s'],timing['request_to_filled_s']), actual_published_s=timing['request_to_published_s'], verify_publish_s=flow['verify_and_publish_s'], published_prediction=None)
        if len(flow['plan']['ops']) == 1:
            observed = max(r['elapsed_s'] for w in flow['execution'] for r in w['records'] if r['role'] in ('target','both'))
            metric.update(actual_service_s=observed,predicted_service_s=prediction['estimated_service_s'],service_relative_error=error(prediction['estimated_service_s'],observed))
        metric['operation_records'] = [{**r,'worker':w['worker']} for w in flow['execution'] for r in w['records']]
        metrics.append(metric)
    final = read('measure/final_inventory.json')
    check('final_catalog_used_transactions_empty', not final['replicas'] and all(not any(w['used_bytes'].values()) and not w['transactions'] and all(r['state'] == 'RELEASED' for r in w['replicas']) for w in final['workers']))
    check('final_three_SSD_files_zero', all(s['files'] == 0 for s in final['ssd'].values()))
    check('all_nine_STOP_and_supervisors_passed', len(summary['all_stop']) == len(summary['workers']) == 9 and all(r['returncode'] == 0 and not r['timed_out'] for r in [*summary['all_stop'].values(),*summary['workers'].values()]))
    budget = read('budget_evidence.json'); monitor = read('budget_monitor.json')
    resources = {}; owners_count = containers_count = 0
    for node in ('node28','node30','node27'):
        before = read('baseline_' + node + '/stdout.json'); after = read('after_stop_' + node + '/stdout.json')
        setup = json.loads((Path(plan['setup_directory']) / (node + '.setup.spec.json')).read_text())
        owner_map = lambda x: {r['pid']:{k:r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')} for r in x}
        container_map = lambda x: {r['id']:{k:r[k] for k in ('id','name','StartedAt','Running','Pid')} for r in x}
        check(node + '_old_owner_and_containers_preserved', owner_map(before['protected_owners']) == owner_map(after['protected_owners']) and container_map(before['protected_containers']) == container_map(after['protected_containers']))
        owners_count += len(before['protected_owners']); containers_count += len(before['protected_containers'])
        check(node + '_quiet_exact_task_container', before['boot_id'] == after['boot_id'] and before['container']['Id'] == after['container']['Id'] == setup['container_id'] and before['container']['State']['StartedAt'] == after['container']['State']['StartedAt'] == setup['container_started_at'] and after['container']['State']['Running'] and after['cgroup']['files']['pids.current'] == '1')
        check(node + '_no_OOM_XML_and_asset_preserved', all(v in after['cgroup']['files']['memory.events'] for v in ('oom 0','oom_kill 0')) and before['original_xml_sha256'] == after['original_xml_sha256'] and all(before['source_assets'].get(r['relative_path']) == after['source_assets'].get(r['relative_path']) == r['sha256'] for r in setup['assets']))
        collect = read('exact_collect_' + node + '/stdout.json')
        check(node + '_exact_collect_no_signals', collect['passed'] and not collect['signals'] and len(collect['reports']) == 3)
        maximum_dram = 0
        for collected in collect['reports']:
            key = collected['key']; supervisor = read('artifacts_' + node + '/raw/operations_process_' + plan['session'] + '_' + key + '/supervisor.summary.json')
            check(key + '_owned_TERM_absent_group_empty', collected['passed'] and not collected['current']['live'] and not collected['group'] and supervisor['passed'] and supervisor['stop_reason'] == 'owned_stop_marker' and supervisor['cleanup']['passed'] and supervisor['cleanup']['signals'] == ['TERM'] and supervisor['child_returncode'] == -15 and not supervisor['cleanup']['pending_group_members'])
            check(key + '_supervisor_same_plan_SHA', supervisor['plan_sha256'] == collected['owner']['plan_sha256'] == summary['plan_ref']['sha256'])
            maximum_dram = max(maximum_dram,max(int(s['memory.current']) for s in supervisor['samples']))
        gates = [budget['nodes'][node]] + [s['nodes'][node] for s in monitor] + [r['gate'][node] for r in rows]
        check(node + '_all_current_DRAM_and_HBM_budget_gates', maximum_dram + (256 << 20) <= 6 << 30 and all(g['dram_current_bytes'] + (256 << 20) <= 6 << 30 and g['hbm_absolute_upper_bytes'] + g['planned_incremental_upper_bytes'] <= 2 << 30 for g in gates))
        resources[node] = dict(supervisor_dram_sample_max_bytes=maximum_dram, same_HIP_absolute_sample_max_bytes=max(g['hbm_absolute_upper_bytes'] for g in gates), continuous_peak_measured=False)
    check('monitor_preserves_active_mode_and_budget', all(g['require_idle'] is False for s in monitor for g in s['nodes'].values()))
    reference(Path(__file__))
    report = dict(status='evidence_review_passed_prediction_observed' if all(c['passed'] for c in checks) else 'evidence_review_failed', checks=checks, check_count=len(checks), source_refs=list(references.values()), metrics=metrics, resources=resources, session=plan['session'], session_elapsed_s=summary['elapsed_s'], two_holdouts_completed=True, prediction_parameters_unchanged=True, verified_bytes_including_bootstrap=bootstrap_bytes+sum(r['nbytes'] for r in rows), old_owner_count=owners_count, old_container_count=containers_count, decision_ready=False, published_prediction_ready=False, prediction_accuracy_certified=False, GPU_or_SSH_by_reviewer=False, limitations=['n=1 per configuration','fresh-session coefficient migration','DRAM generator/schema equality without cross-session full-source SHA','mixed operation timings include waits; no exact measured intrinsic critical-path service','full-filled shared-control estimate is a rough hypothesis','no same-budget placement ordering comparison or live model handoff'])
    (args.out / 'qualification.json').write_text(json.dumps(report,indent=2) + '\n')
    reference(Path(__file__))
    shutil.copyfile(__file__, args.out / 'audit_success.py')
    print(json.dumps(dict(status=report['status'], checks=len(checks), failed=[c['name'] for c in checks if not c['passed']])))
    if not all(c['passed'] for c in checks):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
