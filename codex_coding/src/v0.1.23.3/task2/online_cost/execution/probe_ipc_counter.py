"""Diagnostic only: one 32MiB share tuple, one consumer, one or two raw imports."""
import os
for _key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','RAYON_NUM_THREADS'):
    os.environ[_key]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
os.environ['HIP_VISIBLE_DEVICES']='0,1,2,3,4,5,6,7'
for _key in ('ROCR_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES'):
    os.environ.pop(_key,None)
import argparse
import ctypes
import ctypes.util
import gc
import hashlib
import json
from pathlib import Path
import pickle
import re
import signal
import subprocess
import sys
import threading
import time

ROOT=Path('/home/zhujianian/zhujianian/fluidckpt_zjn')
RESULTS=ROOT/'codex_coding/results/v0.1.23.3/task2/online_cost'
N=32<<20
CAP=2<<30
GPU=4
BDF='0000:85:00.0'


def write(path,body):
    with path.open('x') as stream:json.dump(body,stream,indent=2);stream.write('\n')


def watchdog(seconds,out,cleanup_callback=None):
    def expire():
        time.sleep(seconds)
        try:
            write(out/'deadline.json',dict(deadline_s=seconds,owner_cleanup_required=True))
            if cleanup_callback is not None:
                try:report=cleanup_callback()
                except BaseException as exc:report=dict(passed=False,error=repr(exc))
                write(out/'deadline_child_cleanup.json',report)
        finally:os._exit(124)
    threading.Thread(target=expire,daemon=True).start()


def identity(pid):
    fields=Path('/proc',str(pid),'stat').read_text().rsplit(') ',1)[1].split()
    return dict(pid=pid,start_ticks=fields[19],pgid=int(fields[2]),sid=int(fields[3]),argv=Path('/proc',str(pid),'cmdline').read_bytes(),cgroup=Path('/proc',str(pid),'cgroup').read_bytes())


def cleanup(child,owned):
    signals=[]
    if child.poll() is None:
        if identity(child.pid)!=owned or owned['pgid']!=child.pid or owned['sid']!=child.pid:
            raise ValueError('exact new consumer owner changed; no signal')
        os.killpg(child.pid,signal.SIGTERM);signals.append('TERM')
        try:child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if identity(child.pid)!=owned:raise ValueError('consumer identity changed before KILL')
            os.killpg(child.pid,signal.SIGKILL);signals.append('KILL');child.wait(timeout=2)
    deadline=time.monotonic()+2;reaped=[]
    while True:
        try:pid,status=os.waitpid(-1,os.WNOHANG)
        except ChildProcessError:return dict(passed=True,signals=signals,reaped=reaped,returncode=child.returncode)
        if pid:reaped.append(dict(pid=pid,status=status));continue
        if time.monotonic()>deadline:return dict(passed=False,signals=signals,reaped=reaped,returncode=child.returncode)
        time.sleep(.025)


def load_torch():
    import torch
    torch.set_num_threads(1);torch.cuda.set_device(GPU)
    lib=ctypes.CDLL(ctypes.util.find_library('amdhip64') or 'libamdhip64.so')
    buf=ctypes.create_string_buffer(64)
    if lib.hipDeviceGetPCIBusId(buf,len(buf),GPU):raise ValueError('HIP BDF query failed')
    actual=buf.value.decode().lower();actual=f"{int(actual.split(':')[0],16):04x}:"+':'.join(actual.split(':')[1:])
    if actual!=BDF:raise ValueError('physical GPU4 BDF mismatch')
    return torch


def sample(torch,phase,out):
    torch.cuda.synchronize(GPU)
    free,total=torch.cuda.mem_get_info(GPU)
    row=dict(phase=phase,monotonic_ns=time.monotonic_ns(),gpu=GPU,bdf=BDF,allocated_bytes=torch.cuda.memory_allocated(GPU),reserved_bytes=torch.cuda.memory_reserved(GPU),free_bytes=free,total_bytes=total,whole_card_used_upper_bytes=total-free,cap_bytes=CAP)
    write(out/(phase+'.json'),row)
    if total-free>CAP:raise MemoryError('fresh same-HIP whole-card upper exceeds unchanged2GiB diagnostic cap')
    return row


def counter(handle,phase,out):
    # The exported index addresses allocator data, not the raw shm file origin.
    # Do not infer the allocator header size from a different Torch version.
    body=dict(phase=phase,known=False,read_only=True,measurement_performed=False,
              interpretation='allocator_header_not_validated_IPCslot',
              inconclusive_reason='IPC slot byte offset not validated for the actual Torch allocator; no numeric counter read')
    try:
        name=handle[4].decode().rstrip('\0') if isinstance(handle[4],bytes) else str(handle[4])
        basename=name[1:] if name.startswith('/') else name
        offset=handle[5]
        if not re.fullmatch(r'torch_[A-Za-z0-9_]+',basename) or type(offset) is not int or not 0<=offset<131072:
            raise ValueError('own exported refcounter name/offset not within bound')
        body.update(own_shm_basename=basename,exported_refcounter_index=offset)
    except (OSError,ValueError,IndexError,UnicodeError) as exc:body['inconclusive_reason']=repr(exc)
    write(out/(phase+'_counter.json'),body)
    return body


