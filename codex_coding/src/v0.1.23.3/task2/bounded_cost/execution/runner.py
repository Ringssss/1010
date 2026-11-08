"""Validate a frozen bounded model in a fresh, physically verified warm session."""
import argparse
import copy
from pathlib import Path
import sys
import time
from common import ROOT, HERE, CALIBRATION, native, ops, write, ref, require, budget_snapshot, check_reference
from fluidckpt.cluster.control import Cluster, CONTROLLER_CLOCK_DOMAIN
from fluidckpt.cluster.planning import validate_plan
from fluidckpt.cluster.service_cost import new_state, check_state

sys.path.insert(0, str(HERE.parent / 'model'))
import predictor

MIB = 1 << 20
ROUTES = [(4, 'hbm', 4), (2, 'dram', 4), (4, 'ssd', 5)]
CASES = [('local_novel8', [(0, 8 * MIB, 0)]), ('mixed_novel8', [(i, 8 * MIB, 0) for i in range(3)])]

def make_plan(sources, workers, label, specs, owned):
    targets, operations = [], []
    for index, (route_index, size, offset) in enumerate(specs):
        source = sources[route_index]
        dst = ROUTES[route_index][2]
        target = native.target_from(source, workers, label + f'/target{index}', dst, 'hbm', off=offset, n=size)
        native.remember(owned, target, workers)
        op = native.one_plan(source, target, workers)['ops'][0]
        op.update(op_id=index, path=native.adapter.path_key(source['worker'], source['tier'], workers[source['worker']]['numa'], dst, 'hbm'))
        targets.append(target)
        operations.append(op)
    plan = dict(targets=targets, ops=operations, executor='serial', chunk_bytes=32 << 20, estimated_transfer_s=None)
    validate_plan(plan)
    return plan

def physical_fingerprint(workers):
    keys = ('actual_bdf', 'pci_numa_node', 'selected_hca_metadata', 'communication_environment')
    return [dict(**{key: copy.deepcopy(w[key]) for key in ('worker', 'node', 'gpu', 'numa', 'staging_bytes')}, runtime={key: copy.deepcopy(w['runtime_identity'][key]) for key in keys}) for w in workers]

def migration_attestation(workers, setup, launch, mesh, out, sources, content_lineage_ref):
    native.check_workers(workers, setup)
    budget_snapshot(workers, setup)
    require(mesh['incarnations'] == new_state(workers)['incarnations'], 'fresh workers changed after warm')
    require(mesh['necessary_pairs'] == [[2, 4], [4, 5]] and len(mesh['warm']) == 6 and all(w['warm_s'] >= 0 for w in mesh['warm']), 'fresh actual warm missing')
    require(all(w['inventory_diagnosis']['mode'] == 'cached' and w['inventory_diagnosis']['fresh_fields'] for w in workers), 'cached inventory/fresh diagnostics missing')
    old_path = CALIBRATION / 'execution_actual_r02/postwarm.inventory.json'
    old_workers = native.read(old_path)
    require(physical_fingerprint(old_workers) == physical_fingerprint(workers), 'physical calibration path/config mismatch')
    lineage = []
    for spec in ops.specs(Path(launch['setup_directory'])):
        for row in spec['assets']:
            path = ROOT / row['relative_path']
            require(native.sha(path) == row['sha256'], 'local controller/deployed source lineage changed: ' + str(path))
        prior_path = CALIBRATION / 'execution_prepared_r02' / (spec['node'] + '.setup.spec.json')
        previous = native.read(prior_path)
        current = {row['relative_path']: row['sha256'] for row in spec['assets']}
        require(all(current.get(row['relative_path']) == row['sha256'] for row in previous['assets']), 'old source overwritten')
        require(all(spec[key] == previous[key] for key in ('boot_id', 'container_id', 'container_started_at', 'topology_input')), 'prior setup identity/config changed')
        lineage.append(ref(prior_path))
    body = dict(physical_gate_passed=True, config_verified=True, source_lineage_verified=True, control_mode='cached', executor='serial', chunk_bytes=32 << 20, warm_pairs=['2:4', '4:5'], worker_physical=[{key:w[key] for key in ('worker','node','gpu','numa')} for w in workers], worker_incarnations=new_state(workers)['incarnations'], migration_validation=True, prior_inventory_ref=ref(old_path), prior_setup_refs=lineage, model_source_ref=launch['frozen_model_ref'], old_calibration_incarnations=new_state(old_workers)['incarnations'], fresh_incarnations=new_state(workers)['incarnations'], physical_fingerprint=physical_fingerprint(workers), mesh_ref=ref(out.parent / 'mesh/mesh.json'), fresh_workers=workers, claims=dict(new_session_validation=True, old_incarnation_model_validation=False, cold=False, continuous_peak_measured=False))
    body['source_lineage'] = {native.adapter.path_key(source['worker'],source['tier'],source['numa'],ROUTES[index][2],'hbm'):{key:source[key] for key in ('identity','generation','worker','tier','numa')} | dict(replica_id=source['id'],source_epoch=source['epoch']) for index,source in enumerate(sources)}
    body['source_content_lineage_ref'] = content_lineage_ref
    body['source_content_scope'] = 'HBM/SSD full16MiB SHA equal to training; DRAM identical frozen generator/seed/schema, every requested byte verified independently'
    write(out / 'migration_attestation.json', body)
    body['evidence_ref'] = ref(out / 'migration_attestation.json')
    return body

