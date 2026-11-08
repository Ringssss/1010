"""Last two diagnostic requests: repeat identical copies within one HTTP handler."""
import argparse
import copy
from pathlib import Path
import time
from common import ROOT, HERE, CALIBRATION, native, ops, write, ref, require, budget_snapshot, check_reference, load
from fluidckpt.cluster.control import Cluster, CONTROLLER_CLOCK_DOMAIN
from fluidckpt.cluster.service_cost import new_state
from fluidckpt.cluster.planning import validate_plan

CASES = [dict(label=f'host_repeat_{size}MiB', mode='host_repeat', nbytes=size << 20,
              order_index=index, source_offset=0, operations=1, copy_rounds=2)
         for index, size in enumerate((4, 16))]
PROTOCOL = dict(cases=CASES, executor='serial', chunk_bytes=32 << 20, necessary_pairs=[[2,4],[4,5]],
    source_bytes=16 << 20, source_worker=4, source_tier='hbm', target_worker=4, target_tier='hbm',
    mode='cached', target_allocation='fresh exact size, same allocation used in both rounds', fits=0,
    copy_rounds_per_request=2, requests=2, logical_SHA_bytes=sum(c['nbytes'] for c in CASES),
    actual_copy_bytes=2*sum(c['nbytes'] for c in CASES), model_updates=0, further_GPU_requests=0,
    event_policy='no CUDA events and no prepare event initialization or extra synchronization',
    prewarm_policy='none; both copies and all host work remain within request/filled window',
    metadata_policy='pointers/views/stream/memory diagnosis only after both copy rounds',
    interpretation='diagnostic first-operation host cost; second copy also changes target first-touch/cache state; not an optimization benchmark')

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
    plan.update(local_slope_repeat=2, local_slope_repeat_label=case['label'])
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
            diag = records[0]['local_slope_diagnosis']
            require(owner['executor_source'] == 'host_only_same_handler_two_copy_rounds' and records[0]['actual_copy_bytes'] == 2*case['nbytes'], 'two-copy execution evidence missing')
            require(len(diag['rounds']) == 2 and all(r['host_residual_ns'] == 0 for r in diag['rounds']), 'round phase partition not exact')
            require(diag['same_source_view_address'] and diag['same_target_view_address'] and diag['CUDA_events'] == 0, 'same-address host-only contract differs')
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
        write(out / 'summary.json', dict(status='complete', requests=2, mode_sizes=CASES, fits=0,
              logical_measurement_bytes=20 << 20, actual_measurement_copy_bytes=40 << 20, bootstrap_bytes=16 << 20,
              model_updates=0, prediction=None, intervention_requests=2, preregistration_ref=frozen_ref,
              further_GPU_requests=0, continuous_peak_measured=False))
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

