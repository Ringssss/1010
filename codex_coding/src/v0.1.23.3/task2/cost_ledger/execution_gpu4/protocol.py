"""Frozen C0/C1/C2 protocol; design values are never actual call counts."""
SCHEMA = 'cost-ledger-v1'
MIB = 1 << 20
CASES = {'L1':[32], 'L2':[64], 'L3':[32,32], 'L4':[48,16], 'L5':[96],
         'L3_repeat':[32,32], 'V1':[16,16,16], 'V2':[40,24,16]}
ORDERS = {'C1':['L3','L1','L4','L2','L5','L3_repeat'], 'C2':['V2','V1']}
MODE = 'native_serial_thread_scoped_noevents_v1'
STATE = dict(configuration_id='singleworker_node27_gpu4_local_native_v1',path='local_hbm',executor='serial',chunk_bytes=32*MIB,source_bytes=96*MIB,
    source_worker=0,target_worker=0,source_tier='hbm',target_tier='hbm',target_mode='replica',
    topology='single_worker_node27_gpu4_world1',inventory_mode='cached',control_mode='compact',
    target_allocation='fresh_exact_size_each_request',target_writes_before=0,
    handler='native_ThreadingHTTPServer_request_thread',queues=0,pretouch=False,
    CUDA_events=0,copy_rounds=1,logger='native_json_stdout',profile_execute=False)
GATES = dict(ledger=dict(exact_integer_closure=True,legacy_float_tolerance_ns=1000),
    drift=dict(floor_ns=20000,anchor_relative=0.10,observation_floor_ns=5000),
    structure=dict(c_E=2,d_E=2,byte32_E=4,fifth_consistency_E=4,max_residual_E=2),
    independent=dict(worker_abs_ns=250000,worker_relative=.20,service_abs_ns=50000,service_relative=.20,
        controller_execute_abs_ns=1000000,controller_execute_relative=.20,full_filled_abs_ns=2000000,full_filled_relative=.20),
    no_more_than_eight_requests=True,published_prediction=None,weights_ready_prediction=None)
def protocol(stage):
    return dict(schema_version=SCHEMA,stage=stage,measurement_mode=MODE,state_contract=STATE,
        order=ORDERS[stage],cases=[dict(id=key,op_mib=CASES[key],N_mib=sum(CASES[key]),K=len(CASES[key]),
            expected_chunk_mib=[min(32,n-pos) for n in CASES[key] for pos in range(0,n,32)]) for key in ORDERS[stage]],
        gates=GATES,training_ids=['L3','L1','L4','L2','L5'],anchor_id='L3_repeat',bootstrap_bytes=96*MIB,
        repetitions=1,source_reused_within_session=True,new_session_holdout=True)
