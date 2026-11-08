"""Run one finite six-worker session and stop all owned processes on any failure."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import shutil
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
ms = importlib.util.spec_from_file_location('multitier_operations', HERE/'control.py')
ops = importlib.util.module_from_spec(ms)
ms.loader.exec_module(ops)
old, RESULTS = ops.old, ops.RESULTS
COMPATIBLE_SOURCE = ROOT/'codex_coding/src/v0.1.23.3/task2/layout_validation/operations/collect_compatible.py'


def extract_regular(archive, destination, prefixes):
    # Reuse the proven collector's extraction algorithm without its fixed-session imports.
    with tarfile.open(archive) as handle:
        members = handle.getmembers()
        seen = set()
        if sum(member.size for member in members) > 64 << 20:
            raise ValueError('owned artifact archive exceeds 64MiB bound')
        for member in members:
            relative = Path(member.name)
            if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                    or relative.parts[0] not in prefixes or member.name in seen
                    or not (member.isdir() or member.isfile())):
                raise ValueError('unsafe owned archive entry: '+member.name)
            seen.add(member.name)
        destination.mkdir(parents=True,exist_ok=False)
        for member in members:
            target = destination/member.name
            if member.isdir():
                target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with handle.extractfile(member) as source,target.open('xb') as output:
                    shutil.copyfileobj(source,output)


def get(endpoint, suffix, timeout=2):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint+suffix, timeout=timeout) as response:
        raw = response.read((4 << 20)+1)
    if len(raw) > 4 << 20:
        raise ValueError('HTTP metadata exceeds bound')
    return json.loads(raw)


def inventories(plan):
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda w:get(w['endpoint'],'/inventory?compact=1'), plan['workers']))


def assert_live(futures):
    for key, future in futures.items():
        if future.done():
            result = future.result()
            raise RuntimeError('owned supervisor exited early '+key+' '+repr(result[0]))


def budget(plan, workers, baseline, metadata):
    result = dict(purpose='physical',session=plan['session'],valid_until=time.time()+1800,
                  runtime_staging_inclusion='included',budget_accounting_complete=True,nodes={},
                  peak_measured=False, accounting='absolute shared cgroup; selected GPU total-free minus prelaunch used, node27 baseline zero conservative upper bound')
    for node in ('node28','node30','node27'):
        group = [w for w in workers if w['node']==node]
        current = max(w['runtime_identity']['cgroup_memory_current_bytes'] for w in group)
        limit = min(w['runtime_identity']['cgroup_memory_max_bytes'] for w in group)
        pre = {r['gpu']:int(r.get('mem_info_vram_used',0)) for r in baseline[node]['selected_gpus']}
        increment = sum(max(0,w['hbm_total_bytes']-w['headroom_bytes']['hbm']-pre[w['gpu']]) for w in group)
        available = next(int(l.split()[1])*1024 for l in metadata[node]['meminfo'].splitlines() if l.startswith('MemAvailable:'))
        sample = dict(dram_current_bytes=current,dram_limit_bytes=6 << 30,hbm_increment_bytes=increment,hbm_limit_bytes=2 << 30,
                      dram_margin_bytes=(6 << 30)-current,hbm_margin_bytes=(2 << 30)-increment,
                      physical_dram_available_bytes=available,cgroup_available_bytes=limit-current,
                      selected_gpu_free_bytes={str(w['gpu']):w['headroom_bytes']['hbm'] for w in group})
        if current+(256 << 20)>min(limit,6 << 30) or increment+(256 << 20)>2 << 30:
            raise MemoryError('postwarm task aggregate application margin exhausted '+node)
        if available < (2 << 30)+(256 << 20):
            raise MemoryError('host physical DRAM reserve exhausted '+node)
        if any(w['headroom_bytes']['hbm'] < (2 << 30)+(32 << 20) for w in group):
            raise MemoryError('selected GPU physical HBM reserve exhausted '+node)
        result['nodes'][node] = sample
    return result


def controller(argv, directory, seconds, futures, monitor):
    directory.mkdir(parents=True,exist_ok=False)
    start = time.monotonic()
    with (directory/'stdout.txt').open('xb') as stdout, (directory/'stderr.txt').open('xb') as stderr:
        child = subprocess.Popen(argv,stdout=stdout,stderr=stderr,start_new_session=True)
        row = {'argv':argv,'timeout_s':seconds,'controller_pid':child.pid}
        try:
            next_sample = start
            while child.poll() is None:
                assert_live(futures)
                if time.monotonic()-start > seconds:
                    raise TimeoutError('controller stage deadline')
                if monitor and time.monotonic() >= next_sample:
                    monitor()
                    next_sample = time.monotonic()+2
                time.sleep(.2)
            row['returncode'] = child.returncode
            if child.returncode != 0:
                raise RuntimeError('controller failed '+str(child.returncode))
        except BaseException as exc:
            row['failure'] = repr(exc)
            if child.poll() is None:
                # Only the locally created controller is signalled here.
                child.terminate()
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=1)
            row['returncode'] = child.returncode
            raise
        finally:
            row['elapsed_s'] = time.monotonic()-start
            old.write(directory/'command.json',row)
    return row


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--execute',action='store_true')
    a = p.parse_args()
    if not a.execute:
        raise ValueError('parent GPU execution requires --execute')
    a.plan,a.out = a.plan.resolve(),a.out.resolve()
    if a.out.parent != RESULTS or not a.out.name.startswith('operations_'):
        raise ValueError('new owned operations output required')
    plan = json.loads(a.plan.read_text())
    if len(plan['processes'])!=9 or len(plan['workers'])!=6 or plan['lifetime_s']!=1800 or plan['controller_timeout_s']!=1440:
        raise ValueError('finite six-worker/nine-process contract required')
    a.out.mkdir(parents=True,exist_ok=False)
    digest = hashlib.sha256(a.plan.read_bytes()).hexdigest()
    summary = dict(plan_ref=old.ref(a.plan),status='prelaunch',GPU_launches=0,workers={},
                   parent_source_ref=old.ref(Path(__file__)),compatible_extraction_source_ref=old.ref(COMPATIBLE_SOURCE))
    baseline = {}
    ss = ops.specs(Path(plan['setup_directory']))
    bound = json.loads(Path(plan['bindings_ref']['path']).read_text())['containers']
    for spec in ss:
        spec.update(bound[spec['node']])
        body = ops.readback(spec,a.out/('baseline_'+spec['node']))
        baseline[spec['node']] = body
        ci = body['container']
        if ci['Id']!=spec['container_id'] or ci['State']['StartedAt']!=spec['container_started_at'] or body['cgroup']['files']['pids.current']!='1':
            raise ValueError('exact task container not quiet/current')
        if not all(body['ports_free'].values()):
            raise ValueError('selected task ports occupied')
        if any(body['source_assets'].get(x['relative_path'])!=x['sha256'] for x in spec['assets']):
            raise ValueError('final deployed source hash differs')
        deploy = ['docker','exec','-i',spec['container_id'],'bash','-c','set -C\nmkdir -p '+ops.shlex.quote(str(a.plan.parent))+'\ncat > '+ops.shlex.quote(str(a.plan))]
        proc,_ = old.record(ops.command(spec['node'],deploy),a.out/('deploy_plan_'+spec['node']),20,a.plan.read_bytes())
        if proc['returncode']!=0:
            raise RuntimeError('noclobber launch plan deployment failed')
    futures = {}
    session_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=9) as pool:
        try:
            for row in plan['processes']:
                argv = ops.command(row['node'],['docker','exec',row['container_id'],'python3',str(HERE/'supervise.py'),'--plan',str(a.plan),'--key',row['key']])
                futures[row['key']] = pool.submit(old.record,argv,a.out/('process_'+row['key']),1815)
            summary['GPU_launches'] = 6
            ready = None
            while time.monotonic()-session_start < plan['startup_s']:
                assert_live(futures)
                try:
                    ready = inventories(plan)
                    for actual, expected in zip(ready,plan['workers']):
                        rt = actual['runtime_identity']
                        if (actual['node'],actual['gpu'],actual['numa'],actual['session']) != (expected['node'],expected['gpu'],expected['numa'],plan['session']) or rt['actual_bdf']!=expected['bdf'] or rt['boot_id']!=expected['boot_id']:
                            raise ValueError('fresh runtime physical identity mismatch')
                    for ssd in plan['ssd'].values():
                        if get(ssd['endpoint'],'/status')['root']!='/task_ssd':
                            raise ValueError('fresh SSD service backing mismatch')
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    ready = None
                    time.sleep(.25)
            if ready is None:
                raise TimeoutError('startup90 deadline')
            old.write(a.out/'startup.inventory.json',ready)
            setup = dict(purpose='physical',session=plan['session'],valid_until=time.time()+1800,workers=[],ssd=plan['ssd'],
                         evidence_refs={'plan':old.ref(a.plan),'startup':old.ref(a.out/'startup.inventory.json')})
            for actual, expected in zip(ready,plan['workers']):
                rt = actual['runtime_identity']
                setup['workers'].append(dict(expected,actual_bdf=rt['actual_bdf'],pid=rt['pid'],pid_start_ticks=rt['start_ticks']))
            setup_path = a.out/'runtime_setup.json'
            old.write(setup_path,setup)
            runner = HERE.parent/'runner/run.py'
            mesh = a.out/'mesh'
            command_base = [sys.executable,str(runner),'--setup',str(setup_path)]
            # The in-container supervisors enforce DRAM cap throughout mesh init.
            controller(command_base+['--output',str(mesh),'--stage','mesh'],a.out/'mesh_controller',300,futures,None)
            workers = inventories(plan)
            metadata = {s['node']:ops.readback(s,a.out/('postwarm_'+s['node'])) for s in ss}
            accepted = budget(plan,workers,baseline,metadata)
            old.write(a.out/'postwarm.inventory.json',workers)
            budget_path = a.out/'budget_evidence.json'
            old.write(budget_path,accepted)
            monitor_samples = []
            def monitor():
                fresh = inventories(plan)
                for node,sample in accepted['nodes'].items():
                    group = [w for w in fresh if w['node']==node]
                    current = max(w['runtime_identity']['cgroup_memory_current_bytes'] for w in group)
                    delta = sum(max(0,sample['selected_gpu_free_bytes'][str(w['gpu'])]-w['headroom_bytes']['hbm']) for w in group)
                    if current+(256 << 20)>6 << 30 or sample['hbm_increment_bytes']+delta+(256 << 20)>2 << 30:
                        raise MemoryError('live aggregate application margin exhausted '+node)
                    monitor_samples.append({'elapsed_s':time.monotonic()-session_start,'node':node,'dram_current':current,'hbm_increment_upper':sample['hbm_increment_bytes']+delta})
                old.write(a.out/'budget_monitor.json',monitor_samples)
                if time.monotonic()-session_start>1440:
                    raise TimeoutError('whole controller deadline')
            qualification = a.out/'qualification'
            controller(command_base+['--output',str(qualification),'--stage','qualify','--budget',str(budget_path)],a.out/'qualification_controller',600,futures,monitor)
            cases = a.out/'cases'
            controller(command_base+['--output',str(cases),'--stage','cases','--budget',str(budget_path),
                       '--qualification',str(qualification/'qualification.json'),'--release-base-at-end'],a.out/'cases_controller',300,futures,monitor)
            summary['status'] = 'completed_pending_independent_review'
        except BaseException as exc:
            summary.update(status='failed',failure=repr(exc))
        finally:
            summary['all_stop'] = ops.stops(plan,digest,a.out)
            for key,future in futures.items():
                try:
                    summary['workers'][key] = future.result()[0]
                except BaseException as exc:
                    summary['workers'][key] = {'failure':repr(exc)}
            summary['elapsed_s'] = time.monotonic()-session_start
            old.write(a.out/'summary.json',summary)
    summary['collection'] = {}
    for spec in ss:
        node = spec['node']
        try:
            body = ops.readback(spec,a.out/('after_stop_'+node))
            ci = body['container']
            if ci['Id']!=spec['container_id'] or ci['State']['StartedAt']!=spec['container_started_at']:
                raise ValueError('final container identity changed')
            if body['cgroup']['files']['pids.current']!='1':
                raise ValueError('final task container is not quiet')
            before = baseline[node]
            stable_owners = lambda rows:{r['pid']:{k:r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')} for r in rows}
            if body['original_xml_sha256']!=before['original_xml_sha256'] or stable_owners(body['protected_owners'])!=stable_owners(before['protected_owners']):
                raise ValueError('protected XML/owner changed')
            def stable(rows):
                result = {}
                for row in rows:
                    item = {k:row[k] for k in ('id','name','StartedAt','Running','Pid')}
                    identity = row['identity']
                    if identity is not None:
                        stat = identity['stat']
                        item['sleep_start_ticks'] = stat.rsplit(') ',1)[1].split()[19] if isinstance(stat,str) else None
                        item['sleep_cgroup'] = identity['cgroup']
                    result[row['id']] = item
                return result
            if stable(body['protected_containers'])!=stable(before['protected_containers']):
                raise ValueError('protected container changed')
            proc,raw = old.record(ops.command(node,['docker','exec',spec['container_id'],'python3',str(HERE/'collect_owned.py'),
                    '--plan',str(a.plan),'--node',node]),a.out/('exact_collect_'+node),20)
            if proc['returncode']!=0:
                raise RuntimeError('owned exact child/group collection failed')
            old.write(a.out/('exact_collect_'+node)/'stdout.json',json.loads(raw))
            remote_root = Path(spec['stage'])/'project'/RESULTS.relative_to(ROOT)
            names = [Path(r['out']).name for r in plan['processes'] if r['node']==node]
            names.append('operations_dump_'+plan['session'])
            remote_plan = Path(spec['stage'])/'project'/a.plan.relative_to(ROOT)
            proc,raw = old.record(ops.command(node,['python3','-',str(remote_plan),str(remote_root),node]),
                a.out/('artifacts_'+node),30,(HERE/'collect_artifacts.py').read_bytes())
            if proc['returncode']!=0:
                raise RuntimeError('owned artifact tar failed')
            archive = a.out/('artifacts_'+node)/'owned.tar'
            archive.write_bytes(raw)
            extract_regular(archive,a.out/('artifacts_'+node)/'raw',set(names)|{'collection_presence.json'})
            summary['collection'][node] = {'passed':True}
        except BaseException as exc:
            summary['collection'][node] = {'passed':False,'failure':repr(exc)}
    old.write(a.out/'summary.json',summary)
    print(json.dumps(summary))
    if summary['status']=='failed' or any(p.get('returncode')!=0 for p in summary['workers'].values()) or not all(r['passed'] for r in summary['collection'].values()):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