def consumer(a):
    a.out.mkdir(exist_ok=False);watchdog(40,a.out)
    torch=load_torch();rows=[sample(torch,'consumer_init',a.out)]
    raw=a.payload.read_bytes()
    if len(raw)>16384:raise ValueError('share metadata exceeds bound')
    data=pickle.loads(raw)
    if data['n']!=N or data['gpu']!=GPU:raise ValueError('payload metadata differs')
    raw_new_shared_cuda=torch.UntypedStorage._new_shared_cuda
    counters=[counter(data['handle'],'consumer_before_import',a.out)]
    for index in range(a.imports):
        storage=raw_new_shared_cuda(*data['handle'])
        view=torch.empty(0,dtype=torch.uint8,device=f'cuda:{GPU}').set_(storage,0,(N,),(1,))
        exact=bool(torch.all(view==13).item())
        if not exact:raise ValueError('full32MiB known-byte comparison failed')
        rows.append(sample(torch,'consumer_import_'+str(index+1),a.out))
        counters.append(counter(data['handle'],'consumer_after_import_'+str(index+1),a.out))
        del view,storage;gc.collect();torch.cuda.synchronize(GPU)
        rows.append(sample(torch,'consumer_del_'+str(index+1),a.out))
        counters.append(counter(data['handle'],'consumer_after_del_'+str(index+1),a.out))
    torch.cuda.empty_cache();torch.cuda.synchronize(GPU)
    write(a.out/'consumer.json',dict(passed=True,raw_import_count=a.imports,payload_bytes=N,known_byte=13,full_payload_exact=True,handle_sha256=hashlib.sha256(raw).hexdigest(),samples=rows,refcounter_reads=counters))


def producer(a):
    a.out.mkdir(exist_ok=False)
    registered={}
    def deadline_cleanup():
        if not registered:return dict(passed=True,registered_child=False,scope='no registered consumer')
        return cleanup(registered['child'],registered['owner'])
    watchdog(60,a.out,deadline_cleanup)
    lib=ctypes.CDLL(None,use_errno=True)
    if lib.prctl(36,1,0,0,0):raise OSError(ctypes.get_errno(),'PR_SET_CHILD_SUBREAPER failed')
    torch=load_torch();rows=[sample(torch,'producer_init',a.out)]
    source=torch.full((N,),13,dtype=torch.uint8,device=f'cuda:{GPU}')
    storage=source.untyped_storage()
    handle=storage._share_cuda_()  # Exactly one producer export in this run.
    counters=[counter(handle,'producer_after_share',a.out)]
    payload=a.out/'handle.pkl'
    with payload.open('xb') as stream:pickle.dump(dict(handle=handle,n=N,gpu=GPU),stream)
    rows.append(sample(torch,'producer_shared_once',a.out))
    argv=[sys.executable,str(Path(__file__).resolve()),'--consumer','--imports',str(a.imports),'--payload',str(payload),'--out',str(a.out/'consumer')]
    child=None;owned=None;clean=None;failure=None
    try:
        with (a.out/'consumer.log').open('xb') as log:
            child=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        owned=identity(child.pid)
        if owned['pgid']!=child.pid or owned['sid']!=child.pid or owned['argv'].rstrip(b'\0').split(b'\0')!=[arg.encode() for arg in argv]:
            raise ValueError('exact new consumer registration failed')
        registered.update(child=child,owner=owned)
        write(a.out/'consumer.owner.json',{k:(v.decode().replace('\0',' ') if isinstance(v,bytes) else v) for k,v in owned.items()})
        child.wait(timeout=45)
        if child.returncode!=0:raise RuntimeError('consumer failed: '+str(child.returncode))
        report=json.loads((a.out/'consumer/consumer.json').read_text())
        if not report['full_payload_exact'] or report['raw_import_count']!=a.imports:raise ValueError('consumer qualification differs')
        rows.append(sample(torch,'producer_after_child_exit',a.out))
        counters.append(counter(handle,'producer_after_child_exit',a.out))
    except BaseException as exc:
        failure=repr(exc)
    finally:
        if child is not None and owned is not None:
            try:clean=cleanup(child,owned)
            except BaseException as exc:clean=dict(passed=False,error=repr(exc))
        del source,storage;gc.collect()
        try:
            torch.cuda.empty_cache();torch.cuda.ipc_collect();torch.cuda.synchronize(GPU)
            rows.append(sample(torch,'producer_after_drop_gc_empty_ipc_sync',a.out))
            counters.append(counter(handle,'producer_after_drop',a.out))
        except BaseException as exc:failure=repr(exc)
        result=dict(schema='ipc_counter_probe.v1',scope='diagnostic only; no complete-model unique-cause or cost qualification',imports=a.imports,producer_share_calls=1,payload_bytes=N,consumer_cleanup=clean,failure=failure,samples=rows,refcounter_reads=counters,allocated_retained_bytes=rows[-1]['allocated_bytes']-rows[0]['allocated_bytes'],passed=failure is None and bool(clean and clean['passed']),torch_version=torch.__version__)
        write(a.out/'result.json',result);print(json.dumps(result),flush=True)
    if not result['passed']:raise SystemExit(2)


def main():
    p=argparse.ArgumentParser();p.add_argument('--imports',type=int,choices=(1,2),required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--consumer',action='store_true');p.add_argument('--payload',type=Path);a=p.parse_args();a.out=a.out.resolve()
    top=a.out.parent if not a.consumer else a.out.parent.parent
    name=a.out.name if not a.consumer else a.out.parent.name
    if top!=RESULTS or not name.startswith('execution_ipc_probe_'):raise ValueError('new owned diagnostic output required')
    try:
        if a.consumer:consumer(a)
        else:producer(a)
    except BaseException as exc:
        failure=dict(passed=False,error=repr(exc),diagnostic_only=True,phase='consumer' if a.consumer else 'producer')
        if a.out.exists() and not (a.out/'failure.json').exists():write(a.out/'failure.json',failure)
        print(json.dumps(failure),flush=True)
        raise


if __name__=='__main__':main()
