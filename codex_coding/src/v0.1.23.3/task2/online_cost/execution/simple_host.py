"""Main-only deployment adapter for target TP2 and one remote GPU sender."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from build_launch_plan import build, set_arg
from orchestrate import HostOps, HERE, RESULTS, reference, thread_exec_env, write

GiB=1<<30
WORKFLOW='simple_two_node_single_sender_v1'


def build_simple(run_id):
    plan=build(run_id)
    plan['processes']=[r for r in plan['processes'] if r['key']!='worker_node30_5']
    for row in plan['processes']:
        if row['kind']=='worker':
            row['argv'][1]=str(HERE/'simple_worker.py')
            set_arg(row['argv'],'--mesh','node27:4-5,node30:4-4')
            if row['node']=='node30':
                set_arg(row['argv'],'--dram-budget',96*GiB)
                set_arg(row['argv'],'--hbm-budget',2*GiB)
    plan['nodes']['node30'].update(minimum_initial_gpu_free_bytes=4*GiB,ongoing_gpu_free_floor_bytes=2*GiB,
                helper_hbm_scope='planned additional runtime/staging2GiB; reserved and known tensors bounded; full driver attribution unknown')
    plan.update(workflow=WORKFLOW,remote_source_numa_by_rank={'0':4,'1':7},warm_pairs=[[0,2],[1,2]],
                planned_source_bytes_by_numa={node:{'4':35164860416,'7':35164860416} for node in plan['nodes']},
                scope='two-node three-GPU real-model workflow with one serial remote sender; original complex workflow retained',
                hardware_scope='node27 target GPU4/5; node30 helper GPU4 only',
                budget_status='explicit_target192_hard224_helper96_hard128_helper_planned_additional2GiB',
                helper_task_hbm_actual_bytes=None,helper_task_hbm_attribution_complete=False)
    return plan


def admission_spec(spec):
    keys=('node','roles','minimum_initial_gpu_free_bytes','per_selected_gpu_hbm_cap_bytes','task_hbm_cap_bytes')
    row={key:spec[key] for key in keys}
    row['roles']=[{key:r[key] for key in ('gpu','bdf','numa','host_numa') if key in r} for r in spec['roles']]
    if len(row['roles'])!=(2 if row['node']=='node27' else 1) or len(json.dumps(row).encode())>16384:
        raise ValueError('exact two target cards/one helper card required')
    return row


def physical_gpu(spec):
    from hostops_guard import actual_device_identity
    import os
    import torch
    if os.environ.get('HIP_VISIBLE_DEVICES')!='0,1,2,3,4,5,6,7':
        raise ValueError('unchanged eight-card visibility required')
    rows=[]
    for role in spec['roles']:
        torch.cuda.set_device(role['gpu'])
        bdf,numa=actual_device_identity(role['gpu'])
        free,total=torch.cuda.mem_get_info(role['gpu'])
        rows.append(dict(gpu=role['gpu'],actual_bdf=bdf,bdf=role['bdf'],pci_numa=numa,free_bytes=free,
                  total_bytes=total,used_bytes=total-free,allocated_bytes=torch.cuda.memory_allocated(role['gpu']),
                  reserved_bytes=torch.cuda.memory_reserved(role['gpu']),required_free_bytes=spec['minimum_initial_gpu_free_bytes'],
                  passed=bdf==role['bdf'] and numa==role['numa'] and free>=spec['minimum_initial_gpu_free_bytes']))
    return dict(passed=all(r['passed'] for r in rows),gpus=rows,
                helper_planned_additional_bytes=2*GiB if spec['node']=='node30' else None,
                helper_actual_task_total_bytes=None,helper_exact_attribution=False,
                measurement='fresh HIP whole-card free admission; foreign used retained as observation, no subtraction or whole-card helper2GiB cap')


class SimpleHostOps(HostOps):
    def admission(self,bindings):
        self.journal('admission','begin',deadline_s=300)
        admitted=dict(passed=False,containers=bindings['containers'],GPU_queries=0,nodes={})
        for node,spec in self.specs.items():
            before=self.host(node,'admission_before_'+node)
            if self.identity(node,before)!=bindings['containers'][node] or not before['host_memory_admission']['passed'] or not all(before['ports_free'].values()) or before['owned_cgroup']['fields']['pids.current']!='1':
                raise ValueError('fresh exact quiet identity/resources failed '+node)
            from simple_resources import numa_plan_checks
            checks=numa_plan_checks(before['numa_memory'],{'4':35164860416,'7':35164860416})
            if not all(r['passed'] for r in checks):
                write(self.out/('numa_failed_'+node+'.json'),dict(checks=checks,passed=False))
                raise ValueError('fresh separate source NUMA capacity failed '+node)
            args=['docker','exec','-i',*thread_exec_env(),'-e','HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7','-e','ROCR_VISIBLE_DEVICES=','-e','CUDA_VISIBLE_DEVICES=',bindings['containers'][node]['container_id'],'python3','-',json.dumps(admission_spec(spec))]
            code="import os,sys,json,threading,time\nos.environ.pop('ROCR_VISIBLE_DEVICES',None)\nos.environ.pop('CUDA_VISIBLE_DEVICES',None)\nthreading.Thread(target=lambda:(time.sleep(45),os._exit(124)),daemon=True).start()\nsys.path.insert(0,"+repr(str(HERE))+ ")\nfrom simple_host import physical_gpu\nprint(json.dumps(physical_gpu(json.loads(sys.argv[1]))),flush=True)\n"
            raw=self.record(node,args,'gpu_query_'+node,55,code.encode())
            gpu=json.loads(raw);write(self.out/('gpu_query_'+node)/'stdout.json',gpu)
            admitted['nodes'][node]=dict(gpu=gpu,numa_checks=checks);admitted['GPU_queries']+=len(gpu['gpus'])
            after=self.host(node,'admission_after_'+node);self.protected(before,after)
            if self.identity(node,after)!=bindings['containers'][node] or after['owned_cgroup']['fields']['pids.current']!='1' or not gpu['passed']:
                write(self.out/'admission.json',admitted)
                raise ValueError('fresh physical admission/quiet failed '+node)
        admitted.update(passed=True,collected_unix_s=time.time())
        write(self.out/'admission.json',admitted);self.journal('admission','complete',GPU_queries=admitted['GPU_queries'])

    def controller(self,plan,remote,action):
        deadline=plan['phase_deadlines_s'][action];self.journal(action,'begin',deadline_s=deadline)
        runtime=Path(plan['handoff']).parent
        argv=['python3',str(HERE/'simple_controller.py'),'--plan',str(remote),'--action',action,'--out',str(runtime/('phase_'+action)),'--deadline-s',str(deadline),'--execute']
        if action=='bootstrap':argv+=['--binding',str(runtime/'phase_init/init.json')]
        if action in ('calibrate','validate','cleanup'):argv+=['--bootstrap',str(runtime/'phase_bootstrap/bootstrap.json')]
        if action=='validate':argv+=['--calibration',str(runtime/'phase_calibrate/calibration.json')]
        self.record('node27',['docker','exec',*thread_exec_env(),plan['nodes']['node27']['container_id'],*argv],'controller_'+action,deadline+15)
        self.journal(action,'complete')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('setup','admission','bind','run','collect'))
    p.add_argument('--setup-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--bindings',type=Path);p.add_argument('--admission',type=Path);p.add_argument('--plan',type=Path)
    p.add_argument('--execute',action='store_true');p.add_argument('--main-approved',action='store_true')
    a=p.parse_args();out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_simple_'):
        raise ValueError('fresh execution_host_simple_* output required')
    if a.action!='bind' and not a.execute:
        print(json.dumps(dict(status='not_executed',action=a.action)));return
    out.mkdir(parents=True,exist_ok=False)
    plan=json.loads((a.setup_dir/'launch_plan.unbound.json').read_text())
    if plan.get('workflow')!=WORKFLOW or len(plan['processes'])!=4:
        raise ValueError('exact fresh simple workflow required')
    if a.action=='bind':
        from bind_plan import bind
        plan=bind(plan,json.loads(a.bindings.read_text()),json.loads(a.admission.read_text()),a.main_approved)
        plan.update(scope='fresh four-process single-sender workflow; native lifecycle mandatory',
                    budget_status='explicit_simple_helper_planned2GiB_freeguard_verified')
        plan['binding_source_refs']={n:reference(getattr(a,n)) for n in ('bindings','admission')}
        write(out/'launch_plan.json',plan);print(json.dumps(dict(status='bound_plan_only',plan=str(out/'launch_plan.json'))));return
    ops=SimpleHostOps(a.setup_dir,out)
    try:
        if a.action=='setup':ops.setup(a.setup_dir)
        elif a.action=='admission':ops.admission(json.loads(a.bindings.read_text()))
        else:
            plan=json.loads(a.plan.read_text())
            if plan.get('workflow')!=WORKFLOW:raise ValueError('simple bound workflow required')
            if a.action=='run':ops.run(a.plan,plan)
            else:ops.collect(plan)
    except BaseException as exc:
        ops.journal(a.action,'aborted',failure=repr(exc));raise


if __name__=='__main__':main()
