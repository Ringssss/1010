"""Exercise the authentic six-worker local path with a frozen eight-request matrix."""
import argparse
import copy
from pathlib import Path
import time
from common import ROOT, HERE, CALIBRATION, PROTOCOL, CASES, native, ops, write, ref, require, budget_snapshot, check_reference, load
from fluidckpt.cluster.control import Cluster, CONTROLLER_CLOCK_DOMAIN
from fluidckpt.cluster.service_cost import new_state
from fluidckpt.cluster.planning import validate_plan

def bootstrap_content_matches(flow, old):
    def content(item):
        require(len(item['plan']['ops']) == 1 and item['verification']['passed'], 'single bootstrap verification required')
        op = item['plan']['ops'][0]
        values = item['verification']['operations']
        row = values.get(op['op_id'], values.get(str(op['op_id'])))
        require(row is not None and item['verification']['checked_bytes'] == op['n'] == row['n'], 'all bootstrap bytes required')
        require(row['source_sha256'] == row['target_sha256'], 'bootstrap source/target mismatch')
        return {key:row[key] for key in ('n', 'source_sha256', 'target_sha256')}
    require(content(flow) == content(old), 'original16MiB source content differs')

def fingerprint(workers):
    keys = ('actual_bdf', 'pci_numa_node', 'selected_hca_metadata', 'communication_environment')
    return [dict(**{key:copy.deepcopy(w[key]) for key in ('worker', 'node', 'gpu', 'numa', 'staging_bytes')},
                 runtime={key:copy.deepcopy(w['runtime_identity'][key]) for key in keys}) for w in workers]

def make_plan(source, workers, session, case, owned):
    target = native.target_from(source, workers, session + '/' + case['label'] + '/target0', 4, 'hbm', off=0, n=case['nbytes'])
    native.remember(owned, target, workers)
    plan = native.one_plan(source, target, workers)
    plan.update(local_slope_mode=case['mode'], local_slope_label=case['label'])
    plan['ops'][0]['path'] = native.adapter.path_key(4, 'hbm', workers[4]['numa'], 4, 'hbm')
    validate_plan(plan)
    return plan

def initialize_mesh(cluster, setup, out):
    raw = cluster.call_all('inventory')
    write(out / 'premesh.inventory.json', raw)
    require(all(w['worker'] in (None, -1) for w in raw), 'rank assigned before mesh')
    native.check_workers([dict(w, worker=index) for index, w in enumerate(raw)], setup)
    require(all(w['inventory_diagnosis']['mode'] == 'cached' and not w['replicas'] and not any(w['used_bytes'].values()) for w in raw), 'cached empty runtime required')
    reports = cluster.call_all('mesh', [{} for _ in range(6)])
    pairs = [[2, 4], [4, 5]]
    warm = cluster.call_all('warm', [dict(pairs=pairs) for _ in range(6)])
    workers, rows = cluster.inventory()
    native.check_workers(workers, setup)
    require(not rows and len(warm) == 6 and all(w['warm_s'] >= 0 for w in warm), 'actual necessary pair warm failed')
    write(out / 'mesh.json', dict(workers=workers, incarnations=new_state(workers)['incarnations'], mesh=reports, necessary_pairs=pairs, warm=warm, fixed_mode='cached'))

