"""Six native calibration requests or two gated holdouts with whole-request ledgers."""
import argparse
import copy
from pathlib import Path
import time
from common import ROOT,HERE,native,write,ref,require,check_setup,budget_snapshot,check_reference,specs
from protocol import SCHEMA,MODE,STATE,CASES,ORDERS,protocol,MIB
from controller import LedgerCluster,closed_partition,control
from gate import prediction_contract,predeclared_contract
from fluidckpt.cluster.planning import validate_plan
from fluidckpt.cluster.wire import planning_context

def make_plan(source,workers,session,case_id,endpoints):
    target=native.target_from(source,workers,session+'/'+case_id+'/target',0,'hbm',off=0,n=sum(CASES[case_id])*MIB)
    plan=native.one_plan(source,target,workers);template=plan['ops'][0];ops=[];cursor=0
    for i,n in enumerate(CASES[case_id]):
        ops.append(dict(template,op_id=i,n=n*MIB,off=cursor,source_offset=cursor,kind='local_hbm',path='0:hbm:0->0:hbm'))
        cursor+=n*MIB
    plan.update(ops=ops,control_context=planning_context(workers,endpoints),
        control_mode='compact',cost_ledger_case=case_id)
    validate_plan(plan);return plan

def normalize(case_id,flow,begin,returned,spans,session,gate,source_lineage,preregistered):
    ledger=flow['execution'][0]['cost_ledger'];actual=ledger['actual'];expected=protocol('C1' if case_id.startswith('L') else 'C2')
    case=next(c for c in expected['cases'] if c['id']==case_id)
    require(actual['N_mib']==case['N_mib'] and actual['K']==case['K'] and actual['chunk_mib']==case['expected_chunk_mib'],'actual NKC differs')
    require(actual['copy_count']==actual['sync_count']==actual['land_count']==len(case['expected_chunk_mib']),'actual copy/sync differs')
    require(actual['submitted_copy_bytes']==actual['logical_bytes']==case['N_mib']*MIB,'actual bytes differ')
    require(flow['verification']['passed'] and flow['verification']['checked_bytes']==actual['logical_bytes'],'complete SHA required')
    require(ledger['actor'] is not None,'actual HTTP handler birth missing')
    require(ledger['CUDA_events']==0,'events prohibited')
    require(all(i['target'] is not None and not i['target']['writes'] for i in ledger['identities']),'target was not fresh')
    require(all(len(i['writes'])==1 for i in ledger['target_histories_after']),'target write history differs')
    worker=ledger['primary_span'];require(sum(p['end_ns']-p['begin_ns'] for p in ledger['partition'])==worker['end_ns']-worker['begin_ns'],'worker partition mismatch')
    timing=flow['controller_timing'];controller=flow['cost_ledger_controller'];filled=timing['all_targets_filled_ns'];published=timing['all_targets_published_ns']
    parts=closed_partition(begin,returned,spans)
    measured=dict(service_ns=ledger['native_service_ns'],worker_ns=ledger['worker_ns'],controller_execute_ns=controller['controller_execute_ns'],
        full_filled_ns=filled-begin,published_ns=published-begin,request_return_ns=returned-begin,
        raw_service_ns=ledger['raw_service_ns'])
    require(0<measured['service_ns']<=measured['worker_ns']<=measured['controller_execute_ns']<=measured['full_filled_ns'],'layer timing order failed')
    return dict(case_id=case_id,session_id=session,request_id=flow['tx'],target_id=flow['plan']['targets'][0]['id'],
        measurement_mode=MODE,actual=actual,measured=measured,flags=dict(completed=True,full_sha_passed=True,ledger_closed=True,lifecycle_passed=False),
        worker_ledger=ledger,controller_ledger=dict(clock_domain=control.CONTROLLER_CLOCK_DOMAIN,begin_ns=begin,end_ns=returned,
            filled_ns=filled,published_ns=published,partition=parts,rpc_spans=spans,primary=controller),
        source_lineage=source_lineage,gate=gate,preregistration_ref=preregistered,flow=flow,prediction=None,weights_ready_ns=None)

