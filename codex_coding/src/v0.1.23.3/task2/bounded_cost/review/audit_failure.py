"""Audit a failed pre-mesh session from local evidence without remote access."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--actual', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--failure-phase', choices=('premesh', 'prebind'), default='premesh')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, refs = [], []

    def check(name, value):
        checks.append(dict(name=name, passed=bool(value)))

    def read(relative):
        path = args.actual / relative
        refs.append(dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        return json.loads(path.read_text())

    summary = read('summary.json')
    plan_ref = summary['plan_ref']
    plan_path = Path(plan_ref['path'])
    check('frozen_launch_SHA', hashlib.sha256(plan_path.read_bytes()).hexdigest() == plan_ref['sha256'])
    plan = json.loads(plan_path.read_text())
    model_ref = plan['frozen_model_ref']
    check('frozen_model_unchanged', hashlib.sha256(Path(model_ref['path']).read_bytes()).hexdigest() == model_ref['sha256'])
    journal = read(('mesh' if args.failure_phase == 'premesh' else 'measure') + '/journal.json')
    check('runtime_failure_preserved', summary['status'] == 'failed' and journal['status'] == 'failed')
    controller_name = 'mesh_controller' if args.failure_phase == 'premesh' else 'measure_controller'
    stderr = (args.actual / controller_name / 'stderr.txt').read_text()
    if args.failure_phase == 'premesh':
        check('no_own_fixture_created', journal['owned'] == [] and not journal.get('synth') and not journal.get('planned_synth'))
        check('no_mesh_warm_bind_or_prediction', not (args.actual / 'mesh/mesh.json').exists() and not (args.actual / 'measure').exists())
        check('exact_endpoint_order_failure', 'worker identities do not match endpoint order' in stderr)
        check('cleanup_catalog_check_failed_preserved', 'worker identities do not match endpoint order' in journal['cleanup_failure'])
    else:
        check('exact_live_vs_JSON_key_failure', "KeyError: '0'" in stderr and 'bootstrap' in stderr)
        check('two_own_sources_exactly_released', len(journal['owned']) == 2 and len(journal['cleanup']) == 1 and all(r['status'] == 'released' for r in journal['cleanup'][0]['objects']))
        check('unadopted_shards_empty_no_cleanup_failure', not journal['unadopted_shards'] and not journal.get('cleanup_failure'))
        check('no_bind_prediction_or_holdout', all(not (args.actual / 'measure' / p).exists() for p in ('bound_model.json', 'prediction_freeze.json', 'validation.json', 'local_novel8.json', 'mixed_novel8.json')))
        final = read('measure/final_inventory.json')
        check('final_Catalog_used_transactions_empty', not final['replicas'] and all(not any(w['used_bytes'].values()) and not w['transactions'] and all(r['state'] == 'RELEASED' for r in w['replicas']) for w in final['workers']))
        check('three_SSD_stores_file_zero', all(s['files'] == 0 for s in final['ssd'].values()))
        mesh = read('mesh/mesh.json')
        check('actual_mesh_ranks_and_necessary_pair_warm', [w['worker'] for w in mesh['workers']] == list(range(6)) and mesh['necessary_pairs'] == [[2,4],[4,5]] and len(mesh['warm']) == 6)
    startup = read('startup.inventory.json')
    check('six_premesh_rank_null', len(startup) == 6 and all(w['worker'] is None for w in startup))
    check('startup_catalog_used_and_transactions_empty', all(not w['replicas'] and not any(w['used_bytes'].values()) and not w['transactions'] for w in startup))
    check('all_nine_STOP_writes_passed', len(summary['all_stop']) == 9 and all(x['returncode'] == 0 and not x['timed_out'] for x in summary['all_stop'].values()))
    check('all_nine_supervisor_commands_passed', len(summary['workers']) == 9 and all(x['returncode'] == 0 and not x['timed_out'] for x in summary['workers'].values()))
    resource_samples = {}
    owner_count = container_count = 0
    for node in ('node28', 'node30', 'node27'):
        before = read('baseline_' + node + '/stdout.json')
        after = read('after_stop_' + node + '/stdout.json')
        spec = json.loads((Path(plan['setup_directory']) / (node + '.setup.spec.json')).read_text())
        check(node + '_boot_preserved', before['boot_id'] == after['boot_id'])
        check(node + '_exact_task_CID_started_running', before['container']['Id'] == after['container']['Id'] == spec['container_id'] and before['container']['State']['StartedAt'] == after['container']['State']['StartedAt'] == spec['container_started_at'] and after['container']['State']['Running'])
        check(node + '_final_pids1_no_OOM', after['cgroup']['files']['pids.current'] == '1' and all(line in after['cgroup']['files']['memory.events'] for line in ('oom 0', 'oom_kill 0')))
        check(node + '_all_protected_XML_SHA_preserved', before['original_xml_sha256'] == after['original_xml_sha256'])
        owners = lambda rows: {r['pid']: {key: r[key] for key in ('pid', 'start_ticks', 'cgroup', 'argv_sha256')} for r in rows}
        containers = lambda rows: {r['id']: {key: r[key] for key in ('id', 'name', 'StartedAt', 'Running', 'Pid')} for r in rows}
        check(node + '_protected_owners_preserved', owners(before['protected_owners']) == owners(after['protected_owners']))
        check(node + '_protected_containers_preserved', containers(before['protected_containers']) == containers(after['protected_containers']))
        owner_count += len(before['protected_owners']); container_count += len(before['protected_containers'])
        check(node + '_all_deployed_assets_preserved', all(before['source_assets'].get(r['relative_path']) == after['source_assets'].get(r['relative_path']) == r['sha256'] for r in spec['assets']))
        check(node + '_no_node29_or_probe_GPU_writes', all(after[key] == 0 for key in ('gpu_api_calls', 'remote_writes', 'node29_accesses')) and not after['signals'])
        collection = read('exact_collect_' + node + '/stdout.json')
        check(node + '_exact_collector_passed', collection['passed'] and not collection['signals'] and len(collection['reports']) == 3)
        maximum_dram = 0
        for row in collection['reports']:
            key = row['key']; process = read('artifacts_' + node + '/raw/operations_process_' + plan['session'] + '_' + key + '/supervisor.summary.json')
            check(key + '_fresh_pid_absent_group_empty', row['passed'] and not row['current']['live'] and row['current']['state'] == 'absent' and not row['group'])
            check(key + '_planned_TERM_only', process['passed'] and process['stop_reason'] == 'owned_stop_marker' and process['cleanup']['passed'] and process['cleanup']['signals'] == ['TERM'] and process['child_returncode'] == -15 and not process['cleanup']['pending_group_members'])
            check(key + '_supervisor_plan_SHA', process['plan_sha256'] == row['owner']['plan_sha256'] == plan_ref['sha256'])
            maximum_dram = max(maximum_dram, max(int(sample['memory.current']) for sample in process['samples']))
        group = [w for w in startup if w['node'] == node]
        hbm_upper = sum(w['hbm_total_bytes'] - w['headroom_bytes']['hbm'] for w in group)
        check(node + '_sampled_DRAM_with_margin_under_budget', maximum_dram + (256 << 20) <= 6 << 30)
        check(node + '_startup_same_HIP_absolute_upper_under_budget', hbm_upper <= 2 << 30)
        resource_samples[node] = dict(supervisor_sampled_dram_max_bytes=maximum_dram, startup_hbm_absolute_upper_bytes=hbm_upper, full_continuous_peak_measured=False, postwarm_gate_executed=False)
    report = dict(status=('failure_cleanup_passed_validation_not_started' if args.failure_phase == 'premesh' else 'failure_cleanup_passed_holdout_not_started') if all(c['passed'] for c in checks) else 'failure_cleanup_review_failed',
                  checks=checks, check_count=len(checks), source_refs=refs + [plan_ref, model_ref],
                  session=plan['session'], physical_rank_pre_mesh_null=True, failure='runner called strict Cluster.inventory before mesh assigned ranks' if args.failure_phase == 'premesh' else 'live verification uses int operation key; historical JSON uses string key',
                  experiment_completed=False, model_migration_validated=False, holdout_prediction_validation_completed=False,
                  actual_holdout_requests=0, actual_verified_payload_bytes=0 if args.failure_phase == 'premesh' else None,
                  bootstrap_transfer_bytes_inferred_from_code=0 if args.failure_phase == 'premesh' else 16 << 20,
                  bootstrap_digest_record_saved=False, prediction_errors=None, decision_ready=False,
                  catalog_final_check_passed=args.failure_phase != 'premesh', no_own_fixture_created=args.failure_phase == 'premesh', nine_owned_processes_absent=True,
                  old_owner_count=owner_count, old_container_count=container_count, resources=resource_samples,
                  elapsed_session_s=summary['elapsed_s'], GPU_or_SSH_by_reviewer=False)
    (args.out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    shutil.copyfile(args.actual / controller_name / 'stderr.txt', args.out / 'controller.failure.txt')
    print(json.dumps(dict(status=report['status'], checks=len(checks), failures=[c['name'] for c in checks if not c['passed']])))
    if not all(c['passed'] for c in checks):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
