"""Read-only metadata gate and append-only single node launch preparation."""
import argparse
import base64
import copy
import json
from pathlib import Path
from common import ROOT,HERE,PRIOR,ROLE_PRIOR,RESULTS,RUNTIME,native,ops,write,ref,require,fresh_output,quiet_readback,check_reference,specs
from protocol import protocol
from gate import prediction_contract,predeclared_contract

def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=('metadata','build','stage','preflight'))
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--prior-prepared',type=Path,default=PRIOR)
    p.add_argument('--prepared',type=Path)
    p.add_argument('--session',default='cost_ledger_runtime_gpu4_C1_r01')
    p.add_argument('--experiment-stage',choices=('C1','C2'),default='C1')
    p.add_argument('--prediction',type=Path)
    p.add_argument('--predeclared',type=Path)
    p.add_argument('--calibration-review',type=Path)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args();out=fresh_output(a.out)
    if a.mode=='metadata':
        body=quiet_readback(native.read(a.prior_prepared/'node27.setup.spec.json'),out/'current_node27')
        write(out/'qualification.json',dict(passed=True,GPU_launches=0,remote_writes=0,node='node27',
            prior_ref=ref(a.prior_prepared/'node27.setup.spec.json'),container_id=body['container']['Id']))
        return
    if a.mode=='build':
        require(a.predeclared is not None,'final analysis preregistration required')
        predeclared_contract(ref(a.predeclared))
        prior=native.read(a.prior_prepared/'launch_plan.json');plan=copy.deepcopy(prior)
        require(a.session.startswith('cost_ledger_runtime_') and '/' not in a.session and a.session!=prior['session'],'fresh namespace required')
        require(a.experiment_stage!='C2' or a.prediction is not None,'C2 requires gated frozen predictions')
        if a.experiment_stage=='C2':
            require(a.calibration_review is not None,'C2 requires independent C1 qualification')
            prediction_contract(ref(a.prediction),a.session,review_reference=ref(a.calibration_review))
        mode_path=RESULTS/(a.session+'.inventory_mode.json')
        role_source=native.read(ROLE_PRIOR/'launch_plan.json')
        workers=[copy.deepcopy(next(w for w in role_source['workers'] if w['node']=='node27' and w['gpu']==4))]
        workers[0]['worker']=0
        row=copy.deepcopy(next(r for r in role_source['processes'] if r['kind']=='worker' and r['node']=='node27' and r['argv'][r['argv'].index('--gpu')+1]=='4'))
        row['worker']=0;row['out']=str(ops.RESULTS/f'operations_process_{a.session}_{row["key"]}')
        row['argv'][1]=str(HERE/'worker.py')
        for flag,value in [('--session',a.session),('--mesh','node27:4-4'),('--master','12.11.3.27:29981'),('--inventory-mode-file',str(mode_path)),('--hbm-budget',str(384 << 20)),('--dram-budget',str(256 << 20))]:
            row['argv'][row['argv'].index(flag)+1]=value
        row['env']['NCCL_TOPO_DUMP_FILE']=str(ops.RESULTS/('operations_dump_'+a.session)/(row['key']+'.runtime.dump.xml'))
        plan.update(session=a.session,workers=workers,processes=[row],ssd={},setup_directory=str(out),
            prior_setup_directory=str(a.prior_prepared),prior_plan_ref=ref(a.prior_prepared/'launch_plan.json'),
            physical_role_mapping_ref=ref(ROLE_PRIOR/'launch_plan.json'),
            inventory_mode_file=str(mode_path),protocol=protocol(a.experiment_stage),experiment_stage=a.experiment_stage,
            prediction_ref=ref(a.prediction) if a.prediction else None,model_consumed=False,physical_ready=False,
            calibration_review_ref=ref(a.calibration_review) if a.calibration_review else None,
            analysis_predeclared_ref=ref(a.predeclared),
            startup_s=120,lifetime_s=1800,necessary_pairs=[],dump_scope='single worker no pair communicator',
            aggregate_budget=dict(dram=6 << 30,hbm=2 << 30,application_margin=256 << 20))
        # Old model is a protected reference, never a consumed cost model.
        assets=[]
        asset_paths=sorted(HERE.glob('*.py'))+sorted(HERE.glob('*.json'))
        asset_paths += [ROOT/'codex_coding/src/v0.1.23.3/task2/cost_ledger/analysis_gpu4/analyze.py']
        for path in asset_paths:
            assets.append(dict(relative_path=str(path.relative_to(ROOT)),sha256=native.sha(path),base64=base64.b64encode(path.read_bytes()).decode()))
        old=native.read(a.prior_prepared/'node27.setup.spec.json')
        write(out/'node27.prior.spec.json',old)
        old_hash={r['relative_path']:r['sha256'] for r in old['assets']}
        require(all(r['relative_path'] not in old_hash or old_hash[r['relative_path']]==r['sha256'] for r in assets),'would overwrite frozen deployed asset')
        additions=[r for r in assets if r['relative_path'] not in old_hash]
        updated=copy.deepcopy(old);updated['assets'] += [{k:v for k,v in r.items() if k!='base64'} for r in additions]
        updated['ports']=[35104,29981]
        role_spec=native.read(ROLE_PRIOR/'node27.setup.spec.json')
        updated['roles']=[r for r in role_spec['roles'] if r['gpu']==4]
        updated.update(gpu=4,bdf='0000:85:00.0')
        write(out/'node27.setup.spec.json',updated)
        write(out/'append_assets.json',dict(assets=additions,revision='cost_ledger_'+out.name,inventory_mode_file=str(mode_path)))
        write(out/'launch_plan.json',plan)
        write(out/'manifest.json',dict(plan_ref=ref(out/'launch_plan.json'),GPU_launches=0,remote_writes=0,append_only=True))
        print(out/'launch_plan.json');return
    require(a.prepared is not None,'prepared required');plan=native.read(a.prepared/'launch_plan.json')
    require(plan['protocol']==protocol(plan['experiment_stage']),'protocol changed')
    predeclared_contract(plan['analysis_predeclared_ref'])
    if plan.get('prediction_ref'):prediction_contract(plan['prediction_ref'],plan['session'],review_reference=plan.get('calibration_review_ref'))
    spec=native.read(a.prepared/('node27.prior.spec.json' if a.mode=='stage' else 'node27.setup.spec.json'))
    quiet_readback(spec,out/'before_node27')
    write(out/'preflight.json',dict(passed=True,GPU_launches=0,remote_writes=0,plan_ref=ref(a.prepared/'launch_plan.json')))
    if a.mode=='preflight':return
    require(a.execute,'main agent stage requires --execute')
    package=native.read(a.prepared/'append_assets.json');helper=Path(spec['stage'])/'project'/(RUNTIME/'operations/stage_assets.py').relative_to(ROOT)
    proc,raw=ops.old.record(ops.command('node27',['python3',str(helper)]),out/'append_node27',30,
        json.dumps(dict(spec=spec,revision=package['revision'],assets=package['assets'])).encode())
    require(proc['returncode']==0,'append failed');write(out/'append_node27/stdout.json',json.loads(raw))
    proc,raw=ops.old.record(ops.command('node27',['docker','exec',spec['container_id'],'python3',str(HERE/'mode_file.py'),
        '--path',package['inventory_mode_file']]),out/'modefile_node27',15)
    require(proc['returncode']==0,'isolated mode creation failed')
    quiet_readback(native.read(a.prepared/'node27.setup.spec.json'),out/'after_node27')
    write(out/'manifest.json',dict(GPU_launches=0,append_only=True,prepared_ref=ref(a.prepared/'launch_plan.json')))
if __name__=='__main__':main()
