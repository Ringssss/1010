"""Main-only single-node diagnostic using frozen role supervision and host guards."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

from orchestrate import HostOps, HERE, RESULTS, ROOT, pre_mesh_ready, reference, thread_exec_env, write

RUN_ID = 'online_qwen_local_pause_r01'
RUNNER = HERE / 'local_pause.py'


def build_local(setup_dir, bindings, admission, runner):
    plan = copy.deepcopy(json.loads((setup_dir / 'launch_plan.unbound.json').read_text()))
    old = plan['run_id']
    identity = bindings['containers']['node27']
    if not admission['passed'] or admission['containers']['node27'] != identity or time.time()-admission['collected_unix_s'] > 300:
        raise ValueError('fresh exact node27 admission required')
    plan['nodes'] = {'node27': plan['nodes']['node27']}
    plan['nodes']['node27'].update(identity)
    plan['processes'] = [r for r in plan['processes'] if r['node'] == 'node27']
    for row in plan['processes']:
        row.update(container_id=identity['container_id'], boot_id=identity['boot_id'])
        row['argv'] = [v.replace(old, RUN_ID) for v in row['argv']]
        if row['kind'] == 'worker':
            row['argv'][row['argv'].index('--mesh')+1] = 'node27:4-5'
        row['env'] = {k: v.replace(old, RUN_ID) if isinstance(v,str) else v for k,v in row['env'].items()}
        row['out'] = row['out'].replace(old, RUN_ID)
    for key in ('handoff', 'engine_command_path'):
        plan[key] = plan[key].replace(old, RUN_ID)
    plan.update(run_id=RUN_ID, main_approved=True, fresh_admission_passed=True,
                admission_collected_unix_s=admission['collected_unix_s'], lifetime_s=3600,
                phase_deadlines_s={'init':180, 'pause':1800},
                hardware_scope='node27 targets GPU4/5 only',
                scope='complete-model capture, reference and physical pause diagnostic only; no helper or cost calibration',
                diagnostic_scope='local_full_model_physical_pause',
                warm_pairs=[],
                aggregate_budget_by_node={'node27':plan['aggregate_budget_by_node']['node27']},
                addition_ref=reference(runner), frozen_setup_ref=reference(setup_dir/'launch_plan.unbound.json'))
    if len(plan['processes']) != 3 or [r['worker_rank'] for r in plan['processes'] if r['kind']=='worker'] != [0,1]:
        raise ValueError('exact two target workers and one engine required')
    return plan


class LocalHostOps(HostOps):
    def __init__(self, setup_dir, out):
        if socket.gethostname() != 'm03r2n07':
            raise ValueError('local node27 execution required; SSH fallback forbidden')
        super().__init__(setup_dir, out)
        # Load the original immutable two-node setup, then restrict every action.
        self.specs = {'node27': self.specs['node27']}

    def controller(self, plan, remote, action):
        deadline = plan['phase_deadlines_s'][action]
        self.journal(action, 'begin', deadline_s=deadline)
        runtime = Path(plan['handoff']).parent
        argv = ['python3', str(RUNNER), '--plan', str(remote), '--action', action,
                '--out', str(runtime/('phase_'+action)), '--deadline-s', str(deadline), '--execute']
        if action == 'pause':
            argv += ['--binding', str(runtime/'phase_init/init.json')]
        self.record('node27', ['docker','exec',*thread_exec_env(),plan['nodes']['node27']['container_id'],*argv],
                    'controller_'+action, deadline+15)
        self.journal(action, 'complete')

    def run(self, plan_path, plan, addition):
        if plan['run_id'] != RUN_ID or set(plan['nodes']) != {'node27'} or len(plan['processes']) != 3:
            raise ValueError('exact local diagnostic namespace/roles required')
        if not plan['main_approved'] or not plan['fresh_admission_passed'] or time.time()-plan['admission_collected_unix_s']>300:
            raise ValueError('main-approved fresh target admission required')
        if reference(addition)['sha256'] != plan['addition_ref']['sha256']:
            raise ValueError('frozen diagnostic addition changed')
        before = self.host('node27','run_before_node27')
        if self.identity('node27',before) != {k:plan['nodes']['node27'][k] for k in ('container_id','container_started_at','boot_id')} or before['owned_cgroup']['fields']['pids.current'] != '1':
            raise ValueError('fresh exact own27 identity/quiet required')
        self.run_before = {'node27':before}
        ci = plan['nodes']['node27']['container_id']
        code = "from pathlib import Path;import sys,hashlib;raw=sys.stdin.buffer.read(131073);assert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==sys.argv[2];p=Path(sys.argv[1]);assert p.parent.is_dir();p.open('xb').write(raw)"
        self.record('node27',['docker','exec','-i',ci,'python3','-c',code,str(RUNNER),plan['addition_ref']['sha256']],
                    'runner_addition_upload',15,addition.read_bytes())
        remote = self.deploy_plan(plan_path,plan)
        clients=[];failure=None;cleanup_failures=[]
        def start(row):
            log=(self.out/('supervisor_client_'+row['key']+'.log')).open('xb')
            argv=self.transport.command('node27',['docker','exec',ci,'python3',str(HERE/'supervise.py'),'--plan',str(remote),'--key',row['key'],'--execute'])
            child=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            clients.append((row,child,log));write(self.out/('client_'+row['key']+'.json'),dict(argv=argv,pid=child.pid))
        try:
            workers=[r for r in plan['processes'] if r['kind']=='worker']
            for row in workers:start(row)
            self.journal('worker_ready','begin',deadline_s=120)
            until=time.monotonic()+120;waiting={r['key']:r for r in workers}
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while waiting:
                if time.monotonic()>until or any(c.poll() is not None for _,c,_ in clients):
                    raise RuntimeError('worker startup deadline/early supervisor exit')
                for key,row in list(waiting.items()):
                    try:
                        with opener.open(row['endpoint']+'/inventory?compact=1',timeout=1) as resp:
                            body=json.loads(resp.read((1<<20)+1))
                        if pre_mesh_ready(body,row,plan['run_id']):del waiting[key]
                    except Exception:pass
                if waiting:time.sleep(.25)
            self.journal('worker_ready','complete')
            self.controller(plan,remote,'init')
            start(next(r for r in plan['processes'] if r['kind']=='engine'))
            self.controller(plan,remote,'pause')
        except BaseException as exc:
            failure=repr(exc);self.journal('local_pause','failed',failure=failure)
        finally:
            digest=hashlib.sha256(plan_path.read_bytes()).hexdigest()
            for row,client,log in sorted(clients,key=lambda item:item[0]['kind']!='engine'):
                try:
                    code="from pathlib import Path;import sys,json;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.open('x').write(json.dumps({'plan_sha256':sys.argv[2]}))"
                    self.record('node27',['docker','exec',ci,'python3','-c',code,row['out']+'/STOP.json',digest],'stop_'+row['key'],15)
                    client.wait(timeout=25)
                except BaseException as exc:
                    cleanup_failures.append(row['key']+': '+repr(exc))
                    if client.poll() is None:
                        client.kill();client.wait(timeout=2)
                log.close();write(self.out/('client_exit_'+row['key']+'.json'),dict(returncode=client.returncode))
                if client.returncode!=0:cleanup_failures.append('supervisor failed '+row['key'])
            try:self.collect(plan)
            except BaseException as exc:cleanup_failures.append(repr(exc))
            write(self.out/'run_summary.json',dict(passed=not failure and not cleanup_failures,failure=failure,
                  cleanup_failures=cleanup_failures,process_count=len(clients),plan_ref=reference(plan_path),
                  scope=plan['scope'],helper_transfers=0,cost_calibrations=0))
        if failure or cleanup_failures:raise RuntimeError('local diagnostic failed; all artifacts preserved')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('admission','prepare','run','collect'))
    p.add_argument('--setup-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--bindings',type=Path);p.add_argument('--admission',type=Path);p.add_argument('--plan',type=Path)
    p.add_argument('--addition',type=Path,default=RUNNER);p.add_argument('--execute',action='store_true')
    a=p.parse_args();out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_local_pause_'):
        raise ValueError('new local diagnostic result required')
    if a.action!='prepare' and not a.execute:
        print(json.dumps(dict(status='not_executed',action=a.action)));return
    out.mkdir(exist_ok=False)
    if a.action=='prepare':
        plan=build_local(a.setup_dir,json.loads(a.bindings.read_text()),json.loads(a.admission.read_text()),a.addition)
        frozen=out/'local_pause.source.py';frozen.write_bytes(a.addition.read_bytes())
        plan['addition_ref']=reference(frozen)
        write(out/'launch_plan.json',plan);print(json.dumps(dict(status='plan_only',plan=str(out/'launch_plan.json'))));return
    ops=LocalHostOps(a.setup_dir,out)
    if a.action=='admission':ops.admission(json.loads(a.bindings.read_text()))
    elif a.action=='collect':ops.collect(json.loads(a.plan.read_text()))
    else:
        plan=json.loads(a.plan.read_text());ops.run(a.plan,plan,Path(plan['addition_ref']['path']))


if __name__=='__main__':main()
