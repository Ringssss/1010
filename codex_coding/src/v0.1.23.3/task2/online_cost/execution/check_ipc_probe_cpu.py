"""CPU-only import count, shared tuple, unknown IPC slot and capacity checks."""
import argparse
import json
import os
from pathlib import Path
import pickle
import struct
import sys
from types import SimpleNamespace
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import probe_ipc_counter as probe


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=a.out.resolve()
    if out.parent!=probe.RESULTS or not out.name.startswith('execution_host_ipc_cpu_'):raise ValueError('new owned CPU output required')
    out.mkdir(exist_ok=False);checks=[];calls=[]
    def check(name,test):checks.append(dict(name=name,passed=bool(test)))
    class View:
        def set_(self,*args):return self
        def __eq__(self,value):return value==13
    def raw(*handle):calls.append(handle);return object()
    fake=SimpleNamespace(UntypedStorage=SimpleNamespace(_new_shared_cuda=raw),empty=lambda *a,**k:View(),uint8='CPU_FIXTURE',all=lambda value:SimpleNamespace(item=lambda:value),cuda=SimpleNamespace(synchronize=lambda *a:None,mem_get_info=lambda *a:(64<<30,64<<30),memory_allocated=lambda *a:0,memory_reserved=lambda *a:0,empty_cache=lambda:None))
    handle=(4,b'CPU_FIXTURE',probe.N,0,b'/torch_CPU_missing_counter',0,b'',False)
    payload=out/'CPU_FIXTURE_handle.pkl'
    with payload.open('xb') as stream:pickle.dump(dict(handle=handle,n=probe.N,gpu=4),stream)
    for count in (1,2):
        calls.clear()
        with patch.object(probe,'load_torch',return_value=fake),patch.object(probe,'watchdog'):
            probe.consumer(SimpleNamespace(out=out/('consumer'+str(count)),payload=payload,imports=count))
        check('raw_exact_'+str(count)+'_imports_same_share_tuple',len(calls)==count and all(c==handle for c in calls))
    name='torch_online_ipc_CPU_'+str(os.getpid());shm=Path('/dev/shm')/name
    with shm.open('xb') as stream:stream.write(struct.pack('=Q',(1<<64)-1))
    try:
        read_handle=(4,b'',probe.N,0,('/'+name).encode(),0,b'',False)
        with patch.object(probe.os,'open',side_effect=AssertionError('unvalidated IPC slot must not open shm')):
            row=probe.counter(read_handle,'CPU_owned_uint64_max',out)
        check('unvalidated_counter_unknown_without_numeric_read',not row['known'] and not row['measurement_performed'] and 'unsigned' not in row and 'signed' not in row and row['exported_refcounter_index']==0)
        check('counter_file_unchanged',shm.read_bytes()==struct.pack('=Q',(1<<64)-1))
    finally:shm.unlink()
    missing=probe.counter(handle,'CPU_counter_missing',out)
    check('missing_counter_inconclusive',not missing['known'] and 'inconclusive_reason' in missing)
    fake.cuda.mem_get_info=lambda *a:(61<<30,64<<30)
    rejected=False
    try:probe.sample(fake,'CPU_over_cap',out)
    except MemoryError:rejected=True
    check('whole_card_2GiB_gate_refuses_upper3GiB',rejected)
    check('CPU_test_never_imported_torch','torch' not in sys.modules)
    result=dict(CPU_FIXTURE_ONLY=True,checks=checks,passed=all(c['passed'] for c in checks),GPU_launches=0,SSH_commands=0)
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(passed=result['passed'],checks=len(checks),out=str(out))))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
