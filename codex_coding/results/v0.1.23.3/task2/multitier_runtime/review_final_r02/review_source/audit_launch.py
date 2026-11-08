"""Review one concrete finite launch plan and freeze its executable sources."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[6]
HERE=Path(__file__).resolve().parent
RESULTS=ROOT/'codex_coding/results/v0.1.23.3/task2/multitier_runtime'
EXPECTED=[('node28',0,0),('node28',1,3),('node30',4,4),('node30',5,7),('node27',0,0),('node27',4,4)]


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    refs,checks=[],[]
    def freeze(path):
        path=path.resolve();raw=path.read_bytes()
        dest=a.out/'snapshot'/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        refs.append(dict(source_path=str(path),path=str(dest),sha256=hashlib.sha256(raw).hexdigest()))
        return raw
    def check(name,ok):checks.append(dict(name=name,passed=bool(ok)))
    plan=json.loads(freeze(a.plan))
    check('six_worker_mapping',[tuple(w[k] for k in ('node','gpu','numa')) for w in plan['workers']]==EXPECTED and
          [w['worker'] for w in plan['workers']]==list(range(6)))
    check('finite_nine_processes',len(plan['processes'])==9 and len({r['key'] for r in plan['processes']})==9 and
          plan['startup_s']==90 and plan['lifetime_s']==1800 and plan['controller_timeout_s']==1440)
    check('aggregate_budget',plan['aggregate_budget']==dict(dram=6<<30,hbm=2<<30,application_margin=256<<20))
    worker_rows=[r for r in plan['processes'] if r['kind']=='worker']
    check('exact_selected_GPU_roles',[(r['node'],r['gpu'],r['numa']) for r in worker_rows]==EXPECTED)
    for row in plan['processes']:
        check(row['key']+'_owned_process',row['node'] in ('node28','node30','node27') and len(row['container_id'])==64 and
              row['boot_id'] and Path(row['out']).parent==RESULTS and Path(row['out']).name.startswith('operations_process_'+plan['session']))
        if row['kind']=='worker':
            env=row['env'];argv=row['argv']
            check(row['key']+'_native_runtime',argv[1]==str(HERE.parent/'runner/runtime_worker.py') and
                  argv[argv.index('--mesh')+1]=='node28:0-1,node30:4-5,node27:0-0,node27:4-4' and
                  argv[argv.index('--session')+1]==plan['session'])
            check(row['key']+'_isolated_RCCL',env['NCCL_NET']=='IBext_v8' and env['NCCL_IB_HCA']=='shca_0' and
                  env['NCCL_TOPO_FILE']!=env['NCCL_TOPO_DUMP_FILE'] and
                  Path(env['NCCL_TOPO_DUMP_FILE']).parent==RESULTS/('operations_dump_'+plan['session']) and
                  env['NCCL_TOPO_DUMP_FILE_RANK']==('1' if row['worker'] in (3,5) else '0'))
        else:
            check(row['key']+'_SSD_service',row['argv'][:3]==['python3','-m','fluidckpt.serving.ssd_store'] and
                  row['argv'][row['argv'].index('--directory')+1]=='/task_ssd')
    for node in ('node28','node30','node27'):
        spec=json.loads(freeze(Path(plan['setup_directory'])/(node+'.setup.spec.json')))
        current_assets=True
        for asset in spec['assets']:
            path=ROOT/asset['relative_path']
            current_assets=current_assets and path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==asset['sha256']
        check(node+'_final_source_matches_stage_contract',current_assets)
    sources=[HERE.parent/'operations'/name for name in ('run_session.py','control.py','supervise.py','collect_owned.py','host_readback.py')]
    sources += [HERE.parent/'runner'/name for name in ('run.py','runtime_worker.py')]
    sources += [ROOT/'main_lib/fluidckpt/cluster/worker.py',ROOT/'codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/adapter.py']
    for path in sources:ast.parse(freeze(path))
    controller=(HERE.parent/'operations/run_session.py').read_text()
    check('all_STOP_finally_and_compatible_collection',"summary['all_stop'] = ops.stops(plan,digest,a.out)" in controller and
          "filter='data'" not in controller and 'stable_owners' in controller)
    report=dict(status='passed' if all(c['passed'] for c in checks) else 'failed',checks=checks,source_refs=refs,
        clearance='one finite synthetic six-worker session using this exact frozen launch plan',
        pre_execution_certified=False,physical_peak_measured=False,model_ready=False,
        validation_boundary='runtime BDF/NUMA/session, postwarm aggregate, required paths and payloads remain actual gates')
    (a.out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(status=report['status'],clearance=report['clearance'],checks=len(checks))))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
