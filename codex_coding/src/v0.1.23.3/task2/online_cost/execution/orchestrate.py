"""Main-invoked finite setup, fresh admission, five-process workflow and collection.

Transport and command recording reuse the established operations implementation.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[6]
HERE=Path(__file__).resolve().parent
RESULTS=ROOT/'codex_coding/results/v0.1.23.3/task2/online_cost'
OPS=ROOT/'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations'


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def write(path,body):
    with path.open('x') as stream:
        json.dump(body,stream,indent=2);stream.write('\n')


def reference(path):
    return dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


THREAD_ENV={'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'}


def thread_exec_env():
    return [item for key,value in THREAD_ENV.items() for item in ('-e',key+'='+value)]


def gpu_admission_spec(spec):
    # Send only bounded physical-query inputs; frozen source payloads stay local.
    keys=('node','roles','minimum_initial_gpu_free_bytes','per_selected_gpu_hbm_cap_bytes','task_hbm_cap_bytes')
    result={key:spec[key] for key in keys}
    result['roles']=[{key:role[key] for key in ('gpu','bdf','numa','host_numa') if key in role} for role in spec['roles']]
    if len(result['roles'])!=2 or len(json.dumps(result).encode())>=16384:
        raise ValueError('bounded two-card admission spec required')
    return result


def pre_mesh_ready(body, role, session):
    runtime=body.get('runtime_identity',{})
    return (body.get('session')==session and body.get('worker') is None
            and body.get('node')==role['node'] and body.get('gpu')==role['gpu']
            and runtime.get('actual_bdf')==role['bdf'] and runtime.get('boot_id')==role['boot_id'])


class HostOps:
    RUN_PHASES = ('bootstrap', 'calibrate', 'validate')

    def __init__(self,setup_dir,out):
        self.transport=module('online_existing_transport',OPS/'control.py')
        frozen=json.loads((setup_dir/'launch_plan.unbound.json').read_text())
        if set(frozen['nodes']) != {'node27','node30'}:
            raise ValueError('current approved two-node topology required')
        self.specs={n:json.loads((setup_dir/(n+'.setup.spec.json')).read_text()) for n in frozen['nodes']}
        self.out=out
        self.events=0
        self.deadline_at=None
        readback=(OPS/'host_readback.py').read_text().rsplit("if __name__ == '__main__':",1)[0]
        metadata=(HERE.parent/'review/host_metadata.py').read_text().rsplit("if __name__ == '__main__':",1)[0]
        guard=(HERE/'hostops_guard.py').read_text().rsplit("if __name__ == '__main__':",1)[0]
        self.host_source=(readback+'\n'+metadata+'\n'+guard+"\nthreading.Thread(target=lambda: (time.sleep(30), os._exit(124)), daemon=True).start()\nspec=json.loads(sys.argv[1])\nprint(json.dumps(extend_host(collect(spec),spec,docker,cgroup_for)),flush=True)\n").encode()

    def journal(self,phase,state,**fields):
        if state=='begin' and 'deadline_s' in fields:
            self.deadline_at=time.monotonic()+fields['deadline_s']
        elif state in ('complete','failed','aborted'):
            self.deadline_at=None
        self.events+=1
        write(self.out/(f'journal_{self.events:03d}_{phase}_{state}.json'),dict(phase=phase,state=state,unix_s=time.time(),**fields))

    def record(self,node,argv,name,timeout,payload=None):
        if self.deadline_at is not None:
            remaining=self.deadline_at-time.monotonic()
            if remaining<=0:
                raise TimeoutError('intrinsic host phase total deadline exceeded before '+name)
            timeout=min(timeout,remaining)
        p,raw=self.transport.old.record(self.transport.command(node,argv),self.out/name,timeout,payload)
        if p['returncode'] != 0:
            raise RuntimeError('command failed '+name+'; raw failure preserved')
        return raw

    def host(self,node,name):
        s=self.specs[node]
        spec=dict(s,candidate_container_name=s['container_name'],minimum_host_available_bytes=s['minimum_host_available_bytes'],inspect_all_numa=True,model_path=s['model_path'] if node=='node27' else None)
        spec['assets']=[{k:v for k,v in a.items() if k!='base64'} for a in s['assets']]
        raw=self.record(node,['python3','-',json.dumps(spec)],name,35,self.host_source)
        row=json.loads(raw);write(self.out/name/'stdout.json',row)
        if row['boot_id'] != s['boot_id'] or row['hostname'] != s['hostname']:
            raise ValueError('host continuity failed')
        return row

    @staticmethod
    def canonical_mounts(mounts):
        return sorted((dict(m) for m in mounts),key=lambda m:json.dumps(m,sort_keys=True,separators=(',',':')))

    @staticmethod
    def protected(before,after):
        by_id={r['id']:r for r in after['protected_containers']}
        for old in before['protected_containers']:
            new=by_id.get(old['id'])
            if not new or any(new.get(k)!=old.get(k) for k in ('id','name','image_id','running','started_at','pid','host_config')) or HostOps.canonical_mounts(new['mounts'])!=HostOps.canonical_mounts(old['mounts']):
                raise ValueError('protected old container changed: '+old['name'])
        if before['original_xml_sha256'] != after['original_xml_sha256']:
            raise ValueError('original/shared XML changed')

    def identity(self,node,row):
        s=self.specs[node];ci=row['owned_container'];hc=ci['HostConfig']
        if ci.get('Name')!='/'+s['container_name'] or ci.get('Image')!=s['image_id'] or not ci['State']['Running']:
            raise ValueError('owned container identity mismatch')
        if hc['Memory']!=s['container_hard_cap_bytes'] or hc['MemorySwap']!=s['container_hard_cap_bytes'] or hc['Privileged'] or hc['IpcMode']!='private' or hc['NetworkMode']!='host' or hc['PidsLimit']!=512 or hc['ShmSize']!=1<<30:
            raise ValueError('container resource/permission mismatch')
        if not {'IPC_LOCK','SYS_NICE'} <= set(hc['CapAdd']):
            raise ValueError('container caps differ')
        mounts={m['Destination']:(m['Source'],m['RW']) for m in ci['Mounts']}
        expected={str(ROOT):(s['stage']+'/project',True),'/opt/hyhal':('/opt/hyhal',False)}
        if node=='node27': expected[s['model_path']]=(s['model_path'],False)
        if mounts!=expected:
            raise ValueError('exact isolated mount set differs')
        if any(row['source_assets'].get(a['relative_path'])!=a['sha256'] for a in s['assets']):
            raise ValueError('source snapshot differs')
        return dict(container_id=ci['Id'],container_started_at=ci['State']['StartedAt'],boot_id=row['boot_id'])

    def setup(self,setup_dir,resume_setup=None):
        self.journal('setup','begin',deadline_s=300)
        manifest=dict(containers={},GPU_launches=0)
        recovery=dict(skipped_existing_nodes=[],new_nodes=[],GPU_launches=0,local_orchestrator_ref=reference(Path(__file__)),frozen_setup_ref=reference(setup_dir/'launch_plan.unbound.json'))
        if resume_setup is not None:
            resume_setup=resume_setup.resolve()
            if resume_setup.parent!=RESULTS or not resume_setup.name.startswith('execution_host_setup_') or resume_setup==self.out:
                raise ValueError('prior immutable owned setup result required')
            recovery['prior_setup']=str(resume_setup)
        wrapper=(setup_dir/'setup_host.source.py').read_bytes()
        for node,s in self.specs.items():
            prior_after=resume_setup/('after_'+node)/'stdout.json' if resume_setup else None
            if prior_after is not None and prior_after.exists():
                prior_before=resume_setup/('before_'+node)/'stdout.json'
                prior_command=resume_setup/('setup_'+node)/'command.json'
                prior_result=resume_setup/('setup_'+node)/'stdout.json'
                command=json.loads(prior_command.read_text())
                earlier=json.loads(prior_after.read_text())
                initial=json.loads(prior_before.read_text())
                created=json.loads(prior_result.read_text())
                prior_identity=self.identity(node,earlier)
                if command.get('returncode')!=0 or command.get('timed_out') or created['container']['id']!=prior_identity['container_id'] or not initial['candidate_name_free']:
                    raise ValueError('prior setup is not a proven new owned container')
                fresh=self.host(node,'resume_existing_'+node)
                if self.identity(node,fresh)!=prior_identity or fresh['owned_cgroup']['fields']['pids.current']!='1' or not fresh['host_memory_admission']['passed'] or not all(fresh['ports_free'].values()):
                    raise ValueError('resume exact container/source/quiet identity differs')
                self.protected(initial,fresh)
                manifest['containers'][node]=prior_identity
                write(self.out/('identity_'+node+'.json'),prior_identity)
                recovery['skipped_existing_nodes'].append(dict(node=node,identity=prior_identity,prior_refs=[reference(p) for p in (prior_before,prior_after,prior_command,prior_result)],fresh_ref=reference(self.out/('resume_existing_'+node)/'stdout.json'),frozen_asset_orchestrator_sha256=next(a['sha256'] for a in s['assets'] if a['relative_path']==str(Path(__file__).relative_to(ROOT)))))
                self.journal('setup_resume','verified',node=node,identity=prior_identity)
                continue
            before=self.host(node,'before_'+node)
            if not before['candidate_name_free'] or not all(before['ports_free'].values()) or not before['host_memory_admission']['passed'] or not before['requested_image_present']:
                raise ValueError('fresh CPU setup gate failed '+node)
            bootstrap='/tmp/fluidckpt_'+s['run']+'_bootstrap'
            dest=bootstrap+'/'+node+'.bootstrap_setup.py'
            self.record(node,['bash','-c','set -C\nmkdir -p '+shlex.quote(bootstrap)+'\ncat > '+shlex.quote(dest)],'upload_'+node,15,(OPS/'bootstrap_setup.py').read_bytes())
            payload=json.dumps(dict(spec=s,wrapper=base64.b64encode(wrapper).decode())).encode()
            raw=self.record(node,['python3',dest,bootstrap,node,s['boot_id']],'setup_'+node,45,payload)
            write(self.out/('setup_'+node)/'stdout.json',json.loads(raw))
            after=self.host(node,'after_'+node)
            self.protected(before,after)
            manifest['containers'][node]=self.identity(node,after)
            if after['owned_cgroup']['fields']['pids.current']!='1':
                raise ValueError('new setup not quiet')
            write(self.out/('identity_'+node+'.json'),manifest['containers'][node])
            recovery['new_nodes'].append(node)
        write(self.out/'recovery_provenance.json',recovery)
        write(self.out/'setup.json',manifest)
        self.journal('setup','complete',containers=manifest['containers'])

    def admission(self,bindings):
        self.journal('admission','begin',deadline_s=300)
        admitted=dict(passed=False,containers=bindings['containers'],GPU_queries=0)
        for node,s in self.specs.items():
            before=self.host(node,'admission_before_'+node)
            if self.identity(node,before)!=bindings['containers'][node] or not before['host_memory_admission']['passed'] or not all(before['ports_free'].values()) or before['owned_cgroup']['fields']['pids.current']!='1':
                raise ValueError('fresh pre-GPU setup identity/resources/ports changed')
            query=['docker','exec','-i',*thread_exec_env(),'-e','HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7','-e','ROCR_VISIBLE_DEVICES=','-e','CUDA_VISIBLE_DEVICES=',bindings['containers'][node]['container_id'],'python3','-',json.dumps(gpu_admission_spec(s))]
            # Empty inherited visibility is unset before importing Torch.
            code="import os\nos.environ.pop('ROCR_VISIBLE_DEVICES',None)\nos.environ.pop('CUDA_VISIBLE_DEVICES',None)\n"+(HERE/'hostops_guard.py').read_text()
            raw=self.record(node,query,'gpu_query_'+node,55,code.encode())
            gpu=json.loads(raw);write(self.out/('gpu_query_'+node)/'stdout.json',gpu)
            admitted['GPU_queries']+=len(gpu['gpus'])
            after=self.host(node,'admission_after_'+node)
            self.protected(before,after)
            if self.identity(node,after)!=bindings['containers'][node] or after['owned_cgroup']['fields']['pids.current']!='1':
                raise ValueError('owned query did not exit quietly')
            if not gpu['passed']:
                raise ValueError('fresh physical free memory/aggregate helper cap failed '+node)
        admitted.update(passed=True,collected_unix_s=time.time())
        write(self.out/'admission.json',admitted)
        self.journal('admission','complete',GPU_queries=admitted['GPU_queries'])

    def deploy_plan(self,plan_path,plan):
        remote=Path(plan['handoff']).parent/'launch_plan.json'
        for node in self.specs:
            ci=plan['nodes'][node]['container_id']
            code="from pathlib import Path;import sys;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.open('xb').write(sys.stdin.buffer.read())"
            self.record(node,['docker','exec','-i',ci,'python3','-c',code,str(remote)],'plan_upload_'+node,15,plan_path.read_bytes())
        return remote

    def controller(self,plan,remote,action):
        deadline=plan['phase_deadlines_s'][action]
        self.journal(action,'begin',deadline_s=deadline)
        runtime=Path(plan['handoff']).parent
        argv=['python3',str(HERE/'controller.py'),'--plan',str(remote),'--action',action,'--out',str(runtime/('phase_'+action)),'--deadline-s',str(deadline),'--execute']
        if action=='bootstrap':
            argv+=['--binding',str(runtime/'phase_init/init.json')]
        if action in ('calibrate','validate','cleanup'):
            argv+=['--bootstrap',str(runtime/'phase_bootstrap/bootstrap.json')]
        if action=='validate':
            argv+=['--calibration',str(runtime/'phase_calibrate/calibration.json')]
        self.record('node27',['docker','exec',*thread_exec_env(),plan['nodes']['node27']['container_id'],*argv],'controller_'+action,deadline+15)
        self.journal(action,'complete')

    def collect(self,plan,label='collection'):
        self.journal(label,'begin',deadline_s=180)
        runtime=Path(plan['handoff']).parent
        code="import io,pathlib,sys,tarfile;root=pathlib.Path(sys.argv[1]);files=[p for p in root.rglob('*') if p.is_file()];total=sum(p.stat().st_size for p in files);assert len(files)<=20000 and total<=512*1024*1024, 'artifact bound exceeded';t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|');[t.add(str(p),arcname=str(p.relative_to(root)),recursive=False) for p in files];t.close()"
        results={}
        for node in self.specs:
            result={'archive_collected':False,'metadata_collected':False,'quiet':False,'failures':[]}
            try:
                raw=self.record(node,['docker','exec',plan['nodes'][node]['container_id'],'python3','-c',code,str(runtime)],label+'_'+node,40)
                archive=self.out/(label+'_'+node+'.tar')
                with archive.open('xb') as stream:
                    stream.write(raw)
                result.update(archive_collected=True,archive_ref=reference(archive))
            except BaseException as exc:
                result['failures'].append('archive: '+repr(exc))
            try:
                body=self.host(node,label+'_metadata_'+node)
                result.update(metadata_collected=True,metadata_ref=reference(self.out/(label+'_metadata_'+node)/'stdout.json'),pids_current=body['owned_cgroup']['fields']['pids.current'])
                if getattr(self,'run_before',None):
                    self.protected(self.run_before[node],body)
                if self.identity(node,body)!={k:plan['nodes'][node][k] for k in ('container_id','container_started_at','boot_id')}:
                    raise ValueError('final container continuity changed')
                result['quiet']=result['pids_current']=='1'
                if not result['quiet']:
                    result['failures'].append('owned supervised process group not fully quiet after STOP')
            except BaseException as exc:
                result['failures'].append('metadata qualification: '+repr(exc))
            result['passed']=not result['failures'] and result['archive_collected'] and result['metadata_collected'] and result['quiet']
            results[node]=result
        qualification=dict(nodes=results,passed=all(r['passed'] for r in results.values()),scope='all-node artifact collection and exact quiet qualification; a failed node does not skip later nodes')
        write(self.out/(label+'_qualification.json'),qualification)
        self.journal(label,'complete' if qualification['passed'] else 'failed',nodes=results)
        if not qualification['passed']:
            raise ValueError('all-node collection finished with failed quiet/identity qualification; see '+label+'_qualification.json')

    def run(self,plan_path,plan):
        if not plan['main_approved'] or not plan['fresh_admission_passed'] or time.time()-plan['admission_collected_unix_s']>300:
            raise ValueError('main-approved fresh bound plan required')
        started=time.monotonic()
        self.run_before={n:self.host(n,'run_before_'+n) for n in self.specs}
        for node,body in self.run_before.items():
            if self.identity(node,body)!={k:plan['nodes'][node][k] for k in ('container_id','container_started_at','boot_id')} or body['owned_cgroup']['fields']['pids.current']!='1':
                raise ValueError('fresh pre-run exact container identity/quiet failed')
        remote=self.deploy_plan(plan_path,plan)
        clients=[];failure=None;cleanup_failure=None
        def start(row):
            log=(self.out/('supervisor_client_'+row['key']+'.log')).open('xb')
            argv=self.transport.command(row['node'],['docker','exec',row['container_id'],'python3',str(HERE/'supervise.py'),'--plan',str(remote),'--key',row['key'],'--execute'])
            child=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            clients.append((row,child,log));write(self.out/('client_'+row['key']+'.json'),dict(argv=argv,pid=child.pid))
        try:
            for row in plan['processes']:
                if row['kind']=='worker': start(row)
            self.journal('worker_ready','begin',deadline_s=120)
            until=time.monotonic()+120
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            waiting={r['key']:r for r in plan['processes'] if r['kind']=='worker'}
            while waiting:
                if time.monotonic()>until or any(c.poll() is not None for _,c,_ in clients):
                    raise RuntimeError('worker startup deadline/early supervisor exit')
                for key,row in list(waiting.items()):
                    try:
                        with opener.open(row['endpoint']+'/inventory?compact=1',timeout=1) as resp:
                            body=json.loads(resp.read((1<<20)+1))
                        if pre_mesh_ready(body,row,plan['run_id']):
                            del waiting[key]
                    except Exception:
                        pass
                if waiting: time.sleep(.25)
            self.journal('worker_ready','complete')
            self.controller(plan,remote,'init')
            start(next(r for r in plan['processes'] if r['kind']=='engine'))
            for phase in self.RUN_PHASES:
                if time.monotonic()-started>plan['lifetime_s']-240:
                    raise RuntimeError('workflow lifetime reserves cleanup window')
                self.controller(plan,remote,phase)
            self.controller(plan,remote,'cleanup')
        except BaseException as exc:
            failure=repr(exc);self.journal('run','failed',failure=failure)
            try:
                self.controller(plan,remote,'cleanup')
            except BaseException as error:
                cleanup_failure=repr(error)
        finally:
            digest=hashlib.sha256(plan_path.read_bytes()).hexdigest()
            for row,client,log in sorted(clients,key=lambda item: item[0]['kind']!='engine'):
                try:
                    code="from pathlib import Path;import sys,json;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.open('x').write(json.dumps({'plan_sha256':sys.argv[2]}))"
                    self.record(row['node'],['docker','exec',row['container_id'],'python3','-c',code,row['out']+'/STOP.json',digest],'stop_'+row['key'],15)
                except BaseException as exc:
                    cleanup_failure=repr(exc)
                try:
                    client.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    # Stop only this newly created local transport client.
                    client.kill();client.wait(timeout=2)
                    cleanup_failure='remote supervisor did not acknowledge STOP '+row['key']
                log.close()
                write(self.out/('client_exit_'+row['key']+'.json'),dict(returncode=client.returncode))
                if client.returncode!=0: cleanup_failure='supervisor failed '+row['key']
            try:
                self.collect(plan)
            except BaseException as exc:
                cleanup_failure=repr(exc)
            write(self.out/'run_summary.json',dict(passed=not failure and not cleanup_failure,failure=failure,cleanup_failure=cleanup_failure,process_count=len(clients),plan_ref=reference(plan_path)))
        if failure or cleanup_failure:
            raise RuntimeError('workflow failed; preserved exact phase and cleanup artifacts')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('setup','admission','run','collect'))
    p.add_argument('--setup-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--bindings',type=Path)
    p.add_argument('--resume-setup',type=Path)
    p.add_argument('--plan',type=Path)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args();out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_'):
        raise ValueError('new execution_host_* output required')
    if not a.execute:
        print(json.dumps(dict(status='not_executed',action=a.action,setup_dir=str(a.setup_dir),out=str(out))));return
    out.mkdir(parents=True,exist_ok=False)
    ops=HostOps(a.setup_dir,out)
    try:
        if a.action=='setup':ops.setup(a.setup_dir,a.resume_setup)
        elif a.action=='admission':ops.admission(json.loads(a.bindings.read_text()))
        else:
            plan=json.loads(a.plan.read_text())
            if a.action=='run':ops.run(a.plan,plan)
            else:ops.collect(plan)
    except BaseException as exc:
        ops.journal(a.action,'aborted',failure=repr(exc));raise
    print(json.dumps(dict(status='completed',action=a.action,out=str(out))))


if __name__=='__main__':main()
