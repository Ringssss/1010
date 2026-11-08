"""Read collected single-sender artifacts without extracting or running GPU code."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'execution'))
from simple_contracts import RANK_BYTES, serialized_sender_evidence


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    runtime = Path(plan['handoff']).parent
    archives, refs, checks, values = {}, [], [], {}
    def check(name, passed, detail=None):
        checks.append(dict(name=name, passed=bool(passed), detail=detail))
    for node in plan['nodes']:
        path = args.run_dir / ('collection_' + node + '.tar')
        if not path.exists():
            check(node + '_archive_available', False)
            archives[node] = {}
            continue
        refs.append(dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        content = {}
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            if len(members) > 20000 or sum(m.size for m in members) > 512 << 20:
                raise ValueError('collected archive exceeds registered bound')
            for member in members:
                name = PurePosixPath(member.name)
                if not member.isfile() or name.is_absolute() or '..' in name.parts:
                    raise ValueError('unexpected archive member')
                if member.name in content:
                    raise ValueError('duplicate collected archive member')
                content[member.name] = archive.extractfile(member).read()
        archives[node] = content
        check(node + '_archive_available', True, len(content))
    def load(name, node='node27'):
        raw = archives[node].get(name)
        return json.loads(raw) if raw is not None else None
    def verified_ref(ref):
        path = Path(ref['path'])
        try:
            relative = str(path.relative_to(runtime))
            raw = archives['node27'].get(relative)
        except ValueError:
            raw = path.read_bytes() if path.is_file() else None
        return raw is not None and hashlib.sha256(raw).hexdigest() == ref['sha256']
    def service(flow):
        ready = {0: 0., 1: 0., 2: 0.}
        for op in flow['ops']:
            if model.get('schema') == 'simple_exact_extent_model_v1':
                duration = model['point_costs'][op['path']][str(op['n'])]['service_s']
            else:
                cost = model['paths'][op['path']]
                duration = cost['operation_s'] + op['n'] / 1e9 / cost['warm_GBps']
            end = max(ready[op['src_w']], ready[op['dst_w']]) + duration
            ready[op['src_w']] = ready[op['dst_w']] = end
        return max(ready.values())
    bootstrap = load('phase_bootstrap/bootstrap.json')
    check('full_bootstrap_available', bootstrap is not None)
    if bootstrap:
        check('complete_verified_capture_and_physical_pause', bootstrap['status'] == 'passed' and
              bootstrap['physical_park_passed'] and [m['n'] for m in bootstrap['manifests']] == [RANK_BYTES] * 2 and
              all(row[0]['verification']['passed'] and row[0]['verification']['checked_bytes'] == RANK_BYTES for row in bootstrap['capture']))
        remote = [r for r in bootstrap['replicas'] if r['worker'] == 2]
        check('two_complete_helper_rank_sources_correct_NUMA', sorted((r['tp'], r['numa'], r['n'], r['tier'], r['state']) for r in remote) ==
              [(0, 4, RANK_BYTES, 'dram', 'VALID'), (1, 7, RANK_BYTES, 'dram', 'VALID')])
        helper = next(w for w in bootstrap['workers'] if w['worker'] == 2)
        allocations = {a.get('id'): a for a in helper['host_allocations'] if a.get('id')}
        check('helper_sources_touched_registered_sampled_on_each_NUMA', all(
            r['id'] in allocations and allocations[r['id']]['registered'] and
            allocations[r['id']]['sampled_page_distribution'] == {str(r['numa']): 1.0}
            for r in remote))
    calibration = load('phase_calibrate/calibration.json')
    model = load('phase_calibrate/model.json')
    frozen = load('phase_calibrate/frozen_prediction.json')
    protocol = load('phase_calibrate/protocol.json')
    observations = load('phase_validate/observations.json')
    check('calibration_freeze_holdouts_available', all(x is not None for x in [calibration, model, frozen, protocol, observations]))
    if calibration:
        sizes = calibration['context'].get('calibration_sizes', [1 << 29, 1 << 30])
        check('six_paths_exact_two_sizes_no_shared_samples', len(calibration['samples']) == 6 and calibration['shared_samples'] == {} and
              all(sorted(r['nbytes'] for r in rows) == sizes and
                  all(r['passed'] and r['verified_bytes'] == r['nbytes'] for r in rows)
                  for rows in calibration['samples'].values()))
        prefix = 'phase_calibrate/path_cal_' if 'path_cost_policy' in calibration['context'] else 'phase_calibrate/path_'
        path_files = [n for n in archives['node27'] if n.startswith(prefix) and n.endswith('.json')]
        check('twelve_raw_calibration_flows_all_SHA', len(path_files) == 12 and all(
            load(n)['flow']['verification']['passed'] and load(n)['resource_gate']['passed'] and load(n)['target_retired']
            for n in path_files))
        if 'path_cost_policy' in calibration['context']:
            warmups = [n for n in archives['node27'] if n.startswith('phase_calibrate/path_warmup_') and n.endswith('.json')]
            check('six_verified1GiB_path_warmups_outside_calibration', len(warmups) == 6 and all(
                load(n)['flow']['verification']['passed'] and load(n)['flow']['verification']['checked_bytes'] == 1 << 30 and
                load(n)['resource_gate']['passed'] and load(n)['target_retired'] for n in warmups))
    if model:
        check('native_single_sender_model_only', model['context']['shared_resource_paths'] == {} and model['shared_qualifiers'] == {})
        c0 = load('phase_calibrate/C0_mixed.json')
        check('C0_outer_residual_once_recomputed', c0 is not None and abs(
            model['online_outer_s'] - ((c0['all_weights_ready_ns'] - c0['load_begin_ns']) / 1e9 - service(c0['plan']))) <= 1e-9)
    if frozen:
        payload = frozen['payload']
        check('immutable_frozen_payload_SHA', digest(payload) == frozen['payload_sha256'])
        check('all_calibration_refs_immutable', all(verified_ref(ref) for ref in payload['calibration_refs']))
        check('model_and_protocol_match_freeze', model is not None and digest(model) == payload['model_sha256'] and verified_ref(payload['protocol_ref']))
        check('both_holdouts_frozen_together', set(payload['predictions']) == {'Hnear', 'Hremote'})
    for case, phase in [('C0_mixed', 'phase_calibrate'), ('Hnear', 'phase_validate'), ('Hremote', 'phase_validate')]:
        record = load(f'{phase}/{case}.json')
        if record is None:
            check(case + '_complete_record', False)
            continue
        raw = load(f'{phase}/{case}_raw.json')
        check(case + '_raw_clock_consistency', raw is not None and all(raw[k] == record[k] for k in
              ['load_begin_ns', 'all_weights_ready_ns', 'load_begin_wall_ns', 'clock_domain']))
        actual = record['all_weights_ready_ns'] - record['load_begin_ns']
        values[case] = dict(actual_online_ns=actual)
        check(case + '_actual_new_single_sender_request', record['evidence_kind'] == 'actual_model_online' and
              record['session'] == plan['run_id'] and record['workflow'] == plan['workflow'])
        timing = raw['rank_timing']['ranks'] if raw else {}
        check(case + '_both_weights_ready_before_ACK_clock', set(timing) == {'0', '1'} and
              len({r['host'] for r in timing.values()}) == 1 and all(
                  record['load_begin_ns'] <= r['begin_ns'] <= r['end_ns'] <= record['all_weights_ready_ns']
                  for r in timing.values()))
        check(case + '_full_workload_66ops', len(record['plan']['ops']) == 66 and sum(o['n'] for o in record['plan']['ops']) == 2 * RANK_BYTES)
        check(case + '_SHA_after_ready_exact_golden', record['verification_begin_ns'] >= record['all_weights_ready_ns'] and
              record['verification']['passed'] and record['verification']['checked_bytes'] == 2 * RANK_BYTES and
              record['exact_reference_output_passed'] and bootstrap is not None and record['reply']['text'] == bootstrap['reference']['text'])
        check(case + '_every_op_full_SHA', len(record['verification']['operations']) == 66 and all(
            v['source_sha256'] == v['target_sha256'] and v['source_sha256'] for v in record['verification']['operations'].values()))
        check(case + '_actual_physical_park_both90percent', record['physical_park']['passed'] and all(
            10 * r['engine_allocated_drop_bytes'] >= 9 * r['weight_bytes'] and
            10 * r['physical_free_gain_bytes'] >= 9 * r['weight_bytes'] for r in record['physical_park']['rows']))
        check(case + '_resource_gates_live_and_parked', record['ready_resource_gate']['passed'] and record['resource_gate']['passed'])
        try:
            evidence = serialized_sender_evidence(raw['flow']['execution'], record['plan'])
            check(case + '_native_sender_serial_evidence', evidence['passed'])
        except (ValueError, KeyError) as exc:
            check(case + '_native_sender_serial_evidence', False, str(exc))
        if case != 'C0_mixed' and frozen and model and protocol:
            prediction = frozen['payload']['predictions'][case]
            predicted = prediction['estimated_online_ns']
            check(case + '_new_request_after_immutable_freeze', record['load_begin_wall_ns'] > frozen['payload']['freeze_wall_ns'] and
                  record['run_id'] not in frozen['payload']['calibration_run_ids'])
            check(case + '_frozen_plan_matches_actual', digest(record['plan']) == prediction['plan_sha256'])
            check(case + '_prediction_recomputed_DAG_outer_once', round((service(record['plan']) + model['online_outer_s']) * 1e9) == predicted)
            error = predicted - actual
            values[case].update(predicted_online_ns=predicted, signed_relative_error=error / actual, ten_percent_passed=10 * abs(error) <= actual)
            check(case + '_whole_online_10percent', 10 * abs(error) <= actual)
    guard = archives['node30'].get('helper_memory_guard.jsonl')
    check('helper_normal_guard_samples_available', guard is not None)
    if guard:
        samples = [json.loads(line) for line in guard.splitlines()]
        check('helper_guard_all_samples_free_reserved_bounds', samples and all(
            s['free_bytes'] >= 2 << 30 and s['reserved_bytes'] <= 2 << 30 and s['interval_s'] == .5 and not s['peak_verified'] for s in samples))
        values['helper_guard'] = dict(samples=len(samples), minimum_sampled_free_bytes=min(s['free_bytes'] for s in samples),
                                     maximum_sampled_reserved_bytes=max(s['reserved_bytes'] for s in samples), peak_verified=False)
    check('no_helper_ALARM', not any('ALARM' in name for name in archives['node30']))
    cleanup = load('phase_cleanup/cleanup.json')
    check('Catalog_final_cleanup', cleanup is not None and cleanup['passed'] and all(
        not any(w['used_bytes'].values()) and not w['transactions'] for w in cleanup['workers']))
    for process in plan['processes']:
        name = Path(process['out']).name + '/supervisor.summary.json'
        summary = load(name, process['node'])
        check(process['key'] + '_final_exact_owned_closure', summary is not None and summary['passed'] and summary['final_owned_closure']['passed'])
    summary_path = args.run_dir / 'run_summary.json'
    collection_path = args.run_dir / 'collection_qualification.json'
    check('host_complete_run', summary_path.exists() and json.loads(summary_path.read_text())['passed'])
    check('both_final_nodes_quiet', collection_path.exists() and json.loads(collection_path.read_text())['passed'])
    result = dict(passed=all(c['passed'] for c in checks), checks=checks, measurements=values, archive_refs=refs,
                  actual_model_online_qualified=all(c['passed'] for c in checks), concurrent_multiNIC_qualified=False,
                  task_hbm_actual_bytes=None, task_hbm_attribution_complete=False, peak_verified=False,
                  reviewer_GPU_SSH_Docker_actions=0, scope='Collected complete online single-sender model domain only.')
    (args.out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(passed=result['passed'], check_count=len(checks), failed=[c['name'] for c in checks if not c['passed']], measurements=values)))


if __name__ == '__main__':
    main()
