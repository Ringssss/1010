"""Pure-stdlib fixed experiment contract; importable in isolated deployment."""
BYTES = 8 << 20
CELLS = {1:dict(executor_mode='F',target_state='fresh'),
         2:dict(executor_mode='P',target_state='fresh'),
         3:dict(executor_mode='F',target_state='pretouched'),
         4:dict(executor_mode='P',target_state='pretouched')}
ORDERS = {'A':[1,4,3,2], 'B':[2,3,4,1]}
SCHEMA = 'first_op_validation.v1'
def protocol(stage):
    if stage not in ORDERS:
        raise ValueError('only registered A/B stages permitted')
    return dict(schema_version=SCHEMA,stage=stage,cell_order=ORDERS[stage],cells={str(key):value for key,value in CELLS.items()},
                fixed_bytes=BYTES,source_bytes=16 << 20,source_worker=4,target_worker=4,
                requests=8,copy_rounds_per_request=2,measurement_copy_bytes=128 << 20,
                intervention_copy_bytes=64 << 20,logical_SHA_bytes=64 << 20,
                executor='serial',chunk_bytes=32 << 20,necessary_pairs=[[2,4],[4,5]],
                mode='cached',CUDA_events=0,bootstrap_separate=True,
                target_fixture_bytes=24 << 20,fixture_source_total_bytes=40 << 20,
                full_scope='fresh inventory through filled; includes cell allocation/create/prepare intervention/dispatch/join/diagnostics',
                published_prediction=None,max_stages=2,max_requests=16,old_model_prediction=None)
