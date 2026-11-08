"""Inherit exact service evidence; identify one post-prime outer residual."""
import copy
import importlib.util
import json
from pathlib import Path

from evaluate import digest, require, verify_ref
from simple_contracts import RANK_BYTES
from simple_model import (CALIBRATION_SIZES, GiB, build_model,
                          calibrate_outer as simple_calibrate_outer,
                          physical, predict as simple_predict,
                          transfer_service as simple_transfer_service)

DOMAIN = 'post_one_fullrestore_samecontroller'
ORDER = ['C0_prime', 'C0_cal', 'Hremote', 'Hnear']
INHERITANCE_SCHEMA = 'immutable_exact_path_inheritance_v1'


def load_ref(ref):
    verify_ref(ref)
    return json.loads(Path(ref['path']).read_text())


def validate_registration(registration):
    require(registration['schema'] == 'post_prime_outer_protocol_v1', 'outer registration schema')
    require(registration['outer_domain'] == DOMAIN, 'post-prime same-controller domain required')
    require(registration['complete_request_order'] == ORDER and
            registration['validation_order'] == ORDER[2:] and
            registration['max_complete_requests'] == 4, 'exact finite request sequence required')
    require(registration['prime_run_id'] == ORDER[0] and
            registration['outer_calibration_run_id'] == ORDER[1], 'prime/calibration aliases changed')
    require(type(registration['controller_pid']) is int and registration['controller_pid'] > 0 and
            registration['controller_clock_domain'] and registration['current_session'],
            'persistent current controller identity required')
    require(type(registration['registered_wall_ns']) is int, 'registration wall clock required')


def validate_source(source):
    require(source['schema'] == 'simple_exact_extent_model_v1', 'r02 exact service checkpoint required')
    require(source['online_outer_s'] is None and source['online_qualified'] is False and
            'outer_calibration_service' not in source, 'historical outer calibration must be discarded')
    refs = source['source_refs']
    require(len(refs) == 18 and len({r['path'] for r in refs}) == 18,
            'only original six warmup and twelve calibration raw refs required')
    raws = [load_ref(ref) for ref in refs]
    samples = {}
    for path in source['context']['path_context']:
        warmups = [(ref, raw) for ref, raw in zip(refs, raws)
                   if raw['path'] == path and raw['sample_role'] == 'warmup']
        rows = [(ref, raw) for ref, raw in zip(refs, raws)
                if raw['path'] == path and raw['sample_role'] == 'cal']
        require(len(warmups) == 1 and len(rows) == 2, 'original exact raw path domain changed')
        samples[path] = []
        for ref, raw in rows:
            n = raw['flow']['verification']['checked_bytes']
            durations = [r['elapsed_s'] for w in raw['flow']['execution'] for r in w['records']]
            require(durations, 'original operation durations missing')
            samples[path].append(dict(nbytes=n, service_s=max(durations), passed=True,
                verified_bytes=n, evidence_kind='actual_path_calibration',
                config_sha256=raw['config_sha256'], session=raw['session'],
                source_ref=ref, warmup_ref=warmups[0][0]))
    require(len(samples) == 6 and all(sorted(r['nbytes'] for r in rows) == CALIBRATION_SIZES
                                    for rows in samples.values()), 'six paths by exact two sizes required')
    return samples