def main():
    p = argparse.ArgumentParser()
    for key in ('setup', 'plan', 'output'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--stage', choices=('mesh', 'measure'), required=True)
    a = p.parse_args()
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=False)
    setup, launch = native.read(a.setup), native.read(a.plan)
    native.check_setup(setup)
    require(launch['protocol'] == PROTOCOL and launch['model_consumed'] is False, 'fixed no-fit diagnostic protocol required')
    check_reference(launch['frozen_model_ref'])
    cluster = Cluster([w['endpoint'] for w in setup['workers']], {}, executor='serial')
    journal = dict(status='running', owned=[], synthetic=True, fits=0, model_updates=0, GPU_measurements=0)
    write(out / 'journal.json', journal)
    meshed = a.stage == 'measure'
    try:
        if a.stage == 'mesh':
            require(all(row['files'] == 0 for row in native.ssd_stats(setup).values()), 'unowned SSD files present')
            initialize_mesh(cluster, setup, out)
            meshed = True
            return
        mesh = native.read(out.parent / 'mesh/mesh.json')
        workers, rows = cluster.inventory()
        native.check_workers(workers, setup)
        budget_snapshot(workers, setup)
        require(mesh['incarnations'] == new_state(workers)['incarnations'], 'worker incarnation changed after warm')
        old_workers = native.read(CALIBRATION / 'execution_actual_r02/postwarm.inventory.json')
        require(fingerprint(workers) == fingerprint(old_workers), 'original physical execution configuration differs')
        for spec in ops.specs(Path(launch['setup_directory'])):
            for row in spec['assets']:
                require(native.sha(ROOT / row['relative_path']) == row['sha256'], 'controller frozen source lineage changed')
        old_measure = CALIBRATION / 'execution_actual_r02/measure'
        old_seed = native.read(old_measure / 'journal.json')['synth'][0]['row']
        prefix = setup['session'] + '/source0'
        seed = native.synth_adopt(cluster, setup, workers, prefix, 4, 0, 16 << 20, journal['owned'], journal, out)
        keys = ('identity', 'tp', 'meta', 'index', 'off', 'n', 'worker', 'tier', 'numa')
        require(all(seed[key] == old_seed[key] for key in keys), 'original synthetic source schema differs')
        target = native.target_from(seed, workers, prefix + '/hbm', 4, 'hbm')
        native.remember(journal['owned'], target, workers)
        write(out / 'journal.json', journal)
        bootstrap = cluster.transfer(native.one_plan(seed, target, workers))
        write(out / 'bootstrap0.json', bootstrap)
        bootstrap_content_matches(bootstrap, native.read(old_measure / 'bootstrap0.json'))
        workers, rows = cluster.inventory()
        source = next(row for row in rows if row['id'] == target['id'])
        write(out / 'source_lineage.json', dict(source=source, old_synth_ref=ref(old_measure / 'journal.json'),
              old_bootstrap_ref=ref(old_measure / 'bootstrap0.json'), same_source_schema=True, full16MiB_SHA_equal=True,
              physical_fingerprint=fingerprint(workers), model_used=False))
        plans = {case['label']:make_plan(source, workers, setup['session'], case, []) for case in CASES}
        write(out / 'preregistration.json', dict(protocol=PROTOCOL, plans=plans, saved_ns=time.monotonic_ns(), measurements=0))
        frozen_ref = ref(out / 'preregistration.json')
        results = []
        for case in CASES:
            journal['current_targets'] = []
            begin = time.monotonic_ns()
            telemetry = []
            fresh, _ = cluster.inventory(compact=True, telemetry=telemetry)
            inventory_end = time.monotonic_ns()
            gate = budget_snapshot(fresh, setup)
            plan = make_plan(source, fresh, setup['session'], case, journal['current_targets'])
            check_reference(frozen_ref)
            require(plan == plans[case['label']] and native.read(out / 'preregistration.json')['saved_ns'] < begin, 'fresh plan differs or preregistration late')
            write(out / 'journal.json', journal)
            flow = cluster.transfer(plan)
            require(flow['verification']['passed'] and flow['verification']['checked_bytes'] == case['nbytes'], 'all-byte SHA verification required')
            owner = flow['execution'][4]
            records = owner['records']
            require(len(records) == 1 and records[0]['role'] == 'both' and records[0]['bytes'] == case['nbytes'], 'single local operation record required')
            require(owner['local_slope_prepare']['mode'] == case['mode'], 'actual worker mode differs')
            if case['mode'] == 'native':
                require(owner['executor_source'] == 'unchanged native ClusterWorker._execute' and 'local_slope_diagnosis' not in records[0], 'control did not use original native executor')
            else:
                require(records[0]['local_slope_diagnosis']['host_residual_ns'] == 0, 'host phase partition not exact')
            timing = flow['controller_timing']
            require(timing['clock_domain'] == CONTROLLER_CLOCK_DOMAIN, 'controller clock domain differs')
            row = dict(case=case, flow=flow, service_s=records[0]['elapsed_s'], worker_execute_s=owner['execute_s'],
                request_begin_ns=begin, inventory_end_ns=inventory_end,
                request_to_filled_s=(timing['all_targets_filled_ns']-begin)/1e9,
                request_to_published_s=(timing['all_targets_published_ns']-begin)/1e9,
                fresh_query_rpc=telemetry, gate=gate, preregistration_ref=frozen_ref,
                model_frozen_ref=launch['frozen_model_ref'], prediction=None, fit=None,
                clock_scope='controller single monotonic clock for request; worker durations independently recorded')
            write(out / (case['label'] + '.json'), row)
            results.append(row)
            write(out / 'measurements.json', results)
            journal['GPU_measurements'] += 1
            native.exact_release(cluster, journal['current_targets'], journal, 'end_' + case['label'])
            journal['current_targets'] = []
            write(out / 'journal.json', journal)
        check_reference(launch['frozen_model_ref'])
        journal['status'] = 'complete'
        write(out / 'summary.json', dict(status='complete', requests=8, mode_sizes=[case for case in CASES], fits=0,
              model_updates=0, prediction=None, intervention_requests=0, preregistration_ref=frozen_ref,
              ordering_limit=PROTOCOL['ordering_limit'], continuous_peak_measured=False))
    except BaseException as exc:
        journal.update(status='failed', failure=repr(exc))
        raise
    finally:
        try:
            owned = {row['id']:row for row in journal['owned'] + journal.get('current_targets', [])}
            native.exact_release(cluster, list(owned.values()), journal, 'explicit_end_of_run')
            if meshed:
                workers, rows = cluster.inventory()
            else:
                workers, rows = cluster.call_all('inventory'), []
            require(not rows and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in workers), 'owned objects or transactions remain')
            ssd = native.ssd_stats(setup)
            require(all(row['files'] == 0 for row in ssd.values()), 'SSD files remain')
            write(out / 'final_inventory.json', dict(workers=workers, replicas=rows, ssd=ssd))
        except BaseException as exc:
            journal['cleanup_failure'] = repr(exc)
        write(out / 'journal.json', journal)
        if journal.get('cleanup_failure') and not journal.get('failure'):
            raise RuntimeError(journal['cleanup_failure'])

if __name__ == '__main__':
    main()
