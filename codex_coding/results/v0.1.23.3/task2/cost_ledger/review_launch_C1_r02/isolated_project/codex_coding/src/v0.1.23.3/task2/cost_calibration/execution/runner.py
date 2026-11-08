"""Three-path warm calibration and one frozen mixed-content holdout."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from common import ROOT, HERE, native, ops, write, ref, require, budget_snapshot
from fluidckpt.cluster.control import Cluster, CONTROLLER_CLOCK_DOMAIN
from fluidckpt.cluster.service_cost import fit_path, fit_request_model, new_state, observe_success, estimate_execution, check_state, pair_key
from fluidckpt.cluster.planning import validate_plan
MIB = 1 << 20
ROUTES = [(4, 'hbm', 4), (2, 'dram', 4), (4, 'ssd', 5)]

def warm_only_estimate(plan, workers, model, state):
    check_state(workers,state)
    needed={pair_key(o['src_w'],o['dst_w']) for o in plan['ops'] if o['src_w']!=o['dst_w']}
    require(needed <= set(state['warm_pairs']),'warm-only prediction refuses unwarmed pair')
    return estimate_execution(plan,workers,model,state)

def set_mode(plan, mode, out):
    for node in ('node28', 'node30', 'node27'):
        row = next(r for r in plan['processes'] if r['node'] == node)
        proc, raw = ops.old.record(ops.command(node, ['docker','exec', row['container_id'], 'python3',str(HERE/'mode_file.py'),'set','--path',plan['inventory_mode_file'],'--mode',mode]), out/node, 15)
        require(proc['returncode'] == 0, 'owned mode change failed '+node)
        write(out/node/'stdout.json',json.loads(raw))

def query_modes(cluster, plan, out):
    rounds = []
    for mode in ('legacy', 'cached'):
        set_mode(plan, mode, out/('mode_'+mode))
        for index in range(2):
            begin = time.monotonic_ns(); telemetry = []
            workers, rows = cluster.inventory(compact=True, telemetry=telemetry)
            end = time.monotonic_ns()
            require(not rows, 'query A/B must have no payload objects')
            require(all(w['inventory_diagnosis']['mode'] == mode for w in workers), 'mode not effective')
            rounds.append(dict(mode=mode, index=index, begin_ns=begin, end_ns=end,
                elapsed_s=(end-begin)/1e9, workers=workers, rpc=telemetry))
            write(out/'inventory_ab.json', rounds)
    def median(mode, key):
        vals=[]
        for row in rounds:
            if row['mode'] == mode:
                if key == 'wall': vals.append(row['elapsed_s'])
                else:
                    vals.append(max(sum(w['inventory_diagnosis']['segments_ns'].get(k,0) for k in ('library_resolution','cdll_load')) for w in row['workers'])/1e9)
        return statistics.median(vals)
    legacy, cached = median('legacy','wall'), median('cached','wall')
    library_drop = median('legacy','library')-median('cached','library')
    stable = True
    for idx in range(6):
        rows=[r['workers'][idx] for r in rounds]
        for key in ('epoch','session','worker','node','gpu','numa'):
            stable = stable and len({json.dumps(r[key],sort_keys=True) for r in rows}) == 1
        for key in ('boot_id','pid','start_ticks','actual_bdf','pci_numa_node','cgroup_path','selected_hca_metadata'):
            stable = stable and len({json.dumps(r['runtime_identity'][key],sort_keys=True) for r in rows}) == 1
        stable = stable and all(r['inventory_diagnosis']['fresh_fields'] for r in rows)
    accepted = stable and cached < legacy and median('legacy','library') >= .5*legacy and library_drop >= .7*(legacy-cached)
    selected = 'cached' if accepted else 'legacy'
    decision = dict(selected_mode=selected, static_identity_stable=stable, legacy_median_s=legacy,
        cached_median_s=cached, library_drop_s=library_drop, cached_admitted=accepted,
        rule='cached faster; legacy library >=50% wall; library reduction >=70% wall reduction; all exact static identity equal; fresh_fields retained')
    write(out/'inventory_decision.json',decision)
    set_mode(plan, selected, out/'mode_selected')
    return decision

def make_plan(sources, workers, label, specs, owned):
    targets, operations = [], []
    for index,(route_index,size,offset) in enumerate(specs):
        source = sources[route_index]; dst=ROUTES[route_index][2]
        target = native.target_from(source,workers,label+f'/target{index}',dst,'hbm',off=offset,n=size)
        native.remember(owned,target,workers)
        op = native.one_plan(source,target,workers)['ops'][0]
        op.update(op_id=index,path=native.adapter.path_key(source['worker'],source['tier'],workers[source['worker']]['numa'],dst,'hbm'))
        targets.append(target); operations.append(op)
    plan = dict(targets=targets,ops=operations,executor='serial',chunk_bytes=32 << 20,estimated_transfer_s=None)
    validate_plan(plan)
    return plan

def fit_warm(workers, samples, out):
    # Compatibility inputs are explicitly constrained warm references, not cold measurements.
    compatibility = copy.deepcopy(samples)
    paths, failures = {}, {}
    for path in sorted({s['path'] for s in samples}):
        by={s['label']:s for s in samples if s['path']==path}
        small,large=by['warm_small'],by['warm_large']
        try:
            value=fit_path(small,small,large); value['first_pair_s']=0.
            paths[path]=value
        except ValueError as exc:
            failures[path]=dict(error=str(exc),raw_byte_slope=(large['service_s']-small['service_s'])/(large['nbytes']-small['nbytes']))
        constrained=dict(small,label='cold_small',measurement_status='unmeasured_warm_only_gamma_zero_compatibility',observed=False,synthetic=True,derived_from=dict(path=path,label='warm_small',fit_input_observed_ref=ref(out/'fit_input_observed.json')))
        compatibility.append(constrained)
    write(out/'fit_inputs.json',compatibility)
    diagnostic=dict(schema1_paths=paths,schema1_failures=failures,
        cold_measurement_count=0,first_pair_s_policy='constrained zero; valid only after explicitly warming needed pairs')
    try:
        model=fit_request_model(workers,compatibility)
        for cost in model['paths'].values(): cost['first_pair_s']=0.
        model.update(chunk_bytes=32 << 20,fit_scope='warm-only; cold first-pair unavailable',cold_measurement_count=0)
        model['scope']+='; warm-only gamma is unmeasured and constrained zero; requires successful necessary-pair warm in these incarnations'
        return model, diagnostic
    except ValueError as exc:
        diagnostic['schema2_failure']=str(exc)
        diagnostic['raw_target_slopes']={p:(next(s for s in samples if s['path']==p and s['label']=='warm_large')['target_s']-next(s for s in samples if s['path']==p and s['label']=='warm_small')['target_s'])/(12*MIB) for p in {s['path'] for s in samples}}
        return None,diagnostic

def main():
    p=argparse.ArgumentParser();p.add_argument('--setup',required=True);p.add_argument('--plan',required=True);p.add_argument('--output',required=True);p.add_argument('--stage',choices=('mesh','measure'),required=True);p.add_argument('--paired-query-holdout',action='store_true')
    a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    setup=native.read(a.setup);plan=native.read(a.plan);native.check_setup(setup)
    cluster=Cluster([w['endpoint'] for w in setup['workers']],{},executor='serial')
    journal=dict(status='running',owned=[],synthetic=True,cold_pair_claim=False,cold_disk_claim=False)
    write(out/'journal.json',journal)
    try:
        if a.stage == 'mesh':
            reports=cluster.call_all('mesh',[{} for _ in range(6)])
            pairs=[[2,4],[4,5]]
            warm=cluster.call_all('warm',[dict(pairs=pairs) for _ in range(6)])
            decision=query_modes(cluster,plan,out)
            workers,rows=cluster.inventory();native.check_workers(workers,setup);require(not rows,'mesh has objects')
            require(len(warm)==6 and all(w['warm_s']>=0 for w in warm),'six successful necessary-pair warm responses required')
            write(out/'mesh.json',dict(decision=decision,workers=workers,incarnations=new_state(workers)['incarnations'],mesh=reports,necessary_pairs=pairs,warm=warm))
            return
        mesh=native.read(Path(a.output).parent/'mesh/mesh.json');decision=mesh['decision']
        write(out/'protocol.json',dict(routes=ROUTES,train=[dict(label=l,total_bytes=n,operations=k) for l,n,k in [('warm_small',4*MIB,1),('warm_large',16*MIB,1),('warm_batch',4*MIB,4)]],holdout=dict(route_order=[0,1,2],bytes_each=12*MIB,repeat_modes=[decision['selected_mode'],'legacy'] if a.paired_query_holdout and decision['cached_admitted'] else [decision['selected_mode']]),selection_rule=decision['rule'],model_frozen_before_holdout=True,recalibrate_on_failure=False,nonincreasing_retry_rule='only failing path original three configurations once more in reverse order; keep all; arithmetic mean both observations per configuration; no third run or new size'))
        workers,_=cluster.inventory();budget_snapshot(workers,setup)
        require(mesh['incarnations']==new_state(workers)['incarnations'] and mesh['necessary_pairs']==[[2,4],[4,5]] and len(mesh['warm'])==6,'warm evidence/incarnation missing')
        state=new_state(workers);state['warm_pairs']=['2:4','4:5']
        sources=[]
        for index,(owner,tier,dst) in enumerate(ROUTES):
            workers,_=cluster.inventory();budget_snapshot(workers,setup)
            prefix=setup['session']+f'/source{index}'
            seed=native.synth_adopt(cluster,setup,workers,prefix,owner,index,16*MIB,journal['owned'],journal,out)
            if tier != 'dram':
                target=native.target_from(seed,workers,prefix+'/'+tier,owner,tier,ssd=setup['ssd'][f'n{owner//2}'])
                native.remember(journal['owned'],target,workers);write(out/'journal.json',journal)
                bootstrap=cluster.transfer(native.one_plan(seed,target,workers));require(bootstrap['verification']['passed'],'bootstrap SHA')
                _,rows=cluster.inventory();seed=next(r for r in rows if r['id']==target['id'])
                write(out/f'bootstrap{index}.json',bootstrap)
            sources.append(seed)
        samples=[]
        def measure(label,specs,model=None,frozen=None,prediction_ref=None):
            owned=[];journal['current_targets']=owned
            begin=time.monotonic_ns();telemetry=[]
            fresh,rows=cluster.inventory(compact=True,telemetry=telemetry)
            inventoried=time.monotonic_ns();gate=budget_snapshot(fresh,setup,incremental_reserve=64*MIB)
            gated=time.monotonic_ns();request_plan=make_plan(sources,fresh,setup['session']+'/'+label,specs,owned)
            planned=time.monotonic_ns();prediction=warm_only_estimate(request_plan,fresh,model,state) if model else None
            if prediction_ref:
                require(ref(prediction_ref['path'])==prediction_ref,'preregistered prediction changed')
                expected=native.read(prediction_ref['path'])
                require(expected['plan']==request_plan and expected['prediction']==prediction,'in-window fresh planning differs from preregistered prediction')
            pending=out/(label+'.pending.json')
            write(pending,dict(request_begin_ns=begin,plan=request_plan,prediction=prediction,model_ref=frozen,preregistered_prediction_ref=prediction_ref))
            pending_ref=ref(pending)
            write(out/(label+'.prediction_freeze.json'),dict(pending_prediction_ref=pending_ref,preregistered_prediction_ref=prediction_ref,frozen_model_ref=frozen,freeze_saved_ns=time.monotonic_ns()))
            write(out/'journal.json',journal)
            before=native.ssd_stats(setup);flow=cluster.transfer(request_plan);after=native.ssd_stats(setup)
            require(flow['verification']['passed'] and flow['verification']['checked_bytes']==sum(s[1] for s in specs),'all-byte SHA absent')
            require(ref(pending)==pending_ref,'pending prediction changed during execution')
            if frozen:require(ref(frozen['path'])==frozen,'frozen model changed during execution')
            target_workers={op['dst_w'] for op in request_plan['ops']}
            target_s=max(w['execute_s'] for w in flow['execution'] if w['worker'] in target_workers)
            maximum=max(w['execute_s'] for w in flow['execution'])
            timing=flow['controller_timing'];filled=(timing['all_targets_filled_ns']-begin)/1e9;published=(timing['all_targets_published_ns']-begin)/1e9
            row=dict(label=label,flow=flow,request_timing=dict(clock_domain=CONTROLLER_CLOCK_DOMAIN,request_begin_ns=begin,inventory_end_ns=inventoried,gate_end_ns=gated,plan_end_ns=planned,filled_ns=timing['all_targets_filled_ns'],published_ns=timing['all_targets_published_ns'],request_to_filled_s=filled,request_to_published_s=published),fresh_query_rpc=telemetry,gate=gate,nbytes=sum(s[1] for s in specs),operations=len(specs),target_s=target_s,dispatch_s=max(0.,flow['execute_wall_s']-maximum),dispatch_scope='controller-minus-maximum-worker duration residual',prediction=prediction,frozen_model_ref=frozen,preregistered_prediction_ref=prediction_ref,pending_prediction_ref=pending_ref,ssd_io=native.stats_delta(before,after))
            if len({s[0] for s in specs})==1:
                row['path']=request_plan['ops'][0]['path'];row['service_s']=max(r['elapsed_s'] for w in flow['execution'] for r in w['records'] if r['role'] in ('target','both'))
            if prediction:
                row['predicted_request_to_filled_s']=prediction['estimated_execute_s']+model['full_request_overhead']['to_filled_without_execute_s']
                row['predicted_request_to_published_s']=row['predicted_request_to_filled_s']+model['full_request_overhead']['verify_publish_s']
            write(out/(label+'.json'),row);observe_success(request_plan,fresh,state)
            native.exact_release(cluster,owned,journal,'end_'+label);journal['current_targets']=[];write(out/'journal.json',journal)
            return row
        for index in range(3):
            for label,n,k in [('warm_small',4*MIB,1),('warm_large',16*MIB,1),('warm_batch',4*MIB,4)]:
                row=measure(f'p{index}_{label}',[(index,n//k,j*n//k) for j in range(k)])
                row['label']=label;samples.append(row);write(out/'training.json',samples)
        # A predeclared, bounded second observation resolves only nonpositive target slopes.
        failed_paths=[]
        for index in range(3):
            own=samples[index*3:index*3+3]
            if own[1]['target_s'] <= own[0]['target_s']:
                failed_paths.append(index)
        aggregated=copy.deepcopy(samples)
        repeats=[]
        for index in failed_paths:
            second={}
            for label,n,k in [('warm_batch',4*MIB,4),('warm_large',16*MIB,1),('warm_small',4*MIB,1)]:
                row=measure(f'p{index}_{label}_retry1',[(index,n//k,j*n//k) for j in range(k)])
                row['label']=label;second[label]=row;repeats.append(row);write(out/'training_repeats.json',repeats)
            for row in aggregated[index*3:index*3+3]:
                original=copy.deepcopy(row)
                for key in ('target_s','service_s','dispatch_s'):
                    row[key]=statistics.mean([row[key],second[row['label']][key]])
                row['aggregate_rule']='arithmetic mean of original and one prescribed retry'
                row['observations']=[original,second[row['label']]]
        write(out/'fit_input_observed.json',aggregated)
        workers,_=cluster.inventory();model,diagnostic=fit_warm(workers,aggregated,out)
        if model:
            model['full_request_overhead']=dict(to_filled_without_execute_s=statistics.median(r['request_timing']['request_to_filled_s']-r['flow']['execute_wall_s'] for r in samples),verify_publish_s=statistics.median(r['flow']['verify_and_publish_s'] for r in samples),scope='training medians of identical workflow; public control and full-byte SHA may mask execution errors')
        frozen=dict(model=model,fit_diagnostics=diagnostic,training_ref=ref(out/'training.json'),fit_input_observed_ref=ref(out/'fit_input_observed.json'),fit_inputs_ref=ref(out/'fit_inputs.json'),decision=decision,repeated_paths=failed_paths)
        write(out/'frozen_model.json',frozen);frozen_ref=ref(out/'frozen_model.json');write(out/'model_freeze.json',dict(frozen_model_ref=frozen_ref,freeze_ns=time.monotonic_ns(),holdout_not_executed=True))
        holdout_specs=[(i,12*MIB,0) for i in range(3)]
        holdout_plan=make_plan(sources,workers,setup['session']+'/holdout_prediction',holdout_specs,[])
        preregistered=out/'holdout_preregistered_prediction.json'
        write(preregistered,dict(plan=holdout_plan,prediction=warm_only_estimate(holdout_plan,workers,model,state) if model else None,frozen_model_ref=frozen_ref,saved_ns=time.monotonic_ns()))
        prediction_ref=ref(preregistered)
        validation=[measure('holdout_prediction',holdout_specs,model,frozen_ref,prediction_ref)]
        if a.paired_query_holdout and decision['cached_admitted']:
            set_mode(plan,'legacy',out/'mode_holdout_legacy')
            validation.append(measure('holdout_legacy_control',[(i,12*MIB,0) for i in range(3)]))
        require(ref(out/'frozen_model.json')==frozen_ref,'frozen model changed')
        write(out/'validation.json',validation)
        journal['status']='complete'
        write(out/'summary.json',dict(status='complete',samples=len(samples),validation=len(validation),model_identified=model is not None,fit_diagnostics=diagnostic,selected_mode=decision['selected_mode'],frozen_model_ref=frozen_ref,physical_peak_measured=False))
    except BaseException as exc:
        journal.update(status='failed',failure=repr(exc));raise
    finally:
        try:
            owned={r['id']:r for r in journal['owned']+journal.get('current_targets',[])}
            native.exact_release(cluster,list(owned.values()),journal,'explicit_end_of_run')
            workers,rows=cluster.inventory();require(not rows,'owned run left valid objects');require(all(not w['transactions'] and not any(w['used_bytes'].values()) for w in workers),'runtime usage/transactions remain')
            write(out/'final_inventory.json',dict(workers=workers,replicas=rows,ssd=native.ssd_stats(setup)))
        except BaseException as exc:
            journal['cleanup_failure']=repr(exc)
        write(out/'journal.json',journal)
        if journal.get('cleanup_failure'):raise RuntimeError(journal['cleanup_failure'])
if __name__=='__main__':main()
