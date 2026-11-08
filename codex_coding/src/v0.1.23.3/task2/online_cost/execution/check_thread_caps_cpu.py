"""CPU-only retry namespace and pre-import numerical-thread cap checks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from build_launch_plan import build,RESULTS
from build_retry_plan import retry,THREADS
from orchestrate import HostOps,THREAD_ENV


def main():
    p=argparse.ArgumentParser();p.add_argument('--previous-plan',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_thread_cpu_'):raise ValueError('new owned thread CPU output required')
    out.mkdir(exist_ok=False);checks=[]
    def check(name,test):checks.append(dict(name=name,passed=bool(test)))
    prior=json.loads(a.previous_plan.read_text());row=retry(prior,'online_qwen_two_node_r02')
    check('six_preimport_caps_all_five_roles',all(all(p['env'].get(k)==v for k,v in THREADS.items()) for p in row['processes']))
    check('default_builder_same_thread_caps',all(all(p['env'].get(k)==v for k,v in THREADS.items()) for p in build('online_CPU_FIXTURE')['processes']))
    check('budgets_and_exact_CIDs_unchanged',all(row['nodes'][n][k]==prior['nodes'][n][k] for n in prior['nodes'] for k in ('container_id','container_name','container_hard_cap_bytes','task_dram_cap_bytes','task_hbm_cap_bytes','docker_create_resource_args')))
    check('core129_assets_unchanged',row['source_assets_sha256']==prior['source_assets_sha256'] and len(row['source_assets_sha256'])==129)
    check('new_runtime_not_old_paths',all('online_qwen_two_node_r02' in p['out'] and 'online_qwen_two_node_r01' not in p['out'] for p in row['processes']) and 'online_qwen_two_node_r02' in row['handoff'])
    check('new_worker_sessions',all(p['argv'][p['argv'].index('--session')+1]=='online_qwen_two_node_r02' for p in row['processes'] if p['kind']=='worker'))
    check('new_run_fresh_admission_not_inferred',not row['main_approved'] and not row['fresh_admission_passed'] and 'admission_collected_unix_s' not in row)
    ops=HostOps.__new__(HostOps);ops.out=out;ops.events=0;ops.deadline_at=None;captured=[]
    ops.record=lambda node,argv,name,timeout,payload=None:captured.append(argv)
    ops.controller(row,Path(row['handoff']).parent/'launch_plan.json','init')
    command=captured[0];prefix=command[:command.index('python3')]
    check('controller_preimport_docker_exec_caps',all(k+'='+v in prefix for k,v in THREAD_ENV.items()) and THREAD_ENV==THREADS)
    check('pids512_remains',all('--pids-limit' in n['docker_create_resource_args'] and n['docker_create_resource_args'][n['docker_create_resource_args'].index('--pids-limit')+1]=='512' for n in row['nodes'].values()))
    result=dict(CPU_FIXTURE_ONLY=True,checks=checks,passed=all(c['passed'] for c in checks),GPU_launches=0,SSH_commands=0,source_assets_unchanged=True,controller_command=command,thread_env=THREADS)
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(passed=result['passed'],checks=len(checks),out=str(out))))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