def inherit_model(source_ref, context, workers, state, registration_ref):
    source = load_ref(source_ref)
    samples = validate_source(source)
    registration = load_ref(registration_ref)
    validate_registration(registration)
    require(registration['source_checkpoint_path_model_ref'] == source_ref, 'registered source alias changed')
    require(registration['current_session'] not in source['calibration_sessions'], 'old session relabeling forbidden')
    prior = source['context']
    require({k: v for k, v in context.items() if k != 'clock_domain'} ==
            {k: v for k, v in prior.items() if k != 'clock_domain'},
            'inherited path hypothesis allows only new controller clock; physical/path/workload changed')
    require(context['clock_domain'] != prior['clock_domain'] and
            context['clock_domain'] == registration['controller_clock_domain'], 'new controller clock required')
    rebuilt = build_model(prior, workers, samples, {}, source['source_refs'])
    require(rebuilt['point_costs'] == source['point_costs'] and
            rebuilt['topology_id'] == source['topology_id'] and
            rebuilt['calibration_sessions'] == source['calibration_sessions'],
            'original exact source parameters differ from immutable raw evidence')
    require(state['config_sha256'] == digest(context) and state['binding_source_refs'],
            'current source binding required')
    for ref in state['binding_source_refs']:
        verify_ref(ref)
    result = copy.deepcopy(source)
    result['context'] = copy.deepcopy(context)
    result['calibration_sessions'] = []
    result['source_refs'] = copy.deepcopy(source['source_refs']) + [source_ref, registration_ref] + state['binding_source_refs']
    result['path_cost_inheritance'] = dict(schema=INHERITANCE_SCHEMA,
        source_checkpoint_path_model_ref=source_ref, source_context=copy.deepcopy(prior),
        source_context_sha256=digest(prior), source_raw_refs=copy.deepcopy(source['source_refs']),
        source_calibration_sessions=copy.deepcopy(source['calibration_sessions']),
        point_costs_sha256=digest(source['point_costs']), registration_ref=registration_ref,
        current_session=registration['current_session'],
        interpretation='Original r02 exact service points inherited as an explicit same-physical-scope hypothesis, not newly measured current-session service.')
    result['outer_domain'] = DOMAIN
    result['current_controller'] = dict(pid=registration['controller_pid'],
                                       clock_domain=context['clock_domain'])
    validate_inheritance(result)
    return result


def validate_inheritance(model):
    lineage = model['path_cost_inheritance']
    require(lineage['schema'] == INHERITANCE_SCHEMA and model['outer_domain'] == DOMAIN, 'inherited model domain')
    source = load_ref(lineage['source_checkpoint_path_model_ref'])
    registration = load_ref(lineage['registration_ref'])
    validate_registration(registration)
    require(source['online_outer_s'] is None and 'outer_calibration_service' not in source,
            'historical outer model is not an inheritance source')
    require(lineage['source_context'] == source['context'] and
            lineage['source_context_sha256'] == digest(source['context']) and
            lineage['source_raw_refs'] == source['source_refs'] and
            lineage['source_calibration_sessions'] == source['calibration_sessions'], 'original source provenance mutated')
    require(model['point_costs'] == source['point_costs'] and
            lineage['point_costs_sha256'] == digest(source['point_costs']), 'original exact service costs changed')
    require({k: v for k, v in model['context'].items() if k != 'clock_domain'} ==
            {k: v for k, v in source['context'].items() if k != 'clock_domain'}, 'inherited context changed')
    require(model['context']['clock_domain'] == registration['controller_clock_domain'] and
            model['current_controller'] == dict(pid=registration['controller_pid'],
                clock_domain=registration['controller_clock_domain']) and
            lineage['current_session'] == registration['current_session'], 'current controller binding changed')
    require(all(ref in model['source_refs'] for ref in source['source_refs'] +
                [lineage['source_checkpoint_path_model_ref'], lineage['registration_ref']]), 'source provenance references removed')
    for ref in model['source_refs']:
        verify_ref(ref)
    return registration


