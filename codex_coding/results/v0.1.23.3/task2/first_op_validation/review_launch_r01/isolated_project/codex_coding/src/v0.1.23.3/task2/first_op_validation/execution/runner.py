"""Eight fixed requests, normal native publication, and independently leased fixtures."""
import argparse
import copy
import io
import json
from pathlib import Path
import time
from common import ROOT,HERE,RESULTS,OLD_RESULTS,CALIBRATION,LOCAL,native,ops,write,ref,require,budget_snapshot,check_reference,load
from protocol import BYTES,CELLS,ORDERS,SCHEMA,protocol
from fluidckpt.cluster.control import Cluster,CONTROLLER_CLOCK_DOMAIN
from fluidckpt.cluster.service_cost import new_state
from fluidckpt.cluster.planning import validate_plan
prior_runner = load('firstop_immutable_repeat_runner',LOCAL/'repeat_runner.py')

def prediction_contract(launch, setup):
    stage=launch['experiment_stage']
    if stage=='A':
        return None,{}
    reference=launch.get('prediction_ref')
    require(reference is not None,'B requires frozen prediction reference')
    check_reference(reference)
    document=native.read(reference['path'])
    expected=[f'B.cell{cell_id}.{CELLS[cell_id]["executor_mode"]}.R{ordinal}'
              for cell_id in ORDERS['B'] for ordinal in (1,2)]
    require(document.get('schema_version')=='first_op_model.v1' and document.get('frozen') is True,'frozen model schema required')
    require(not document.get('CPU_REPLAY_ONLY') or setup.get('CPU_REPLAY_ONLY'),'CPU predictions forbidden for GPU stage')
    require(document.get('expected_prediction_keys')==expected,'B frozen key order differs')
    predictions=document.get('predictions',[])
    require(len(predictions)==8 and [row.get('prediction_key') for row in predictions]==expected,'eight frozen B predictions required')
    rows={}
    for index,row in enumerate(predictions):
        cell_id=ORDERS['B'][index//2]
        ordinal=index%2+1
        case=row.get('case',{})
        require(row.get('accepted') is True and case.get('stage')=='B' and case.get('cell_id')==cell_id and
                case.get('request_ordinal')==ordinal and case.get('bytes')==BYTES and case.get('copies')==2 and
                row.get('executor_mode')==CELLS[cell_id]['executor_mode'],'B prediction case differs')
        require(set(row.get('predictions',{}))=={'C0','C1','C2'},'all candidate predictions required')
        require(row.get('old_bounded_prediction') is None,'old model is out of domain')
        rows[row['prediction_key']]=row
    return document,rows


def fixture(source,workers,session,cell_id):
    return {label:native.target_from(source,workers,session+'/cell'+str(cell_id)+'/'+label,4,'hbm',off=0,n=BYTES)
            for label in ('A','B','scratch')}

def make_plan(source,workers,cell_key,cell_id,ordinal,buffers):
    plan=native.one_plan(source,buffers['A' if ordinal==1 else 'B'],workers)
    plan['ops'][0]['path']=native.adapter.path_key(4,'hbm',workers[4]['numa'],4,'hbm')
    plan['first_op_validation']=dict(cell_key=cell_key,cell_id=cell_id,request_ordinal=ordinal,**CELLS[cell_id])
    validate_plan(plan)
    return plan

def canonical(case,flow,begin,query_ns,cell_open_ns,gate,preregistration,fixture_evidence):
    owner=flow['execution'][4]
    record=owner['records'][0]
    diagnosis=record['first_op_diagnosis']
    prepare=owner['first_op_prepare']
    executor=owner['executor']
    opsrows=diagnosis['ops']
    require(flow['verification']['passed'] and flow['verification']['checked_bytes']==BYTES,'full SHA required')
    require(record['actual_copy_bytes']==2*BYTES and len(opsrows)==2,'two physical copies required')
    require(diagnosis['CUDA_events']==0 and diagnosis['no_metadata_between_rounds'],'instrumentation contract differs')
    require(all(op['host_residual_ns']==0 for op in opsrows),'op partition must close')
    require(executor['birth']!=prepare['prepare_birth'],'prepare/executor must be distinct generations')
    require(prepare['target_writes_before_measurement']==(1 if case['target_state']=='pretouched' else 0),'target write history differs')
    components=owner['timing_components']
    worker_ns=owner['worker_execute_ns']
    require(sum(components.values())==worker_ns,'worker timing must close')
    timing=flow['controller_timing']
    require(timing['clock_domain']==CONTROLLER_CLOCK_DOMAIN,'controller clock differs')
    controller_ns=round(flow['execute_wall_s']*1e9)
    full_ns=timing['all_targets_filled_ns']-begin
    prepare_ns=round(flow['prepare_rpc_s']*1e9)+cell_open_ns
    query_ns+=round(flow['inventory_rpc_s']*1e9)
    control=dict(query_ns=query_ns,prepare_ns=prepare_ns,full_other_ns=full_ns-controller_ns-query_ns-prepare_ns)
    require(control['full_other_ns']>=0,'full clock partition negative')
    return dict(**case,request_id=flow['tx'],bytes=BYTES,executor_birth=executor['birth'],prepare_birth=prepare['prepare_birth'],
        native_tid=executor['native_tid'],executor_alive_between_requests=executor['alive_between_requests'],
        target_writes_before_measurement=prepare['target_writes_before_measurement'],ops=opsrows,
        record_total_ns=sum(op['record_total_ns'] for op in opsrows),service_ns=round(record['elapsed_s']*1e9),
        worker_execute_ns=worker_ns,controller_execute_ns=controller_ns,controller_residual_ns=controller_ns-worker_ns,
        full_ns=full_ns,published_ns=timing['all_targets_published_ns']-begin,controller_components=control,
        timing_components=components,executor=executor,prepare=prepare,diagnosis=diagnosis,gate=gate,
        fixture=fixture_evidence,preregistration_ref=preregistration,flow=flow,prediction=None,old_model_prediction=None,
        request_begin_ns=begin,clock_scope='local controller request and worker independent durations')

def main():
    parser=argparse.ArgumentParser()
    for key in ('setup','plan','output'):
        parser.add_argument('--'+key,required=True)
    parser.add_argument('--stage',choices=('mesh','measure'),required=True)
    a=parser.parse_args()
    out=Path(a.output)
    out.mkdir(parents=True,exist_ok=False)
    setup,launch=native.read(a.setup),native.read(a.plan)
    stage=launch['experiment_stage']
    require(launch['protocol']==protocol(stage),'predeclared protocol required')
    check_reference(launch['frozen_model_ref'])
    if stage=='B':
        require(launch['prediction_ref'] is not None,'B prediction reference missing')
        check_reference(launch['prediction_ref'])
    native.check_setup(setup)
    frozen_predictions,prediction_rows=prediction_contract(launch,setup)
    cluster=Cluster([w['endpoint'] for w in setup['workers']],{},executor='serial')
    journal=dict(status='running',owned=[],fixture_owned=[],GPU_measurements=0,stage=stage,model_updates=0)
    write(out/'journal.json',journal)
    meshed=a.stage=='measure'
    open_cell=None
    try:
        if a.stage=='mesh':
            require(all(row['files']==0 for row in native.ssd_stats(setup).values()),'unowned SSD files present')
            prior_runner.initialize_mesh(cluster,setup,out)
            meshed=True
            return
        mesh=native.read(out.parent/'mesh/mesh.json')
        workers,rows=cluster.inventory()
        budget_snapshot(workers,setup)
        require(mesh['incarnations']==new_state(workers)['incarnations'],'worker incarnation changed after warm')
        old_workers=native.read(CALIBRATION/'execution_actual_r02/postwarm.inventory.json')
        require(prior_runner.fingerprint(workers)==prior_runner.fingerprint(old_workers),'physical execution configuration differs')
        for spec in ops.specs(Path(launch['setup_directory'])):
            for item in spec['assets']:
                require(native.sha(ROOT/item['relative_path'])==item['sha256'],'frozen source lineage changed')
        prefix=setup['session']+'/source0'
        seed=native.synth_adopt(cluster,setup,workers,prefix,4,0,16 << 20,journal['owned'],journal,out)
        source_target=native.target_from(seed,workers,prefix+'/hbm',4,'hbm')
        native.remember(journal['owned'],source_target,workers)
        write(out/'journal.json',journal)
        bootstrap=cluster.transfer(native.one_plan(seed,source_target,workers))
        write(out/'bootstrap0.json',bootstrap)
        prior_runner.bootstrap_content_matches(bootstrap,native.read(CALIBRATION/'execution_actual_r02/measure/bootstrap0.json'))
        workers,rows=cluster.inventory()
        source=next(row for row in rows if row['id']==source_target['id'])
        write(out/'source_lineage.json',dict(source=source,bootstrap_ref=ref(out/'bootstrap0.json'),
            old_bootstrap_ref=ref(CALIBRATION/'execution_actual_r02/measure/bootstrap0.json'),full16MiB_SHA_equal=True,
            physical_fingerprint=prior_runner.fingerprint(workers),model_used=False))
        registered=[]
        for cell_id in ORDERS[stage]:
            buffers=fixture(source,workers,setup['session'],cell_id)
            cell_key=setup['session']+'/cell'+str(cell_id)
            registered.append(dict(cell_id=cell_id,cell_key=cell_key,buffers=buffers,
                plans=[make_plan(source,workers,cell_key,cell_id,ordinal,buffers) for ordinal in (1,2)]))
        write(out/'preregistration.json',dict(protocol=protocol(stage),registered=registered,saved_ns=time.monotonic_ns(),measurements=0,
            frozen_prediction_ref=launch.get('prediction_ref'),expected_prediction_keys=list(prediction_rows),
            prediction_keys_match_registered_cases=True if stage=='B' else None))
        prereg=ref(out/'preregistration.json')
        observed=native.request(setup['workers'][4]['endpoint'],'prepare',dict(first_op_control='observer'))
        write(out/'observer_check.json',observed)
        results=[]
        clean=[]
        for spec in registered:
            cell_id=spec['cell_id']
            for ordinal in (1,2):
                begin=time.monotonic_ns()
                telemetry=[]
                fresh,_=cluster.inventory(compact=True,telemetry=telemetry)
                query_end=time.monotonic_ns()
                gate=budget_snapshot(fresh,setup,incremental_reserve=(24 << 20) if ordinal==1 else 0)
                cell_open_ns=0
                if ordinal==1:
                    open_begin=time.monotonic_ns()
                    opened=native.request(setup['workers'][4]['endpoint'],'prepare',dict(first_op_control='open',
                        cell_key=spec['cell_key'],buffers=spec['buffers'],**CELLS[cell_id]))
                    cell_open_ns=time.monotonic_ns()-open_begin
                    open_cell=spec['cell_key']
                    for target in spec['buffers'].values():
                        native.remember(journal['fixture_owned'],target,workers)
                    write(out/('cell'+str(cell_id)+'.open.json'),opened)
                plan=make_plan(source,fresh,spec['cell_key'],cell_id,ordinal,spec['buffers'])
                require(plan==spec['plans'][ordinal-1],'fresh plan differs from preregistration')
                check_reference(prereg)
                require(native.read(out/'preregistration.json')['saved_ns']<begin,'preregistration too late')
                write(out/'journal.json',journal)
                flow=cluster.transfer(plan)
                case=dict(cell_id=cell_id,executor_mode=CELLS[cell_id]['executor_mode'],target_state=CELLS[cell_id]['target_state'],
                    request_ordinal=ordinal,order_index=len(results),stage=stage,session_id=setup['session'])
                row=canonical(case,flow,begin,query_end-begin,cell_open_ns,gate,prereg,opened['fixture'])
                if stage=='B':
                    prediction_key=f'B.cell{cell_id}.{CELLS[cell_id]["executor_mode"]}.R{ordinal}'
                    row.update(frozen_prediction_ref=launch['prediction_ref'],prediction_key=prediction_key,
                        prediction=prediction_rows[prediction_key],prediction_preregistered=True)
                row['fresh_query_rpc']=telemetry
                results.append(row)
                write(out/(f'cell{cell_id}_request{ordinal}.json'),row)
                write(out/'measurements.json',results)
                write(out/'stage_measurements.json',dict(schema_version=SCHEMA,stage=stage,session_id=setup['session'],
                    fixed_bytes=BYTES,requests=results,observer_check=observed,CPU_REPLAY_ONLY=setup.get('CPU_REPLAY_ONLY',False),
                    source_lineage_ref=ref(out/'source_lineage.json'),qualification=dict(passed=False,pending_cleanup=True)))
                journal['GPU_measurements']+=1
                write(out/'journal.json',journal)
            closed=native.request(setup['workers'][4]['endpoint'],'prepare',dict(first_op_control='close',cell_key=open_cell))
            require(closed['all_released'],'fixture leases failed to release')
            open_cell=None
            clean.append(closed)
            write(out/(f'cell{cell_id}.close.json'),closed)
        require(len(results)==8,'exactly eight requests required')
        check_reference(launch['frozen_model_ref'])
        if launch.get('prediction_ref'):
            check_reference(launch['prediction_ref'])
        journal['status']='complete'
        write(out/'summary.json',dict(status='complete',requests=8,measurement_copy_bytes=128 << 20,
            intervention_copy_bytes=64 << 20,logical_SHA_bytes=64 << 20,bootstrap_bytes=16 << 20,
            model_updates=0,further_GPU_requests=0,continuous_peak_measured=False))
    except BaseException as exc:
        journal.update(status='failed',failure=repr(exc))
        raise
    finally:
        try:
            if open_cell is not None:
                native.request(setup['workers'][4]['endpoint'],'prepare',dict(first_op_control='close',cell_key=open_cell))
            owned={row['id']:row for row in journal['owned']+journal['fixture_owned']}
            native.exact_release(cluster,list(owned.values()),journal,'explicit_end_of_run')
            workers,rows=cluster.inventory() if meshed else (cluster.call_all('inventory'),[])
            require(not rows and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in workers),'owned objects/transactions remain')
            ssd=native.ssd_stats(setup)
            require(all(row['files']==0 for row in ssd.values()),'SSD files remain')
            write(out/'final_inventory.json',dict(workers=workers,replicas=rows,ssd=ssd))
            if a.stage=='measure' and journal['status']=='complete':
                document=native.read(out/'stage_measurements.json')
                document['qualification']=dict(passed=True,checks=dict(exact_requests=True,full_SHA=True,
                    timing_closure=True,fixture_lease_cleanup=True,prepare_executor_isolation=True),independent_review_pending=True)
                document['cleanup']=clean
                write(out/'stage_measurements.json',document)
        except BaseException as exc:
            journal['cleanup_failure']=repr(exc)
        write(out/'journal.json',journal)
        if journal.get('cleanup_failure') and not journal.get('failure'):
            raise RuntimeError(journal['cleanup_failure'])

if __name__=='__main__':
    import sys
    if '--cpu-replay' in sys.argv:
        from cpu_replay import main as replay
        replay()
    else:
        main()
