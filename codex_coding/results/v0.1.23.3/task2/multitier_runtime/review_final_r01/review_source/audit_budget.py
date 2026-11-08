"""Exercise exact coordinator budget gates on accounting boundary fixtures."""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[6]
SOURCE = ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/run.py'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    raw = SOURCE.read_bytes()
    tree = ast.parse(raw)
    wanted = {'require', 'check_workers', 'budget_gate'}
    nodes = [copy.deepcopy(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'BDFS' for t in n.targets):
            nodes.insert(0, copy.deepcopy(n))
    env = dict(time=time)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), env)
    mapping = [('node28',0,0),('node28',1,3),('node30',4,4),('node30',5,7),('node27',0,0),('node27',4,4)]
    workers, setup = [], dict(session='review_fixture', workers=[])
    for i, (node,gpu,numa) in enumerate(mapping):
        item = dict(worker=i,node=node,gpu=gpu,numa=numa,session=setup['session'],staging_bytes=64<<20,
                    transactions=[],headroom_bytes=dict(hbm=15<<30),
                    runtime_identity=dict(boot_id='boot'+node,start_ticks=i+1,actual_bdf=env['BDFS'][i],
                        pci_numa_node=numa,cgroup_memory_current_bytes=4<<30,cgroup_memory_max_bytes=8<<30))
        workers.append(item)
        setup['workers'].append(dict(worker=i,node=node,gpu=gpu,numa=numa,boot_id='boot'+node,pid_start_ticks=i+1))
    budget = dict(purpose='physical',session=setup['session'],valid_until=time.time()+600,
                  runtime_staging_inclusion='included',budget_accounting_complete=True,nodes={})
    for node in ('node28','node30','node27'):
        budget['nodes'][node]=dict(dram_limit_bytes=6<<30,hbm_limit_bytes=2<<30,
            physical_dram_available_bytes=16<<30,cgroup_available_bytes=2<<30,hbm_increment_bytes=512<<20,
            selected_gpu_free_bytes={str(w['gpu']):15<<30 for w in workers if w['node']==node})
    cases = [('shared_4GiB_counted_once_and_8GiB_container',None,None,True)]
    cases.append(('BDF_change', lambda w: w[4]['runtime_identity'].update(actual_bdf='0000:00:00.0'),None,False))
    cases.append(('DRAM_margin_exhausted',lambda w: w[0]['runtime_identity'].update(cgroup_memory_current_bytes=(6<<30)-(128<<20)),None,False))
    cases.append(('HBM_actual_increment_plus_new_delta',lambda w: [x['headroom_bytes'].update(hbm=(15<<30)-(256<<20)) for x in w],None,True))
    cases.append(('HBM_aggregate_exhausted',lambda w: [x['headroom_bytes'].update(hbm=(15<<30)-(800<<20)) for x in w],None,False))
    cases.append(('staging_double_count_mode_rejected',None,lambda b: b.update(runtime_staging_inclusion='separate'),False))
    cases.append(('expired_budget',None,lambda b: b.update(valid_until=time.time()-1),False))
    rows=[]
    for name,wm,bm,accepted in cases:
        w,b=copy.deepcopy(workers),copy.deepcopy(budget)
        if wm: wm(w)
        if bm: bm(b)
        error=None
        try: env['budget_gate'](w,setup,b)
        except ValueError as exc: error=str(exc)
        rows.append(dict(name=name,passed=(error is None)==accepted,error=error))
    (a.out/'run.source.py').write_bytes(raw)
    result=dict(status='passed' if all(x['passed'] for x in rows) else 'failed',checks=rows,
                source_sha256=hashlib.sha256(raw).hexdigest(),
                scope='CPU exact-function budget boundary fixtures; not physical admission',GPU_calls=0)
    (a.out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],checks=len(rows),source_sha256=result['source_sha256'])))
    if result['status']!='passed': raise SystemExit(1)


if __name__=='__main__': main()