def main():
    p=argparse.ArgumentParser()
    for key in ('setup','plan','output'):p.add_argument('--'+key,required=True)
    p.add_argument('--stage',choices=('mesh','measure'),required=True)
    a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    setup,launch=native.read(a.setup),native.read(a.plan);check_setup(setup)
    stage=launch['experiment_stage'];require(launch['protocol']==protocol(stage),'fixed protocol changed')
    predeclared_contract(launch['analysis_predeclared_ref'])
    frozen=None
    if stage=='C2':frozen=prediction_contract(launch['prediction_ref'],setup['session'],cpu_only=setup.get('CPU_REPLAY_ONLY',False),review_reference=launch.get('calibration_review_ref'))
    cluster=LedgerCluster([w['endpoint'] for w in setup['workers']],{},executor='serial')
    journal=dict(status='running',owned=[],current_targets=[],GPU_measurements=0,simulated_requests=0,CPU_REPLAY_ONLY=setup.get('CPU_REPLAY_ONLY',False))
    write(out/'journal.json',journal);samples=[];meshed=a.stage=='measure'
    try:
        if a.stage=='mesh':
            raw=cluster.call_all('inventory');require(len(raw)==1 and raw[0]['worker'] in (None,-1),'rank assigned before mesh')
            require(raw[0]['inventory_diagnosis']['mode']=='cached' and not raw[0]['replicas'] and not any(raw[0]['used_bytes'].values()),'empty cached runtime required')
            write(out/'premesh.inventory.json',raw);reports=cluster.call_all('mesh',[{}]);workers,rows=cluster.inventory();budget_snapshot(workers,setup)
            require(not rows,'bootstrap precondition empty catalog')
            write(out/'mesh.json',dict(workers=workers,epochs=[w['epoch'] for w in workers],mesh=reports,warm=[],necessary_pairs=[],world_size=1))
            meshed=True;return
        mesh=native.read(out.parent/'mesh/mesh.json');workers,rows=cluster.inventory();budget_snapshot(workers,setup,incremental_reserve=192*MIB)
        require([w['epoch'] for w in workers]==mesh['epochs'],'worker epoch changed after mesh')
        for spec in specs(Path(launch['setup_directory'])):
            for item in spec['assets']:require(native.sha(ROOT/item['relative_path'])==item['sha256'],'source lineage changed')
        bootstrap_begin=time.monotonic_ns();prefix=setup['session']+'/source'
        seed=native.synth_adopt(cluster,setup,workers,prefix,0,0,96*MIB,journal['owned'],journal,out)
        source_target=native.target_from(seed,workers,prefix+'/hbm',0,'hbm');native.remember(journal['owned'],source_target,workers)
        write(out/'journal.json',journal)
        bootstrap=cluster.transfer(native.one_plan(seed,source_target,workers));bootstrap_end=time.monotonic_ns()
        write(out/'bootstrap.json',dict(begin_ns=bootstrap_begin,end_ns=bootstrap_end,flow=bootstrap,fit_sample=False))
        workers,rows=cluster.inventory();source=next(r for r in rows if r['id']==source_target['id'])
        native.exact_release(cluster,[seed],journal,'release_bootstrap_dram_seed')
        source_lineage=dict(source=source,bootstrap_ref=ref(out/'bootstrap.json'),source_bytes=96*MIB,
            source_sha256=next(iter(bootstrap['verification']['operations'].values()))['target_sha256'],immutable_within_session=True)
        write(out/'source_lineage.json',source_lineage)
        prereg=dict(protocol=protocol(stage),plans={key:make_plan(source,workers,setup['session'],key,cluster.endpoints) for key in ORDERS[stage]},
            saved_ns=time.monotonic_ns(),measurements=0,prediction_ref=launch.get('prediction_ref'))
        write(out/'preregistration.json',prereg);preref=ref(out/'preregistration.json')
        observer=native.request(setup['workers'][0]['endpoint'],'prepare',dict(cost_ledger_control='observer'))
        write(out/'observer_check.json',observer)
        for case_id in ORDERS[stage]:
            cluster.ledger_spans=[]
            begin=time.monotonic_ns();fresh,_=cluster.inventory(compact=True)
            gate_begin=time.monotonic_ns();gate=budget_snapshot(fresh,setup,incremental_reserve=sum(CASES[case_id])*MIB)
            gate_end=time.monotonic_ns();plan=make_plan(source,fresh,setup['session'],case_id,cluster.endpoints);plan_end=time.monotonic_ns()
            require(plan==prereg['plans'][case_id],'fresh plan changed');check_reference(preref)
            native.remember(journal['current_targets'],plan['targets'][0],fresh)
            write(out/'journal.json',journal)
            flow=cluster.transfer(plan);returned=time.monotonic_ns()
            extra=[dict(name='request_budget_gate',begin_ns=gate_begin,end_ns=gate_end),dict(name='request_plan_build',begin_ns=gate_end,end_ns=plan_end)]
            row=normalize(case_id,flow,begin,returned,cluster.ledger_spans+extra,setup['session'],gate,source_lineage,preref)
            if frozen:row['prediction']=next(x for x in frozen['predictions'] if x['case_id']==case_id)
            native.exact_release(cluster,journal['current_targets'],journal,'end_'+case_id)
            row['cleanup']=journal['cleanup'][-1];row['flags']['lifecycle_passed']=True
            journal['current_targets']=[];journal['simulated_requests' if setup.get('CPU_REPLAY_ONLY') else 'GPU_measurements']+=1
            write(out/(case_id+'.json'),row)
            sample={k:copy.deepcopy(row[k]) for k in ('case_id','session_id','request_id','target_id','measurement_mode','actual','measured','flags')}
            sample['raw_refs']=[ref(out/(case_id+'.json'))];samples.append(sample)
            write(out/'stage_measurements.json',dict(schema_version=SCHEMA,stage=stage,measurement_mode=MODE,session_id=setup['session'],
                configuration=dict(STATE,measurement_mode=MODE),state_contract=STATE,samples=samples,observer=observer,
                source_lineage_ref=ref(out/'source_lineage.json'),CPU_REPLAY_ONLY=setup.get('CPU_REPLAY_ONLY',False),
                qualification=dict(passed=False,pending_cleanup=True)))
            write(out/'journal.json',journal)
        journal['status']='complete'
        predeclared_contract(launch['analysis_predeclared_ref'])
    except BaseException as exc:
        journal.update(status='failed',failure=repr(exc));raise
    finally:
        try:
            owned={r['id']:r for r in journal['owned']+journal['current_targets']}
            native.exact_release(cluster,list(owned.values()),journal,'explicit_end_of_run')
            workers,rows=cluster.inventory() if meshed else (cluster.call_all('inventory'),[])
            require(not rows and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in workers),'objects/tx remain')
            write(out/'final_inventory.json',dict(workers=workers,replicas=rows,ssd={}))
            if samples and journal['status']=='complete':
                bundle=native.read(out/'stage_measurements.json');bundle['qualification']=dict(passed=True,independent_review_pending=True)
                bundle['cleanup_ref']=ref(out/'final_inventory.json');write(out/'stage_measurements.json',bundle)
        except BaseException as exc:journal['cleanup_failure']=repr(exc)
        write(out/'journal.json',journal)
        if journal.get('cleanup_failure') and not journal.get('failure'):raise RuntimeError(journal['cleanup_failure'])
if __name__=='__main__':main()
