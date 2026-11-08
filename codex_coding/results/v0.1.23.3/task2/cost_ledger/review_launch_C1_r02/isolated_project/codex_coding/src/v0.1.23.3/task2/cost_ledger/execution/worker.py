"""Thread-scoped hooks around unchanged native execution and HTTP handlers."""
import argparse
import copy
import importlib.util
import itertools
import os
from pathlib import Path
import socket
import threading
import time
import uuid
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[6]
spec=importlib.util.spec_from_file_location('cost_ledger_inventory_worker',ROOT/'codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/worker.py')
previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
native=previous.original.native
torch=native.torch
TLS=threading.local()
GENERATIONS=itertools.count(1)
CLOCK_DOMAIN='worker/'+socket.gethostname()+'/'+str(os.getpid())+'/'+Path('/proc/sys/kernel/random/boot_id').read_text().strip()+'/monotonic_ns'

class NativeClockProxy:
    def __getattr__(self,name):return getattr(time,name)
    def monotonic(self):
        value=time.monotonic();raw=time.monotonic_ns()
        tape=getattr(TLS,'tape',None)
        if tape is not None:tape['native_clock_samples'].append((value,raw))
        return value

def handler_birth():
    return dict(birth=uuid.uuid4().hex,generation=next(GENERATIONS),born_ns=time.monotonic_ns(),
        ident=threading.get_ident(),native_tid=threading.get_native_id(),name=threading.current_thread().name,
        clock_domain=CLOCK_DOMAIN,pid=os.getpid())

class LedgerHTTPServer(ThreadingHTTPServer):
    def process_request_thread(self,request,client_address):
        TLS.handler=handler_birth()
        try:return super().process_request_thread(request,client_address)
        finally:TLS.handler=None

def install_hooks():
    if getattr(torch,'_cost_ledger_hooks_installed',False):return
    original_copy=torch.Tensor.copy_
    original_sync=torch.cuda.synchronize
    original_set=torch.cuda.set_device
    def copy_(tensor,source,*args,**kwargs):
        tape=getattr(TLS,'tape',None)
        if tape is None:return original_copy(tensor,source,*args,**kwargs)
        start=time.monotonic_ns()
        result=original_copy(tensor,source,*args,**kwargs)
        end=time.monotonic_ns()
        # Sizes and indices are inspected after completion; the primitive submit span stays compact.
        tape['copies'].append((tape['op'],tape['chunk'],start,end,source.numel()))
        return result
    def sync(*args,**kwargs):
        tape=getattr(TLS,'tape',None)
        if tape is None:return original_sync(*args,**kwargs)
        start=time.monotonic_ns();result=original_sync(*args,**kwargs);end=time.monotonic_ns()
        tape['spans'].append(('device_sync',tape['op'],tape['chunk'],start,end,0))
        tape['syncs'].append((tape['op'],tape['chunk'],start,end))
        tape['source_bundle_begin']=end
        return result
    def set_device(*args,**kwargs):
        tape=getattr(TLS,'tape',None)
        if tape is None:return original_set(*args,**kwargs)
        start=time.monotonic_ns();result=original_set(*args,**kwargs);end=time.monotonic_ns()
        tape['spans'].append(('set_device',None,None,start,end,0))
        return result
    torch.Tensor.copy_=copy_
    torch.cuda.synchronize=sync
    torch.cuda.set_device=set_device
    native.time=NativeClockProxy()
    torch._cost_ledger_hooks_installed=True

def partition(begin,end,spans):
    # Explicit measured child spans are independent observations; gaps are not given causal names.
    ordered=sorted(spans,key=lambda row:row['begin_ns'])
    cursor=begin;parts=[]
    for row in ordered:
        if not (cursor<=row['begin_ns']<=row['end_ns']<=end):
            raise ValueError('non-nested/overlapping worker primitive spans')
        if cursor<row['begin_ns']:parts.append(dict(name='unattributed_gap',begin_ns=cursor,end_ns=row['begin_ns']))
        parts.append(row);cursor=row['end_ns']
    if cursor<end:parts.append(dict(name='unattributed_gap',begin_ns=cursor,end_ns=end))
    if sum(p['end_ns']-p['begin_ns'] for p in parts)!=end-begin:raise ValueError('integer partition failed')
    return parts