def cpu_replay():
    import sys
    from types import ModuleType, SimpleNamespace
    from common import RESULTS, fresh_output
    import cpu_replay as prior_cpu
    p = argparse.ArgumentParser()
    p.add_argument('--cpu-replay', action='store_true')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    fake_torch = ModuleType('torch')
    fake_torch.cuda = SimpleNamespace(set_device=lambda *a:None, synchronize=lambda *a:None,
        mem_get_info=lambda *a:(15 << 30,16 << 30), memory_allocated=lambda *a:0, memory_reserved=lambda *a:0,
        current_stream=lambda *a:SimpleNamespace(cuda_stream=0), empty_cache=lambda:None, ipc_collect=lambda:None)
    fake_torch.uint8 = 'torch.uint8'
    sys.modules['torch'] = fake_torch
    overlay = load('local_slope_repeat_cpu_worker', HERE / 'repeat_worker.py')
    previous = RESULTS / 'execution_actual_r01'
    prior_prepared = RESULTS / 'execution_prepared_r03'
    workers = native.read(previous/'mesh/mesh.json')['workers']
    setup = native.read(previous/'runtime_setup.json')
    launch = native.read(prior_prepared/'launch_plan.json')
    session = 'local_slope_runtime_repeat_cpu'
    launch.update(session=session, protocol=PROTOCOL, setup_directory=str(prior_prepared), model_consumed=False)
    launch_path = out/'launch_plan.json'
    write(launch_path,launch)
    setup.update(session=session,valid_until=time.time()+300,evidence_refs={'plan':ref(launch_path)},CPU_REPLAY_ONLY=True)
    setup_path = out/'runtime_setup.json'
    write(setup_path,setup)
    for w in workers:
        w.update(session=session,epoch='cpu_repeat/'+str(w['worker']),replicas=[],used_bytes=dict(hbm=0,dram=0,ssd=0),transactions=[])
    mesh = native.read(previous/'mesh/mesh.json')
    mesh.update(workers=workers,incarnations=new_state(workers)['incarnations'],CPU_REPLAY_ONLY=True)
    (out/'mesh').mkdir()
    write(out/'mesh/mesh.json',mesh)
    training = CALIBRATION/'execution_actual_r02/measure'
    synth = native.read(training/'journal.json')['synth'][0]
    hashes = {}
    for name in ('p0_warm_small','p0_warm_large'):
        row = next(iter(native.read(training/(name+'.json'))['flow']['verification']['operations'].values()))
        hashes[row['n']] = row['source_sha256']
    from fluidckpt.cluster.catalog import Catalog
    pattern = bytes(range(256))*((16 << 20)//256)
    fake_workers, copy_sizes, logs = [], [], []
    old_copy = prior_cpu.CpuTensor.copy_
    def tracked_copy(tensor, source, non_blocking=False):
        result = old_copy(tensor,source,non_blocking)
        copy_sizes.append(source.numel())
        return result
    prior_cpu.CpuTensor.copy_ = tracked_copy
    for index,w in enumerate(workers):
        cls = overlay.RepeatWorker if index == 4 else overlay.previous.original.NATIVE_WORKER
        worker = cls.__new__(cls)
        worker.rank,worker.node,worker.gpu,worker.dev = index,w['node'],w['gpu'],'cuda:0'
        worker.epoch,worker.a = w['epoch'],SimpleNamespace(numa=w['numa'])
        worker.runs,worker.catalog = {},Catalog(dict(hbm=1 << 30,dram=1 << 30,ssd=1 << 30))
        worker.execution_profile,worker._slope_events = None,{}
        worker.log = lambda **row:logs.append(row)
        worker.allocate = lambda desc:(prior_cpu.CpuTensor(bytearray(desc['n'])),None)
        def inventory(compact=False,index=index,worker=worker):
            result = copy.deepcopy(workers[index])
            result.update(replicas=worker.catalog.inventory(compact=compact),used_bytes=worker.catalog.used(),transactions=list(worker.runs))
            return result
        worker.inventory = inventory
        def digest(entry,off,n):
            require(entry['tensor'].array[off:off+n] == pattern[:n],'actual CPU bytes differ')
            return hashes[n]
        worker.extent_digest,worker.target_digest = digest,digest
        fake_workers.append(worker)
    def request(endpoint,operation,body):
        index = next(w['worker'] for w in setup['workers'] if w['endpoint']==endpoint)
        worker = fake_workers[index]
        if operation == 'synth':
            require(body['seed']==3100 and body['nbytes']==16 << 20,'source generator contract differs')
            return copy.deepcopy(synth['diagnostics'])
        if operation == 'adopt':
            row = copy.deepcopy(synth['row'])
            row.update(id=body['id'],epoch=worker.epoch)
            worker.catalog.reserve(row,lambda:(prior_cpu.CpuTensor(bytearray(pattern)),None))
            worker.catalog.publish(row['id'],True)
            return next(r for r in worker.catalog.inventory() if r['id']==row['id'])
        if operation == 'retire':
            return worker.retire(body)
        raise AssertionError(operation)
    global Cluster
    class ReplayCluster(Cluster):
        def call_all(self,operation,bodies=None,telemetry=None):
            if operation.startswith('inventory'):
                return [w.inventory(compact=operation.endswith('compact=1')) for w in fake_workers]
            return [getattr(w,operation)(bodies[index]) for index,w in enumerate(fake_workers)]
    Cluster = ReplayCluster
    native.request = request
    native.ssd_stats = lambda s:copy.deepcopy(native.read(training/'final_inventory.json')['ssd'])
    sys.argv = ['repeat_runner.py','--setup',str(setup_path),'--plan',str(launch_path),'--output',str(out/'measure'),'--stage','measure']
    main()
    results = native.read(out/'measure/measurements.json')
    require([r['case'] for r in results]==CASES,'last two-request matrix differs')
    require(copy_sizes == [16 << 20,4 << 20,4 << 20,16 << 20,16 << 20],'actual CPU physical copy count/size differs')
    require(all(not w.runs and not any(w.catalog.used().values()) and not w._slope_events for w in fake_workers),'exact cleanup failed')
    for row in results:
        diag = row['flow']['execution'][4]['records'][0]['local_slope_diagnosis']
        require(diag['CUDA_events']==0 and not diag['extra_prewarm'] and not diag['extra_prepare_sync'],'unapproved prewarm/event intervention')
        require(diag['rounds'][0]['native_tid']==diag['rounds'][1]['native_tid'],'rounds did not use one handler')
    write(out/'qualification.json',dict(passed=True,CPU_REPLAY_ONLY=True,GPU_launches=0,remote_writes=0,
        simulated_requests=2,actual_GPU_measurements=0,logical_SHA_bytes=36 << 20,physical_copy_bytes=56 << 20,
        checks=['real source0 schema and old bootstrap SHA metadata oracle','native transfer prepare execute verify publish finish',
        'both rounds bytearray copy all logical bytes','two copies same CPU handler/source/target addresses',
        'no CUDA events/extra prewarm/prepare sync','actual copy count2n and logical SHA n','native exact release clears catalog/leases/transactions'],
        digest_policy='old SHA metadata oracles; CPU pattern compared byte-for-byte independently; CPU timings are not GPU evidence'))
    print(str(out/'qualification.json'))

def analyse():
    from common import fresh_output
    p = argparse.ArgumentParser()
    p.add_argument('--analyse', action='store_true')
    p.add_argument('--actual', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    require(not native.read(a.actual/'runtime_setup.json').get('CPU_REPLAY_ONLY'),'CPU timing is not actual evidence')
    rows = native.read(a.actual/'measure/measurements.json')
    require([row['case'] for row in rows]==CASES,'two-request matrix differs')
    result = []
    lines = ['# 同handler两次相同复制：最后两请求','','|size / phase|first wall us|second wall us|first CPU us|second CPU us|','|---|---:|---:|---:|---:|']
    for row in rows:
        record = row['flow']['execution'][4]['records'][0]
        diag = record['local_slope_diagnosis']
        first,second = diag['rounds']
        differences = {}
        for name in first['host_phases']:
            f,s = first['host_phases'][name],second['host_phases'][name]
            differences[name] = dict(second_minus_first_wall_ns=s['wall_ns']-f['wall_ns'],second_minus_first_thread_cpu_ns=s['thread_cpu_ns']-f['thread_cpu_ns'])
            lines.append(f'|{row["case"]["nbytes"]//(1 << 20)}MiB / {name}|{f["wall_ns"]/1000:.3f}|{s["wall_ns"]/1000:.3f}|{f["thread_cpu_ns"]/1000:.3f}|{s["thread_cpu_ns"]/1000:.3f}|')
        result.append(dict(case=row['case'],diagnosis=diag,phase_differences=differences,record=record,
            verification=row['flow']['verification'],raw_ref=ref(a.actual/'measure'/(row['case']['label']+'.json'))))
    lines += ['','逻辑请求4/16MiB各1，实际每请求复制两遍，logical测量SHA20MiB、actual测量copy40MiB，另bootstrap16MiB；filled包含两轮，不能将第二轮时间当优化收益。',
        '', '首→次source_slice的thread CPU变化用于检验首操作host项；copy/sync同时受目标first-touch/cache改变影响，不能归为纯host或物理带宽。无events、无prewarm、metadata仅两轮之后采样。',
        '', '这是最后两个GPU请求；旧模型参数与原结果保持，无拟合或继续补测。']
    write(out/'summary.json',dict(results=result,requests=2,logical_measurement_SHA_bytes=20 << 20,actual_measurement_copy_bytes=40 << 20,
        bootstrap_bytes=16 << 20,fits=0,model_updates=0,further_GPU_requests=0,decision_ready=False))
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    print(str(out/'summary.json'))

if __name__ == '__main__':
    import sys
    if '--cpu-replay' in sys.argv:
        cpu_replay()
    elif '--analyse' in sys.argv:
        analyse()
    else:
        main()
