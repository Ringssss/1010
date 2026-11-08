"""Fixed local HBM protocol with real thread generations and cell allocation leases."""
import argparse
import importlib.util
import os
import json
from pathlib import Path
import queue
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[6]
LOCAL = ROOT / 'codex_coding/src/v0.1.23.3/task2/local_slope/execution'
spec = importlib.util.spec_from_file_location('firstop_native_overlay', LOCAL / 'worker.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
native, torch = previous.native, previous.torch
N = 8 << 20

class Actor:
    """One task queue for both fresh and persistent execution; no worker lock acquisition."""
    def __init__(self, name):
        self.birth = uuid.uuid4().hex
        self.inbox = queue.Queue(maxsize=1)
        self.op_count = 0
        self.thread = threading.Thread(target=self._loop, name=name+'-'+self.birth[:8], daemon=True)
        self.created_ns = time.monotonic_ns()
        self.thread.start()
        self.start_return_ns = time.monotonic_ns()
    def _loop(self):
        self.native_tid = threading.get_native_id()
        self.ident = threading.get_ident()
        self.entered_ns = time.monotonic_ns()
        while True:
            task = self.inbox.get()
            if task is None:
                self.exit_ns = time.monotonic_ns()
                return
            function, result, queued_ns = task
            pop_ns = time.monotonic_ns()
            try:
                result.put((True, function(self), queued_ns, pop_ns))
            except BaseException as exc:
                result.put((False, exc, queued_ns, pop_ns))
    def call(self, function):
        result = queue.Queue(maxsize=1)
        queued_ns = time.monotonic_ns()
        self.inbox.put((function,result,queued_ns))
        ok, value, queued_ns, pop_ns = result.get()
        returned_ns = time.monotonic_ns()
        if not ok:
            raise value
        return value, dict(enqueue_ns=queued_ns,pop_ns=pop_ns,return_ns=returned_ns,
                           queue_wait_ns=pop_ns-queued_ns,queue_response_ns=returned_ns-pop_ns)
    def close(self):
        begin = time.monotonic_ns()
        self.inbox.put(None)
        self.thread.join(timeout=30)
        end = time.monotonic_ns()
        if self.thread.is_alive():
            raise RuntimeError('executor did not join')
        return dict(join_begin_ns=begin,join_end_ns=end,join_ns=end-begin,
                    exit_ns=self.exit_ns,joined=True,alive_after_join=False)

class FirstOperationWorker(previous.LocalSlopeWorker):
    def __init__(self,args):
        super().__init__(args)
        self.first_cells = {}
    def _cells(self):
        if not hasattr(self,'first_cells'):
            self.first_cells = {}
        return self.first_cells
    def prepare(self, body):
        if 'first_op_control' in body:
            return self._cell_control(body)
        config = body['plan'].get('first_op_validation')
        if config is None:
            return previous.original.NATIVE_WORKER.prepare(self,body)
        if self.rank != 4:
            return previous.original.NATIVE_WORKER.prepare(self,body)
        cell = self._cells()[config['cell_key']]
        ordinal = config['request_ordinal']
        if ordinal != len(cell['consumed'])+1 or ordinal not in (1,2):
            raise ValueError('request sequence/target reuse rejected')
        op = body['plan']['ops'][0]
        descriptor = body['plan']['targets'][0]
        key = 'A' if ordinal == 1 else 'B'
        target = cell['buffers'][key]
        source = self.catalog.entries[op['replica_id']]
        if (len(body['plan']['ops']),self.rank,op['src_w'],op['dst_w'],op['kind'],
            source['tier'],source['n'],op['n'],op['source_offset'],op['off']) != (1,4,4,4,'local_hbm','hbm',16 << 20,N,0,0):
            raise ValueError('fixed 8MiB same-worker source contract required')
        if body['plan']['executor'] != 'serial' or body['plan']['chunk_bytes'] != 32 << 20:
            raise ValueError('frozen serial chunk contract required')
        if config['executor_mode'] != cell['mode'] or config['target_state'] != cell['state']:
            raise ValueError('cell condition changed')
        if descriptor['id'] != target['id'] or target['state'] != 'BUILDING' or cell['writes'][key] != 0:
            raise ValueError('target must be an unconsumed leased BUILDING allocation')
        # Only this already-reserved target is borrowed; native lifecycle is never reset.
        reserve = self.catalog.reserve
        def borrow(desc, allocate):
            if desc['id'] != target['id']:
                raise ValueError('diagnostic prepare cannot allocate another target')
            for name,value in desc.items():
                if target.get(name) != value:
                    raise ValueError('borrowed target descriptor mismatch '+name)
            if target['leases'] != 1:
                raise ValueError('cell target lease missing')
            return target
        self.catalog.reserve = borrow
        try:
            result = previous.original.NATIVE_WORKER.prepare(self,body)
        finally:
            self.catalog.reserve = reserve
        try:
            def intervention(actor):
                begin = time.monotonic_ns()
                torch.cuda.set_device(self.gpu)
                destination = cell['buffers'][key if cell['state']=='pretouched' else 'scratch']
                destination['tensor'][:N].copy_(source['tensor'][:N], non_blocking=True)
                torch.cuda.synchronize(self.dev)
                end = time.monotonic_ns()
                cell['writes'][key if cell['state']=='pretouched' else 'scratch'] += 1
                return dict(birth=actor.birth,native_tid=actor.native_tid,thread_ident=actor.ident,
                            begin_ns=begin,end_ns=end,wall_ns=end-begin,bytes=N,
                            target_id=destination['id'],kind='pretouch' if cell['state']=='pretouched' else 'scratch')
            evidence, dispatch = cell['prepare_actor'].call(intervention)
            cell['consumed'].append(key)
            run = self.runs[body['tx']]
            run['first_op_prepare'] = dict(intervention=evidence,queue=dispatch,prepare_birth=cell['prepare_actor'].birth,
                target_writes_before_measurement=cell['writes'][key],target_allocation_uuid=cell['allocations'][key],
                target_lease_count=target['leases'],fixture=cell['fixture'])
            return dict(result,first_op_prepare=run['first_op_prepare'])
        except BaseException:
            self.abort({'tx':body['tx']})
            raise
    def _cell_control(self,body):
        if self.rank != 4:
            raise ValueError('cell control only worker4')
        cells = self._cells()
        if body['first_op_control'] == 'observer':
            actor = Actor('firstop-independent-cpu-observer')
            try:
                def sample(a):
                    paired, logs = [], []
                    for index in range(64):
                        begin = time.monotonic_ns()
                        time.thread_time_ns()
                        middle = time.monotonic_ns()
                        time.thread_time_ns()
                        end = time.monotonic_ns()
                        paired.append(max(0,end-middle))
                        begin = time.monotonic_ns()
                        self.log(ev='firstop_cpu_observer_dummy',src=4,dst=4,n=N,kind='local_hbm',observer_sample=index)
                        logs.append(time.monotonic_ns()-begin)
                    percentile = lambda values,p:sorted(values)[int((len(values)-1)*p)]
                    median = percentile(logs,.5)
                    return dict(empty_paired_clock_extra_p95_ns=percentile(paired,.95),
                        dummy_log_p95_ns=percentile(logs,.95),
                        dummy_log_fluctuation_p95_ns=percentile([abs(v-median) for v in logs],.95),
                        raw_paired_clock_extra_ns=paired,raw_dummy_log_ns=logs,samples=64,
                        birth=a.birth,native_tid=a.native_tid,pid=os.getpid(),worker=4,CPU_ONLY=True,
                        context='independent actor in actual worker process, before matrix; native logger',
                        no_gpu_operations_in_observer_actor=True,subtracted_from_measurements=False)
                result,dispatch = actor.call(sample)
            finally:
                joined = actor.close()
            return dict(result,queue=dispatch,**joined)
        key = body['cell_key']
        if body['first_op_control'] == 'open':
            if key in cells or cells:
                raise ValueError('one distinct sequential cell required')
            if body['executor_mode'] not in ('F','P') or body['target_state'] not in ('fresh','pretouched'):
                raise ValueError('unknown condition')
            cell = dict(mode=body['executor_mode'],state=body['target_state'],buffers={},writes={'A':0,'B':0,'scratch':0},
                        allocations={},consumed=[],executor=None,history=[])
            cells[key] = cell
            try:
                for label,descriptor in body['buffers'].items():
                    if label not in ('A','B','scratch') or descriptor['n'] != N or descriptor['tier'] != 'hbm':
                        raise ValueError('identical A/B/scratch fixture required')
                    desc = {k:descriptor[k] for k in ('id','identity','tp','off','n','tier','meta','index')}
                    desc.update(worker=4,node=self.node,gpu=self.gpu,numa=descriptor.get('numa',self.a.numa),epoch=self.epoch)
                    entry = self.catalog.reserve(desc,lambda desc=desc:self.allocate(desc))
                    # A real lease holds BUILDING and subsequently VALID/RETIRING buffers until cell close.
                    entry['leases'] += 1
                    cell['buffers'][label] = entry
                    cell['allocations'][label] = uuid.uuid4().hex
                if set(cell['buffers']) != {'A','B','scratch'}:
                    raise ValueError('all three fixture buffers required')
                ranges = {k:(v['tensor'].data_ptr(),v['tensor'].data_ptr()+N) for k,v in cell['buffers'].items()}
                pairs = [('A','B'),('A','scratch'),('B','scratch')]
                if not all(ranges[a][1]<=ranges[b][0] or ranges[b][1]<=ranges[a][0] for a,b in pairs):
                    raise ValueError('fixture addresses overlap')
                cell['prepare_actor'] = Actor('firstop-prepare')
                cell['fixture'] = dict(ranges=ranges,allocation_uuids=cell['allocations'],
                    simultaneous_alive=True,allocated_bytes=3*N,lease_counts={k:v['leases'] for k,v in cell['buffers'].items()},
                    prepare_birth=cell['prepare_actor'].birth,pid=os.getpid(),process_epoch=self.epoch,
                    allocation_writes={'A':0,'B':0,'scratch':0},allocation_method='native empty via allocate, no zero/init')
                return dict(opened=True,cell_key=key,fixture=cell['fixture'])
            except BaseException:
                self._close_cell(key)
                raise
        if body['first_op_control'] == 'close':
            return self._close_cell(key)
        raise ValueError('unknown cell control')
    def _close_cell(self,key):
        cell = self._cells().pop(key)
        joins = []
        for actor in (cell.get('executor'),cell.get('prepare_actor')):
            if actor is not None and actor.thread.is_alive():
                joins.append(dict(birth=actor.birth,**actor.close()))
        for entry in cell['buffers'].values():
            entry['leases'] -= 1
            if entry['leases'] != 0:
                raise ValueError('unexpected remaining fixture leases')
            self.catalog.retire(entry['id'])
        return dict(closed=True,cell_key=key,joins=joins,writes=cell['writes'],history=cell['history'],
                    all_released=all(e['state']=='RELEASED' for e in cell['buffers'].values()))
    def execute(self,body):
        run = self.runs[body['tx']]
        if 'first_op_validation' not in run['plan'] or self.rank != 4:
            return previous.original.NATIVE_WORKER.execute(self,body)
        begin = time.monotonic_ns()
        config = run['plan']['first_op_validation']
        cell = self._cells()[config['cell_key']]
        handler_thread = threading.current_thread()
        if not hasattr(handler_thread, 'first_op_handler_birth'):
            handler_thread.first_op_handler_birth = uuid.uuid4().hex
        handler_birth = handler_thread.first_op_handler_birth
        old = cell['executor']
        alive_between = bool(old is not None and old.thread.is_alive())
        if cell['mode']=='F' or old is None:
            actor = Actor('firstop-executor')
            cell['executor'] = actor
            create_ns = actor.start_return_ns-actor.created_ns
        else:
            actor,create_ns = old,0
        try:
            result, dispatch = actor.call(lambda a:self._measured_execute(body,a))
        finally:
            if cell['mode']=='F' or config['request_ordinal']==2:
                joined = actor.close()
            else:
                joined = dict(join_ns=0,joined=False,alive_after_join=actor.thread.is_alive())
        end = time.monotonic_ns()
        internal = result.pop('_actual_executor_ns')
        result.update(execute_s=(end-begin)/1e9,worker_execute_ns=end-begin,
            handler=dict(birth=handler_birth,native_tid=threading.get_native_id(),thread_ident=threading.get_ident()),
            executor=dict(birth=actor.birth,native_tid=actor.native_tid,thread_ident=actor.ident,
                created_ns=actor.created_ns,entered_ns=actor.entered_ns,create_ns=create_ns,
                op_count_after=actor.op_count,alive_between_requests=alive_between,queue=dispatch,**joined))
        result['timing_components']['dispatch_create_join_ns'] = end-begin-internal
        if sum(result['timing_components'].values()) != end-begin:
            raise RuntimeError('worker timing partition does not close')
        result['first_op_prepare'] = run['first_op_prepare']
        cell['history'].append(dict(request_id=body['tx'],executor_birth=actor.birth,ordinal=config['request_ordinal'],
                                   alive_between_requests=alive_between,joined=joined['joined']))
        return result
    def _measured_execute(self,body,actor):
        # No torch/view/log/query has run on this actor before this counted device selection.
        begin = time.monotonic_ns()
        torch.cuda.set_device(self.gpu)
        device_end = time.monotonic_ns()
        run = self.runs[body['tx']]
        if run['phase'] != 'PREPARED':
            raise ValueError('transaction must be PREPARED')
        run['phase'] = 'EXECUTING'
        config = run['plan']['first_op_validation']
        op = run['plan']['ops'][0]
        source = self.catalog.entries[op['replica_id']]
        target = run['targets'][op['target']]
        raw, views, ordinals = [],[],[]
        service_begin = time.monotonic_ns()
        try:
            for index in range(2):
                actor.op_count += 1
                ordinals.append(actor.op_count)
                stamps = [previous.clocks()]
                self.log(ev='cluster_op_begin',tx=body['tx'],op_id=op['op_id'],src=4,dst=4,n=N,kind='local_hbm',
                         repeat_round=index,executor_birth=actor.birth,thread_op_ordinal=actor.op_count)
                stamps.append(previous.clocks())
                view = source['tensor'][0:N]
                stamps.append(previous.clocks())
                destination = target['tensor'][0:view.numel()]
                stamps.append(previous.clocks())
                destination.copy_(view,non_blocking=True)
                stamps.append(previous.clocks())
                torch.cuda.synchronize(self.dev)
                stamps.append(previous.clocks())
                raw.append(stamps)
                views.append((view,destination))
                self.log(ev='cluster_op_end',tx=body['tx'],op_id=op['op_id'],repeat_round=index,
                         elapsed_s=(stamps[-1][0]-stamps[0][0])/1e9,executor_birth=actor.birth)
            service_end = time.monotonic_ns()
            cell = self._cells()[config['cell_key']]
            label = 'A' if config['request_ordinal']==1 else 'B'
            writes_before = cell['writes'][label]
            cell['writes'][label] += 2
            record = dict(op_id=op['op_id'],role='both',replica_id=op['replica_id'],bytes=N,kind='local_hbm',
                          elapsed_s=(service_end-service_begin)/1e9,pipeline={},copy_rounds=2,actual_copy_bytes=2*N,logical_SHA_bytes=N)
            run.update(phase='FILLED',records=[record])
            self.record_targets(run,'target_copy_complete')
            post_begin = time.monotonic_ns()
            names = ('begin_log','source_slice','target_slice','copy_submit','sync')
            measured = []
            for index,stamps in enumerate(raw):
                phases = {name:dict(wall_ns=b[0]-a[0],cpu_ns=b[1]-a[1]) for name,a,b in zip(names,stamps,stamps[1:])}
                measured.append(dict(ordinal=index+1,thread_op_ordinal=ordinals[index],executor_birth=actor.birth,
                    prepare_birth=run['first_op_prepare']['prepare_birth'],native_tid=actor.native_tid,
                    begin_ns=stamps[0][0],end_ns=stamps[-1][0],raw_clocks=stamps,record_total_ns=stamps[-1][0]-stamps[0][0],
                    record_cpu_ns=stamps[-1][1]-stamps[0][1],phases=phases,bytes=N,
                    host_residual_ns=stamps[-1][0]-stamps[0][0]-sum(p['wall_ns'] for p in phases.values()),
                    source_view=previous.tensor_metadata(views[index][0]),target_view=previous.tensor_metadata(views[index][1])))
            diagnosis = dict(ops=measured,CUDA_events=0,metadata_after_both_rounds=True,
                target_writes_before_measurement=writes_before,stream_after_both_rounds=int(torch.cuda.current_stream(self.dev).cuda_stream),
                memory_after_both_rounds=previous.memory_metadata(self.dev),source=previous.tensor_metadata(source['tensor']),
                target=previous.tensor_metadata(target['tensor']),source_identity={k:source[k] for k in ('id','identity','epoch','generation','off','n')},
                process_pid=os.getpid(),process_epoch=self.epoch,
                process_start_ticks=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]),
                cpu_affinity=sorted(os.sched_getaffinity(0)),numa=self.a.numa,
                torch_num_threads=torch.get_num_threads(),
                process_history='mesh/warm24,45/source bootstrap plus matching independent prepare copy before every request',
                prepare_executor_distinct=actor.birth!=run['first_op_prepare']['prepare_birth'],
                current_request_pre_copy_gpu_operations=['set_device'],
                prior_request_count=(ordinals[0]-1)//2,op_count_before=ordinals[0]-1,service_begin_ns=service_begin,service_end_ns=service_end,
                logger_backend='unchanged native self.log',clock_domain='worker monotonic_ns/thread_time_ns',
                no_metadata_between_rounds=True)
            record['first_op_diagnosis'] = diagnosis
            end = time.monotonic_ns()
            total_records = sum(r['record_total_ns'] for r in measured)
            components = dict(record_total_ns=total_records,set_device_ns=device_end-begin,
                record_gap_ns=service_end-service_begin-total_records,postdiagnosis_ns=end-post_begin,
                dispatch_create_join_ns=0,worker_other_ns=(end-begin)-(total_records+device_end-begin+
                    service_end-service_begin-total_records+end-post_begin))
            return dict(worker=4,records=[record],executor='serial',staging_bytes=native.STAGING_BYTES,
                chunk_bytes=32 << 20,timing_components=components,_actual_executor_ns=end-begin,
                executor_source='firstop_same_queue_local_slice_copy_sync')
        except BaseException:
            run['phase']='FAILED'
            raise

def main():
    parser=argparse.ArgumentParser()
    for key in ('node','mesh','master','handoff','session'):
        parser.add_argument('--'+key,required=True)
    for key in ('gpu','port-base','numa'):
        parser.add_argument('--'+key,type=int,required=True)
    for tier in ('dram','hbm','ssd'):
        parser.add_argument('--'+tier+'-budget',type=int,default=64 << 20)
    parser.add_argument('--inventory-mode-file',type=Path)
    parser.add_argument('--allow-static-cache',action='store_true')
    parser.set_defaults(memory_backend='numa_registered',replica_budget_bytes=64 << 20)
    native.ClusterWorker=FirstOperationWorker
    native.main(parser.parse_args())
if __name__=='__main__':
    main()
