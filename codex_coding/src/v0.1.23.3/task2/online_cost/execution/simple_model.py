"""Exact extent service observations scheduled by the unchanged product DAG."""
import copy
import importlib.util
import math
import json
from pathlib import Path

from evaluate import digest, require, verify_ref
from predictor import (calibrate_outer as native_calibrate_outer, physical, plan_workload,
                       predict as native_predict, transfer_service as native_transfer_service)
from fluidckpt.cluster.path_catalog import topology_id

GiB = 1 << 30
TAIL_BYTES = 805120688
POLICY = 'exact_extent_service_points_v1'
CALIBRATION_SIZES = [TAIL_BYTES, GiB]


def build_model(context, workers, samples, shared_samples, source_refs):
    require(context.get('workflow') == 'simple_two_node_single_sender_v1', 'simple context required')
    require(context['shared_resource_paths'] == {} and shared_samples == {}, 'single sender uses native serialization, no disjoint-pair qualifier')
    require([w['worker'] for w in workers] == [0, 1, 2], 'three-worker native DAG required')
    remote = [fields for fields in context['path_context'].values() if fields['operation_kind'] == 'network']
    require(sorted((p['endpoints'], p['source_numa']) for p in remote) == [([2, 0], 4), ([2, 1], 7)], 'remote rank-specific NUMA paths changed')
    require(len(context['path_context']) == 6, 'exact six concrete simple paths required')
    require(context.get('path_cost_policy') == POLICY and context.get('calibration_sizes') == CALIBRATION_SIZES,
            'exact extent point policy must be registered before data')
    require(context.get('path_warmup_bytes') == GiB, 'one per-path 1GiB warmup required')
    require(set(samples) == set(context['path_context']) and source_refs, 'exact registered paths and raw references required')
    require(context['worker_physical'] == physical(workers), 'physical mapping changed')
    require(set(context['worker_BDF']) == {str(w['worker']) for w in workers} and all(context['worker_BDF'].values()), 'all GPU BDFs required')
    require(context['executor'] == 'serial' and context['clock_scope'] == 'single_controller', 'native serial and controller clock required')
    for ref in source_refs:
        verify_ref(ref)
    costs, sessions = {}, set()
    for path, rows in samples.items():
        require(len(rows) == 2 and sorted(row['nbytes'] for row in rows) == CALIBRATION_SIZES, 'one sample per exact extent size required')
        costs[path] = {}
        for row in rows:
            require(math.isfinite(row['service_s']) and row['service_s'] > 0, 'positive observed operation service required')
            require(row['evidence_kind'] == 'actual_path_calibration' and row['passed'] is True
                    and row['verified_bytes'] == row['nbytes'], 'actual verified path sample required')
            require(row['config_sha256'] == digest(context) and row['session'], 'sample configuration/session changed')
            for kind, ref in [('warmup', row.get('warmup_ref')), ('cal', row.get('source_ref'))]:
                require(ref in source_refs, 'per-path warmup/calibration evidence must be bound into model')
                verify_ref(ref)
                raw = json.loads(Path(ref['path']).read_text())
                require(raw['sample_role'] == kind and raw['path'] == path and raw['config_sha256'] == digest(context)
                        and raw['session'] == row['session'], 'raw path/state/session changed')
                count = GiB if kind == 'warmup' else row['nbytes']
                require(raw['flow']['verification']['passed'] is True and raw['flow']['verification']['checked_bytes'] == count
                        and raw['resource_gate']['passed'] is True and raw['target_retired'] is True,
                        'verified warmup/calibration and cleanup required')
                if kind == 'cal':
                    durations = [record['elapsed_s'] for worker in raw['flow']['execution'] for record in worker['records']]
                    require(durations and max(durations) == row['service_s'], 'sample service differs from immutable raw operation records')
            costs[path][str(row['nbytes'])] = dict(service_s=row['service_s'], nbytes=row['nbytes'])
            sessions.add(row['session'])
    require(len(sessions) == 1, 'one newly initialized calibration session required')
    return dict(schema='simple_exact_extent_model_v1', context=copy.deepcopy(context), topology_id=topology_id(workers),
                point_costs=costs, source_refs=copy.deepcopy(source_refs), calibration_sessions=sorted(sessions),
                shared_qualifiers={}, online_outer_s=None, online_qualified=False, physical_capacity_certified=False,
                execution_interpretation='Observed service only at exact 1GiB/tail sizes after per-path warmup; original worker-order DAG serializes worker2; no bandwidth fit, interpolation or shared multiplier.')


