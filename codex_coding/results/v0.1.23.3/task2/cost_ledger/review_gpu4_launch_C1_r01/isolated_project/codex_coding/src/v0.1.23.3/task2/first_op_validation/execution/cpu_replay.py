"""Actual overlay/native state-machine replay on poisoned bytearray tensor doubles."""
import argparse
import copy
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import ModuleType,SimpleNamespace
from common import ROOT,HERE,RESULTS,PREVIOUS,OLD_RESULTS,CALIBRATION,native,write,ref,require,fresh_output,load
from protocol import BYTES,CELLS,ORDERS,protocol
import runner

TRACE=[]
class Tensor:
    def __init__(self,array,base=None,offset=0):
        self.array=memoryview(array)
        self.base=base if base is not None else array
        self.offset=offset
        self.dtype,self.device,self.shape='torch.uint8','cuda:0',(len(array),)
    def _trace(self,event):
        TRACE.append(dict(event=event,name=threading.current_thread().name,ident=threading.get_ident(),base_id=id(self.base),offset=self.offset,n=self.numel()))
    def __getitem__(self,key):
        self._trace('view')
        require(isinstance(key,slice) and key.step in (None,1),'contiguous slice required')
        return Tensor(self.array[key],self.base,self.offset+(key.start or 0))
    def numel(self): return len(self.array)
    def stride(self): return (1,)
    def storage_offset(self): return self.offset
    def is_contiguous(self): return True
    def data_ptr(self):
        self._trace('metadata')
        return ctypes.addressof(ctypes.c_ubyte.from_buffer(self.array))
    def copy_(self,source,non_blocking=False):
        self._trace('copy')
        self.array[:]=source.array
        require(self.array==source.array,'all copied bytes differ')
        return self

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cpu-replay',action='store_true')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--experiment-stage',choices=('A','B'),default='A')
    a=p.parse_args()
    out=fresh_output(a.out)
    stage=a.experiment_stage
    def cuda_event(name):
        def record(*args):
            TRACE.append(dict(event=name,name=threading.current_thread().name,ident=threading.get_ident()))
            return None
        return record
    fake=ModuleType('torch')
    fake.cuda=SimpleNamespace(set_device=cuda_event('set_device'),synchronize=cuda_event('sync'),
        mem_get_info=lambda *args:(15 << 30,16 << 30),memory_allocated=lambda *args:0,memory_reserved=lambda *args:0,
        current_stream=lambda *args:SimpleNamespace(cuda_stream=0),empty_cache=lambda:None,ipc_collect=lambda:None)
    fake.uint8='torch.uint8'
    fake.get_num_threads=lambda:1
    sys.modules['torch']=fake
    overlay=load('firstop_CPU_actual_overlay',HERE/'worker.py')
    old=OLD_RESULTS/'execution_repeat_actual_r01'
    workers=native.read(old/'mesh/mesh.json')['workers']
    setup=native.read(old/'runtime_setup.json')
    session='first_op_runtime_'+stage+'_cpu_'+out.name
    launch=native.read(PREVIOUS/'launch_plan.json')
    launch.update(session=session,protocol=protocol(stage),experiment_stage=stage,setup_directory=str(PREVIOUS),prediction_ref=None)
    if stage=='B':
        keys=[f'B.cell{cell_id}.{CELLS[cell_id]["executor_mode"]}.R{ordinal}' for cell_id in ORDERS['B'] for ordinal in (1,2)]
        predictions=[dict(accepted=True,prediction_key=key,case=dict(stage='B',cell_id=cell_id,request_ordinal=ordinal,bytes=BYTES,copies=2),
            executor_mode=CELLS[cell_id]['executor_mode'],predictions={name:dict(worker_execute_ns=1,full_ns=1,published_ns=None) for name in ('C0','C1','C2')},
            old_bounded_prediction=None) for key,(cell_id,ordinal) in zip(keys,[(c,o) for c in ORDERS['B'] for o in (1,2)])]
        write(out/'CPU_ONLY_prediction.json',dict(schema_version='first_op_model.v1',frozen=True,CPU_REPLAY_ONLY=True,
            stage='B',expected_prediction_keys=keys,predictions=predictions))
        launch['prediction_ref']=ref(out/'CPU_ONLY_prediction.json')
    launch_path=out/'launch_plan.json'
    write(launch_path,launch)
    setup.update(session=session,valid_until=time.time()+600,evidence_refs={'plan':ref(launch_path)},CPU_REPLAY_ONLY=True)
    setup_path=out/'runtime_setup.json'
    write(setup_path,setup)
    for index,w in enumerate(workers):
        w.update(session=session,epoch='cpu_'+stage+'/'+str(index),replicas=[],used_bytes=dict(hbm=0,dram=0,ssd=0),transactions=[])
    mesh=native.read(old/'mesh/mesh.json')
    mesh.update(workers=workers,incarnations=runner.new_state(workers)['incarnations'],CPU_REPLAY_ONLY=True)
    (out/'mesh').mkdir()
    write(out/'mesh/mesh.json',mesh)
    training=CALIBRATION/'execution_actual_r02/measure'
    synth=native.read(training/'journal.json')['synth'][0]
    oldbootstrap=native.read(training/'bootstrap0.json')
    bootstrap_sha=next(iter(oldbootstrap['verification']['operations'].values()))['source_sha256']
    pattern=bytes(range(256))*((16 << 20)//256)
    from fluidckpt.cluster.catalog import Catalog
    fake_workers=[]
    logs=[]
    state_trace=[]
    for index,w in enumerate(workers):
        cls=overlay.FirstOperationWorker if index==4 else overlay.previous.original.NATIVE_WORKER
        worker=cls.__new__(cls)
        worker.rank,worker.node,worker.gpu,worker.dev=index,w['node'],w['gpu'],'cuda:0'
        worker.epoch,worker.a=w['epoch'],SimpleNamespace(numa=w['numa'])
        worker.runs,worker.catalog={},Catalog(dict(hbm=64 << 20,dram=64 << 20,ssd=64 << 20))
        worker.execution_profile,worker._slope_events,worker.first_cells=None,{},{}
        def log(index=index,**row):
            TRACE.append(dict(event=row['ev'],name=threading.current_thread().name,ident=threading.get_ident()))
            logs.append(dict(worker=index,**row))
        worker.log=log
        worker.allocate=lambda desc:(Tensor(bytearray([0xA7])*desc['n']),None)
        def inventory(compact=False,index=index,worker=worker):
            result=copy.deepcopy(workers[index])
            result.update(replicas=worker.catalog.inventory(compact=compact),used_bytes=worker.catalog.used(),transactions=list(worker.runs))
            return result
        worker.inventory=inventory
        def digest(entry,off,n):
            require(entry['tensor'].array[off:off+n]==pattern[:n],'CPU full logical byte verification differs')
            # Bootstrap validates frozen original metadata, measured data use independent CPU hashes.
            return bootstrap_sha if n==16 << 20 else hashlib.sha256(entry['tensor'].array[off:off+n]).hexdigest()
        worker.extent_digest,worker.target_digest=digest,digest
        fake_workers.append(worker)
    def request(endpoint,operation,body):
        index=next(w['worker'] for w in setup['workers'] if w['endpoint']==endpoint)
        worker=fake_workers[index]
        if operation=='synth':
            require(index==4 and body['seed']==3100 and body['nbytes']==16 << 20,'old source generator schema differs')
            return copy.deepcopy(synth['diagnostics'])
        if operation=='adopt':
            row=copy.deepcopy(synth['row'])
            row.update(id=body['id'],epoch=worker.epoch)
            worker.catalog.reserve(row,lambda:(Tensor(bytearray(pattern)),None))
            worker.catalog.publish(row['id'],True)
            return next(r for r in worker.catalog.inventory() if r['id']==row['id'])
        if operation in ('retire','prepare'):
            result=getattr(worker,operation)(body)
            if operation=='prepare' and body.get('first_op_control')=='open':
                cell=worker.first_cells[body['cell_key']]
                require(all(e['tensor'].array==bytes([0xA7])*BYTES for e in cell['buffers'].values()),'empty fixture secretly written')
            return result
        raise AssertionError(operation)
    class ReplayCluster(runner.Cluster):
        def call_all(self,operation,bodies=None,telemetry=None):
            if operation.startswith('inventory'):
                return [w.inventory(compact=operation.endswith('compact=1')) for w in fake_workers]
            values=[getattr(w,operation)(bodies[index]) for index,w in enumerate(fake_workers)]
            if operation in ('prepare','execute','complete','finish','abort'):
                state_trace.append(dict(operation=operation,phases={str(i):{tx:r['phase'] for tx,r in w.runs.items()} for i,w in enumerate(fake_workers)},
                    catalog4=[{k:e[k] for k in ('id','state','leases')} for e in fake_workers[4].catalog.entries.values()]))
            return values
    runner.Cluster=ReplayCluster
    native.request=request
    native.ssd_stats=lambda setup:copy.deepcopy(native.read(training/'final_inventory.json')['ssd'])
    sys.argv=['runner.py','--setup',str(setup_path),'--plan',str(launch_path),'--output',str(out/'measure'),'--stage','measure']
    runner.main()
    doc=native.read(out/'measure/stage_measurements.json')
    rows=doc['requests']
    require(len(rows)==8 and [r['cell_id'] for r in rows]==[c for c in ORDERS[stage] for _ in range(2)],'fixed order/count differs')
    measured=[t for t in TRACE if t['event']=='copy' and t['name'].startswith('firstop-executor')]
    interventions=[t for t in TRACE if t['event']=='copy' and t['name'].startswith('firstop-prepare')]
    require(len(measured)==16 and len(interventions)==8 and all(t['n']==BYTES for t in measured+interventions),'physical payload count differs')
    require(sum(t['n'] for t in TRACE if t['event']=='copy')==208 << 20,'bootstrap+measure+intervention ledger differs')
    for cell_id in ORDERS[stage]:
        one,two=[r for r in rows if r['cell_id']==cell_id]
        persistent=one['executor_mode']=='P'
        require((one['executor_birth']==two['executor_birth'])==persistent,'actual thread generation differs')
        require(one['prepare_birth']==two['prepare_birth'],'prepare must share independent thread context')
        require(one['executor_birth']!=one['prepare_birth'] and two['executor_birth']!=two['prepare_birth'],'prepare/executor alias')
        require(two['executor_alive_between_requests']==persistent,'P must prove still alive, F must be joined')
        require(one['executor']['joined']!=persistent and two['executor']['joined'],'join policy differs')
        require([op['thread_op_ordinal'] for op in one['ops']]==[1,2],'first request ordinal differs')
        require([op['thread_op_ordinal'] for op in two['ops']]==([3,4] if persistent else [1,2]),'second request ordinal differs')
        for r in (one,two):
            require(sum(r['timing_components'].values())==r['worker_execute_ns'],'worker clock partition not exact')
            require(sum(r['controller_components'].values())+r['controller_execute_ns']==r['full_ns'],'full clock partition not exact')
            require(r['record_total_ns']==sum(op['record_total_ns'] for op in r['ops']),'record totals differ')
            for op in r['ops']:
                require(sum(v['wall_ns'] for v in op['phases'].values())==op['record_total_ns'],'phase telescope differs')
            require(r['ops'][0]['target_view']['data_ptr']==r['ops'][1]['target_view']['data_ptr'],'same target per request required')
        require(one['ops'][0]['target_view']['data_ptr']!=two['ops'][0]['target_view']['data_ptr'],'A/B must remain simultaneously distinct')
    for name in {t['name'] for t in measured}:
        trace=[t for t in TRACE if t['name']==name]
        firstlog=next(i for i,t in enumerate(trace) if t['event']=='cluster_op_begin')
        require([t['event'] for t in trace[:firstlog]]==['set_device'],'executor warmed before actual first op')
        for index,t in enumerate(trace):
            if t['event']=='metadata':
                require(len([v for v in trace[:index] if v['event']=='copy'])>=2,'metadata before both copies')
    require(all(not w.runs and not any(w.catalog.used().values()) and not w.catalog.transactions and not w.first_cells for w in fake_workers),'exact cleanup failed')
    require(not any(t.name.startswith('firstop-') for t in threading.enumerate()),'thread leaked')
    require(len([e for e in logs if e['ev']=='cluster_op_begin' and 'executor_birth' in e])==16,'all measured begin logs required')
    write(out/'state_trace.json',state_trace)
    write(out/'tensor_operation_trace.json',TRACE)
    write(out/'CPU_schema.json',dict(actual_overlay=True,CPU_REPLAY_ONLY=True,canonical_ref=ref(out/'measure/stage_measurements.json')))
    isolated=out/'isolated'
    isolated.mkdir()
    shutil.copyfile(HERE/'mode_file.py',isolated/'mode_file.py')
    target=isolated/'first_op_validation/first_op_runtime_isolated.inventory_mode.json'
    command=[sys.executable,'-I',str(isolated/'mode_file.py'),'--path',str(target)]
    result=subprocess.run(command,cwd=isolated,capture_output=True,text=True,timeout=10)
    require(result.returncode==0 and json.loads(target.read_text())=={'mode':'cached'},'isolated real stdlib CLI failed')
    duplicate=subprocess.run(command,cwd=isolated,capture_output=True,text=True,timeout=10)
    require(duplicate.returncode!=0 and 'FileExistsError' in duplicate.stderr,'isolated CLI overwrote mode file')
    write(out/'isolated_cli.json',dict(command=command,returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,
        copied_files=['mode_file.py'],duplicate_returncode=duplicate.returncode))
    write(out/'qualification.json',dict(passed=True,CPU_REPLAY_ONLY=True,GPU_launches=0,remote_writes=0,
        stage=stage,simulated_requests=8,actual_GPU_measurements=0,canonical_ref=ref(out/'measure/stage_measurements.json'),
        physical_copy_bytes=208 << 20,logical_SHA_bytes=80 << 20,
        digest_policy='bootstrap frozen metadata oracle + independently byte-compared CPU pattern; measurement genuine CPU SHA256',
        checks=['actual native transfer prepare execute verify publish finish chain','F/P real thread birth/liveness/join/op counter',
            'same independent prepare context and scratch control','A/B/scratch poisoned unwritten empty fixture and nonoverlap',
            'catalog budget-accounted BUILDING lease borrow only once; no phase reset','exact phase/worker/controller/full timing closure',
            'all logical measurement bytes independently SHA256 checked','pre-copy actor only set_device; metadata only after two copies',
            'exact fixture/source/transaction/thread cleanup','real isolated stdlib mode --path CLI and noclobber']))
    print(str(out/'qualification.json'))
if __name__=='__main__':
    main()
