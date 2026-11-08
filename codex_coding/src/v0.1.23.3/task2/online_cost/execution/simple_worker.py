"""Single-sender diagnostics over the unchanged verified native transfer worker."""
import argparse
import os
from pathlib import Path
import json
import threading
import time

from worker import OnlineWorker, native, capture_module, verified_capture
from simple_resources import own_fdinfo_vram


class SimpleWorker(OnlineWorker):
    def __init__(self,args):
        self.helper_memory_guard=dict(enabled=args.node=='node30',interval_s=.5,samples=0,
                                      minimum_observed_free_bytes=None,maximum_observed_reserved_bytes=None,
                                      peak_verified=False,latest=None)
        super().__init__(args)
        if self.node=='node30':
            def watch():
                try:
                    native.torch.cuda.set_device(self.gpu)
                    sample_path=Path(args.handoff).parent/'helper_memory_guard.jsonl'
                    sample_path.parent.mkdir(parents=True,exist_ok=True)
                    with sample_path.open('x',buffering=1) as samples:
                      while True:
                        free,total=native.torch.cuda.mem_get_info(self.dev)
                        reserved=native.torch.cuda.memory_reserved(self.dev)
                        sample=dict(monotonic_ns=time.monotonic_ns(),pid=os.getpid(),gpu=self.gpu,
                                    free_bytes=free,total_bytes=total,reserved_bytes=reserved,
                                    interval_s=.5,peak_verified=False)
                        samples.write(json.dumps(sample)+'\n')
                        state=self.helper_memory_guard
                        state.update(samples=state['samples']+1,latest=sample,
                            minimum_observed_free_bytes=min(free,state['minimum_observed_free_bytes'] or free),
                            maximum_observed_reserved_bytes=max(reserved,state['maximum_observed_reserved_bytes'] or reserved))
                        if state['samples']>14500:
                            raise RuntimeError('bounded helper guard sample count exceeded')
                        if free<2<<30 or reserved>2<<30:
                            alarm=dict(reason='helper_live_memory_guard',free_bytes=free,total_bytes=total,
                                       reserved_bytes=reserved,free_floor_bytes=2<<30,reserved_cap_bytes=2<<30,
                                       pid=os.getpid(),node=self.node,gpu=self.gpu,monotonic_ns=time.monotonic_ns())
                            path=Path(args.handoff).parent/'helper_memory_ALARM.json'
                            path.parent.mkdir(parents=True,exist_ok=True)
                            with path.open('x') as stream:json.dump(alarm,stream)
                            print(json.dumps(alarm),flush=True);os._exit(70)
                        time.sleep(.5)
                except BaseException as exc:
                    print(json.dumps(dict(reason='helper_live_memory_guard_unavailable',error=repr(exc))),flush=True)
                    os._exit(71)
            threading.Thread(target=watch,daemon=True).start()

    def inventory(self, compact=False):
        result = super().inventory(compact)
        from fluidckpt.topology.discover import memory_node
        numa_nodes = (4,7) if self.node == 'node30' else (self.a.numa,)
        result['numa_headroom_bytes'] = {}
        for numa in numa_nodes:
            mem = memory_node(numa)
            result['numa_headroom_bytes'][str(numa)] = max(0,mem.get('MemFree',0)+mem.get('FilePages',0)-mem.get('Shmem',0))
        unique, allocations = {}, []
        def record(label,tensor):
            if not isinstance(tensor,native.torch.Tensor) or tensor.device.type != 'cuda':
                return
            storage=tensor.untyped_storage()
            key=(str(tensor.device),storage.data_ptr())
            n=storage.nbytes()
            if key not in unique:
                unique[key]=n;allocations.append(dict(label=label,storage_bytes=n))
        record('native_mesh_staging',getattr(self,'staging',None))
        record('native_service_reserve',getattr(self,'service_reserve',None))
        for key,entry in self.catalog.entries.items():
            record('catalog/'+str(key),entry.get('tensor'))
        result['own_hbm_ledger']=dict(known_unique_tensor_bytes=sum(unique.values()),allocations=allocations,
                allocated_bytes=result['hbm_allocated_bytes'],reserved_bytes=result['hbm_reserved_bytes'],
                exact_driver_runtime_total=False,
                scope='live native staging/reserve/catalog tensor storages, deduplicated; PyTorch allocator records separate; RCCL/driver allocations unknown')
        result['own_process_gpu_memory']=own_fdinfo_vram()
        result['helper_memory_guard']=getattr(self,'helper_memory_guard',None)
        return result


def main():
    p=argparse.ArgumentParser()
    for name in ('node','mesh','master','handoff','session'):
        p.add_argument('--'+name,required=True)
    for name in ('gpu','numa','port-base','dram-budget','hbm-budget','ssd-budget'):
        p.add_argument('--'+name,required=True,type=int)
    p.set_defaults(memory_backend='numa_registered',replica_budget_bytes=1<<30)
    capture_module.capture_regions=verified_capture
    native.ClusterWorker=SimpleWorker
    native.main(p.parse_args())


if __name__=='__main__':main()
