"""Read completed r02 artifacts; calculate scope-aware descriptive metrics only."""
import hashlib
import json
from pathlib import Path
import statistics
ROOT=Path(__file__).resolve().parents[6]
BASE=ROOT/'codex_coding/results/v0.1.23.3/task2/cost_calibration'
ACTUAL=BASE/'execution_actual_r02'
OUT=Path(__file__).resolve().parent
MIB=1 << 20

def read(path):return json.loads(path.read_text())
def ref(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
def write(path,data):path.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')

def main():
    parent=read(ACTUAL/'summary.json');measure=read(ACTUAL/'measure/summary.json');validation=read(ACTUAL/'measure/validation.json');training=read(ACTUAL/'measure/training.json');retries=read(ACTUAL/'measure/training_repeats.json');inputs=read(ACTUAL/'measure/fit_input_observed.json');frozen=read(ACTUAL/'measure/frozen_model.json');decision=read(ACTUAL/'mesh/inventory_decision.json');final=read(ACTUAL/'measure/final_inventory.json')
    times=[]
    for row in validation:
        flow=row['flow'];t=row['request_timing'];native=flow['controller_timing'];ops=flow['plan']['ops']
        times.append(dict(label=row['label'],request_filled_ms=t['request_to_filled_s']*1e3,request_published_ms=t['request_to_published_s']*1e3,request_inventory_ms=(t['inventory_end_ns']-t['request_begin_ns'])/1e6,gate_ms=(t['gate_end_ns']-t['inventory_end_ns'])/1e6,plan_ms=(t['plan_end_ns']-t['gate_end_ns'])/1e6,request_to_native_begin_ms=(native['load_begin_ns']-t['request_begin_ns'])/1e6,native_filled_ms=(native['all_targets_filled_ns']-native['load_begin_ns'])/1e6,native_published_ms=(native['all_targets_published_ns']-native['load_begin_ns'])/1e6,native_inventory_ms=flow['inventory_rpc_s']*1e3,prepare_rpc_ms=flow['prepare_rpc_s']*1e3,execute_ms=flow['execute_wall_s']*1e3,verify_publish_ms=flow['verify_and_publish_s']*1e3,maximum_worker_execute_ms=max(w['execute_s'] for w in flow['execution'])*1e3,prediction=row['prediction'],predicted_request_to_filled_s=row.get('predicted_request_to_filled_s'),predicted_request_to_published_s=row.get('predicted_request_to_published_s'),ssd_io=row['ssd_io'],nbytes=row['nbytes'],operations=len(ops)))
    deltas={key:dict(legacy_minus_cached_ms=times[1][key]-times[0][key],cached_reduction_percent=100*(1-times[0][key]/times[1][key])) for key in ('request_filled_ms','request_published_ms','native_filled_ms','native_published_ms','execute_ms')}
    query_delta=times[1]['request_inventory_ms']+times[1]['native_inventory_ms']-times[0]['request_inventory_ms']-times[0]['native_inventory_ms']
    raw_paths={}
    for path in sorted({r['path'] for r in inputs}):
        by={r['label']:r for r in inputs if r['path']==path};sm,la,ba=(by[k] for k in ('warm_small','warm_large','warm_batch'))
        beta=(la['target_s']-sm['target_s'])/(la['nbytes']-sm['nbytes']);raw_op=(ba['target_s']-sm['target_s'])/(ba['operations']-1);op=max(0.,raw_op);raw_request=sm['target_s']-op-beta*sm['nbytes']
        raw_paths[path]=dict(original_target_small_ms=next(r for r in training if r['path']==path and r['label']=='warm_small')['target_s']*1e3,original_target_large_ms=next(r for r in training if r['path']==path and r['label']=='warm_large')['target_s']*1e3,fit_target_small_ms=sm['target_s']*1e3,fit_target_large_ms=la['target_s']*1e3,fit_target_batch_ms=ba['target_s']*1e3,raw_beta_s_per_byte=beta,raw_operation_s=raw_op,operation_if_clamped_s=op,raw_request_s=raw_request,request_if_clamped_s=max(0.,raw_request),would_clamp_operation=raw_op<0,would_clamp_request=raw_request<0,slope_identifiable=beta>0,schema1_path=frozen['fit_diagnostics']['schema1_paths'].get(path),scope='post-run arithmetic of frozen observed inputs; original whole schema2 rejected, these are not an admitted partial model')
    freeze=read(ACTUAL/'measure/model_freeze.json');pred=read(ACTUAL/'measure/holdout_preregistered_prediction.json');first=validation[0]
    same={key:True for key in ('identity','tp','off','n','source_offset','replica_id','generation','source_epoch','src_w','dst_w','kind','path')}
    for key in same:same[key]=all(a[key]==b[key] for a,b in zip(validation[0]['flow']['plan']['ops'],validation[1]['flow']['plan']['ops']))
    targets_differ=all(a['target']!=b['target'] for a,b in zip(validation[0]['flow']['plan']['ops'],validation[1]['flow']['plan']['ops']))
    supervisors=[]
    dram_peaks={}
    for node in ('node28','node30','node27'):
        ds=[]
        for path in (ACTUAL/('artifacts_'+node)/'raw').glob('*/supervisor.summary.json'):
            row=read(path);cleanup=row['cleanup'];ds.extend(int(x['memory.current']) for x in row['samples'] if isinstance(x.get('memory.current'),str) and x['memory.current'].isdigit());supervisors.append(dict(key=row['key'],stop_reason=row['stop_reason'],child_returncode=row['child_returncode'],signals=cleanup['signals'],cleanup_passed=cleanup['passed'],group_empty=not cleanup['pending_group_members'],after_live=cleanup['after'].get('live')))
        dram_peaks[node]=max(ds)
    monitor=read(ACTUAL/'budget_monitor.json');gates=[r['gate'] for r in training+retries+validation]+[read(ACTUAL/'budget_evidence.json')['nodes']]
    hip_upper={node:max(group[node]['hbm_absolute_upper_bytes'] for group in gates+[r['nodes'] for r in monitor]) for node in ('node28','node30','node27')}
    refs=[ACTUAL/'summary.json',ACTUAL/'measure/summary.json',ACTUAL/'measure/training.json',ACTUAL/'measure/training_repeats.json',ACTUAL/'measure/fit_input_observed.json',ACTUAL/'measure/frozen_model.json',ACTUAL/'measure/model_freeze.json',ACTUAL/'measure/holdout_preregistered_prediction.json',ACTUAL/'measure/validation.json',ACTUAL/'measure/final_inventory.json',ACTUAL/'mesh/inventory_decision.json',ACTUAL/'budget_monitor.json',BASE/'execution_ssd_cleanup_r01/summary.json']
    summary=dict(status='actual_complete_with_preregistered_model_unfit',run_status=parent['status'],session_elapsed_s=parent['elapsed_s'],timings=times,deltas=deltas,query_explains_filled_delta_percent=100*query_delta/deltas['request_filled_ms']['legacy_minus_cached_ms'],inventory_decision=decision,calibration=dict(original_logical_requests=len(training),retry_logical_requests=len(retries),training_bytes=sum(r['nbytes'] for r in training),retry_bytes=sum(r['nbytes'] for r in retries),holdout_requests=len(validation),holdout_bytes=sum(r['nbytes'] for r in validation),bootstrap_bytes=sum(read(ACTUAL/'measure'/f'bootstrap{i}.json')['verification']['checked_bytes'] for i in (0,2)),fit_inputs_are_nine_aggregates=len(inputs),observed_cold_samples=0,raw_paths=raw_paths),official_prediction=None,official_accuracy=None,sorting_certified=False,model_identified=False,frozen_model_ref=ref(ACTUAL/'measure/frozen_model.json'),freeze_before_request=freeze['freeze_ns']<first['request_timing']['request_begin_ns'],prediction_preregister_before_request=pred['saved_ns']<first['request_timing']['request_begin_ns'],model_sha_matches=first['frozen_model_ref']==ref(ACTUAL/'measure/frozen_model.json'),pair_content_same=same,target_ids_differ=targets_differ,all_byte_sha_passed=all(r['flow']['verification']['passed'] for r in training+retries+validation),final_catalog_valid_rows=len(final['replicas']),final_used=[w['used_bytes'] for w in final['workers']],final_transactions=[w['transactions'] for w in final['workers']],final_ssd_files={n:s['files'] for n,s in final['ssd'].items()},aggregate_resource_observed=dict(dram_sampled_peak_bytes=dram_peaks,same_hip_absolute_sampled_upper_bytes=hip_upper,hbm_limit_bytes=2 << 30,dram_limit_bytes=6 << 30,peak_complete=False),supervisors=supervisors,collection=parent['collection'],references=[ref(x) for x in refs],limitations=['fixed cached then legacy order, one observation per mode, no statistical certification','request window includes prerequisite inventory/gate/planning and task-observation persistence/SSD status','HIP absolute upper bounds count any selected-device allocations conservatively; no sysfs-HIP subtraction','global model unfit; schema1 two-path effective service is not physical link bandwidth or independent mixed prediction'])
    write(OUT/'summary.json',summary)
    print(json.dumps(dict(status=summary['status'],raw_paths=raw_paths,hip_upper=hip_upper,dram_peaks=dram_peaks,query_share=summary['query_explains_filled_delta_percent'])))
if __name__=='__main__':main()
