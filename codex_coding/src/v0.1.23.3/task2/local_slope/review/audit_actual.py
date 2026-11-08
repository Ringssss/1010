"""Independently review completed local-slope GPU evidence using local JSON only."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[6]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--actual', required=True, type=Path)
    parser.add_argument('--prepared', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repeat', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    checks, refs = [], []

    def check(name, ok):
        checks.append({'name': name, 'passed': bool(ok)})
        assert ok, name

    def read(path):
        refs.append({'path': str(path.resolve()), 'sha256': sha(path)})
        return json.loads(path.read_text())

    plan = read(args.prepared / 'launch_plan.json')
    total = read(args.actual / 'summary.json')
    rows = read(args.actual / 'measure/measurements.json')
    prereg = read(args.actual / 'measure/preregistration.json')
    final = read(args.actual / 'measure/final_inventory.json')
    lineage = read(args.actual / 'measure/source_lineage.json')
    bootstrap = read(args.actual / 'measure/bootstrap0.json')
    old_bootstrap = read(Path(lineage['old_bootstrap_ref']['path']))
    cases = plan['protocol']['cases']
    check('completed actual session', total['status'] == 'completed_pending_independent_review')
    check('exact frozen plan launched', total['plan_ref']['sha256'] == sha(args.prepared / 'launch_plan.json'))
    check('all cases complete and in preregistered order', len(rows) == (2 if args.repeat else 8) and [r['case'] for r in rows] == cases)
    check('preregistration before first measured request', prereg['saved_ns'] < rows[0]['request_begin_ns'])
    check('fresh actual source schema/full16MiB verified', lineage['same_source_schema'] and lineage['full16MiB_SHA_equal'] and lineage['model_used'] is False)
    check('bootstrap all16MiB SHA', bootstrap['verification']['passed'] and bootstrap['verification']['checked_bytes'] == 16 << 20)
    new_hashes = next(iter(bootstrap['verification']['operations'].values()))
    old_hashes = next(iter(old_bootstrap['verification']['operations'].values()))
    check('source content equals original all-byte hash', all(new_hashes[k] == old_hashes[k] for k in ('n', 'source_sha256', 'target_sha256')))
    details, diag_rows = [], []
    native_tids, diagnostic_tids = [], []
    for row in rows:
        label = row['case']['label']
        observed = read(args.actual / 'measure' / (label + '.json'))
        check(label + ': standalone raw matches aggregate', observed == row)
        flow = row['flow']
        worker = flow['execution'][4]
        record = worker['records'][0]
        prepare = worker['local_slope_prepare']
        check(label + ': preregistered plan intact', flow['plan'] == prereg['plans'][label])
        check(label + ': all-byte SHA and count', flow['verification']['passed'] and flow['verification']['checked_bytes'] == row['case']['nbytes'])
        hashes = next(iter(flow['verification']['operations'].values()))
        check(label + ': source/target SHA equality', hashes['source_sha256'] == hashes['target_sha256'])
        check(label + ': original local primitive', len(worker['records']) == 1 and record['role'] == 'both' and record['kind'] == 'local_hbm')
        check(label + ': independent host windows consistent', row['service_s'] == record['elapsed_s'] and worker['execute_s'] >= row['service_s'])
        check(label + ': fixed lineage/flat source and exact target', (args.repeat or prepare['source_identity']['identity'] == lineage['source']['identity'])
              and prepare['source']['numel'] == 16 << 20 and prepare['target']['numel'] == row['case']['nbytes']
              and prepare['source']['stride'] == prepare['target']['stride'] == [1]
              and prepare['source']['storage_offset'] == prepare['target']['storage_offset'] == 0)
        check(label + ': request clock arithmetic', row['request_to_filled_s'] == (flow['controller_timing']['all_targets_filled_ns']-row['request_begin_ns'])/1e9)
        check(label + ': no fit or prediction', row['fit'] is None and row['prediction'] is None)
        result = {'label': label, 'service_ms': row['service_s'] * 1000,
                  'worker_ms': worker['execute_s'] * 1000, 'source_ptr': prepare['source']['data_ptr'],
                  'target_ptr': prepare['target']['data_ptr']}
        if args.repeat:
            diag = record['local_slope_diagnosis']
            rounds = diag['rounds']
            check(label + ': exact two-copy bytes/logical SHA separation', len(rounds) == 2 and record['actual_copy_bytes'] == 2*row['case']['nbytes']
                  and record['logical_SHA_bytes'] == row['case']['nbytes'] and record['copy_rounds'] == 2)
            check(label + ': same actual handler and addresses', len({r['native_tid'] for r in rounds}) == 1
                  and diag['same_source_view_address'] and diag['same_target_view_address'])
            check(label + ': no GPUevents/prewarm/prepare sync', diag['CUDA_events'] == 0 and not diag['extra_prewarm'] and not diag['extra_prepare_sync'])
            check(label + ': metadata after both rounds', prepare['metadata_after_both_rounds'] and all(r['metadata_after_both_rounds'] for r in rounds))
            check(label + ': complete double-round host window retained', record['elapsed_s'] == (diag['service_end_ns']-diag['service_begin_ns'])/1e9
                  and diag['between_round_and_bookkeeping_ns'] >= 0)
            for ri, round_row in enumerate(rounds):
                check(label + ': round phase partition ' + str(ri), sum(p['wall_ns'] for p in round_row['host_phases'].values()) == round_row['elapsed_ns']
                      and round_row['host_residual_ns'] == 0)
            native_tids.append(rounds[0]['native_tid'])
            result['rounds'] = rounds
            result['source_slice_first_minus_second_us'] = {
                k: (rounds[0]['host_phases']['source_slice'][k]-rounds[1]['host_phases']['source_slice'][k])/1000
                for k in ('wall_ns','thread_cpu_ns')}
        elif row['case']['mode'] == 'native':
            check(label + ': immutable native method executed', worker['executor_source'] == 'unchanged native ClusterWorker._execute'
                  and 'local_slope_diagnosis' not in record)
            native_tids.append(worker['execute_thread']['native_tid'])
        else:
            diag = record['local_slope_diagnosis']
            check(label + ': phases partition full observed host interval', sum(p['wall_ns'] for p in diag['host_phases'].values())
                  == diag['service_end_ns']-diag['service_begin_ns'] == diag['host_phase_sum_ns']
                  and abs(diag['host_phase_sum_ns']/1e9-row['service_s']) < 1e-12)
            check(label + ': CPU/nonnegative phases', all(p['wall_ns'] >= 0 and p['thread_cpu_ns'] >= 0 for p in diag['host_phases'].values()))
            check(label + ': observer scope explicit', diag['phase_observer_overhead_included']
                  and 'zero is not proof' in diag['residual_scope'] and 'not a pure' in diag['gpu_event_scope'])
            check(label + ': post-service observer retained in controller', diag['post_service_diagnosis_wall_ns'] >= 0
                  and 'included in HTTP/controller' in diag['post_service_diagnosis_scope'])
            check(label + ': prepare/execute actual threads distinct', prepare['prepare_native_tid'] != diag['execute_native_tid'])
            diagnostic_tids.append(diag['execute_native_tid'])
            result.update(host_phases=diag['host_phases'], event_ms=diag['gpu_event_elapsed_ms'],
                          post_service_diagnosis_ms=diag['post_service_diagnosis_wall_ns']/1e6)
            diag_rows.append(row)
        details.append(result)
    check('distinct actual native request TIDs', len(set(native_tids + diagnostic_tids)) == len(rows))
    check('one source pointer unchanged', len({d['source_ptr'] for d in details}) == 1)
    check('all source/targets retired', not final['replicas'] and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in final['workers']))
    check('all three SSD stores empty', all(s['files'] == 0 for s in final['ssd'].values()))
    check('all supervisors and STOP rc0', len(total['workers']) == len(total['all_stop']) == 9
          and all(r['returncode'] == 0 for r in total['workers'].values()) and all(r['returncode'] == 0 for r in total['all_stop'].values()))
    resources = {}
    for node in ('node28', 'node30', 'node27'):
        before = read(args.actual / ('baseline_' + node) / 'stdout.json')
        after = read(args.actual / ('after_stop_' + node) / 'stdout.json')
        exact = read(args.actual / ('exact_collect_' + node) / 'stdout.json')
        spec = read(args.prepared / (node + '.setup.spec.json'))
        check(node + ': original XML preserved', before['original_xml_sha256'] == after['original_xml_sha256'])
        for field in ('protected_owners', 'protected_containers'):
            # Readback fields include incidental samples; only exact identity columns are invariant.
            cols = ('pid', 'start_ticks', 'cgroup', 'argv_sha256') if field == 'protected_owners' else ('id', 'name', 'StartedAt', 'Running', 'Pid')
            check(node + ': protected identities ' + field,
                  [{k:r[k] for k in cols} for r in before[field]] == [{k:r[k] for k in cols} for r in after[field]])
        check(node + ': deployed source SHA intact', all(after['source_assets'].get(a['relative_path']) == a['sha256'] for a in spec['assets']))
        check(node + ': quiet exact container', after['container']['Id'] == spec['container_id']
              and after['container']['State']['StartedAt'] == spec['container_started_at'] and after['cgroup']['files']['pids.current'] == '1')
        check(node + ': exact child/group absence', exact['passed'] and not exact['signals']
              and all(not r['current']['live'] and not r['group'] for r in exact['reports']))
        peak = 0
        for process in (p for p in plan['processes'] if p['node'] == node):
            summary_path = args.actual / ('artifacts_' + node) / 'raw' / Path(process['out']).name / 'supervisor.summary.json'
            supervisor = read(summary_path)
            check(process['key'] + ': planned STOP/TERM exact cleanup', supervisor['passed'] and supervisor['stop_reason'] == 'owned_stop_marker'
                  and supervisor['child_returncode'] == -15 and supervisor['cleanup']['signals'] == ['TERM']
                  and not supervisor['cleanup']['pending_group_members'] and not supervisor['cleanup']['after']['live'])
            peak = max(peak, *(int(s['memory.current']) for s in supervisor['samples']))
        check(node + ': sampled aggregate DRAM under6GiB', peak < 6 << 30)
        resources[node] = {'sampled_dram_peak_bytes': peak, 'continuous_peak_certified': False}
    budget = read(args.actual / 'budget_evidence.json')
    monitor = read(args.actual / 'budget_monitor.json')
    for node in resources:
        snapshots = [budget['nodes'][node]] + [sample['nodes'][node] for sample in monitor] + [r['gate'][node] for r in rows]
        upper = max(s['hbm_absolute_upper_bytes'] for s in snapshots)
        check(node + ': same-HIP absolute sampled upper under2GiB', upper < 2 << 30)
        resources[node]['hbm_absolute_sampled_upper_bytes'] = upper
    model_path = Path(plan['frozen_model_ref']['path'])
    check('original frozen model untouched', sha(model_path) == plan['frozen_model_ref']['sha256']
          == 'e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f')
    mean = {}
    for mode in (() if args.repeat else ('native', 'diagnostic')):
        mean[mode] = {str(size): statistics.mean(r['service_s'] for r in rows
                      if r['case']['mode'] == mode and r['case']['nbytes'] == size << 20)*1000 for size in (4, 16)}
        mean[mode]['large_minus_small_ms'] = mean[mode]['16']-mean[mode]['4']
    contributions = {}
    for phase in ([] if args.repeat else diag_rows[0]['flow']['execution'][4]['records'][0]['local_slope_diagnosis']['host_phases']):
        values = {size: statistics.mean(r['flow']['execution'][4]['records'][0]['local_slope_diagnosis']['host_phases'][phase]['wall_ns']
                  for r in diag_rows if r['case']['nbytes'] == size << 20)/1000 for size in (4, 16)}
        contributions[phase] = {'small_us': values[4], 'large_us': values[16], 'small_minus_large_us': values[4]-values[16]}
    result = {'status': 'repeat_actual_evidence_passed' if args.repeat else 'local_slope_actual_evidence_passed_cause_unidentified', 'checks': checks,
              'source_refs': refs, 'all_byte_sha_checked_bytes': bootstrap['verification']['checked_bytes'] + sum(r['flow']['verification']['checked_bytes'] for r in rows),
              'details': details, 'means_ms': mean, 'diagnostic_phase_contrasts': contributions, 'resources': resources,
              'root_cause_certified': False, 'native_slope_stably_reproduced': False,
              'intervention_recommendation': 'No source-slice warm intervention justified by these phases; event observer variation is larger. Keep all native data, including the 1.037124ms 16MiB observation.'}
    if args.repeat:
        result.update(actual_copy_bytes_including_bootstrap=(16 << 20)+sum(2*r['case']['nbytes'] for r in rows),
            source_slice_first_operation_cost_observed=all(d['source_slice_first_minus_second_us']['thread_cpu_ns'] > 0 for d in details),
            intervention_recommendation='No further GPU requests; same-handler first-operation host timing evidence does not attribute every historical size-slope observation.')
    (args.output / 'qualification.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'checks': len(checks), 'refs': len(refs), 'bytes': result['all_byte_sha_checked_bytes']}))


if __name__ == '__main__':
    main()
