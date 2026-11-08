"""Compile frozen witnesses and bind them to current serial Cluster catalogs."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster.planning import validate_plan
from fluidckpt.cluster.path_catalog import path_key
from fluidckpt.serving.replicas import identity
from fluidckpt.executor.chunks import STAGING_BYTES, DEFAULT_CHUNK

FROZEN = '741def7882e9163fb6afd299c27c1bff9c0207529d9d555c754d66f5492111fe'
CONTRACT = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_scenarios/contract_cpu_r02/contract.json'


def load_contract(path=CONTRACT):
    contract = json.loads(Path(path).read_text())
    unsigned = {k: v for k, v in contract.items() if k != 'contract_hash'}
    computed = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if contract['contract_hash'] != FROZEN or computed != FROZEN:
        raise ValueError('frozen contract digest changed')
    return contract


def projection(contract):
    locations = {x['id']: x for x in contract['locations']}
    blocks = {x['id']: x for x in contract['blocks']}
    workers = {x['id']: i for i, x in enumerate(contract['workers'])}
    candidates = []
    for candidate in contract['candidates']:
        loc, block = locations[candidate['location']], blocks[candidate['block']]
        candidates.append(dict(candidate, worker=workers[loc['worker']], tier=loc['tier'],
                               logical_node=loc['node'], logical_numa=loc['numa'],
                               tp=block['rank'], off=block['offset_bytes'], n=block['nbytes'],
                               physical_source_selectable=False))
    cm = {c['id']: c for c in candidates}
    phases = []
    ssd_only = dict(id='SSD_only', capacity_bytes={loc: 0 for loc in contract['phases'][0]['capacity_bytes']},
                    witness=[dict(block=b['id'], candidate=f"{b['id']}@{b['authoritative_location']}") for b in blocks.values()])
    for phase in contract['phases'] + [ssd_only]:
        save, restore = [], []
        for witness in phase['witness']:
            chosen, block = cm[witness['candidate']], blocks[witness['block']]
            base = cm[f"{block['id']}@{block['authoritative_location']}"]
            if not chosen['fixed']:
                save.append(dict(block=block['id'], src_candidate=base['id'], dst_candidate=chosen['id'],
                                 src_w=base['worker'], src_tier='ssd', dst_w=chosen['worker'], dst_tier=chosen['tier']))
            restore.append(dict(block=block['id'], src_candidate=chosen['id'],
                                src_w=chosen['worker'], src_tier=chosen['tier'],
                                dst_w=workers[block['target_worker']], dst_tier='hbm'))
        phases.append(dict(id=phase['id'], save_requirements=save, restore_requirements=restore,
                           ledger=ledger(contract, phase, cm)))
    return dict(schema='multitier.serial_adapter.v1', contract_hash=FROZEN,
                candidates=candidates, phases=phases, execution_ready=False,
                predicted_ready_seconds=None, gpu_command=None,
                missing=['physical binding', 'current SSD catalog owners', 'processed manifests',
                         'selected save/restore path qualification', 'phase runtime admission'])


def ledger(contract, phase, candidates):
    usage = {location: 0 for location in phase['capacity_bytes']}
    selected = [candidates[w['candidate']] for w in phase['witness']]
    for c in selected:
        if not c['fixed']:
            usage[c['location']] += (c['n'] + 4095) // 4096 * 4096
    if any(n > phase['capacity_bytes'][loc] for loc, n in usage.items()):
        raise ValueError('witness exceeds frozen pause capacity')
    hbm = sum(n for loc, n in usage.items() if loc.startswith('hbm/'))
    dram = sum(n for loc, n in usage.items() if loc.startswith('dram/'))
    return dict(pause=dict(hbm_bytes=hbm, dram_bytes=dram, by_location=usage,
                           complete_ssd_base_bytes=contract['payload_bytes']),
                save=dict(logical_ssd_read_bytes=hbm + dram, optional_copy_write_bytes=hbm + dram,
                          initial_processed_payload_bytes=None, transition_peak_bytes=None,
                          elapsed_seconds=None),
                restore=dict(target_hbm_bytes=contract['payload_bytes'],
                             retained_hbm_bytes=hbm, retained_dram_bytes=dram,
                             serial_staging_hbm_bytes=6 * STAGING_BYTES,
                             serial_staging_hbm_bytes_per_worker=STAGING_BYTES,
                             known_hbm_items_bytes=hbm + contract['payload_bytes'] + 6 * STAGING_BYTES,
                             logical_ssd_source_bytes=sum(c['n'] for c in selected if c['fixed']),
                             runtime_hbm_bytes=None, runtime_dram_bytes=None,
                             host_read_buffers_bytes=None, page_cache_bytes=None,
                             complete_physical_peak_bytes=None,
                             runtime_staging_double_count_allowed=False,
                             alias_credit_bytes=0, elapsed_seconds=None))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def check_binding(binding, contract):
    _require(binding is not None, 'unbound execution refused')
    _require(binding.get('contract_hash') == FROZEN, 'binding contract mismatch')
    _require(binding.get('purpose') in ('physical', 'cpu_api_fixture'), 'missing binding purpose')
    _require(isinstance(binding.get('evidence_refs'), dict) and binding['evidence_refs'], 'missing binding evidence')
    for ref in binding['evidence_refs'].values():
        _require(isinstance(ref, dict) and isinstance(ref.get('path'), str), 'evidence must name a local immutable artifact')
        path = Path(ref['path'])
        _require(path.is_absolute() and path.is_file(), 'evidence artifact missing')
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == ref.get('sha256'), 'evidence artifact SHA mismatch')
    _require(binding.get('valid_until', 0) > time.time(), 'binding expired')
    ws = binding.get('workers', [])
    _require(len(ws) == 6 and [w.get('worker') for w in ws] == list(range(6)), 'six ordered workers required')
    _require(len({w.get('session') for w in ws}) == 1 and ws[0].get('session'), 'worker session mismatch')
    _require(len({(w['node'], w['gpu']) for w in ws}) == 6, 'duplicate physical GPU')
    for i, w in enumerate(ws):
        _require(w.get('logical_worker') == f'w{i}' and isinstance(w.get('epoch'), str) and w['epoch'], 'worker binding mismatch')
        _require(type(w['gpu']) is int and type(w['numa']) is int, 'physical GPU/NUMA integers required')
        _require(w.get('staging_bytes') == STAGING_BYTES, 'serial staging allocation mismatch')
    for node_index in range(3):
        _require(ws[2 * node_index]['node'] == ws[2 * node_index + 1]['node'], 'logical node split')
    _require(len({w['node'] for w in ws}) == 3, 'three distinct GPU nodes required')
    manifests = binding.get('manifests', {})
    for rank in range(4):
        manifest = manifests.get(str(rank), {})
        _require(manifest.get('meta', {}).get('tp_size') == 4 and manifest['meta'].get('tp_rank') == rank,
                 'processed TP4 manifest mismatch')
        _require(identity(manifest['meta'], manifest['index']) == manifest.get('identity'), 'manifest identity mismatch')
        intervals = sorted(tuple(v) for v in manifest['index'].values())
        cursor = 0
        for off, n in intervals:
            _require(type(off) is int and type(n) is int and off == cursor and n > 0, 'manifest packed coverage mismatch')
            cursor += n
        _require(cursor == 2 * contract['block_unit_bytes'], 'manifest size differs from frozen small qualification')
    _require(set(binding.get('ssd', {})) == {'n0', 'n1', 'n2'}, 'three SSD bindings required')
    for node, ssd in binding['ssd'].items():
        _require(isinstance(ssd.get('endpoint'), str) and ssd['endpoint'].startswith(('http://', 'https://')),
                 'SSD endpoint missing')
        _require(ssd.get('storage_node') == ws[2 * int(node[1:])]['node'], 'SSD backing is not bound to its logical node')
        _require(ssd.get('current_owner_session') == ws[0]['session'] and ssd.get('backing_evidence_ref'),
                 'SSD live owner/backing evidence missing')
        _require(ssd['backing_evidence_ref'] in binding['evidence_refs'], 'unknown SSD backing evidence')
    return ws


def descriptor(candidate, binding, run_id, phase_id, authoritative=False):
    w = binding['workers'][candidate['worker']]
    manifest = binding['manifests'][str(candidate['tp'])]
    desc = dict(id=binding['base_ids'][candidate['block']] if authoritative else
                f'{run_id}/{phase_id}/cache/{candidate["block"]}', identity=manifest['identity'],
                tp=candidate['tp'], off=candidate['off'], n=candidate['n'], tier=candidate['tier'],
                worker=w['worker'], node=w['node'], gpu=w['gpu'], numa=w['numa'],
                epoch=w['epoch'], meta=manifest['meta'], index=manifest['index'])
    if candidate['tier'] == 'ssd':
        ssd = binding['ssd'][candidate['logical_node']]
        desc.update(store_endpoint=ssd['endpoint'], storage_node=ssd['storage_node'])
    return desc


def bind_source(expected, rows, workers):
    matches = [r for r in rows if r.get('id') == expected['id']]
    _require(len(matches) == 1, 'source missing or duplicate in current catalog')
    row = matches[0]
    for key in ('id', 'identity', 'tp', 'off', 'n', 'tier', 'worker', 'node', 'gpu', 'numa', 'epoch'):
        _require(row.get(key) == expected[key], f'current source mismatch: {key}')
    _require(row.get('state') == 'VALID' and row.get('leases') == 0 and not row.get('preview_only'), 'source is not unleased VALID')
    if 'generation' in expected:
        _require(row.get('generation') == expected['generation'], 'source generation changed')
    _require(type(row.get('generation')) is int and row['generation'] > 0, 'source generation invalid')
    _require(row.get('allocated_bytes') == (row['n'] + 4095) // 4096 * 4096, 'source physical allocation mismatch')
    _require(row['epoch'] == workers[row['worker']]['epoch'], 'source worker restarted')
    if row['tier'] == 'ssd':
        for key in ('store_endpoint', 'storage_node'):
            _require(row.get(key) == expected[key], f'SSD descriptor lost {key}')
    return copy.deepcopy(row)


def _path(source, target, binding):
    key = path_key(source['worker'], source['tier'], source['numa'], target['worker'], target['tier'])
    evidence = binding.get('paths', {}).get(key, {})
    _require(evidence.get('status') == 'verified' and evidence.get('executor') == 'serial', f'unqualified selected path: {key}')
    _require(evidence.get('valid_until', 0) > time.time() and evidence.get('evidence_ref'), f'path expired or unreferenced: {key}')
    _require(evidence['evidence_ref'] in binding['evidence_refs'], 'unknown path evidence reference')
    _require(evidence.get('source_epoch') == source['epoch'] and
             evidence.get('target_epoch') == binding['workers'][target['worker']]['epoch'], 'path incarnation changed')
    if source['tier'] == 'ssd':
        _require(evidence.get('store_endpoint') == source['store_endpoint'] and
                 evidence.get('storage_node') == source['storage_node'], 'SSD HTTP leg unqualified')
    sw, dw = binding['workers'][source['worker']], binding['workers'][target['worker']]
    kind = 'local_' + source['tier'] if sw['worker'] == dw['worker'] else 'peer' if sw['node'] == dw['node'] else 'network'
    _require(kind in sw['capabilities'] and kind in dw['capabilities'], 'worker path capability missing')
    return key, kind


def compile_phase(contract, binding, phase_id, run_id, current_rows, stage='save'):
    workers = check_binding(binding, contract)
    _require(re.fullmatch(r'[A-Za-z0-9_.-]+', run_id) is not None, 'invalid run ID')
    _require(stage in ('save', 'restore'), 'unknown stage')
    projected = projection(contract)
    phase = next(p for p in projected['phases'] if p['id'] == phase_id)
    cm = {c['id']: c for c in projected['candidates']}
    # Every run must preserve complete, currently owned SSD fallback before doing any work.
    bases = {c['block']: bind_source(descriptor(c, binding, run_id, phase_id, True), current_rows, workers)
             for c in cm.values() if c['fixed']}
    admission = binding.get('phase_admission', {}).get(phase_id, {})
    _require(admission.get('status') == 'admitted' and admission.get('evidence_ref') and
             admission.get('valid_until', 0) > time.time(), 'phase runtime budget not admitted')
    _require(admission['evidence_ref'] in binding['evidence_refs'], 'unknown budget evidence reference')
    _require(admission.get('runtime_staging_inclusion') in ('included', 'separate') and
             admission.get('budget_accounting_complete') is True, 'runtime/staging or budget accounting incomplete')
    targets, ops, needed_paths = [], [], []
    requirements = phase[f'{stage}_requirements']
    if stage == 'restore':
        for rank, worker in enumerate((0, 2, 4, 5)):
            manifest = binding['manifests'][str(rank)]
            target = dict(key=f'{run_id}/{phase_id}/target/{rank}', id=f'{run_id}/{phase_id}/target/{rank}',
                          mode='replica', worker=worker, identity=manifest['identity'], tp=rank, off=0,
                          n=2 * contract['block_unit_bytes'], tier='hbm', numa=workers[worker]['numa'],
                          meta=manifest['meta'], index=manifest['index'])
            targets.append(target)
    for req in requirements:
        c = cm[req['src_candidate']]
        expected = descriptor(c, binding, run_id, phase_id, c['fixed'])
        source = bases[c['block']] if c['fixed'] else bind_source(expected, current_rows, workers)
        if stage == 'save':
            destination = descriptor(cm[req['dst_candidate']], binding, run_id, phase_id)
            _require(not any(r['id'] == destination['id'] and r['state'] != 'RELEASED' for r in current_rows), 'run copy already exists')
            destination.update(key=destination['id'], mode='replica')
            target = destination
            targets.append(target)
        else:
            target = next(t for t in targets if t['tp'] == c['tp'])
        key, kind = _path(source, target, binding)
        needed_paths.append(key)
        ops.append(dict(op_id=len(ops), target=target['key'], identity=source['identity'], tp=c['tp'],
                        off=c['off'], n=c['n'], source_offset=c['off'] - source['off'],
                        replica_id=source['id'], generation=source['generation'], source_epoch=source['epoch'],
                        src_w=source['worker'], dst_w=target['worker'], kind=kind, path=key, resources=[]))
    plan = dict(targets=targets, ops=ops, executor='serial', chunk_bytes=DEFAULT_CHUNK,
                strategy='frozen_witness', estimated_transfer_s=None, bandwidth_lower_bound_s=None,
                adapter_stage=stage, contract_hash=FROZEN, needed_paths=sorted(set(needed_paths)),
                scope='current-source serial witness; no rate model or runtime prediction')
    if targets:
        validate_plan(plan)
    _require(not any(r['id'] == t['id'] for r in current_rows for t in targets), 'run allocation ID was already used')
    for w in workers:
        for tier in ('hbm', 'dram', 'ssd'):
            allocated = sum((r['n'] + 4095) // 4096 * 4096 for r in current_rows
                            if r['worker'] == w['worker'] and r['tier'] == tier and r['state'] != 'RELEASED')
            added = sum((t['n'] + 4095) // 4096 * 4096 for t in targets
                        if t['worker'] == w['worker'] and t['tier'] == tier)
            _require(allocated + added <= w['budget_bytes'][tier], 'current catalog allocation budget exceeded')
    return plan


def check_current_workers(actual, binding):
    _require(len(actual) == 6, 'worker count changed')
    by_id = {w['worker']: w for w in actual}
    _require(set(by_id) == set(range(6)), 'worker ranks changed')
    for expected in binding['workers']:
        current = by_id[expected['worker']]
        for key in ('worker', 'node', 'gpu', 'numa', 'epoch', 'session', 'staging_bytes'):
            _require(current.get(key) == expected.get(key), f'current worker changed: {key}')


class AdapterExecutionError(RuntimeError):
    def __init__(self, cause, journal):
        super().__init__(f'adapter execution failed: {cause}')
        self.journal = journal


def execute_phase(cluster, contract, binding, phase_id, run_id, *, trusted_admission=None, journal_path=None):
    """The caller coordinates physical authorization; this entry never launches workers."""
    _require(binding.get('purpose') == 'physical', 'CPU fixture cannot authorize physical execution')
    _require(callable(trusted_admission), 'physical execution requires the coordinator trusted admission gate')
    approval = trusted_admission(binding, phase_id, run_id)
    _require(isinstance(approval, dict) and approval.get('status') == 'admitted' and
             approval.get('contract_hash') == FROZEN and approval.get('phase') == phase_id and
             approval.get('run_id') == run_id, 'coordinator trusted admission gate refused')
    _require(journal_path is not None, 'physical execution requires a persistent run journal')
    path = Path(journal_path)
    _require(not path.exists() and path.parent.is_dir(), 'journal path already exists or parent missing')
    check_binding(binding, contract)
    _require(getattr(cluster, 'executor', None) == 'serial', 'only existing serial executor supported')
    expected_endpoints = [w.get('endpoint') for w in binding['workers']]
    _require(cluster.endpoints == expected_endpoints, 'cluster endpoints changed')
    result = dict(run_id=run_id, phase=phase_id, save=None, restore=None, owned_ids=[],
                  base_ids=list(binding['base_ids'].values()), base_retirement_allowed=False,
                  owned_descriptors=[], status='preflight', workers=copy.deepcopy(binding['workers']),
                  coordinator_admission=approval, physical_peak_measured=False)
    def record():
        path.write_text(json.dumps(result, indent=2) + '\n')
    record()
    try:
        workers, rows = cluster.inventory()
        check_current_workers(workers, binding)
        result['base_descriptors'] = [copy.deepcopy(r) for r in rows if r['id'] in result['base_ids']]
        for stage in ('save', 'restore'):
            if stage == 'restore':
                workers, rows = cluster.inventory()
                check_current_workers(workers, binding)
            plan = compile_phase(contract, binding, phase_id, run_id, rows, stage)
            result[stage + '_plan'] = plan
            for target in plan['targets']:
                result['owned_ids'].append(target['id'])
                worker = workers[target['worker']]
                result['owned_descriptors'].append(dict(target, node=worker['node'], gpu=worker['gpu'],
                                                         epoch=worker['epoch']))
            result['status'] = stage + '_planned'
            record()
            if plan['targets']:
                result[stage] = cluster.transfer(plan, verify_before_ready=True)
            result['status'] = stage + '_complete'
            record()
        workers, rows = cluster.inventory()
        check_current_workers(workers, binding)
        for expected in result['owned_descriptors'] + result['base_descriptors']:
            bind_source(expected, rows, workers)
        result['target_scope'] = 'four complete HBM replica targets, functional bytes-ready; no live model handoff'
        result['status'] = 'complete'
        record()
        return result
    except BaseException as exc:
        result.update(status='failed', error=repr(exc), cleanup_required=True)
        record()
        raise AdapterExecutionError(exc, result) from exc


def cleanup_owned(cluster, result):
    """Release exact run allocations while preserving all authoritative SSD owners."""
    _require(not set(result['owned_ids']) & set(result['base_ids']), 'cleanup overlaps authoritative base')
    _require(len(set(result['owned_ids'])) == len(result['owned_ids']), 'duplicate owned cleanup ID')
    prefix = f"{result['run_id']}/{result['phase']}/"
    _require(all(r.startswith(prefix) for r in result['owned_ids']), 'cleanup ID outside run')
    workers, rows = cluster.inventory()
    if result.get('workers'):
        check_current_workers(workers, dict(workers=result['workers']))
    _require(all(not w.get('transactions') for w in workers), 'cleanup refused with active transactions')
    expected = {d['id']: d for d in result.get('owned_descriptors', [])}
    _require(set(expected) == set(result['owned_ids']), 'cleanup requires planned owned descriptors')
    bases = result.get('base_descriptors', [])
    _require({b['id'] for b in bases} == set(result['base_ids']), 'cleanup requires original base descriptors')
    for base in bases:
        bind_source(base, rows, workers)
    retired, absent = [], []
    for rid in result['owned_ids']:
        matches = [r for r in rows if r['id'] == rid]
        if not matches or matches[0]['state'] == 'RELEASED':
            absent.append(rid)
            continue
        _require(len(matches) == 1, 'duplicate owned catalog row')
        row = matches[0]
        _require(row.get('leases') == 0 and row['state'] in ('VALID', 'BUILDING'), 'owned allocation still leased/retiring')
        for key in ('id', 'identity', 'tp', 'off', 'n', 'tier', 'worker', 'node', 'gpu', 'numa', 'epoch'):
            _require(row.get(key) == expected[rid].get(key), f'cleanup owned descriptor mismatch: {key}')
        _require(row['tier'] in ('hbm', 'dram'), 'cleanup cannot retire any SSD allocation')
        cluster.retire(rid)
        retired.append(rid)
    after_workers, after = cluster.inventory()
    _require(all(not w.get('transactions') for w in after_workers), 'transactions remain after cleanup')
    for base in bases:
        bind_source(base, after, after_workers)
    _require(all(r['state'] == 'RELEASED' for r in after if r['id'] in result['owned_ids']), 'owned allocations remain after cleanup')
    return dict(retired=retired, absent_or_released=absent, base_ids_preserved=[b['id'] for b in bases],
                preserved_base_descriptors=[r for r in after if r['id'] in result['base_ids']],
                preservation_checked_from_current_catalog=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--binding')
    parser.add_argument('--current-inventory')
    parser.add_argument('--phase', default='C3')
    parser.add_argument('--run-id', default='adapter_preview')
    args = parser.parse_args()
    contract = load_contract()
    output = projection(contract)
    if args.binding:
        _require(args.current_inventory is not None, 'current inventory required with binding')
        binding = json.loads(Path(args.binding).read_text())
        inventory = json.loads(Path(args.current_inventory).read_text())
        check_current_workers(inventory['workers'], binding)
        output['save_plan'] = compile_phase(contract, binding, args.phase, args.run_id, inventory['replicas'], 'save')
        output['binding_status'] = 'prepared save plan only; refresh catalog after save before compiling restore'
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'adapter.json').write_text(json.dumps(output, indent=2, sort_keys=True) + '\n')
    print(str(destination / 'adapter.json'))


if __name__ == '__main__':
    main()
