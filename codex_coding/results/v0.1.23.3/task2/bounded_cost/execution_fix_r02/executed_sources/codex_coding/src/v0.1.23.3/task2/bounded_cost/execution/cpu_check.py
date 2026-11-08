"""Check native plans, migration refusals and active-monitor gates without GPUs."""
import argparse
import copy
from pathlib import Path
from common import ROOT, CALIBRATION, native, ops, write, ref, require, fresh_output, budget_snapshot
from runner import ROUTES, CASES, make_plan, physical_fingerprint, predictor, predict
from fluidckpt.cluster.service_cost import new_state

def refused(action, message):
    try:
        action()
    except ValueError:
        return
    raise AssertionError(message)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    actual = CALIBRATION / 'execution_actual_r02'
    workers = native.read(actual / 'postwarm.inventory.json')
    setup = native.read(actual / 'runtime_setup.json')
    old_physical = physical_fingerprint(workers)
    synth = native.read(actual / 'measure/journal.json')['synth']
    sources = []
    for i, (src, tier, dst) in enumerate(ROUTES):
        row = copy.deepcopy(synth[i]['row'])
        row.update(tier=tier, id='cpu/source' + str(i))
        sources.append(row)
    plans = {label: make_plan(sources, workers, 'cpu/' + label, specs, []) for label, specs in CASES}
    require([(op['src_w'], op['dst_w'], op['kind']) for op in plans['mixed_novel8']['ops']] == [(4, 4, 'local_hbm'), (2, 4, 'network'), (4, 5, 'peer')], 'native route/type correspondence')
    for spec in ops.specs(CALIBRATION / 'execution_prepared_r02'):
        require(all(native.sha(ROOT / row['relative_path']) == row['sha256'] for row in spec['assets']), 'old local source lineage mismatch')
    for w in workers:
        w['session'] = 'cpu_new_session'
        w['epoch'] = 'cpu_new_epoch_' + str(w['worker'])
    for source in sources:
        source['epoch'] = workers[source['worker']]['epoch']
    require(physical_fingerprint(workers) == old_physical, 'physical mapping must be independent of incarnation')
    plans = {label: make_plan(sources, workers, 'cpu/' + label, specs, []) for label, specs in CASES}
    frozen = predictor.load_frozen(a.model)
    att = dict(physical_gate_passed=True, config_verified=True, source_lineage_verified=True, control_mode='cached', executor='serial', chunk_bytes=32 << 20, warm_pairs=['2:4','4:5'], worker_physical=predictor.physical(workers), worker_incarnations=predictor.incarnations(workers), model_source_ref=frozen['artifact_ref'], CPU_fixture_only=True, source_lineage={native.adapter.path_key(src,tier,workers[src]['numa'],dst,'hbm'):dict(replica_id=sources[index]['id'],identity=sources[index]['identity'],generation=sources[index]['generation'],source_epoch=sources[index]['epoch'],worker=src,tier=tier,numa=workers[src]['numa']) for index,(src,tier,dst) in enumerate(ROUTES)})
    write(out / 'cpu_attestation.json', att)
    att['evidence_ref'] = ref(out / 'cpu_attestation.json')
    bound = predictor.bind_session(frozen, workers, att)
    state = new_state(workers)
    state['warm_pairs'] = ['2:4','4:5']
    predictions = {label: predict(plan,workers,bound,state) for label,plan in plans.items()}
    tl = predictions['mixed_novel8']['timeline']
    require(tl[1]['start_s'] == tl[0]['end_s'] and tl[2]['start_s'] == tl[1]['end_s'], 'w4 local-receive-send serial dependence')
    refused(lambda: predict(plans['mixed_novel8'],workers,bound,new_state(workers)), 'cold migration must fail')
    restarted = copy.deepcopy(workers)
    restarted[4]['epoch'] += '_changed'
    refused(lambda: predict(plans['mixed_novel8'],restarted,bound,state), 'changed incarnation must fail')
    wrong_source = copy.deepcopy(plans['mixed_novel8'])
    wrong_source['ops'][0]['replica_id'] = sources[2]['id']
    refused(lambda: predict(wrong_source,workers,bound,state), 'wrong tier/source lineage must fail')
    fake = copy.deepcopy(att)
    fake['physical_gate_passed'] = False
    fake.pop('evidence_ref')
    write(out / 'failed_cpu_attestation.json',fake)
    fake['evidence_ref'] = ref(out / 'failed_cpu_attestation.json')
    refused(lambda: predictor.bind_session(frozen,workers,fake), 'migration must require successful real gates')
    live = native.read(actual / 'postwarm.inventory.json')
    live[4]['transactions'] = ['cpu_active']
    refused(lambda: budget_snapshot(live,setup), 'idle admission must refuse active transaction')
    monitored = budget_snapshot(live,setup,require_idle=False)
    require(monitored['node27']['transactions']['4'] == ['cpu_active'], 'monitor must retain actual active evidence')
    live[4]['runtime_identity']['actual_bdf'] = '0000:ff:00.0'
    refused(lambda: budget_snapshot(live,setup,require_idle=False), 'monitor must retain identity checks')
    changed = copy.deepcopy(workers)
    changed[4]['runtime_identity']['selected_hca_metadata']['name'] = 'wrong_hca'
    require(physical_fingerprint(changed) != old_physical, 'physical fingerprint must detect changed HCA')
    write(out / 'qualification.json',dict(passed=True,checks=['native local/network/peer routes','all prior local deployed-source SHAs','physical mapping independent of new epoch','fresh bind with saved attestation','w4 serial local-receive-send dependency','cold pair refusal','changed epoch refusal','wrong source tier/lineage refusal','failed physical gate refusal','idle gate rejects active','monitor retains active tx','monitor rejects changed BDF','physical HCA mismatch detection'],predictions=predictions,CPU_fixture_only=True,GPU_launches=0,remote_writes=0,source_frozen_model_ref=ref(a.model)))
    print(str(out / 'qualification.json'))

if __name__ == '__main__':
    main()
