"""Behavioral CPU checks of coefficient lineage, domain refusal, and DAG use."""
import argparse
import copy
import json
from pathlib import Path
import statistics

from predictor import MIB, CHUNK_BYTES, bind_session, digest, file_ref, incarnations, load_frozen, physical, predict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frozen', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    frozen = load_frozen(args.frozen)
    checks = []

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)

    def refuses(name, function):
        try:
            function()
        except (ValueError, KeyError):
            check(name, True)
        else:
            check(name, False)

    workers = [dict(w, session='CPU_FIXTURE_NO_GPU', epoch='CPU_FIXTURE_epoch_' + str(w['worker'])) for w in frozen['model']['worker_physical']]
    paths = ['4:hbm:0->4', '2:dram:4->4', '4:ssd:0->5']
    source_lineage = {p: dict(replica_id='CPU_FIXTURE_' + p, identity='CPU_FIXTURE_identity_' + p,
                             generation=1, source_epoch=workers[c['endpoints'][0]]['epoch'],
                             worker=c['endpoints'][0], tier=c['source_tier'], numa=c['source_numa'])
                      for p, c in frozen['model']['paths'].items()}
    attestation = dict(physical_gate_passed=True, config_verified=True, source_lineage_verified=True,
                       control_mode='cached', executor='serial', chunk_bytes=CHUNK_BYTES,
                       worker_physical=physical(workers), worker_incarnations=incarnations(workers),
                       warm_pairs=['2:4', '4:5'], model_source_ref=frozen['artifact_ref'],
                       source_lineage=source_lineage,
                       evidence_scope='CPU API fixture; no actual physical or GPU qualification')
    attest_path = out / 'cpu_fixture_attestation.json'
    attest_path.write_text(json.dumps(attestation, indent=2, sort_keys=True) + '\n')
    attestation['evidence_ref'] = file_ref(attest_path)
    model = bind_session(frozen, workers, attestation)
    state = {'incarnations': incarnations(workers), 'warm_pairs': ['2:4', '4:5']}
    ops = [dict(op_id=i, path=p, src_w=model['paths'][p]['endpoints'][0],
                dst_w=model['paths'][p]['endpoints'][1], n=8 * MIB,
                kind=model['paths'][p]['operation_kind'],
                replica_id=source_lineage[p]['replica_id'], identity=source_lineage[p]['identity'], generation=1,
                source_epoch=workers[model['paths'][p]['endpoints'][0]]['epoch']) for i, p in enumerate(paths)]
    mixed = dict(executor='serial', chunk_bytes=CHUNK_BYTES, ops=ops, estimated_transfer_s=None)
    local = dict(mixed, ops=ops[:1])
    result = predict(mixed, workers, model, state)
    local_result = predict(local, workers, model, state)
    old_raw = json.loads(Path(frozen['source_refs']['training']['path']).read_text()) + json.loads(Path(frozen['source_refs']['training_repeats']['path']).read_text())
    expected_local = statistics.mean(r['service_s'] for r in old_raw if r['path'] == paths[0] and r['operations'] == 1)
    check('local coefficient uses all four prescribed single-op points', model['paths'][paths[0]]['operation_s'] == expected_local)
    check('local slope and bandwidth remain unidentified', model['paths'][paths[0]]['byte_slope_s_per_byte'] is None and model['paths'][paths[0]]['warm_GBps'] is None)
    diagnostic = json.loads(Path(frozen['source_refs']['frozen_model']['path']).read_text())['fit_diagnostics']['schema1_paths']
    for path in paths[1:]:
        check('unchanged existing fit ' + path, all(model['paths'][path][k] == diagnostic[path][k] for k in ('operation_s', 'warm_GBps')))
    durations = [model['paths'][p]['operation_s'] + (0 if p == paths[0] else 8 * MIB / (model['paths'][p]['warm_GBps'] * 1e9)) for p in paths]
    check('mixed dependency duration equals closed-form sum', abs(result['estimated_service_s'] - sum(durations)) < 1e-15)
    check('request residual counted once', abs(result['estimated_worker_s'] - sum(durations) - model['request_s']) < 1e-15)
    check('controller residual counted once', abs(result['estimated_execute_s'] - result['estimated_worker_s'] - model['dispatch_s']) < 1e-15)
    check('second operation waits for first at w4', result['timeline'][1]['source_wait_s'] == result['timeline'][0]['end_s'])
    check('third operation waits for reception at w4', result['timeline'][2]['target_wait_s'] == result['timeline'][1]['end_s'])
    check('no unsupported decision certification', result['decision_ready'] is False)
    check('unidentified publication cost is not predicted', result['estimated_full_published_s'] is None)
    for n in (MIB, 4 * MIB - 1, 16 * MIB + 1):
        bad = copy.deepcopy(local); bad['ops'][0]['n'] = n
        refuses('reject out-of-domain bytes ' + str(n), lambda: predict(bad, workers, model, state))
    for n in (4 * MIB, 16 * MIB):
        edge = copy.deepcopy(local); edge['ops'][0]['n'] = n
        check('accept bounded endpoint ' + str(n), predict(edge, workers, model, state)['estimated_service_s'] == expected_local)
    bad = copy.deepcopy(mixed); bad['ops'].append(dict(ops[0], op_id=3))
    refuses('reject four operations and batch1MiB generalization', lambda: predict(bad, workers, model, state))
    bad = dict(mixed, ops=[ops[0], dict(ops[0], op_id=1)])
    refuses('reject repeated path within three-operation limit', lambda: predict(bad, workers, model, state))
    refuses('reject cold pair', lambda: predict(mixed, workers, model, dict(state, warm_pairs=[])))
    stale = copy.deepcopy(workers); stale[4]['epoch'] = 'STALE'
    refuses('reject changed incarnation', lambda: predict(mixed, stale, model, state))
    shifted = copy.deepcopy(workers); shifted[0]['gpu'] = 7
    refuses('reject changed physical mapping', lambda: predict(mixed, shifted, model, state))
    changed = copy.deepcopy(model); changed['request_s'] = 0.
    refuses('reject post-binding coefficient tuning', lambda: predict(mixed, workers, changed, state))
    refuses('reject unbound model', lambda: predict(mixed, workers, frozen['model'], state))
    refuses('reject different chunk', lambda: predict(dict(mixed, chunk_bytes=16 * MIB), workers, model, state))
    refuses('reject different executor', lambda: predict(dict(mixed, executor='parallel'), workers, model, state))
    bad = copy.deepcopy(local); bad['ops'][0]['source_epoch'] = 'WRONG_SOURCE'
    refuses('reject stale source lineage', lambda: predict(bad, workers, model, state))
    bad = copy.deepcopy(local); bad['ops'][0]['identity'] = 'UNATTESTED'
    refuses('reject unattested source identity', lambda: predict(bad, workers, model, state))
    bad = copy.deepcopy(local); bad['ops'][0]['kind'] = 'local_dram'
    refuses('reject primitive inconsistent with calibrated path', lambda: predict(bad, workers, model, state))
    bad_attestation = copy.deepcopy(attestation); bad_attestation['config_verified'] = False
    refuses('reject attestation field differing from saved file', lambda: bind_session(frozen, workers, bad_attestation))
    check('frozen digest unchanged after CPU checks', digest(frozen['model']) == frozen['model_sha256'])
    report = {'status': 'cpu_api_passed', 'check_count': len(checks), 'checks': checks,
              'model_ref': frozen['artifact_ref'], 'local8_fixture_prediction': local_result,
              'mixed8_fixture_prediction': result, 'actual_validation': False,
              'scope': 'CPU fixtures validate arithmetic and refusal behavior only; no hardware success is implied.'}
    (out / 'qualification.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(checks), 'result': str(out / 'qualification.json')}))


if __name__ == '__main__':
    main()