def _project(plan, workers, model, state):
    """Map exact observed costs onto scorer path aliases without changing its DAG."""
    require(model['schema'] == 'simple_exact_extent_model_v1', 'exact point model required')
    context = model['context']
    require(state['config_sha256'] == digest(context), 'original attested context changed')
    require(context['path_cost_policy'] == POLICY, 'point policy changed')
    require(context['calibration_sizes'] == CALIBRATION_SIZES and context['path_warmup_bytes'] == GiB,
            'exact extent/warmup registration changed')
    projected = copy.deepcopy(model)
    pcontext = projected['context']
    pcontext['path_context'], projected['paths'] = {}, {}
    aliases = {}
    for path, points in model['point_costs'].items():
        require(path in context['path_context'] and sorted(map(int, points)) == CALIBRATION_SIZES, 'point path/size domain changed')
        for size, point in points.items():
            count, service = int(size), point['service_s']
            require(point['nbytes'] == count and math.isfinite(service) and service > 0, 'invalid exact service point')
            alias = path + '/exact_nbytes=' + size
            aliases[alias] = path
            pcontext['path_context'][alias] = copy.deepcopy(context['path_context'][path])
            # The existing scorer evaluates n/scale. At this single n it equals
            # the measured service exactly; scale carries no bandwidth meaning.
            projected['paths'][alias] = dict(warm_GBps=count / 1e9 / service,
                operation_s=0., first_pair_s=0., fit_bytes=[count, count],
                fit_scope='Synthetic normalization for one exact observed service point; not bandwidth.')
    require(set(model['point_costs']) == set(context['path_context']), 'exact path domain changed')
    pplan = copy.deepcopy(plan)
    for original, op in zip(plan['ops'], pplan['ops']):
        require(original['path'] in model['point_costs'] and str(original['n']) in model['point_costs'][original['path']],
                'unobserved extent size/path; no interpolation or extrapolation')
        op['path'] = original['path'] + '/exact_nbytes=' + str(original['n'])
    pstate = dict(state, config_sha256=digest(pcontext))
    return pplan, projected, pstate, aliases


def _unproject(result, aliases):
    for row in result['timeline']:
        row['point_path_alias'] = row['path']
        row['path'] = aliases[row['path']]
    result['shared_score_interpretation'] = 'Exact observed per-operation service scheduled by original worker-order DAG; no shared qualifier.'
    result['path_cost_policy'] = POLICY
    return result


def transfer_service(plan, workers, model, state):
    pplan, projected, pstate, aliases = _project(plan, workers, model, state)
    return _unproject(native_transfer_service(pplan, workers, projected, pstate), aliases)


def calibrate_outer(model, calibration):
    require(calibration['config_sha256'] == digest(model['context']), 'original C0 configuration changed')
    pplan, projected, pstate, aliases = _project(calibration['plan'], calibration['workers'], model, calibration['state'])
    pcal = dict(calibration, plan=pplan, state=pstate, config_sha256=digest(projected['context']))
    bound = native_calibrate_outer(projected, pcal)
    result = copy.deepcopy(model)
    for key in ('online_outer_s', 'source_refs', 'calibration_sessions', 'outer_interpretation'):
        result[key] = bound[key]
    result['outer_calibration_service'] = _unproject(bound['outer_calibration_service'], aliases)
    return result


def predict(plan, workers, model, state):
    pplan, projected, pstate, aliases = _project(plan, workers, model, state)
    return _unproject(native_predict(pplan, workers, projected, pstate), aliases)


# Load the existing freeze implementation into a private adapter namespace.
# Its references and chronology checks remain intact; complex mode globals
# never change, including when both adapters are imported by CPU review.
_spec = importlib.util.spec_from_file_location('simple_exact_point_freeze', Path(__file__).resolve().parent.parent / 'model/freeze.py')
_freeze_adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_freeze_adapter)
_freeze_adapter.predict = predict
freeze = _freeze_adapter.freeze
