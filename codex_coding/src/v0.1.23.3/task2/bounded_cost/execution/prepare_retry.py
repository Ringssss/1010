"""Build one runner-only retry from the currently staged frozen bounded plan."""
import argparse
import base64
import copy
from pathlib import Path
from common import ROOT, HERE, RESULTS, native, ops, write, ref, require, fresh_output

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prior',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--session',required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    prior = a.prior.resolve()
    old_path = prior/'launch_plan.json'
    plan = copy.deepcopy(native.read(old_path))
    require((plan['session'],a.session) in (('bounded_cost_runtime_r01','bounded_cost_runtime_r02'),('bounded_cost_runtime_r02','bounded_cost_runtime_r03')), 'only declared fresh preparation retry allowed')
    mode_path = RESULTS/(a.session+'.inventory_mode.json')
    plan.update(session=a.session,prior_plan_ref=ref(old_path),prior_setup_directory=str(prior),setup_directory=str(out),inventory_mode_file=str(mode_path),retry_reason='preparation schema/lifecycle correction only; coefficients and two holdouts unchanged')
    runner = HERE/'runner.py'
    asset = dict(relative_path=str(runner.relative_to(ROOT)),sha256=native.sha(runner),base64=base64.b64encode(runner.read_bytes()).decode())
    for row in plan['processes']:
        row['out'] = str(ops.RESULTS/f'operations_process_{a.session}_{row["key"]}')
        if row['kind']=='worker':
            row['argv'][row['argv'].index('--session')+1]=a.session
            row['argv'][row['argv'].index('--inventory-mode-file')+1]=str(mode_path)
            row['env']['NCCL_TOPO_DUMP_FILE']=str(ops.RESULTS/('operations_dump_'+a.session)/(row['key']+'.runtime.dump.xml'))
    for spec in ops.specs(prior):
        write(out/(spec['node']+'.prior.spec.json'),spec)
        assets={row['relative_path']:row for row in spec['assets']}
        require(asset['relative_path'] in assets,'currently staged runner source missing')
        for row in spec['assets']:
            if row['relative_path']!=asset['relative_path']:
                require(native.sha(ROOT/row['relative_path'])==row['sha256'],'unrelated frozen source changed')
        assets[asset['relative_path']]={k:v for k,v in asset.items() if k!='base64'}
        spec['assets']=list(assets.values())
        write(out/(spec['node']+'.setup.spec.json'),spec)
    write(out/'append_assets.json',dict(assets=[asset],revision='bounded_cost_'+out.name,inventory_mode_file=str(mode_path)))
    write(out/'launch_plan.json',plan)
    write(out/'manifest.json',dict(GPU_launches=0,remote_writes=0,runner_only_update=True,plan_ref=ref(out/'launch_plan.json'),prior_plan_ref=ref(old_path),frozen_model_ref=plan['frozen_model_ref']))
    print(str(out/'launch_plan.json'))

if __name__=='__main__':
    main()
