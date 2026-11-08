"""CPU coordinator for bounded native serial qualification and four frozen recipes."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
sys.path.insert(0, str(ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter'))
import adapter
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.cluster.planning import validate_plan

Q = 4 << 20
PHASES = ('SSD_only', 'C0', 'C1', 'C3')
EXPECTED = [('node28', 0, 0), ('node28', 1, 3), ('node30', 4, 4), ('node30', 5, 7), ('node27', 0, 0), ('node27', 4, 4)]
BDFS = ('0000:09:00.0', '0000:36:00.0', '0000:85:00.0', '0000:b5:00.0', '0000:09:00.0', '0000:85:00.0')
ADAPTER_SHA = '5038e02d38a53a327a826be32aa935a3c0694d56c6244f61656a4c1b4097f646'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def ref(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


def check_setup(setup):
    require(setup['purpose'] == 'physical' and setup['session'], 'physical setup/session required')
    require(len(setup['workers']) == 6, 'six selected workers required')
    for i, (worker, wanted) in enumerate(zip(setup['workers'], EXPECTED)):
        require((worker['node'], worker['gpu'], worker['numa']) == wanted and worker['worker'] == i, 'mapping differs from r03')
        require(worker['actual_bdf'].lower() == BDFS[i], 'expected BDF differs from r03')
        require(len(worker['container_id']) == 64 and worker['boot_id'] and type(worker['pid_start_ticks']) is int,
                'fresh exact container/boot/start identity missing')
    for node in range(3):
        ssd = setup['ssd'][f'n{node}']
        require(ssd['storage_node'] == EXPECTED[2*node][0] and ssd['directory'].startswith('/'), 'SSD position missing')
    for reference in setup['evidence_refs'].values():
        require(sha(reference['path']) == reference['sha256'], 'setup evidence changed')
    require(setup['valid_until'] > time.time(), 'setup expired')


def check_workers(workers, setup):
    require(len(workers) == 6, 'worker count mismatch')
    for i, (actual, expected) in enumerate(zip(workers, setup['workers'])):
        for key in ('worker', 'node', 'gpu', 'numa'):
            require(actual[key] == expected[key], f'worker {i} {key} mismatch')
        require(actual['session'] == setup['session'] and actual['staging_bytes'] == 64 << 20, 'session/staging mismatch')
        require(not actual['transactions'], 'active transaction at gate')
        runtime = actual['runtime_identity']
        for key, value in [('boot_id', expected['boot_id']), ('start_ticks', expected['pid_start_ticks']),
                           ('actual_bdf', BDFS[i]), ('pci_numa_node', expected['numa'])]:
            require(runtime[key] == value, f'worker {i} runtime {key} mismatch')
        if 'pid' in expected:
            require(runtime['pid'] == expected['pid'], 'container PID mismatch')
        require(runtime['cgroup_memory_max_bytes'] is not None and runtime['cgroup_memory_max_bytes'] <= 8 << 30,
                'container ceiling missing/above 8GiB')


def budget_gate(workers, setup, budget):
    check_workers(workers, setup)
    require(budget['purpose'] == 'physical' and budget['session'] == setup['session'] and budget['valid_until'] > time.time(),
            'fresh reviewed physical budget required')
    require(budget['runtime_staging_inclusion'] == 'included' and budget['budget_accounting_complete'] is True,
            'postwarm accounting must include native staging and pagecache')
    for node in ('node28', 'node30', 'node27'):
        sample = budget['nodes'][node]
        group = [w for w in workers if w['node'] == node]
        # Both worker samples observe one container: never sum the shared cgroup twice.
        current = max(w['runtime_identity']['cgroup_memory_current_bytes'] for w in group)
        limit = min(w['runtime_identity']['cgroup_memory_max_bytes'] for w in group)
        require(sample['dram_limit_bytes'] <= 6 << 30 and sample['hbm_limit_bytes'] <= 2 << 30, 'aggregate task caps exceeded')
        require(current + (256 << 20) <= min(limit, sample['dram_limit_bytes']), 'postwarm DRAM application margin exhausted')
        require(sample['physical_dram_available_bytes'] >= (2 << 30) + (256 << 20), 'host physical DRAM reserve exhausted')
        require(sample['cgroup_available_bytes'] >= 256 << 20, 'measured cgroup margin missing')
        delta = sum(max(0, sample['selected_gpu_free_bytes'][str(w['gpu'])] - w['headroom_bytes']['hbm']) for w in group)
        require(sample['hbm_increment_bytes'] + delta + (64 << 20) <= sample['hbm_limit_bytes'], 'aggregate HBM cap/margin exhausted')
        require(all(w['headroom_bytes']['hbm'] >= (2 << 30) + (32 << 20) for w in group), 'selected GPU free reserve exhausted')
    return True


def required_paths(setup):
    result = {}
    for phase in adapter.projection(adapter.load_contract())['phases']:
        if phase['id'] not in PHASES:
            continue
        for operation in phase['save_requirements'] + phase['restore_requirements']:
            src, dst = operation['src_w'], operation['dst_w']
            key = adapter.path_key(src, operation['src_tier'], setup['workers'][src]['numa'], dst, operation['dst_tier'])
            result[key] = dict(source_worker=src, source_tier=operation['src_tier'], target_worker=dst,
                               target_tier=operation['dst_tier'], source_numa=setup['workers'][src]['numa'],
                               target_numa=setup['workers'][dst]['numa'])
    return result


def exact_release(cluster, owned, journal, event):
    current = cluster.call_all('inventory')
    require(all(not w['transactions'] for w in current), 'cleanup has active transaction')
    result = []
    for expected in owned:
        worker = current[expected['worker']]
        require(worker['epoch'] == expected['epoch'], 'cleanup owner restarted')
        rows = [r for r in worker['replicas'] if r['id'] == expected['id']]
        if not rows or rows[0]['state'] == 'RELEASED':
            result.append(dict(id=expected['id'], status='absent_or_released'))
            continue
        row = rows[0]
        require(row['leases'] == 0 and row['state'] in ('VALID', 'BUILDING'), 'cleanup source is leased')
        for key in ('id', 'identity', 'tp', 'off', 'n', 'tier', 'worker', 'epoch'):
            require(row[key] == expected[key], f'exact cleanup {key} changed')
        for key in ('node', 'gpu', 'numa'):
            require(row[key] == expected.get(key, worker[key]), f'exact cleanup {key} changed')
        # This explicit end-of-qualification/end-of-run lifetime can release a final copy.
        request(cluster.endpoints[expected['worker']], 'retire', {'id': expected['id']})
        result.append(dict(id=expected['id'], status='released'))
    after = cluster.call_all('inventory')
    require(all(not w['transactions'] for w in after), 'cleanup transaction remains')
    for expected in owned:
        worker = after[expected['worker']]
        require(worker['epoch'] == expected['epoch'], 'cleanup final owner changed')
        require(all(r['state'] == 'RELEASED' for r in worker['replicas'] if r['id'] == expected['id']),
                'planned object remains after exact cleanup')
    journal.setdefault('cleanup', []).append(dict(event=event, objects=result))
    return result


def target_from(row, workers, target_id, worker, tier, *, off=None, n=None, ssd=None):
    result = {k: copy.deepcopy(row[k]) for k in ('identity', 'tp', 'meta', 'index')}
    result.update(key=target_id, id=target_id, mode='replica', worker=worker, tier=tier,
                  numa=workers[worker]['numa'], off=row['off'] if off is None else off, n=row['n'] if n is None else n)
    if tier == 'ssd':
        result.update(store_endpoint=ssd['endpoint'], storage_node=ssd['storage_node'])
    return result


def one_plan(source, target, workers):
    sw, dw = workers[source['worker']], workers[target['worker']]
    kind = 'local_' + source['tier'] if sw['worker'] == dw['worker'] else 'peer' if sw['node'] == dw['node'] else 'network'
    plan = dict(targets=[target], ops=[dict(op_id=0, target=target['key'], identity=source['identity'], tp=source['tp'],
                off=target['off'], n=target['n'], source_offset=target['off']-source['off'], replica_id=source['id'],
                generation=source['generation'], source_epoch=source['epoch'], src_w=source['worker'],
                dst_w=target['worker'], kind=kind, resources=[])], executor='serial', chunk_bytes=32 << 20)
    validate_plan(plan)
    return plan


def remember(owned, target, workers):
    worker = workers[target['worker']]
    owned.append(dict(target, epoch=worker['epoch'], node=worker['node'], gpu=worker['gpu']))


def synth_adopt(cluster, setup, workers, prefix, worker, rank, nbytes, owned, journal, out):
    snap = prefix + '/snap'
    rid = prefix + '/dram'
    target = dict(id=rid, worker=worker, epoch=workers[worker]['epoch'])
    journal.setdefault('planned_synth', []).append(dict(snap=snap, tp=rank, worker=worker, nbytes=nbytes, adopt_id=rid))
    journal.setdefault('unadopted_shards', []).append(dict(snap=snap, tp=rank, worker=worker,
        cleanup='no native release_shard API; if adoption fails, terminate only current owned worker at final cleanup'))
    write(out / 'journal.json', journal)
    result = request(cluster.endpoints[worker], 'synth', dict(snap=snap, tp=rank, tp_size=4, nbytes=nbytes, seed=3100+rank))
    info = result['host_allocation']
    require(info['registered'] is True and info['backend'] == 'numa_registered' and info['numa'] == workers[worker]['numa'], 'host not registered on selected NUMA')
    require(info['sampled_page_distribution'] == {str(workers[worker]['numa']): 1.0} and info['placement_samples'] == 256,
            'host actual touched page placement mismatch')
    row = request(cluster.endpoints[worker], 'adopt', dict(snap=snap, tp=rank, id=rid))
    journal['unadopted_shards'] = [x for x in journal['unadopted_shards'] if (x['snap'], x['tp']) != (snap, rank)]
    owned.append(row)
    write(out / 'journal.json', journal)
    require(row['meta']['tp_size'] == 4 and row['meta']['tp_rank'] == rank and row['meta']['synthetic'] is True,
            'synthetic processed TP4 schema mismatch')
    journal.setdefault('synth', []).append(dict(row=row, diagnostics=result))
    write(out / 'journal.json', journal)
    return row


def ssd_stats(setup):
    result = {}
    for node, ssd in setup['ssd'].items():
        with urllib.request.urlopen(ssd['endpoint'] + '/status', timeout=10) as response:
            row = json.load(response)
        require(row['root'] == ssd.get('container_directory', ssd['directory']), 'SSD service backing directory changed')
        result[node] = row
    return result


def stats_delta(before, after):
    return {node: dict(logical_http_read_bytes=after[node]['read_bytes']-before[node]['read_bytes'],
                       written_bytes=after[node]['written_bytes']-before[node]['written_bytes'],
                       committed_bytes=after[node]['committed_bytes']-before[node]['committed_bytes'],
                       server_kernel_read_bytes=after[node]['kernel_io']['read_bytes']-before[node]['kernel_io']['read_bytes'],
                       server_kernel_write_bytes=after[node]['kernel_io']['write_bytes']-before[node]['kernel_io']['write_bytes'],
                       buffered_read=True, cold_disk_claim=False) for node in before}


def qualify(cluster, setup, budget, out, journal):
    workers, _ = cluster.inventory()
    budget_gate(workers, setup, budget)
    paths = required_paths(setup)
    result = dict(purpose='physical', mode='bounded_qualification', session=setup['session'],
                  payload_bytes_per_path=Q, valid_until=min(setup['valid_until'], budget['valid_until']), paths={},
                  workers=workers, synthetic=True, model_ready=False)
    for index, (key, route) in enumerate(sorted(paths.items())):
        workers, _ = cluster.inventory()
        budget_gate(workers, setup, budget)
        owned = []
        prefix = setup['session'] + f'/qualify/{index}'
        journal['qualification_current_owned'] = owned
        try:
            source = synth_adopt(cluster, setup, workers, prefix, route['source_worker'], 0, Q, owned, journal, out)
            before = ssd_stats(setup)
            if route['source_tier'] != 'dram':
                target = target_from(source, workers, prefix+'/source', source['worker'], route['source_tier'],
                                     ssd=setup['ssd'][f'n{source["worker"]//2}'])
                remember(owned, target, workers)
                write(out / 'journal.json', journal)
                bootstrap = cluster.transfer(one_plan(source, target, workers), verify_before_ready=True)
                workers, rows = cluster.inventory()
                source = next(r for r in rows if r['id'] == target['id'])
            else:
                bootstrap = None
            target = target_from(source, workers, prefix+'/target', route['target_worker'], route['target_tier'])
            remember(owned, target, workers)
            write(out / 'journal.json', journal)
            transfer = cluster.transfer(one_plan(source, target, workers), verify_before_ready=True)
            require(transfer['verification']['passed'] and transfer['verification']['checked_bytes'] == Q, 'qualification missing every-byte SHA')
            after = ssd_stats(setup)
            proof = dict(route, executor='serial', source_epoch=workers[source['worker']]['epoch'],
                         target_epoch=workers[target['worker']]['epoch'], status='verified', nbytes=Q,
                         source_bootstrap=bootstrap, transfer=transfer, ssd_io=stats_delta(before, after))
            if route['source_tier'] == 'ssd':
                proof.update(store_endpoint=source['store_endpoint'], storage_node=source['storage_node'])
            result['paths'][key] = proof
            write(out / 'qualification.json', result)
        finally:
            exact_release(cluster, owned, journal, 'end_of_qualification_path')
            write(out / 'journal.json', journal)
    workers, rows = cluster.inventory()
    budget_gate(workers, setup, budget)
    require(not rows, 'qualification allocations remain')
    result.update(status='complete', postqualification_workers=workers)
    write(out / 'qualification.json', result)
    return result


def build_base(cluster, setup, workers, owned, journal, out):
    bases, manifests, transfers = {}, {}, []
    for rank, owner in enumerate((0, 0, 2, 4)):
        source_owned = []
        journal['base_seed_current_owned'] = source_owned
        source = synth_adopt(cluster, setup, workers, setup['session']+f'/base_seed/{rank}', owner, rank, 2*Q,
                             source_owned, journal, out)
        journal.setdefault('base_seed_owned', []).extend(source_owned)
        manifests[str(rank)] = {k: source[k] for k in ('meta', 'index', 'identity')}
        targets, ops = [], []
        for block in range(2):
            bid = f'b{rank*2+block}'
            target = target_from(source, workers, setup['session']+'/base/'+bid, owner, 'ssd', off=block*Q, n=Q,
                                 ssd=setup['ssd'][f'n{owner//2}'])
            remember(owned, target, workers)
            bases[bid] = target['id']
            targets.append(target)
            op = one_plan(source, target, workers)['ops'][0]
            op['op_id'] = block
            ops.append(op)
        write(out / 'journal.json', journal)
        plan = dict(targets=targets, ops=ops, executor='serial', chunk_bytes=32 << 20)
        validate_plan(plan)
        transfer = cluster.transfer(plan, verify_before_ready=True)
        require(transfer['verification']['checked_bytes'] == 2*Q, 'base build full SHA missing')
        transfers.append(transfer)
        # The complete SSD extents now cover this synthetic rank before its DRAM seed is retired.
        exact_release(cluster, source_owned, journal, 'base_seed_replaced_by_verified_ssd')
        write(out / 'journal.json', journal)
    current_workers, rows = cluster.inventory()
    require({r['id'] for r in rows} == set(bases.values()) and len(rows) == 8, 'base catalog coverage mismatch')
    for row in rows:
        require(row['tier'] == 'ssd' and row['state'] == 'VALID' and row['leases'] == 0 and row['n'] == Q, 'base extent invalid')
    write(out / 'base.json', dict(synthetic=True, scope='processed TP4-schema byte fixture; no real model',
                                  manifests=manifests, base_ids=bases, current_catalog=rows, transfers=transfers,
                                  complete_bytes=8*Q, distribution_bytes=[4*Q, 2*Q, 2*Q], fsync_by_native_complete=True,
                                  reboot_recovery_claim=False))
    return bases, manifests


def make_binding(setup, workers, qualification, budget, bases, manifests, refs):
    lifetime = min(setup['valid_until'], qualification['valid_until'], budget['valid_until'])
    binding = dict(contract_hash=adapter.FROZEN, purpose='physical', valid_until=lifetime,
                   workers=copy.deepcopy(workers), manifests=manifests, base_ids=bases, ssd=copy.deepcopy(setup['ssd']),
                   evidence_refs=refs, paths={}, phase_admission={})
    for i, worker in enumerate(binding['workers']):
        worker.update(logical_worker=f'w{i}', endpoint=setup['workers'][i]['endpoint'])
    for key, proof in qualification['paths'].items():
        binding['paths'][key] = {k: proof[k] for k in ('status', 'executor', 'source_epoch', 'target_epoch')}
        binding['paths'][key].update(valid_until=lifetime, evidence_ref='qualification')
        if proof['source_tier'] == 'ssd':
            binding['paths'][key].update(store_endpoint=proof['store_endpoint'], storage_node=proof['storage_node'])
    for ssd in binding['ssd'].values():
        ssd.update(current_owner_session=setup['session'], backing_evidence_ref='setup')
    for phase in PHASES:
        binding['phase_admission'][phase] = dict(status='admitted', valid_until=lifetime, evidence_ref='budget',
            runtime_staging_inclusion='included', budget_accounting_complete=True, physical_peak_measured=False)
    return binding


def cases(cluster, setup, budget, qualification, out, journal, args):
    require(qualification.get('purpose') == 'physical' and qualification.get('mode') == 'bounded_qualification' and
            qualification.get('status') == 'complete' and qualification['session'] == setup['session'], 'real completed qualification required')
    require(set(qualification['paths']) == set(required_paths(setup)), 'selected paths not fully qualified')
    workers, rows = cluster.inventory()
    budget_gate(workers, setup, budget)
    require(not rows, 'fresh scenario catalog expected')
    adapter.check_current_workers(workers, dict(workers=qualification['workers']))
    for key, proof in qualification['paths'].items():
        require(proof['transfer']['verification']['passed'] and proof['transfer']['verification']['checked_bytes'] == Q,
                'qualification full SHA mismatch')
        require(proof['source_epoch'] == workers[proof['source_worker']]['epoch'] and
                proof['target_epoch'] == workers[proof['target_worker']]['epoch'], 'qualification incarnation changed')
    base_owned = []
    journal['base_owned'] = base_owned
    before = ssd_stats(setup)
    bases, manifests = build_base(cluster, setup, workers, base_owned, journal, out)
    base_io = stats_delta(before, ssd_stats(setup))
    workers, _ = cluster.inventory()
    refs = dict(setup=ref(args.setup), budget=ref(args.budget), qualification=ref(args.qualification), base=ref(out/'base.json'))
    binding = make_binding(setup, workers, qualification, budget, bases, manifests, refs)
    write(out / 'binding.json', binding)
    contract = adapter.load_contract()
    results = []
    def trusted_gate(current_binding, phase, run_id):
        require(current_binding is binding and phase in PHASES and run_id == setup['session']+'_cases', 'unexpected physical request')
        fresh, _ = cluster.inventory()
        budget_gate(fresh, setup, budget)
        adapter.check_current_workers(fresh, binding)
        for reference in refs.values():
            require(sha(reference['path']) == reference['sha256'], 'gate evidence SHA changed')
        return dict(status='admitted', contract_hash=adapter.FROZEN, phase=phase, run_id=run_id,
                    mode='native_functional_bytes', physical_peak_measured=False, model_ready=False)
    for phase in PHASES:
        before = ssd_stats(setup)
        result = None
        try:
            result = adapter.execute_phase(cluster, contract, binding, phase, setup['session']+'_cases',
                                           trusted_admission=trusted_gate, journal_path=out / f'{phase}.json')
            result['ssd_io'] = stats_delta(before, ssd_stats(setup))
            for stage in ('save', 'restore'):
                if result[stage]:
                    require(result[stage]['verification']['passed'], 'case SHA missing')
            require(result['restore']['verification']['checked_bytes'] == 8*Q, 'restore did not verify all 32MiB')
        except adapter.AdapterExecutionError as exc:
            result = exc.journal
            raise
        finally:
            if result:
                result['cleanup'] = adapter.cleanup_owned(cluster, result)
                write(out / f'{phase}.json', result)
        results.append(dict(phase=phase, status=result['status'], save=result['save'], restore=result['restore'], ssd_io=result['ssd_io']))
        write(out / 'summary.json', dict(status='running', synthetic=True, results=results, model_ready=False))
    _, final_rows = cluster.inventory()
    require({r['id'] for r in final_rows} == set(bases.values()), 'optional/target cleanup changed full base')
    journal.update(status='end_of_run', base_retirement_allowed=True, base_preserved_through_every_case=True)
    write(out / 'journal.json', journal)
    if args.release_base_at_end:
        exact_release(cluster, base_owned, journal, 'explicit_end_of_run_full_base_release')
    final_workers, final_rows = cluster.inventory()
    write(out / 'final_inventory.json', dict(workers=final_workers, replicas=final_rows))
    write(out / 'journal.json', journal)
    write(out / 'summary.json', dict(status='complete', synthetic=True, payload_bytes=8*Q, results=results,
          base_build_ssd_io=base_io, base_preserved_through_every_case=True, base_released_at_end=args.release_base_at_end,
          timing_scope='same-clock native coordinator save/prepare/filled/verify-publish; no model handoff',
          model_ready=False, physical_peak_measured=False, cold_disk_claim=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--stage', required=True, choices=('mesh', 'qualify', 'cases'))
    parser.add_argument('--budget')
    parser.add_argument('--qualification')
    parser.add_argument('--release-base-at-end', action='store_true')
    args = parser.parse_args()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    journal = dict(stage=args.stage, status='started', owned_scope='current task exact IDs only',
                   old_keepers_touched=False, model_ready=False)
    cluster = None
    write(out / 'journal.json', journal)
    try:
        require(sha(adapter.__file__) == ADAPTER_SHA, 'frozen serial adapter source changed')
        write(out / 'source_shas.json', {str(path): sha(path) for path in [Path(__file__),
              Path(__file__).with_name('runtime_worker.py'), Path(adapter.__file__),
              ROOT/'main_lib/fluidckpt/cluster/control.py', ROOT/'main_lib/fluidckpt/cluster/worker.py',
              ROOT/'main_lib/fluidckpt/cluster/catalog.py', ROOT/'main_lib/fluidckpt/memory/host.py',
              ROOT/'main_lib/fluidckpt/memory/ssd.py', ROOT/'main_lib/fluidckpt/serving/ssd_store.py']})
        setup = read(args.setup)
        check_setup(setup)
        cluster = Cluster([w['endpoint'] for w in setup['workers']], {}, executor='serial')
        if args.stage == 'mesh':
            write(out / 'premesh.json', cluster.call_all('inventory'))
            reports = cluster.call_all('mesh', [{} for _ in range(6)])
            pairs = sorted({tuple(sorted((p['source_worker'], p['target_worker']))) for p in required_paths(setup).values()
                            if p['source_worker'] != p['target_worker']})
            warm = cluster.call_all('warm', [dict(pairs=pairs) for _ in range(6)])
            workers, rows = cluster.inventory()
            check_workers(workers, setup)
            require(not rows, 'fresh mesh should have no allocations')
            write(out / 'mesh.json', dict(status='complete', purpose='physical', session=setup['session'],
                                         workers=workers, replicas=rows, mesh=reports, necessary_pairs=pairs, warm=warm,
                                         required_paths=required_paths(setup), budget_admitted=False))
        else:
            require(args.budget is not None, 'reviewed budget required after mesh')
            budget = read(args.budget)
            if args.stage == 'qualify':
                qualify(cluster, setup, budget, out, journal)
            else:
                require(args.qualification is not None, 'real qualification file required')
                cases(cluster, setup, budget, read(args.qualification), out, journal, args)
        journal['status'] = 'complete'
        write(out / 'journal.json', journal)
    except BaseException as exc:
        journal.update(status='failed', error=repr(exc), cleanup_requires_exact_current_task_identity=True)
        if cluster is not None:
            owned = journal.get('qualification_current_owned', []) + journal.get('base_seed_owned', []) + journal.get('base_seed_current_owned', [])
            if args.release_base_at_end:
                owned += journal.get('base_owned', [])
                journal.update(base_retirement_allowed=True, failed_run_has_ended=True)
            unique = {row['id']: row for row in owned}
            try:
                exact_release(cluster, list(unique.values()), journal, 'failed_run_exact_cleanup')
            except BaseException as cleanup:
                journal['cleanup_failure'] = repr(cleanup)
        write(out / 'journal.json', journal)
        raise


if __name__ == '__main__':
    main()
