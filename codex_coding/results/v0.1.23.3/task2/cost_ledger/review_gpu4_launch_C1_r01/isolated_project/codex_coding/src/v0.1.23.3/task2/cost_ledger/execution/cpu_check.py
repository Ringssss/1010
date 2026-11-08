"""Replay actual native HTTP/worker/catalog methods using independently traced CPU bytes."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import ModuleType,SimpleNamespace
from common import ROOT,HERE,RESULTS,PRIOR,load,native,write,ref,require,fresh_output
from protocol import MIB,ORDERS,CASES,protocol
TRACE=[]

class Tensor:
    def __init__(self,array,base=None,offset=0):
        self.array=memoryview(array);self.base=base if base is not None else array;self.offset=offset
    def __getitem__(self,key):
        require(isinstance(key,slice) and key.step in (None,1),'CPU contiguous slices only')
        return Tensor(self.array[key],self.base,self.offset+(key.start or 0))
    def numel(self):return len(self.array)
    def copy_(self,source,non_blocking=False):
        TRACE.append(dict(event='copy',begin_ns=time.monotonic_ns(),source_base=id(source.base),target_base=id(self.base),
            source_offset=source.offset,target_offset=self.offset,n=source.numel(),non_blocking=non_blocking,
            native_tid=threading.get_native_id()))
        self.array[:]=source.array
        require(self.array==source.array,'real CPU byte copy failed')
        TRACE[-1]['end_ns']=time.monotonic_ns();return self
    def cpu(self):return self
    def numpy(self):return self
    def tobytes(self):return bytes(self.array)

class Owner:
    def __init__(self,n):self.tensor=Tensor(bytearray([0xa5])*n);self.registered=True;self.numa=0
    def info(self):return dict(backend='numa_registered',numa=0,sampled_page_distribution={'0':1.0},placement_samples=256,closed=False)
    def close(self):self.tensor=None

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=fresh_output(a.out)
    fake=ModuleType('torch');fake.Tensor=Tensor;fake.uint8='uint8';fake.device=lambda *a:'cuda:0';fake.set_num_threads=lambda *a:None
    def record(name):
        def call(*a,**kw):TRACE.append(dict(event=name,ns=time.monotonic_ns(),native_tid=threading.get_native_id(),args=list(a),kwargs={k:(v if isinstance(v,(str,int,float,bool,type(None))) else str(v)) for k,v in kw.items()}))
        return call
    fake.cuda=SimpleNamespace(set_device=record('set_device'),synchronize=record('sync'),empty_cache=record('empty_cache'),ipc_collect=record('ipc_collect'))
    fake.cuda.Event=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('GPU events prohibited'))
    fake.cuda.mem_get_info=lambda *a:(15 << 30,16 << 30)
    fake.empty=lambda n,**kw:Tensor(bytearray([0xa5])*n)
    fake.Generator=lambda:SimpleNamespace(manual_seed=lambda seed:SimpleNamespace(seed=seed))
    fake.randint=lambda low,high,size,**kw:Tensor(bytearray(bytes((kw['generator'].seed+i)%256 for i in range(256))*(size[0]//256)))
    fake.distributed=SimpleNamespace(init_process_group=record('init_process_group'),new_group=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('world1 pair group prohibited')),
        broadcast=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('local broadcast prohibited')))
    sys.modules['torch']=fake
    worker_module=load('cost_ledger_actual_CPU_worker',HERE/'worker.py');worker_module.install_hooks()
    instances=[];boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    class CPUWorker(worker_module.LedgerWorker):
        def __init__(self,args):super().__init__(args);instances.append(self)
        def allocate_host(self,nbytes,numa=None):return Owner(nbytes)
        def checksum(self,snap,tp):return dict(sum=0,GiB=self.shards[snap,tp].nbytes/(1 << 30),CPU_REPLAY_ONLY=True)
        def inventory(self,compact=False):
            used=self.catalog.used();gpu_used=used['hbm']+(64*MIB if self.dist_ready else 0)
            return dict(worker=self.rank,node='node27',gpu=0,numa=0,epoch=self.epoch,session=self.a.session,pid=12345,
                capabilities=['local_hbm','local_dram'],budget_bytes=self.catalog.budgets,used_bytes=used,
                replicas=self.catalog.inventory(compact),transactions=list(self.runs),transaction_phases={tx:r['phase'] for tx,r in self.runs.items()},
                staging_bytes=64*MIB,hbm_total_bytes=16 << 30,headroom_bytes={'hbm':(16 << 30)-gpu_used,'dram':8 << 30},
                runtime_identity=dict(boot_id=boot,pid=12345,start_ticks=123,actual_bdf='0000:09:00.0',pci_numa_node=0,
                    cgroup_memory_current_bytes=256*MIB,cgroup_memory_max_bytes=8 << 30),inventory_diagnosis={'mode':'cached'},CPU_REPLAY_ONLY=True)
    servers=[]
    def server_factory(address,handler):
        server=worker_module.LedgerHTTPServer(('127.0.0.1',0),handler);servers.append(server);return server
    worker_module.native.ClusterWorker=CPUWorker;worker_module.native.ThreadingHTTPServer=server_factory
    args=SimpleNamespace(node='node27',gpu=0,numa=0,port_base=0,mesh='node27:0-0',master='127.0.0.1:1',handoff=str(out/'handoff'),
        session='cost_ledger_runtime_C1_cpu_'+out.name,dram_budget=256*MIB,hbm_budget=384*MIB,ssd_budget=64*MIB,
        inventory_mode_file=None,allow_static_cache=True,memory_backend='numa_registered',replica_budget_bytes=64*MIB)
    worker_thread=threading.Thread(target=lambda:worker_module.native.main(args),daemon=True);worker_thread.start()
    deadline=time.monotonic()+10
    while not servers and time.monotonic()<deadline:time.sleep(.01)
    require(servers,'CPU HTTP startup failed');endpoint='http://127.0.0.1:'+str(servers[0].server_port)
    runtime_setup=dict(purpose='physical',session=args.session,valid_until=time.time()+300,ssd={},evidence_refs={},CPU_REPLAY_ONLY=True,
        workers=[dict(worker=0,node='node27',gpu=0,numa=0,endpoint=endpoint,actual_bdf='0000:09:00.0',bdf='0000:09:00.0',
            boot_id=boot,pid=12345,pid_start_ticks=123,container_id='c'*64)])
    prepared=out/'prepared';prepared.mkdir();source_assets=[dict(relative_path=str(path.relative_to(ROOT)),sha256=native.sha(path)) for path in HERE.glob('*.py')]
    write(prepared/'node27.setup.spec.json',dict(node='node27',assets=source_assets))
    analysis=load('cost_ledger_CPU_analysis',ROOT/'codex_coding/src/v0.1.23.3/task2/cost_ledger/analysis/analyze.py')
    predeclared=dict(artifact_type='cost-predeclared-v1',protocol=analysis.protocol(),protocol_sha256=analysis.canonical(analysis.protocol()),source_refs=[ref(HERE/'worker.py')])
    write(out/'predeclared_protocol.json',predeclared)
    launch=dict(session=args.session,experiment_stage='C1',protocol=protocol('C1'),setup_directory=str(prepared),workers=runtime_setup['workers'],prediction_ref=None,
        analysis_predeclared_ref=ref(out/'predeclared_protocol.json'))
    write(out/'runtime_setup.json',runtime_setup);write(out/'launch_plan.json',launch)
    runner=load('cost_ledger_CPU_runner',HERE/'runner.py');original_argv=sys.argv
    try:
        for stage in ('mesh','measure'):
            sys.argv=['runner.py','--setup',str(out/'runtime_setup.json'),'--plan',str(out/'launch_plan.json'),'--output',str(out/stage),'--stage',stage]
            runner.main()
    finally:
        sys.argv=original_argv;servers[0].shutdown();servers[0].server_close();worker_thread.join(timeout=10)
    bundle=native.read(out/'measure/stage_measurements.json');rows=[native.read(s['raw_refs'][0]['path']) for s in bundle['samples']]
    checks=[]
    def check(name,value):checks.append(dict(name=name,passed=bool(value)));require(value,name)
    check('real_native_HTTP_handler_generation',[r['worker_ledger']['actor']['generation'] for r in rows]==sorted(set(r['worker_ledger']['actor']['generation'] for r in rows)))
    check('six_calibration_order',[r['case_id'] for r in rows]==ORDERS['C1'])
    by_tx={r['request_id']:r for r in rows}
    for row in rows:
        ledger=row['worker_ledger'];tid=ledger['actor']['native_tid'];begin=ledger['primary_span']['begin_ns'];end=ledger['primary_span']['end_ns']
        copies=[r for r in TRACE if r['event']=='copy' and r['native_tid']==tid and begin<=r['begin_ns']<=r['end_ns']<=end]
        syncs=[r for r in TRACE if r['event']=='sync' and r['native_tid']==tid and begin<=r['ns']<=end]
        check(row['case_id']+'_independent_actual_copy',[r['n']/MIB for r in copies]==row['actual']['chunk_mib'])
        check(row['case_id']+'_independent_actual_sync',len(syncs)==row['actual']['sync_count'])
        check(row['case_id']+'_nonoverlap_target_writes',all(a['target_offset']+a['n']<=b['target_offset'] for a,b in zip(copies,copies[1:])))
        check(row['case_id']+'_actual_source_target_offsets',all(r['source_offset']==r['target_offset'] for r in copies))
        check(row['case_id']+'_exact_worker_integer_closure',sum(s['end_ns']-s['begin_ns'] for s in ledger['partition'])==end-begin)
        check(row['case_id']+'_exact_request_integer_closure',sum(s['end_ns']-s['begin_ns'] for s in row['controller_ledger']['partition'])==row['measured']['request_return_ns'])
        check(row['case_id']+'_native_setdevice_in_primary',any(s['name']=='set_device' for s in ledger['primitive_spans']))
        check(row['case_id']+'_full_SHA_all_bytes',row['flow']['verification']['checked_bytes']==sum(CASES[row['case_id']])*MIB and row['flow']['verification']['passed'])
    check('world1_init_no_pairs',len([r for r in TRACE if r['event']=='init_process_group'])==1)
    worker=instances[0]
    check('empty_transactions_catalog_budget',not worker.runs and not worker.catalog.transactions and not any(worker.catalog.used().values()))
    check('no_events','Event' not in [r['event'] for r in TRACE])
    # Exercise the standard-library mode CLI from a separate empty Python path.
    isolated=out/'isolated';isolated.mkdir();mode=isolated/'cost_ledger/cost_ledger_runtime_isolated.inventory_mode.json'
    mode.parent.mkdir()
    commands=[]
    for expected_rc in (0,1):
        result=subprocess.run([sys.executable,'-I',str(HERE/'mode_file.py'),'--path',str(mode)],cwd=isolated,capture_output=True,text=True)
        commands.append(dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr))
        check('isolated_mode_cli_'+str(expected_rc),result.returncode==expected_rc)
    write(out/'mode_cli.json',commands);write(out/'primitive_trace.json',TRACE)
    write(out/'qualification.json',dict(passed=True,checks=checks,CPU_REPLAY_ONLY=True,GPU_launches=0,remote_calls=0,
        bundle_ref=ref(out/'measure/stage_measurements.json'),primitive_trace_ref=ref(out/'primitive_trace.json'),
        real_native_methods=['main_HTTP_Handler','init_mesh','synth','adopt','prepare','execute','_execute','land','verify_run','complete','finish','retire'],
        mocked=['torch_bytearray_backend','registered_NUMA_owner','runtime_inventory','synthetic_checksum'],hardware_qualification=False))
    print(json.dumps(dict(passed=True,checks=len(checks),out=str(out),GPU_launches=0)))
if __name__=='__main__':main()