def validate_record(record, model, index):
    registration = validate_inheritance(model)
    require(record['run_id'] == ORDER[index-1] and record['complete_request_index'] == index and
            record['prior_complete_restores'] == index-1, 'complete request sequence changed')
    require(record['controller_pid'] == registration['controller_pid'] and
            record['clock_domain'] == registration['controller_clock_domain'] and
            record['session'] == registration['current_session'], 'online controller incarnation changed')
    require(record['evidence_kind'] == 'actual_model_online' and record['full_sha_passed'] is True and
            record['exact_reference_output_passed'] is True and record['all_target_weights_usable'] is True and
            record['verified_bytes'] == 2 * RANK_BYTES and record['physical_park_passed'] is True and
            record['physical_park']['passed'] is True and record['engine_parked'] is True and
            record['resource_gate_passed'] is True and record['ready_resource_gate']['passed'] is True and
            record['cleanup_passed'] is True, 'complete verified restore and physical park required')
    require(record['verification_position'] == 'after_all_weights_ready' and
            record['verification_begin_ns'] >= record['all_weights_ready_ns'] and
            record['load_begin_ns'] < record['all_weights_ready_ns'], 'online/SHA clock boundary changed')
    require(record['online_weights_ready_s'] ==
            (record['all_weights_ready_ns']-record['load_begin_ns'])/1e9, 'persisted complete restore cost changed')
    require(record['load_begin_wall_ns'] > registration['registered_wall_ns'], 'request predates finite registration')
    require(record['state']['controller_pid'] == registration['controller_pid'] and
            record['state']['outer_domain'] == DOMAIN and
            record['state']['prior_complete_restores'] == index-1 and
            record['state']['current_session'] == registration['current_session'], 'state domain changed')
    require(record['incarnation_sha256'] == digest(record['state']['incarnations']), 'worker incarnation attestation changed')
    raw_refs = record['source_refs']
    require(len(raw_refs) == 1, 'one immutable online raw evidence required')
    raw = load_ref(raw_refs[0])
    require(all(raw[k] == record[k] for k in ('load_begin_ns', 'all_weights_ready_ns',
                'load_begin_wall_ns', 'clock_domain')), 'persisted online raw clock changed')
    engine_incarnations = engine_identity(raw, record)
    if 'current_outer_calibration' in model:
        bound = model['current_outer_calibration']
        require(engine_incarnations == bound['engine_incarnations'] and
                record['incarnation_sha256'] == bound['complete_incarnation_sha256'],
                'engine or worker incarnation changed after prime/calibration')
    simple_transfer_service(record['plan'], record['workers'], model, record['state'])


def engine_identity(raw, record):
    ranks = raw['rank_timing']['ranks']
    require(set(ranks) == {'0', '1'} and len({r['host'] for r in ranks.values()}) == 1,
            'both engine ranks require a common physical host clock')
    result = {}
    for rank, timing in ranks.items():
        begins = [e for e in timing['events'] if e['event'] == 'load_begin']
        ready = [e for e in timing['events'] if e['event'] == 'weights_ready']
        require(len(begins) == len(ready) == 1, 'exact engine load/ready boundaries required')
        first, last = begins[0], ready[0]
        require(first['rank'] == last['rank'] == int(rank) and
                first['host'] == last['host'] == timing['host'] and
                type(first['pid']) is int and first['pid'] > 0 and first['pid'] == last['pid'],
                'engine rank identity changed within restore')
        require(record['load_begin_ns'] <= timing['begin_ns'] == first['monotonic_ns'] <
                timing['end_ns'] == last['monotonic_ns'] <= record['all_weights_ready_ns'],
                'engine weights_ready must lie within inclusive controller clock')
        result[rank] = dict(host=timing['host'], pid=first['pid'])
    return result


def calibrate_outer(model, prime_ref, calibration_ref):
    require(model['online_outer_s'] is None and 'current_outer_calibration' not in model,
            'outer residual can be calibrated only once')
    prime, calibration = load_ref(prime_ref), load_ref(calibration_ref)
    validate_record(prime, model, 1)
    validate_record(calibration, model, 2)
    require(prime['scenario'] == calibration['scenario'] == 'C0_mixed' and
            prime['plan']['ops'] != [] and calibration['plan']['ops'] != [], 'same complete mixed layout required')
    require(prime['incarnation_sha256'] == calibration['incarnation_sha256'], 'prime worker incarnation changed')
    engine = engine_identity(load_ref(prime['source_refs'][0]), prime)
    require(engine == engine_identity(load_ref(calibration['source_refs'][0]), calibration),
            'engine incarnation changed between prime and calibration')
    require(prime['verification_end_ns'] < calibration['load_begin_ns'] and
            prime['completed_wall_ns'] < calibration['load_begin_wall_ns'], 'prime must complete verification and park before calibration')
    require(calibration['state']['prime_ref'] == prime_ref, 'calibration state must bind persisted prime')
    pcal = dict(calibration, evidence_kind='actual_model_online_calibration',
                config_sha256=digest(model['context']), source_ref=calibration_ref)
    result = simple_calibrate_outer(model, pcal)
    result['source_refs'] += [prime_ref, *prime['source_refs'], *calibration['source_refs']]
    result['current_outer_calibration'] = dict(prime_ref=prime_ref, calibration_ref=calibration_ref,
        prime_online_ns=prime['all_weights_ready_ns']-prime['load_begin_ns'],
        calibration_online_ns=calibration['all_weights_ready_ns']-calibration['load_begin_ns'],
        prime_used_for_fitting=False, calibration_run_ids=ORDER[:2],
        complete_incarnation_sha256=calibration['incarnation_sha256'],
        engine_incarnations=engine, outer_domain=DOMAIN)
    result['outer_interpretation'] = 'One C0_cal inclusive online-minus-inherited-service residual after one complete verified prime in the same persistent controller; prime and historical holdouts are excluded from fitting.'
    return result