def predict(plan, workers, model, state):
    check_state(workers, state)
    require(state['warm_pairs'] == ['2:4', '4:5'], 'prediction state not exactly the two actual warm pairs')
    return predictor.predict(plan, workers, model, state)

def raw_identity_and_empty(workers, setup, *, premesh=False):
    # Rank is assigned by mesh, while physical/PID identities already exist.
    if premesh:
        require(all(w['worker'] in (None, -1) for w in workers), 'fresh premesh workers already have ranks')
    view = [dict(w, worker=index) for index, w in enumerate(workers)]
    native.check_workers(view, setup)
    require(all(not w['replicas'] and not any(w['used_bytes'].values()) for w in workers), 'new runtime has preexisting objects')
    return workers

def initialize_mesh(cluster, setup, out):
    raw = cluster.call_all('inventory')
    write(out / 'premesh.inventory.json', raw)
    raw_identity_and_empty(raw, setup, premesh=True)
    require(all(w['inventory_diagnosis']['mode'] == 'cached' for w in raw), 'fixed cached mode absent')
    reports = cluster.call_all('mesh', [{} for _ in range(6)])
    workers, rows = cluster.inventory()
    native.check_workers(workers, setup)
    require(not rows, 'mesh has preexisting objects')
    return reports

def raise_cleanup_if_primary_succeeded(journal):
    if journal.get('cleanup_failure') and not journal.get('failure'):
        raise RuntimeError(journal['cleanup_failure'])

def bootstrap_content_matches(bootstrap, old_bootstrap):
    # Live verify() keys are integer op IDs; JSON reload turns them into strings.
    def content(flow):
        ops = flow['plan']['ops']
        verification = flow['verification']
        require(len(ops) == 1 and verification['passed'] and verification['checked_bytes'] == ops[0]['n'], 'bootstrap full-byte single-op SHA missing')
        op_id = ops[0]['op_id']
        rows = verification['operations']
        require(len(rows) == 1, 'bootstrap verification operation count mismatch')
        row = rows.get(op_id, rows.get(str(op_id)))
        require(row is not None and row['op_id'] == op_id and row['n'] == ops[0]['n'], 'bootstrap op ID/size mismatch')
        require(row['source_sha256'] == row['target_sha256'], 'bootstrap source-target SHA mismatch')
        return {key:row[key] for key in ('n','source_sha256','target_sha256')}
    require(content(bootstrap) == content(old_bootstrap), 'fresh bootstrap all-byte content differs from old source')