class LedgerWorker(previous.DiagnosticRuntimeWorker):
    def __init__(self,args):
        self._ledger_allocations={};self._ledger_allocation_counter=0
        super().__init__(args)
    def allocate(self,desc):
        begin=time.monotonic_ns();tensor,owner=super().allocate(desc);end=time.monotonic_ns()
        self._ledger_allocation_counter+=1
        self._ledger_allocations[desc['id']]=dict(id=desc['id'],allocation_generation=self._ledger_allocation_counter,
            allocation_birth=uuid.uuid4().hex,allocate_begin_ns=begin,allocate_end_ns=end,n=desc['n'],tier=desc['tier'],
            writes=[],strategy='native_torch_empty_exact_size',initialization='no_task_target_write',
            owner=self.epoch,clock_domain=CLOCK_DOMAIN)
        return tensor,owner
    def log(self,**row):
        tape=getattr(TLS,'tape',None)
        if tape is None:return self._emit_log(**row)
        begin=time.monotonic_ns();event=row.get('ev')
        if event=='cluster_op_begin':
            tape['op']=row['op_id'];tape['chunk']=-1
            tape['op_begins'].append((row['op_id'],begin,row['n']))
        elif event=='cluster_op_end':tape['op_ends'].append((row['op_id'],begin))
        result=self._emit_log(**row);end=time.monotonic_ns()
        tape['spans'].append(('begin_log' if event=='cluster_op_begin' else 'end_log',tape['op'],None,begin,end,0))
        if event=='cluster_op_begin':tape['source_bundle_begin']=end
        return result
    def _emit_log(self,**row):return super().log(**row)
    def land(self,target,off,source):
        tape=getattr(TLS,'tape',None)
        if tape is None:return super().land(target,off,source)
        begin=time.monotonic_ns();tape['chunk']+=1
        tape['spans'].append(('source_slice_and_loop',tape['op'],tape['chunk'],tape['source_bundle_begin'],begin,0))
        result=super().land(target,off,source);end=time.monotonic_ns()
        n=source.numel()
        tape['spans'].append(('land_submit',tape['op'],tape['chunk'],begin,end,n))
        tape['lands'].append((tape['op'],tape['chunk'],off,n))
        return result
    def prepare(self,body):
        if body.get('cost_ledger_control')=='observer':return self.observer()
        return super().prepare(body)
    def observer(self):
        # CPU-only clock and actual logger diagnostics; no GPU/target/source work.
        empty=[];extra=[];dummy=[]
        for _ in range(64):
            start=time.monotonic_ns();end=time.monotonic_ns();empty.append(end-start)
            start=time.monotonic_ns();time.monotonic_ns();end=time.monotonic_ns();extra.append(end-start)
        for _ in range(8):
            start=time.monotonic_ns();self._emit_log(ev='cost_ledger_dummy_log');dummy.append(time.monotonic_ns()-start)
        def p95(x):return sorted(x)[int(.95*(len(x)-1))]
        return dict(clock_extra_p95_ns=max(0,p95(extra)-p95(empty)),dummy_log_scale_ns=max(dummy)-min(dummy),
            empty_clock_ns=empty,extra_clock_ns=extra,dummy_log_ns=dummy,clock_domain=CLOCK_DOMAIN,
            measurement_handler=getattr(TLS,'handler',None),GPU_calls=0)
    def execute(self,body):
        wrapper_begin=time.monotonic_ns()
        if getattr(TLS,'tape',None) is not None:raise ValueError('nested execution hook')
        run=self.runs[body['tx']];plan=run['plan']
        if plan.get('executor','serial')!='serial' or any(o['src_w']!=self.rank or o['dst_w']!=self.rank for o in plan['ops']):
            raise ValueError('cost ledger is local native serial only')
        if plan.get('profile_execute') or os.environ.get('FLUIDCKPT_EXECUTE_PROFILE_DIR'):raise ValueError('profile mode prohibited')
        identities=[]
        for target in plan['targets']:
            allocation=self._ledger_allocations.get(target['id'])
            identities.append(dict(target_id=target['id'],target=copy.deepcopy(allocation),
                sources=[dict(replica_id=op['replica_id'],generation=self.catalog.entries[op['replica_id']]['generation'],
                    identity=self.catalog.entries[op['replica_id']]['identity'],epoch=self.epoch,
                    history=copy.deepcopy(self._ledger_allocations.get(op['replica_id']))) for op in plan['ops']]))
        tape=dict(op=None,chunk=-1,copies=[],lands=[],syncs=[],spans=[],op_begins=[],op_ends=[],native_clock_samples=[])
        TLS.tape=tape
        # This independent parent interval wraps the complete original method and return/finally.
        begin=time.monotonic_ns()
        try:result=super().execute(body)
        except BaseException:
            end=time.monotonic_ns();TLS.tape=None
            self._emit_log(ev='cost_ledger_execute_failed',tx=body['tx'],begin_ns=begin,end_ns=end,raw_tape=tape)
            raise
        end=time.monotonic_ns();TLS.tape=None
        metadata_begin=time.monotonic_ns()
        spans=[dict(name=name,op_id=op,chunk_index=chunk,begin_ns=a,end_ns=b,bytes=n)
            for name,op,chunk,a,b,n in tape['spans']]
        copies=[dict(op_id=op,chunk_index=c,begin_ns=a,end_ns=b,bytes=n) for op,c,a,b,n in tape['copies']]
        actual=dict(N_mib=sum(r['bytes'] for r in copies)/(1 << 20),K=len(tape['op_begins']),
            chunk_mib=[r['bytes']/(1 << 20) for r in copies],copy_count=len(copies),sync_count=len(tape['syncs']),
            land_count=len(tape['lands']),logical_bytes=sum(n for _,_,n in tape['op_begins']),submitted_copy_bytes=sum(r['bytes'] for r in copies))
        op_chunks={op['op_id']:[r['bytes'] for r in copies if r['op_id']==op['op_id']] for op in plan['ops']}
        clocks=tape['native_clock_samples']
        if len(clocks)!=2+3*len(plan['ops']):raise ValueError('native clock boundary layout changed')
        services=[dict(op_id=op['op_id'],begin_ns=clocks[1+3*i][1],end_ns=clocks[2+3*i][1],
            elapsed_s=result['records'][i]['elapsed_s'],native_float_difference_ns=round(result['records'][i]['elapsed_s']*1e9)-(clocks[2+3*i][1]-clocks[1+3*i][1]))
            for i,op in enumerate(plan['ops'])]
        ledger=dict(clock_domain=CLOCK_DOMAIN,primary_span=dict(begin_ns=begin,end_ns=end,name='complete_native_execute_method'),
            worker_ns=end-begin,primitive_spans=spans,copy_submit_spans=copies,partition=partition(begin,end,spans),
            raw_tape=tape,actual=actual,actual_op_chunk_bytes=op_chunks,actor=getattr(TLS,'handler',None),
            identities=identities,CUDA_events=0,native_execute_s=result['execute_s'],
            native_service_ns=sum(round(r['elapsed_s']*1e9) for r in result['records']),
            service_spans=services,raw_service_ns=sum(s['end_ns']-s['begin_ns'] for s in services),
            raw_log_bounded_service_ns=sum(b-a for (_,a,_),(_,b) in zip(tape['op_begins'],tape['op_ends'])),
            setup_span=dict(begin_ns=wrapper_begin,end_ns=begin),metadata_begin_ns=metadata_begin,
            original_copy_path='inherited fluidckpt.cluster.worker.ClusterWorker.execute/_execute/land')
        for target in plan['targets']:
            allocation=self._ledger_allocations.get(target['id'])
            if allocation is not None:
                allocation['writes'].append(dict(tx=body['tx'],method_begin_ns=begin,method_end_ns=end,
                    copy_chunks=[dict(op_id=op,chunk_index=c,off=off,n=n) for op,c,off,n in tape['lands'] if any(
                        x['op_id']==op and x['target']==target['key'] for x in plan['ops'])]))
                ledger.setdefault('target_histories_after',[]).append(copy.deepcopy(allocation))
        result['cost_ledger']=ledger
        ledger['metadata_end_ns']=time.monotonic_ns()
        ledger['wrapper_observed_end_ns']=time.monotonic_ns()
        return result

def main():
    p=argparse.ArgumentParser()
    for key in ('node','mesh','master','handoff','session'):p.add_argument('--'+key,required=True)
    for key in ('gpu','numa','port-base'):p.add_argument('--'+key,type=int,required=True)
    for tier in ('dram','hbm','ssd'):p.add_argument('--'+tier+'-budget',type=int,default=256 << 20)
    p.add_argument('--inventory-mode-file',type=Path);p.add_argument('--allow-static-cache',action='store_true')
    p.set_defaults(memory_backend='numa_registered',replica_budget_bytes=64 << 20)
    install_hooks();native.ClusterWorker=LedgerWorker;native.ThreadingHTTPServer=LedgerHTTPServer
    native.main(p.parse_args())
if __name__=='__main__':main()
