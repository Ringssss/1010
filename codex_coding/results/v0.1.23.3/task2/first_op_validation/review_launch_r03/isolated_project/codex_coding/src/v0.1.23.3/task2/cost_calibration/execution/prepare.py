"""Append new sources to a quiet existing stage; derive a fresh bounded session."""
import argparse
import base64
import copy
import json
from pathlib import Path
import shlex
from common import ROOT, HERE, RESULTS, RUNTIME, native, parent, ops, write, ref, require

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('build','stage'));p.add_argument('--out',type=Path,required=True);p.add_argument('--session',default='costcal_runtime_r01');p.add_argument('--execute',action='store_true');p.add_argument('--prepared',type=Path);p.add_argument('--prior-setup',type=Path)
    a=p.parse_args();a.out=a.out.resolve();require(a.out.parent==RESULTS and a.out.name.startswith('execution_'),'new execution output required')
    a.out.mkdir(parents=True,exist_ok=False)
    if a.mode=='build':
        old_path=ROOT/'codex_coding/results/v0.1.23.3/task2/multitier_runtime/operations_launch_r01/launch_plan.json'
        old=native.read(old_path);plan=copy.deepcopy(old)
        require(a.session!=old['session'] and '/' not in a.session,'fresh session name required')
        plan.update(session=a.session,prior_plan_ref=ref(old_path),setup_directory=str(a.out),paired_query_holdout=True)
        mode_path=RESULTS/(a.session+'.inventory_mode.json');plan['inventory_mode_file']=str(mode_path)
        assets=[]
        for path in sorted(list(HERE.glob('*.py'))+list((HERE.parent/'inventory').glob('*.py'))):
            raw=path.read_bytes();assets.append(dict(relative_path=str(path.relative_to(ROOT)),sha256=native.sha(path),base64=base64.b64encode(raw).decode()))
        for row in plan['processes']:
            row['out']=str(ops.RESULTS/f'operations_process_{a.session}_{row["key"]}')
            if row['kind']=='worker':
                row['argv'][1]=str(HERE.parent/'inventory/worker.py')
                idx=row['argv'].index('--session');row['argv'][idx+1]=a.session
                row['argv']+=['--inventory-mode-file',str(mode_path),'--allow-static-cache']
                row['env']['NCCL_TOPO_DUMP_FILE']=str(ops.RESULTS/f'operations_dump_{a.session}'/(row['key']+'.runtime.dump.xml'))
        prior_directory=a.prior_setup.resolve() if a.prior_setup else Path(old['setup_directory'])
        plan['prior_setup_directory']=str(prior_directory)
        for spec in ops.specs(prior_directory):
            spec.update(next({k:r[k] for k in ('container_id','container_started_at','boot_id')} for r in old['processes'] if r['node']==spec['node']))
            # Host guard checks the task's unchanged stage namespace against its original run.
            spec['assets']=[{k:v for k,v in x.items() if k!='base64'} for x in spec['assets']]
            prior={x['relative_path']:x for x in spec['assets']};prior.update({x['relative_path']:{k:v for k,v in x.items() if k!='base64'} for x in assets})
            write(a.out/(spec['node']+'.prior.spec.json'),spec)
            spec['assets']=list(prior.values());write(a.out/(spec['node']+'.setup.spec.json'),spec)
        write(a.out/'append_assets.json',dict(assets=assets,revision=a.out.name,inventory_mode_file=str(mode_path)))
        write(a.out/'launch_plan.json',plan)
        write(a.out/'manifest.json',dict(GPU_launches=0,remote_writes=0,prior_plan_ref=ref(old_path),plan_ref=ref(a.out/'launch_plan.json'),append_only=True))
        print(json.dumps(dict(plan=str(a.out/'launch_plan.json'),GPU_launches=0)))
    else:
        require(a.execute and a.prepared,'parent explicit stage execution required')
        prepared=a.prepared.resolve();package=native.read(prepared/'append_assets.json')
        for node in ('node28','node30','node27'):
            spec=native.read(prepared/(node+'.prior.spec.json'))
            before=ops.readback(spec,a.out/('before_'+node))
            require(before['container']['Id']==spec['container_id'] and before['container']['State']['StartedAt']==spec['container_started_at'] and before['cgroup']['files']['pids.current']=='1','quiet exact container required')
            require(all(before['ports_free'].values()),'old selected ports occupied')
            require(all(before['source_assets'].get(x['relative_path'])==x['sha256'] for x in spec['assets']),'old deployed assets changed')
            payload=dict(spec=spec,revision=package['revision'],assets=package['assets'])
            # Execute the already frozen independent helper in the unchanged owned stage.
            helper=Path(spec['stage'])/'project'/(RUNTIME/'operations/stage_assets.py').relative_to(ROOT)
            proc,raw=ops.old.record(ops.command(node,['python3',str(helper)]),a.out/('append_'+node),30,json.dumps(payload).encode())
            require(proc['returncode']==0,'append source failed '+node);write(a.out/('append_'+node)/'stdout.json',json.loads(raw))
            dest=package['inventory_mode_file']
            proc,raw=ops.old.record(ops.command(node,['docker','exec',spec['container_id'],'python3',str(HERE/'mode_file.py'),'create','--path',dest,'--mode','legacy']),a.out/('modefile_'+node),15)
            require(proc['returncode']==0,'fresh owned mode file creation failed')
            write(a.out/('modefile_'+node)/'stdout.json',json.loads(raw))
            after=ops.readback(native.read(prepared/(node+'.setup.spec.json')),a.out/('after_'+node))
            require(all(after['source_assets'].get(x['relative_path'])==x['sha256'] for x in native.read(prepared/(node+'.setup.spec.json'))['assets']),'staged asset SHA mismatch')
        write(a.out/'manifest.json',dict(GPU_launches=0,append_only=True,prepared_ref=ref(prepared/'launch_plan.json')))
if __name__=='__main__':main()
