"""Narrow CPU-only regression check for bounded owned GPU query arguments."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from orchestrate import gpu_admission_spec, RESULTS
from hostops_guard import physical_gpu


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--setup-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_admission_args_cpu_'):
        raise ValueError('new owned admission argument CPU output required')
    out.mkdir(exist_ok=False)
    checks=[];records=[]
    def check(name,test):checks.append(dict(name=name,passed=bool(test)))
    for node in ('node27','node30'):
        path=a.setup_dir/(node+'.setup.spec.json')
        full=json.loads(path.read_text());small=gpu_admission_spec(full)
        raw=json.dumps(small)
        argv=['docker','exec','-i','-e','HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7','-e','ROCR_VISIBLE_DEVICES=','-e','CUDA_VISIBLE_DEVICES=','CPU_FIXTURE_ID'*5,'python3','-',raw]
        check(node+'_query_ARGV_under_16KiB',sum(len(v.encode())+1 for v in argv)<16384)
        check(node+'_no_source_payload_in_query',set(small)=={'node','roles','minimum_initial_gpu_free_bytes','per_selected_gpu_hbm_cap_bytes','task_hbm_cap_bytes'} and 'assets' not in small and 'base64' not in raw)
        check(node+'_physical_role_and_budget_inputs_unchanged',all(small[k]==full[k] for k in ('node','roles','minimum_initial_gpu_free_bytes','per_selected_gpu_hbm_cap_bytes','task_hbm_cap_bytes')))
        def physical(free,total):
            fake=SimpleNamespace(cuda=SimpleNamespace(set_device=lambda _:None,mem_get_info=lambda _:(free,total)))
            mapping={r['gpu']:(r['bdf'],r['numa']) for r in small['roles']}
            stream=io.StringIO()
            with patch.dict(sys.modules,torch=fake),patch('hostops_guard.threading.Thread'),patch('hostops_guard.actual_device_identity',side_effect=lambda gpu:mapping[gpu]),patch.dict('os.environ',HIP_VISIBLE_DEVICES='0,1,2,3,4,5,6,7'),redirect_stdout(stream):
                physical_gpu(small)
            return json.loads(stream.getvalue())
        if node=='node27':
            check('target_52GiB_gate_preserved',not physical(51<<30,64<<30)['passed'] and physical(53<<30,64<<30)['passed'])
        else:
            check('helper_busy_global_upper_rejected',not physical(4<<30,64<<30)['passed'])
            check('helper_two_card_aggregate_with_margin_rejected',not physical(63<<30,64<<30)['passed'])
            check('helper_two_small_physical_contexts_allowed',physical((64<<30)-(512<<20),64<<30)['passed'])
        records.append(dict(node=node,full_source_spec_bytes=len(json.dumps(full).encode()),gpu_argument_bytes=len(raw.encode()),query_argv_bytes=sum(len(v.encode())+1 for v in argv),frozen_spec_ref=dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest()),frozen_asset_count=len(full['assets'])))
    result=dict(CPU_FIXTURE_ONLY=True,checks=checks,passed=all(c['passed'] for c in checks),records=records,GPU_launches=0,SSH_commands=0,remote_source_assets_unchanged=True)
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],checks=len(checks),out=str(out))))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