def main():
    p = argparse.ArgumentParser()
    for key in ('setup', 'plan', 'model', 'output'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--stage', choices=('mesh', 'measure'), required=True)
    a = p.parse_args()
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=False)
    setup, launch = native.read(a.setup), native.read(a.plan)
    native.check_setup(setup)
    check_reference(launch['frozen_model_ref'])
    require(Path(a.model).resolve() == Path(launch['frozen_model_ref']['path']), 'model argument differs from frozen launch')
    frozen = predictor.load_frozen(a.model)
    cluster = Cluster([w['endpoint'] for w in setup['workers']], {}, executor='serial')
    journal = dict(status='running', owned=[], synthetic=True, training_requests=0, cold_pair_claim=False, cold_disk_claim=False, migration_validation=True)
    write(out / 'journal.json', journal)
    mesh_initialized = False
    try:
        if a.stage == 'mesh':
            ssd = native.ssd_stats(setup)
            require(all(s['files'] == 0 for s in ssd.values()), 'task SSD not empty; preserve unowned files')
            reports = initialize_mesh(cluster, setup, out)
            mesh_initialized = True
            pairs = [[2, 4], [4, 5]]
            warm = cluster.call_all('warm', [dict(pairs=pairs) for _ in range(6)])
            workers, rows = cluster.inventory()
            native.check_workers(workers, setup)
            require(not rows and len(warm) == 6 and all(w['warm_s'] >= 0 for w in warm), 'fresh necessary warm failed')
            write(out / 'mesh.json', dict(workers=workers, incarnations=new_state(workers)['incarnations'], mesh=reports, necessary_pairs=pairs, warm=warm, fixed_mode='cached'))
            return
        mesh = native.read(out.parent / 'mesh/mesh.json')
        mesh_initialized = True
        workers, _ = cluster.inventory()
        require(physical_fingerprint(workers) == physical_fingerprint(native.read(CALIBRATION / 'execution_actual_r02/postwarm.inventory.json')), 'physical calibration path/config mismatch before fixture creation')
        state = new_state(workers)
        state['warm_pairs'] = ['2:4', '4:5']
        sources = []
        source_lineage = []
        prior_measure = CALIBRATION / 'execution_actual_r02/measure'
        old_synth = native.read(prior_measure / 'journal.json')['synth']
        for index, (owner, tier, dst) in enumerate(ROUTES):
            workers, _ = cluster.inventory()
            budget_snapshot(workers, setup)
            prefix = setup['session'] + f'/source{index}'
            seed = native.synth_adopt(cluster, setup, workers, prefix, owner, index, 16 * MIB, journal['owned'], journal, out)
            old_seed = old_synth[index]['row']
            content_keys = ('identity', 'tp', 'meta', 'index', 'off', 'n', 'worker', 'tier', 'numa')
            require(all(seed[key] == old_seed[key] for key in content_keys), 'fresh synthetic fixture source schema differs from training')
            source_evidence = dict(route_index=index, old_synth_ref=ref(prior_measure / 'journal.json'), seed=3100 + index, schema_keys=list(content_keys), same_source_schema=True, current_id=seed['id'], prior_id=old_seed['id'], full_old_source_SHA_compared=False)
            if tier != 'dram':
                target = native.target_from(seed, workers, prefix + '/' + tier, owner, tier, ssd=setup['ssd'][f'n{owner // 2}'])
                native.remember(journal['owned'], target, workers)
                write(out / 'journal.json', journal)
                bootstrap = cluster.transfer(native.one_plan(seed, target, workers))
                write(out / f'bootstrap{index}.json', bootstrap)
                require(bootstrap['verification']['passed'] and bootstrap['verification']['checked_bytes'] == 16 * MIB, 'bootstrap all-byte SHA missing')
                old_bootstrap_path = prior_measure / f'bootstrap{index}.json'
                bootstrap_content_matches(bootstrap, native.read(old_bootstrap_path))
                source_evidence.update(old_bootstrap_ref=ref(old_bootstrap_path), full_old_source_SHA_compared=True)
                _, rows = cluster.inventory()
                seed = next(r for r in rows if r['id'] == target['id'])
            sources.append(seed)
            source_lineage.append(source_evidence)
        write(out / 'source_content_lineage.json', dict(sources=source_lineage, source1_full_old_SHA_comparison=False, source1_scope='identical frozen generator/schema/seed; all requested bytes separately verified in mixed request'))
        workers, _ = cluster.inventory()
        budget_snapshot(workers, setup)
        attestation = migration_attestation(workers, setup, launch, mesh, out, sources, ref(out / 'source_content_lineage.json'))
        model = predictor.bind_session(frozen, workers, attestation)
        write(out / 'bound_model.json', dict(model=model, source_frozen_model_ref=launch['frozen_model_ref'], attestation_ref=attestation['evidence_ref'], training_in_this_session=False))
        bound_ref = ref(out / 'bound_model.json')
        predictions = {}
        # Both predictions are frozen before either observation; local cannot update mixed.
        for label, specs in CASES:
            request_plan = make_plan(sources, workers, setup['session'] + '/' + label, specs, [])
            path = out / (label + '.preregistered_prediction.json')
            write(path, dict(plan=request_plan, prediction=predict(request_plan, workers, model, state), source_frozen_model_ref=launch['frozen_model_ref'], bound_model_ref=bound_ref, saved_ns=time.monotonic_ns()))
            predictions[label] = ref(path)
        freeze_path = out / 'prediction_freeze.json'
        write(freeze_path, dict(source_frozen_model_ref=launch['frozen_model_ref'], bound_model_ref=bound_ref, predictions=predictions, freeze_saved_ns=time.monotonic_ns(), measurement_requests_executed=0))
        freeze_ref = ref(freeze_path)
        validation = []
        for label, specs in CASES:
            owned = []
            journal['current_targets'] = owned
            begin = time.monotonic_ns()
            telemetry = []
            fresh, rows = cluster.inventory(compact=True, telemetry=telemetry)
            inventoried = time.monotonic_ns()
            gate = budget_snapshot(fresh, setup, incremental_reserve=64 * MIB)
            gated = time.monotonic_ns()
            request_plan = make_plan(sources, fresh, setup['session'] + '/' + label, specs, owned)
            prediction = predict(request_plan, fresh, model, state)
            planned = time.monotonic_ns()
            for reference in (launch['frozen_model_ref'], bound_ref, freeze_ref, *predictions.values()):
                check_reference(reference)
            expected = native.read(predictions[label]['path'])
            require(expected['plan'] == request_plan and expected['prediction'] == prediction, 'fresh in-window plan/prediction differs from preregistration')
            require(native.read(freeze_path)['freeze_saved_ns'] < begin, 'both predictions must precede first request')
            write(out / 'journal.json', journal)
            before = native.ssd_stats(setup)
            flow = cluster.transfer(request_plan)
            after = native.ssd_stats(setup)
            total_bytes = sum(s[1] for s in specs)
            require(flow['verification']['passed'] and flow['verification']['checked_bytes'] == total_bytes, 'full request all-byte SHA missing')
            for reference in (launch['frozen_model_ref'], bound_ref, freeze_ref, *predictions.values()):
                check_reference(reference)
            timing = flow['controller_timing']
            require(timing['clock_domain'] == CONTROLLER_CLOCK_DOMAIN, 'native flow timing is not controller clock domain')
            filled = (timing['all_targets_filled_ns'] - begin) / 1e9
            published = (timing['all_targets_published_ns'] - begin) / 1e9
            target_workers = {op['dst_w'] for op in request_plan['ops']}
            target_s = max(w['execute_s'] for w in flow['execution'] if w['worker'] in target_workers)
            row = dict(label=label, nbytes=total_bytes, operations=len(specs), flow=flow, prediction=prediction, source_frozen_model_ref=launch['frozen_model_ref'], bound_model_ref=bound_ref, prediction_freeze_ref=freeze_ref, preregistered_prediction_ref=predictions[label], request_timing=dict(clock_domain=CONTROLLER_CLOCK_DOMAIN, request_begin_ns=begin, inventory_end_ns=inventoried, gate_end_ns=gated, plan_end_ns=planned, filled_ns=timing['all_targets_filled_ns'], published_ns=timing['all_targets_published_ns'], request_to_filled_s=filled, request_to_published_s=published), fresh_query_rpc=telemetry, gate=gate, target_s=target_s, ssd_io=native.stats_delta(before, after), execute_relative_error=(prediction['estimated_execute_s'] - flow['execute_wall_s']) / flow['execute_wall_s'], migration_validation=True, repeated=False)
            row['predicted_request_to_filled_s'] = prediction['estimated_full_filled_s']
            row['predicted_request_to_published_s'] = prediction['estimated_full_published_s']
            row['filled_relative_error'] = (prediction['estimated_full_filled_s'] - filled) / filled
            row['published_relative_error'] = None if prediction['estimated_full_published_s'] is None else (prediction['estimated_full_published_s'] - published) / published
            if len(specs) == 1:
                row.update(path=request_plan['ops'][0]['path'], service_s=max(r['elapsed_s'] for w in flow['execution'] for r in w['records'] if r['role'] in ('target', 'both')))
                row['service_relative_error'] = (prediction['estimated_service_s'] - row['service_s']) / row['service_s']
            write(out / (label + '.json'), row)
            validation.append(row)
            write(out / 'validation.json', validation)
            native.exact_release(cluster, owned, journal, 'end_' + label)
            journal['current_targets'] = []
            write(out / 'journal.json', journal)
        journal['status'] = 'complete'
        write(out / 'summary.json', dict(status='complete', training_requests=0, query_AB_requests=0, validation_requests=2, source_frozen_model_ref=launch['frozen_model_ref'], bound_model_ref=bound_ref, prediction_freeze_ref=freeze_ref, mode='cached', migration_validation=True, continuous_peak_measured=False))
    except BaseException as exc:
        journal.update(status='failed', failure=repr(exc))
        raise
    finally:
        try:
            owned = {r['id']: r for r in journal['owned'] + journal.get('current_targets', [])}
            native.exact_release(cluster, list(owned.values()), journal, 'explicit_end_of_run')
            if mesh_initialized:
                workers, rows = cluster.inventory()
            else:
                workers = raw_identity_and_empty(cluster.call_all('inventory'), setup)
                rows = []
            require(not rows and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in workers), 'owned run left valid objects/usage/transactions')
            ssd = native.ssd_stats(setup)
            require(all(s['files'] == 0 for s in ssd.values()), 'own task SSD files remain')
            write(out / 'final_inventory.json', dict(workers=workers, replicas=rows, ssd=ssd))
        except BaseException as exc:
            journal['cleanup_failure'] = repr(exc)
        write(out / 'journal.json', journal)
        raise_cleanup_if_primary_succeeded(journal)

if __name__ == '__main__':
    main()
