"""CPU-only prediction for explicitly bound, warm, bounded serial requests."""
import copy
import hashlib
import json
import math
from pathlib import Path

MIB = 1 << 20
CHUNK_BYTES = 32 * MIB


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_ref(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_ref(reference):
    require(file_ref(reference['path']) == reference, 'provenance file changed: ' + reference['path'])


def physical(workers):
    return [{k: w[k] for k in ('worker', 'node', 'gpu', 'numa')} for w in workers]


def incarnations(workers):
    return [[w['session'], w['epoch']] for w in workers]


def load_frozen(path):
    package = json.loads(Path(path).read_text())
    require(package['schema'] == 'bounded_cost_frozen_v1', 'unsupported frozen schema')
    require(digest(package['model']) == package['model_sha256'], 'frozen coefficient digest mismatch')
    for reference in package['source_refs'].values():
        verify_ref(reference)
    package['artifact_ref'] = file_ref(path)
    return package


def bind_session(frozen, workers, attestation):
    """Migrate coefficients only after the caller records actual fresh gates."""
    verify_ref(frozen['artifact_ref'])
    require(digest(frozen['model']) == frozen['model_sha256'], 'coefficients changed before binding')
    verify_ref(attestation['evidence_ref'])
    evidence = json.loads(Path(attestation['evidence_ref']['path']).read_text())
    require(evidence == {k: v for k, v in attestation.items() if k != 'evidence_ref'}, 'attestation differs from saved evidence')
    for field in ('physical_gate_passed', 'config_verified', 'source_lineage_verified'):
        require(attestation.get(field) is True, 'missing successful migration gate: ' + field)
    require(attestation.get('control_mode') == 'cached', 'unsupported control mode')
    require(attestation.get('executor') == 'serial' and attestation.get('chunk_bytes') == CHUNK_BYTES, 'configuration mismatch')
    require(attestation.get('worker_physical') == physical(workers) == frozen['model']['worker_physical'], 'physical topology mismatch')
    require(attestation.get('worker_incarnations') == incarnations(workers), 'migration incarnation mismatch')
    require(attestation.get('model_source_ref') == frozen['artifact_ref'], 'migration source mismatch')
    require({'2:4', '4:5'} <= set(attestation.get('warm_pairs', [])), 'migration needs actual warm pairs 24 and 45')
    lineage = attestation.get('source_lineage', {})
    wm = {w['worker']: w for w in workers}
    require(set(lineage) == set(frozen['model']['paths']), 'missing source lineage')
    for path, cost in frozen['model']['paths'].items():
        source = lineage[path]
        require(source['worker'] == cost['endpoints'][0] and source['tier'] == cost['source_tier']
                and source['numa'] == cost['source_numa'], 'source tier or location mismatch')
        require(source['source_epoch'] == wm[source['worker']]['epoch'], 'source lineage epoch mismatch')
        require(type(source['generation']) is int and source['generation'] > 0
                and source['replica_id'] and source['identity'], 'invalid source lineage')
    model = copy.deepcopy(frozen['model'])
    model['binding'] = {'incarnations': incarnations(workers), 'attestation_ref': attestation['evidence_ref'],
                        'artifact_ref': frozen['artifact_ref'], 'model_sha256': frozen['model_sha256'],
                        'source_lineage': copy.deepcopy(lineage)}
    return model


def predict(plan, workers, model, state):
    """Refuse unsupported paths, sizes, repeats, states, and configurations."""
    require('binding' in model, 'model must be bound to a freshly verified session')
    binding = model['binding']
    require(digest({k: v for k, v in model.items() if k != 'binding'}) == binding['model_sha256'], 'bound coefficients changed')
    verify_ref(binding['artifact_ref'])
    verify_ref(binding['attestation_ref'])
    saved_attestation = json.loads(Path(binding['attestation_ref']['path']).read_text())
    require(binding['source_lineage'] == saved_attestation['source_lineage'], 'bound source lineage changed')
    require(physical(workers) == model['worker_physical'], 'model physical topology mismatch')
    require(incarnations(workers) == binding['incarnations'] == state.get('incarnations'), 'model/state incarnation mismatch')
    require(plan.get('executor') == 'serial' and plan.get('chunk_bytes') == CHUNK_BYTES, 'plan configuration mismatch')
    require(isinstance(plan.get('ops'), list) and 1 <= len(plan['ops']) <= 3, 'unsupported operation count')
    ready = {w['worker']: 0. for w in workers}
    wm = {w['worker']: w for w in workers}
    seen, timeline = set(), []
    for op in plan['ops']:
        path = op['path']
        require(path in model['paths'] and path not in seen, 'unknown or repeated path')
        seen.add(path)
        cost = model['paths'][path]
        require(type(op['n']) is int and 4 * MIB <= op['n'] <= 16 * MIB, 'size outside single-op 4–16MiB domain')
        src, dst = op['src_w'], op['dst_w']
        require([src, dst] == cost['endpoints'], 'path endpoint mismatch')
        require(op.get('kind') == cost['operation_kind'], 'operation primitive mismatch')
        require(op.get('source_epoch') == wm[src]['epoch'], 'source epoch mismatch')
        lineage = binding['source_lineage'][path]
        require(all(op.get(k) == lineage[k] for k in ('replica_id', 'identity', 'generation', 'source_epoch')), 'operation source lineage mismatch')
        pair = ':'.join(map(str, sorted((src, dst))))
        require(src == dst or pair in state.get('warm_pairs', []), 'warm-only model rejects cold pair')
        duration = cost['operation_s']
        if cost['kind'] == 'effective_affine':
            duration += op['n'] * cost['byte_slope_s_per_byte']
        require(math.isfinite(duration) and duration > 0, 'invalid duration')
        start = max(ready[src], ready[dst])
        end = start + duration
        timeline.append({'op_id': op['op_id'], 'path': path, 'start_s': start, 'end_s': end,
                         'service_s': duration, 'first_pair_s': None,
                         'source_wait_s': start - ready[src], 'target_wait_s': start - ready[dst]})
        ready[src] = ready[dst] = end
    service = max(ready.values())
    worker = service + model['request_s']
    execute = worker + model['dispatch_s']
    control = model['full_request_overhead']
    return {'estimated_service_s': service, 'estimated_worker_s': worker, 'estimated_execute_s': execute,
            'request_s': model['request_s'], 'dispatch_s': model['dispatch_s'], 'timeline': timeline,
            'estimated_full_filled_s': execute + control['to_filled_without_execute_s'],
            'estimated_full_published_s': None,
            'full_filled_prediction_scope': 'Training control median is a coarse portability hypothesis; service and execute errors require separate validation.',
            'published_prediction_scope': 'Unidentified SHA/publication byte-and-object cost; actual published timing is reported without prediction.',
            'bandwidth_plan_estimate_s': plan.get('estimated_transfer_s'),
            'strategy': 'bounded_warm_serial_service', 'scope': model['scope'],
            'decision_ready': False, 'physical_capacity_certified': False,
            'empirical_local_service_range_s': model['paths']['4:hbm:0->4']['empirical_service_range_s'],
            'uncertainty_scope': 'Observed local single-op envelope only; neither confidence interval nor mixed-request guarantee.',
            'frozen_model_ref': binding['artifact_ref'], 'migration_attestation_ref': binding['attestation_ref']}
