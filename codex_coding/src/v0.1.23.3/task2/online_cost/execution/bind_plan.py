"""Bind exact newly created containers and fresh admission artifacts without launches."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'


def ref(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def bind(plan, setup, admission, approve=False):
    if set(setup['containers']) != set(plan['nodes']) or set(plan['nodes']) != {'node27','node30'} or setup.get('GPU_launches') != 0:
        raise ValueError('approved two-node zero-GPU new-container setup required')
    if not admission.get('passed') or time.time() - admission['collected_unix_s'] > 300:
        raise ValueError('fresh successful owned-context admission required')
    row = copy.deepcopy(plan)
    for node, identity in setup['containers'].items():
        if admission['containers'][node] != identity:
            raise ValueError('admission setup identity mismatch')
        if len(identity.get('container_id','')) != 64 or not identity.get('container_started_at') or not identity.get('boot_id'):
            raise ValueError('exact container ID/start/boot required')
        row['nodes'][node].update(identity, container_setup_status='verified_new_container')
        for process in row['processes']:
            if process['node'] == node:
                process.update(identity)
    row.update(main_approved=approve, fresh_admission_passed=True,
               admission_collected_unix_s=admission['collected_unix_s'],
               budget_status='explicit_two_node_target192_hard224_helper96_hard128_verified',
               pending=['trusted processed source setup','initial legal full restore and park','frozen online predictions'],
               scope='fresh five-process real-model workflow; lifecycle gates remain mandatory')
    return row


def main():
    p=argparse.ArgumentParser()
    for name in ('plan','setup','admission','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--main-approved',action='store_true')
    a=p.parse_args()
    out=a.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('execution_host_bind_'):
        raise ValueError('new execution_host_bind_* output required')
    row=bind(json.loads(a.plan.read_text()),json.loads(a.setup.read_text()),json.loads(a.admission.read_text()),a.main_approved)
    row['binding_source_refs']={n:ref(getattr(a,n)) for n in ('plan','setup','admission')}
    out.mkdir(parents=True,exist_ok=False)
    (out/'launch_plan.json').write_text(json.dumps(row,indent=2)+'\n')
    print(json.dumps({'plan':str(out/'launch_plan.json'),'GPU_launches':0,'main_approved':a.main_approved}))


if __name__=='__main__':
    main()
