"""Reuse six workers and exact supervisors for eight local slope diagnostics."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time
import urllib.error
from common import ROOT, HERE, RESULTS, RUNTIME, PROTOCOL, native, parent, ops, write, ref, require, budget_snapshot, check_reference, quiet_readback

def protect(before,after):
    require(before['original_xml_sha256']==after['original_xml_sha256'],'protected XML changed')
    def owners(rows):return {r['pid']:{k:r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')} for r in rows}
    require(owners(before['protected_owners'])==owners(after['protected_owners']),'protected owner identity changed')
    def containers(rows):
        return {r['id']:{k:r[k] for k in ('id','name','StartedAt','Running','Pid')} for r in rows}
    require(containers(before['protected_containers'])==containers(after['protected_containers']),'protected containers changed')

def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--execute',action='store_true')
    a=p.parse_args();require(a.execute,'parent GPU execution requires --execute');a.plan=a.plan.resolve();a.out=a.out.resolve()
    require(a.out.parent==RESULTS and a.out.name.startswith('execution_'),'fresh execution output required')
    plan=native.read(a.plan);require(len(plan['workers'])==6 and len(plan['processes'])==9 and plan['lifetime_s']==1800,'six-worker nine-process finite contract')
    check_reference(plan['frozen_model_ref'])
    require(plan['protocol']==PROTOCOL and plan['session'].startswith('local_slope_runtime_'),'predeclared diagnostic protocol changed')
    a.out.mkdir(parents=True,exist_ok=False);digest=hashlib.sha256(a.plan.read_bytes()).hexdigest()
    summary=dict(status='prelaunch',GPU_launches=0,plan_ref=ref(a.plan),workers={},collection={})
    baseline={};ss=ops.specs(Path(plan['setup_directory']))
    for spec in ss:
        node=spec['node'];body=quiet_readback(spec,a.out/('baseline_'+node));baseline[node]=body
        ci=body['container'];require(ci['Id']==spec['container_id'] and ci['State']['StartedAt']==spec['container_started_at'] and body['cgroup']['files']['pids.current']=='1','fresh quiet exact container')
        require(all(body['ports_free'].values()),'ports occupied');require(all(body['source_assets'].get(x['relative_path'])==x['sha256'] for x in spec['assets']),'deployed source SHA mismatch')
        deploy=['docker','exec','-i',spec['container_id'],'bash','-c','set -C\nmkdir -p '+shlex.quote(str(a.plan.parent))+'\ncat > '+shlex.quote(str(a.plan))]
        proc,_=ops.old.record(ops.command(node,deploy),a.out/('deploy_plan_'+node),20,a.plan.read_bytes());require(proc['returncode']==0,'noclobber plan deploy failed')
    futures={};start=time.monotonic()
    with ThreadPoolExecutor(max_workers=9) as pool:
        try:
            for row in plan['processes']:
                argv=ops.command(row['node'],['docker','exec',row['container_id'],'python3',str(RUNTIME/'operations/supervise.py'),'--plan',str(a.plan),'--key',row['key']])
                futures[row['key']]=pool.submit(ops.old.record,argv,a.out/('process_'+row['key']),1815)
            summary['GPU_launches']=6;ready=None
            while time.monotonic()-start<plan['startup_s']:
                parent.assert_live(futures)
                try:
                    ready=parent.inventories(plan)
                    for actual,expected in zip(ready,plan['workers']):
                        rt=actual['runtime_identity'];require((actual['node'],actual['gpu'],actual['numa'],actual['session'])==(expected['node'],expected['gpu'],expected['numa'],plan['session']) and rt['actual_bdf']==expected['bdf'] and rt['boot_id']==expected['boot_id'],'fresh physical worker identity mismatch')
                    require(all(parent.get(s['endpoint'],'/status')['root']=='/task_ssd' for s in plan['ssd'].values()),'fresh SSD backing mismatch');break
                except (urllib.error.URLError,TimeoutError,ConnectionError):ready=None;time.sleep(.25)
            require(ready is not None,'startup deadline');write(a.out/'startup.inventory.json',ready)
            setup=dict(purpose='physical',session=plan['session'],valid_until=time.time()+1800,workers=[],ssd=plan['ssd'],evidence_refs={'plan':ref(a.plan),'startup':ref(a.out/'startup.inventory.json')})
            for actual,expected in zip(ready,plan['workers']):
                rt=actual['runtime_identity'];setup['workers'].append(dict(expected,actual_bdf=rt['actual_bdf'],pid=rt['pid'],pid_start_ticks=rt['start_ticks']))
            setup_path=a.out/'runtime_setup.json';write(setup_path,setup)
            command=[sys.executable,str(HERE/'runner.py'),'--setup',str(setup_path),'--plan',str(a.plan)]
            parent.controller(command+['--output',str(a.out/'mesh'),'--stage','mesh'],a.out/'mesh_controller',300,futures,None)
            postwarm=parent.inventories(plan);gate=budget_snapshot(postwarm,setup,incremental_reserve=64 << 20);write(a.out/'postwarm.inventory.json',postwarm)
            metadata={s['node']:ops.readback(s,a.out/('postwarm_'+s['node'])) for s in ss}
            for node,body in metadata.items():
                available=next(int(l.split()[1])*1024 for l in body['meminfo'].splitlines() if l.startswith('MemAvailable:'))
                require(available>=(2 << 30)+(256 << 20),'physical DRAM reserve exhausted '+node);gate[node]['physical_dram_available_bytes']=available
            write(a.out/'budget_evidence.json',dict(nodes=gate,accounting='same-HIP absolute conservative upper; shared cgroup counted once',planned_additional_hbm_upper_bytes_per_node=64 << 20,peak_measured=False))
            samples=[]
            def monitor():
                fresh=parent.inventories(plan);sample=budget_snapshot(fresh,setup,incremental_reserve=0,require_idle=False)
                samples.append(dict(elapsed_s=time.monotonic()-start,nodes=sample));write(a.out/'budget_monitor.json',samples)
            args=command+['--output',str(a.out/'measure'),'--stage','measure']
            parent.controller(args,a.out/'measure_controller',600,futures,monitor)
            check_reference(plan['frozen_model_ref'])
            summary['status']='completed_pending_independent_review'
        except BaseException as exc:summary.update(status='failed',failure=repr(exc))
        finally:
            summary['all_stop']=ops.stops(plan,digest,a.out)
            for key,future in futures.items():
                try:summary['workers'][key]=future.result()[0]
                except BaseException as exc:summary['workers'][key]=dict(failure=repr(exc))
            summary['elapsed_s']=time.monotonic()-start;write(a.out/'summary.json',summary)
    for spec in ss:
        node=spec['node']
        try:
            body=quiet_readback(spec,a.out/('after_stop_'+node));ci=body['container']
            require(ci['Id']==spec['container_id'] and ci['State']['StartedAt']==spec['container_started_at'] and body['cgroup']['files']['pids.current']=='1','final exact quiet container mismatch');protect(baseline[node],body)
            proc,raw=ops.old.record(ops.command(node,['docker','exec',spec['container_id'],'python3',str(RUNTIME/'operations/collect_owned.py'),'--plan',str(a.plan),'--node',node]),a.out/('exact_collect_'+node),20)
            require(proc['returncode']==0,'exact child/group collection failed');write(a.out/('exact_collect_'+node)/'stdout.json',json.loads(raw))
            remote_root=Path(spec['stage'])/'project'/ops.RESULTS.relative_to(ROOT);remote_plan=Path(spec['stage'])/'project'/a.plan.relative_to(ROOT)
            proc,raw=ops.old.record(ops.command(node,['python3','-',str(remote_plan),str(remote_root),node]),a.out/('artifacts_'+node),30,(RUNTIME/'operations/collect_artifacts.py').read_bytes());require(proc['returncode']==0,'owned artifacts failed')
            archive=a.out/('artifacts_'+node)/'owned.tar';archive.write_bytes(raw)
            names={Path(r['out']).name for r in plan['processes'] if r['node']==node}|{'operations_dump_'+plan['session'],'collection_presence.json'}
            parent.extract_regular(archive,a.out/('artifacts_'+node)/'raw',names)
            summary['collection'][node]=dict(passed=True)
        except BaseException as exc:summary['collection'][node]=dict(passed=False,failure=repr(exc))
    write(a.out/'summary.json',summary);print(json.dumps(summary))
    if summary['status']=='failed' or any(r.get('returncode')!=0 for r in summary['workers'].values()) or not all(r['passed'] for r in summary['collection'].values()):raise SystemExit(2)
if __name__=='__main__':main()
