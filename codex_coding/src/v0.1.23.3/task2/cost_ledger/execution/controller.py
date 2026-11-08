"""Native coordinator with raw adjacent phase accounting and unchanged RPC semantics."""
import threading
import time
from fluidckpt.cluster import control
TLS=threading.local()
class ClockProxy:
    def __getattr__(self,key):return getattr(time,key)
    def monotonic(self):
        value=time.monotonic();raw=time.monotonic_ns()
        tape=getattr(TLS,'native_clocks',None)
        if tape is not None:tape.append((value,raw))
        return value
control.time=ClockProxy()
class LedgerCluster(control.Cluster):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.ledger_spans=[]
    def call_all(self,operation,bodies=None,telemetry=None):
        begin=time.monotonic_ns()
        result=super().call_all(operation,bodies,telemetry)
        end=time.monotonic_ns()
        self.ledger_spans.append(dict(name='rpc_'+operation,begin_ns=begin,end_ns=end,clock_domain=control.CONTROLLER_CLOCK_DOMAIN))
        return result
    def transfer(self,*args,**kwargs):
        TLS.native_clocks=[]
        begin=time.monotonic_ns()
        try:result=super().transfer(*args,**kwargs)
        finally:
            clocks=TLS.native_clocks;TLS.native_clocks=None
        end=time.monotonic_ns()
        if len(clocks)!=5:raise ValueError('native controller timing boundary layout changed')
        result['cost_ledger_controller']=dict(transfer_method_span=dict(begin_ns=begin,end_ns=end),native_clocks=clocks,
            inventory_checked_ns=clocks[1][1],prepared_ns=clocks[2][1],
            controller_execute_ns=result['controller_timing']['all_targets_filled_ns']-clocks[2][1],
            native_execute_wall_ns=round(result['execute_wall_s']*1e9),
            clock_domain=control.CONTROLLER_CLOCK_DOMAIN)
        return result

def closed_partition(begin,end,spans):
    cursor=begin;parts=[]
    for span in sorted(spans,key=lambda row:row['begin_ns']):
        a,b=span['begin_ns'],span['end_ns']
        if not cursor<=a<=b<=end:raise ValueError('controller spans overlap or escape parent')
        if cursor<a:parts.append(dict(name='controller_unattributed_gap',begin_ns=cursor,end_ns=a))
        parts.append(span);cursor=b
    if cursor<end:parts.append(dict(name='controller_unattributed_gap',begin_ns=cursor,end_ns=end))
    if sum(p['end_ns']-p['begin_ns'] for p in parts)!=end-begin:raise ValueError('controller partition mismatch')
    return parts
