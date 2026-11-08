"""Verify warm-only fit and mixed-plan interfaces without initializing GPUs."""
import argparse
import copy
import json
from pathlib import Path
from common import ROOT, native, write, require
from runner import make_plan, fit_warm, ROUTES
from fluidckpt.cluster.service_cost import estimate_execution, new_state

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    old=ROOT/'codex_coding/results/v0.1.23.3/task2/multitier_runtime/operations_actual_r01/cases/C3.json'
    raw=native.read(old)['restore'];workers=native.read(old.parent/'final_inventory.json')['workers']
    descriptor=raw['plan']['targets'][0]
    sources=[]
    for i,(sw,tier,dw) in enumerate(ROUTES):
        source=copy.deepcopy(descriptor);source.update(worker=sw,tier=tier,epoch=workers[sw]['epoch'],generation=1,id=f'cpu/source{i}',off=0,n=16 << 20)
        sources.append(source)
    owned=[];plan=make_plan(sources,workers,'cpu/mixed',[(i,12 << 20,0) for i in range(3)],owned)
    require([(o['src_w'],o['dst_w']) for o in plan['ops']]==[(4,4),(2,4),(4,5)],'mixed worker dependency order')
    batch=make_plan(sources,workers,'cpu/batch',[(1,1 << 20,j << 20) for j in range(4)],[])
    require([o['source_offset'] for o in batch['ops']]==[j << 20 for j in range(4)],'batch source offsets')
    samples=[]
    for path in [o['path'] for o in plan['ops']]:
        for label,n,k in [('warm_small',4 << 20,1),('warm_large',16 << 20,1),('warm_batch',4 << 20,4)]:
            samples.append(dict(path=path,label=label,nbytes=n,operations=k,service_s=.001+n/1e9,target_s=.003+k*.001+n/1e9,dispatch_s=.002))
    write(a.out/'fit_input_observed.json',samples);model,diag=fit_warm(workers,samples,a.out)
    require(model is not None and model['cold_measurement_count']==0,'warm model must identify without measured cold')
    require(abs(model['request_s']-.003)<1e-12,'shared request fit')
    require(all(abs(x['operation_s']-.001)<1e-12 and x['first_pair_s']==0 for x in model['paths'].values()),'warm affine operation fit')
    state=new_state(workers);state['warm_pairs']=['2:4','4:5'];prediction=estimate_execution(plan,workers,model,state)
    require(prediction['timeline'][2]['start_s']==prediction['timeline'][1]['end_s'],'same w4 receive before send')
    require(all(t['first_pair_s']==0 for t in prediction['timeline']),'gamma zero warm-only')
    invalid=copy.deepcopy(samples);invalid[1]['target_s']=invalid[0]['target_s']
    failed,negative=fit_warm(workers,invalid,a.out)
    require(failed is None and negative['schema2_failure'],'nonpositive fit cannot fabricate model')
    write(a.out/'qualification.json',dict(passed=True,checks=['native mixed plan validates','native four-op offsets validate','original affine warm-only fit identifies','shared request expected','source-target serial dependency','no fabricated nonpositive slope'],GPU_launches=0,remote_writes=0,prediction=prediction))
    print(json.dumps(dict(passed=True,GPU_launches=0,out=str(a.out))))
if __name__=='__main__':main()
