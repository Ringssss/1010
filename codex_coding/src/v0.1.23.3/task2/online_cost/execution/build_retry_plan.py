"""Freeze a new runtime and explicit thread limits on unchanged owned containers."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[6]
RESULTS=ROOT/'codex_coding/results/v0.1.23.3/task2/online_cost'
THREADS={'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','RAYON_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'}


def ref(path):
    return dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def retry(previous,run_id):
    if not re.fullmatch(r'online_[a-z0-9_]+',run_id) or run_id==previous['run_id'] or set(previous['nodes'])!={'node27','node30'}:
        raise ValueError('new owned two-node runtime ID required')
    old=previous['run_id'];row=copy.deepcopy(previous)
    row.update(run_id=run_id,main_approved=False,fresh_admission_passed=False,budget_status='unchanged_budget_pending_new_fresh_admission',scope='new runtime on exact prior owned containers; four numerical-library threads bounded to one; no model qualification claim')
    row.pop('admission_collected_unix_s',None)
    row.pop('binding_source_refs',None)
    for process in row['processes']:
        process['env']={k:(v.replace(old,run_id) if isinstance(v,str) else v) for k,v in process['env'].items()}
        process['env'].update(THREADS)
        process['argv']=[v.replace(old,run_id) for v in process['argv']]
        process['out']=process['out'].replace(old,run_id)
    for key in ('engine_command_path','handoff'):
        row[key]=row[key].replace(old,run_id)
    for node in row['nodes'].values():
        node.update(new_container_required=False,container_setup_status='reuse_exact_verified_owned_container_pending_quiet_recheck')
    row['pending']=['prior runtime exact-owner cleanup and quiet closure','new fresh owned GPU admission','new runtime bootstrap','new cost freeze and validation']
    return row


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--previous-plan',type=Path,required=True)
    p.add_argument('--setup-dir',type=Path,required=True)
    p.add_argument('--run-id',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_retry_plan_'):
        raise ValueError('new owned retry-plan output required')
    previous=json.loads(a.previous_plan.read_text());row=retry(previous,a.run_id)
    runtime=Path(row['handoff']).parent
    row['retry_provenance']=dict(previous_plan_ref=ref(a.previous_plan),frozen_setup_plan_ref=ref(a.setup_dir/'launch_plan.unbound.json'),builder_ref=ref(Path(__file__)),thread_env=THREADS,hardware_budgets_unchanged=True,source_assets_unchanged=True,new_GPU_launches=0,requires_exact_quiet_closure_before_launch=True)
    if runtime.exists():raise ValueError('retry runtime must be new')
    out.mkdir(exist_ok=False)
    (out/'launch_plan.unbound.json').write_text(json.dumps(row,indent=2)+'\n')
    (out/'provenance.json').write_text(json.dumps(row['retry_provenance'],indent=2)+'\n')
    print(json.dumps(dict(status='retry_plan_only',plan=str(out/'launch_plan.unbound.json'),GPU_launches=0)))


if __name__=='__main__':main()