def predict(plan, workers, model, state):
    registration = validate_inheritance(model)
    current = model['current_outer_calibration']
    prime = load_ref(current['prime_ref'])
    calibration = load_ref(current['calibration_ref'])
    validate_record(prime, model, 1)
    validate_record(calibration, model, 2)
    require(state['current_session'] == registration['current_session'] and
            state['controller_pid'] == registration['controller_pid'] and state['outer_domain'] == DOMAIN and
            state['prior_complete_restores'] >= 2 and state['prime_ref'] == current['prime_ref'] and
            digest(state['incarnations']) == current['complete_incarnation_sha256'], 'prediction lacks current post-prime/controller/worker binding')
    require(model['calibration_sessions'] == [registration['current_session']], 'inherited service session relabeled as current calibration')
    require(current['prime_used_for_fitting'] is False and current['calibration_run_ids'] == ORDER[:2], 'holdout or prime fitted into residual')
    require(current['prime_online_ns'] == prime['all_weights_ready_ns']-prime['load_begin_ns'] and
            current['calibration_online_ns'] == calibration['all_weights_ready_ns']-calibration['load_begin_ns'],
            'persisted prime/calibration costs changed')
    service = simple_transfer_service(calibration['plan'], calibration['workers'], model, calibration['state'])
    expected = (calibration['all_weights_ready_ns']-calibration['load_begin_ns'])/1e9-service['worker_dag_s']
    require(abs(expected-model['online_outer_s']) <= 1e-12, 'outer residual changed after calibration')
    return dict(simple_predict(plan, workers, model, state), outer_domain=DOMAIN,
        source_service_sessions=model['path_cost_inheritance']['source_calibration_sessions'],
        scope='Inherited r02 exact path costs plus one post-prime same-controller common outer residual; whole-model holdout qualification pending.')


_spec = importlib.util.spec_from_file_location('outer_private_freeze', Path(__file__).resolve().parent.parent / 'model/freeze.py')
_adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapter)
_adapter.predict = predict


def freeze(protocol_path, model_path, inputs_path):
    model = json.loads(Path(model_path).read_text())
    registration = validate_inheritance(model)
    protocol = json.loads(Path(protocol_path).read_text())
    require(protocol['outer_domain'] == DOMAIN and protocol['validation_order'] == ORDER[2:] and
            set(protocol['scenarios']) == set(ORDER[2:]), 'freeze only exact registered holdouts')
    inherited = model['path_cost_inheritance']
    old_paths = {str(Path(r['path']).resolve()) for r in inherited['source_raw_refs'] +
                 [inherited['source_checkpoint_path_model_ref']]}
    require(len({str(Path(p).resolve()) for p in (protocol_path, model_path, inputs_path)}) == 3 and
            not old_paths.intersection(str(Path(p).resolve()) for p in (protocol_path, model_path, inputs_path)),
            'freeze must not alias historical source or its own artifacts')
    result = _adapter.freeze(protocol_path, model_path, inputs_path)
    calibration = load_ref(model['current_outer_calibration']['calibration_ref'])
    require(registration['registered_wall_ns'] < calibration['completed_wall_ns'] < result['payload']['freeze_wall_ns'],
            'freeze must follow completed calibration including physical park')
    result['payload'].update(outer_domain=DOMAIN, persistent_controller=model['current_controller'],
        source_service_sessions=inherited['source_calibration_sessions'], validation_order=ORDER[2:],
        prime_used_for_fitting=False)
    result['payload_sha256'] = digest(result['payload'])
    return result
