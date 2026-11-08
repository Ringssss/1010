"""CPU-only interface and refusal checks; no Docker, SSH, or GPU calls."""
import argparse
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
sys.path.insert(0,str(HERE))
from build_launch_plan import build, RESULTS
from bind_plan import bind
import prepare
from prepare import derive_setup
from orchestrate import HostOps, pre_mesh_ready
from hostops_guard import physical_gpu


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_cpu_'):
        raise ValueError('new owned host CPU output required')
    out.mkdir(parents=True,exist_ok=False)
    checks=[]
    def check(name,condition):
        checks.append(dict(name=name,passed=bool(condition)))
    def rejects(name,fn):
        try: fn()
        except (ValueError,MemoryError):check(name,True)
        else:check(name,False)
    plan=build('online_CPU_FIXTURE')
    workers=[r for r in plan['processes'] if r['kind']=='worker']
    check('five_roles_four_workers_one_engine',len(plan['processes'])==5 and len(workers)==4)
    check('two_node_helper30_GPU4_5_mesh_BDF_NUMA',set(plan['nodes'])=={'node27','node30'} and workers[2]['gpu']==4 and workers[2]['numa']==4 and workers[3]['gpu']==5 and workers[3]['numa']==7 and workers[3]['bdf']=='0000:b5:00.0' and workers[3]['endpoint'].endswith(':36015') and 'node27:4-5,node30:4-5' in workers[2]['argv'])
    check('eight_card_visibility_no_remap',all(r['env']['HIP_VISIBLE_DEVICES']=='0,1,2,3,4,5,6,7' for r in plan['processes']))
    check('explicit_budgets_unchanged',plan['nodes']['node27']['task_dram_cap_bytes']==192<<30 and plan['nodes']['node30']['task_dram_cap_bytes']==96<<30 and plan['nodes']['node30']['container_hard_cap_bytes']==128<<30 and plan['nodes']['node30']['task_hbm_cap_bytes']==2<<30)
    check('node28_not_in_active_scope',all(r['node']!='node28' for r in plan['processes']))
    wrapper,changes=derive_setup();compile(wrapper,'derived_setup','exec')
    text=wrapper.decode()
    check('derived_setup_target_only_model_checks_mount',"spec['node'] == 'node27' else {}" in text and "if spec['node'] == 'node27':\n        argv[-2:-2]" in text)
    check('derived_setup_core_and_swap_exact',"str(spec[\"container_hard_cap_bytes\"])" in text and '"--memory-swap"' in text and 'resource.RLIMIT_CORE' in text)
    setup=dict(GPU_launches=0,containers={n:dict(container_id=str(i)*64,container_started_at='CPU_FIXTURE_STARTED',boot_id='CPU_FIXTURE_BOOT'+n) for i,n in enumerate(plan['nodes'],1)})
    admission=dict(passed=True,containers=setup['containers'],collected_unix_s=time.time())
    bound=bind(plan,setup,admission)
    check('binding_does_not_infer_approval',bound['main_approved'] is False and bound['fresh_admission_passed'] is True)
    role=bound['processes'][0]
    prem=dict(session=plan['run_id'],worker=None,node=role['node'],gpu=role['gpu'],runtime_identity=dict(actual_bdf=role['bdf'],boot_id=role['boot_id']))
    check('fresh_HTTP_before_mesh_accepts_rankNone',pre_mesh_ready(prem,role,plan['run_id']))
    foreign=copy.deepcopy(prem);foreign['runtime_identity']['actual_bdf']='0000:09:00.0'
    check('premesh_rejects_foreign_BDF',not pre_mesh_ready(foreign,role,plan['run_id']))
    check('binding_exact_id_start_boot',all(r['container_id']==setup['containers'][r['node']]['container_id'] for r in bound['processes']))
    rejected=copy.deepcopy(admission);rejected['collected_unix_s']-=301
    rejects('stale_admission_rejected',lambda:bind(plan,setup,rejected))
    rejected=copy.deepcopy(admission);rejected['containers']['node30']['container_id']='bad'
    rejects('admission_identity_mismatch_rejected',lambda:bind(plan,setup,rejected))
    protected=dict(protected_containers=[dict(id='CPU_OLD',name='/zhujianian-fk',image_id='old',running=True,started_at='before',pid=123,host_config={},mounts=[])],original_xml_sha256={'original':'CPU_SHA'})
    HostOps.protected(protected,copy.deepcopy(protected));check('all_old_names_protected',True)
    changed=copy.deepcopy(protected);changed['protected_containers'][0]['started_at']='changed'
    rejects('old_container_restart_rejected',lambda:HostOps.protected(protected,changed))
    changed=copy.deepcopy(protected);changed['original_xml_sha256']['original']='changed'
    rejects('original_xml_change_rejected',lambda:HostOps.protected(protected,changed))
    def gpu(free,total,node,count=1):
        spec=dict(node=node,roles=[dict(gpu=i,bdf='0000:09:00.0',numa=0) for i in range(count)],minimum_initial_gpu_free_bytes=(52<<30 if node=='node27' else (2<<30)+(256<<20)),per_selected_gpu_hbm_cap_bytes=2<<30,task_hbm_cap_bytes=2<<30)
        fake=SimpleNamespace(cuda=SimpleNamespace(set_device=lambda _:None,mem_get_info=lambda _:(free,total)))
        stream=io.StringIO()
        with patch.dict(sys.modules,torch=fake),patch('hostops_guard.threading.Thread'),patch('hostops_guard.actual_device_identity',return_value=('0000:09:00.0',0)),patch.dict('os.environ',HIP_VISIBLE_DEVICES='0,1,2,3,4,5,6,7'),redirect_stdout(stream):
            physical_gpu(spec)
        return json.loads(stream.getvalue())
    check('helper_global_used_no_baseline_subtraction',not gpu(4<<30,64<<30,'node30')['passed'])
    check('helper_small_physical_used_allowed',gpu(63<<30,64<<30,'node30')['passed'])
    check('target_52GiB_physical_free_gate',not gpu(51<<30,64<<30,'node27')['passed'] and gpu(53<<30,64<<30,'node27')['passed'])
    check('helper_aggregate_two_card_upper_and_margin',not gpu(63<<30,64<<30,'node30',count=2)['passed'])
    provenance=json.loads((HERE/'supervisor_provenance.json').read_text())
    check('supervisor_provenance_matches',hashlib.sha256((HERE/'derived_supervisor.py').read_bytes()).hexdigest()==provenance['derived_sha256'])
    check('ongoing_host_floor_supervisor_monitor',"host_memory_reserve_breached" in (HERE/'derived_supervisor.py').read_text())
    # Build the real setup bundle against current CPU metadata in disposable storage.
    metadata=RESULTS/'review_two_node_metadata_r01'
    with tempfile.TemporaryDirectory(prefix='online_hostops_CPU_FIXTURE_') as temporary:
        frozen=Path(temporary)/'execution_setup_CPU_FIXTURE'
        argv=['prepare.py','--run-id','online_qwen_two_node_r01','--out',str(frozen),'--metadata-root',str(metadata),'--model-metadata',str(RESULTS/'execution_metadata_local_r01.json')]
        with patch.object(prepare,'RESULTS',Path(temporary)),patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
            prepare.main()
        frozen_plan=json.loads((frozen/'launch_plan.unbound.json').read_text())
        check('actual_prepare_bundle_two_nodes_only',set(frozen_plan['nodes'])=={'node27','node30'} and not (frozen/'node28.setup.spec.json').exists())
        helper_spec=json.loads((frozen/'node30.setup.spec.json').read_text())
        check('actual_prepare_two_helper_ports_and_host_NUMA',helper_spec['ports']==[36014,36015] and [r['host_numa'] for r in helper_spec['roles']]==[4,7])
        check('actual_prepare_isolated_assets_frozen',all(hashlib.sha256(__import__('base64').b64decode(a['base64'])).hexdigest()==a['sha256'] for a in helper_spec['assets']))
        compile((frozen/'setup_host.source.py').read_bytes(),'actual_frozen_setup','exec')
    # Exercise the actual consumer parser for every generated controller phase.
    ops=HostOps.__new__(HostOps);ops.out=out;ops.events=0;ops.deadline_at=None
    captured=[]
    ops.record=lambda node,argv,name,timeout,payload=None: captured.append(dict(node=node,argv=argv,timeout_s=timeout))
    for action in ('init','bootstrap','calibrate','validate','cleanup'):
        ops.controller(bound,Path('/CPU_FIXTURE/launch_plan.json'),action)
    for item in captured:
        command=[v for v in item['argv'][3:] if v!='--execute']
        done=subprocess.run(command,capture_output=True,text=True,timeout=30)
        check('actual_controller_CLI_'+command[command.index('--action')+1],done.returncode==0 and json.loads(done.stdout).get('GPU_or_RPC_calls')==0)
    (out/'CPU_FIXTURE_controller_commands.json').write_text(json.dumps(captured,indent=2)+'\n')
    result=dict(scope='CPU_FIXTURE_ONLY; mocks; no GPU/SSH/Docker/external writes',checks=checks,passed=all(r['passed'] for r in checks),GPU_launches=0,remote_commands=0)
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],checks=len(checks),out=str(out))))
    if not result['passed']: raise SystemExit(1)


if __name__=='__main__':main()
