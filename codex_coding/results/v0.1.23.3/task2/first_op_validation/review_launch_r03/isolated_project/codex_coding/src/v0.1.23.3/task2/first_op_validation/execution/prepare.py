"""Append immutable assets; read-only prior metadata admission is available first."""
import argparse
import base64
import copy
import json
from pathlib import Path
from common import ROOT,HERE,RESULTS,PREVIOUS,native,ops,RUNTIME,write,ref,require,fresh_output,check_reference,quiet_readback
from protocol import protocol

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('metadata','build','stage','preflight'))
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--session',default='first_op_runtime_A_r01')
    parser.add_argument('--experiment-stage',choices=('A','B'),default='A')
    parser.add_argument('--prediction',type=Path)
    parser.add_argument('--prior-prepared',type=Path)
    parser.add_argument('--execute',action='store_true')
    a=parser.parse_args()
    out=fresh_output(a.out)
    if a.mode=='metadata':
        for spec in ops.specs(PREVIOUS):
            quiet_readback(spec,out/('current_'+spec['node']))
        write(out/'qualification.json',dict(passed=True,GPU_launches=0,remote_writes=0,
            source_prior_ref=ref(PREVIOUS/'launch_plan.json'),scope='fresh read-only physical quiet admission; no launch'))
        return
    if a.mode=='build':
        previous_directory=a.prior_prepared.resolve() if a.prior_prepared else PREVIOUS
        if a.experiment_stage=='B':
            require(a.prior_prepared is not None,'B requires latest successfully staged A assets as explicit prior')
        prior=native.read(previous_directory/'launch_plan.json')
        require(a.session.startswith('first_op_runtime_') and '/' not in a.session,'unique first-operation namespace required')
        check_reference(prior['frozen_model_ref'])
        plan=copy.deepcopy(prior)
        mode_path=RESULTS/(a.session+'.inventory_mode.json')
        plan.update(session=a.session,setup_directory=str(out),prior_setup_directory=str(previous_directory),
            prior_plan_ref=ref(previous_directory/'launch_plan.json'),inventory_mode_file=str(mode_path),
            protocol=protocol(a.experiment_stage),experiment_stage=a.experiment_stage,model_consumed=False,
            prediction_ref=ref(a.prediction.resolve()) if a.prediction else None)
        if a.experiment_stage=='B':
            require(a.prediction is not None,'B requires independently frozen predictions')
        for row in plan['processes']:
            row['out']=str(ops.RESULTS/f'operations_process_{a.session}_{row["key"]}')
            if row['kind']=='worker':
                row['argv'][row['argv'].index('--session')+1]=a.session
                row['argv'][row['argv'].index('--inventory-mode-file')+1]=str(mode_path)
                row['env']['NCCL_TOPO_DUMP_FILE']=str(ops.RESULTS/('operations_dump_'+a.session)/(row['key']+'.runtime.dump.xml'))
                if row['worker']==4:
                    row['argv'][1]=str(HERE/'worker.py')
        names=('common.py','protocol.py','worker.py','runner.py','prepare.py','session.py','mode_file.py','cpu_replay.py')
        assets=[]
        for name in names:
            path=HERE/name
            assets.append(dict(relative_path=str(path.relative_to(ROOT)),sha256=native.sha(path),base64=base64.b64encode(path.read_bytes()).decode()))
        append_paths=None
        for spec in ops.specs(previous_directory):
            old=copy.deepcopy(spec)
            write(out/(spec['node']+'.prior.spec.json'),old)
            paths={row['relative_path'] for row in old['assets']}
            old_hashes={row['relative_path']:row['sha256'] for row in old['assets']}
            require(all(row['relative_path'] not in old_hashes or old_hashes[row['relative_path']]==row['sha256'] for row in assets),'would overwrite immutable source')
            missing={row['relative_path'] for row in assets if row['relative_path'] not in paths}
            if append_paths is None:
                append_paths=missing
            require(append_paths==missing,'nodes have different staged asset closure; explicit repair needed')
            spec['assets']=old['assets']+[{k:v for k,v in row.items() if k!='base64'} for row in assets if row['relative_path'] in missing]
            write(out/(spec['node']+'.setup.spec.json'),spec)
        write(out/'append_assets.json',dict(assets=[row for row in assets if row['relative_path'] in append_paths],revision='first_op_'+out.name,inventory_mode_file=str(mode_path)))
        write(out/'launch_plan.json',plan)
        write(out/'manifest.json',dict(plan_ref=ref(out/'launch_plan.json'),GPU_launches=0,remote_writes=0,
            append_only=True,source_prior_ref=ref(previous_directory/'launch_plan.json'),frozen_model_ref=plan['frozen_model_ref']))
        print(str(out/'launch_plan.json'))
        return
    require(a.prepared is not None,'prepared directory required')
    prepared=a.prepared.resolve()
    plan=native.read(prepared/'launch_plan.json')
    require(plan['protocol']==protocol(plan['experiment_stage']),'registered stage differs')
    check_reference(plan['frozen_model_ref'])
    specs=[native.read(prepared/(node+('.prior.spec.json' if a.mode=='stage' else '.setup.spec.json'))) for node in ('node28','node30','node27')]
    for spec in specs:
        quiet_readback(spec,out/('before_'+spec['node']))
    write(out/'preflight.json',dict(passed=True,GPU_launches=0,remote_writes=0,plan_ref=ref(prepared/'launch_plan.json')))
    if a.mode=='preflight':
        return
    require(a.execute,'stage is a main-agent action requiring --execute')
    package=native.read(prepared/'append_assets.json')
    for spec in specs:
        node=spec['node']
        helper=Path(spec['stage'])/'project'/(RUNTIME/'operations/stage_assets.py').relative_to(ROOT)
        proc,raw=ops.old.record(ops.command(node,['python3',str(helper)]),out/('append_'+node),30,
            json.dumps(dict(spec=spec,revision=package['revision'],assets=package['assets'])).encode())
        require(proc['returncode']==0,'append failed '+node)
        write(out/('append_'+node)/'stdout.json',json.loads(raw))
        proc,raw=ops.old.record(ops.command(node,['docker','exec',spec['container_id'],'python3',str(HERE/'mode_file.py'),
            '--path',package['inventory_mode_file']]),out/('modefile_'+node),15)
        require(proc['returncode']==0,'isolated stdlib fixed mode CLI failed '+node)
        quiet_readback(native.read(prepared/(node+'.setup.spec.json')),out/('after_'+node))
    write(out/'manifest.json',dict(GPU_launches=0,append_only=True,prepared_ref=ref(prepared/'launch_plan.json')))
if __name__=='__main__':
    main()
