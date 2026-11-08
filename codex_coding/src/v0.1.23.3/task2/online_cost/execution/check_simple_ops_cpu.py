"""CPU qualification of coexistence gates, fresh roles and nonempty native Catalog."""
import argparse
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from simple_host import build_simple, admission_spec
from simple_resources import resource_snapshot, own_fdinfo_vram, numa_plan_checks
from orchestrate import RESULTS
import prepare

HERE=Path(__file__).resolve().parent
GiB=1<<30


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_simple_ops_cpu_'):raise ValueError('new CPU output required')
    out.mkdir(parents=True,exist_ok=False);checks=[]
    def check(name,condition):checks.append(dict(name=name,passed=bool(condition)))
    plan=build_simple('online_simple_CPU_FIXTURE')
    roles=[r for r in plan['processes'] if r['kind']=='worker']
    check('three_workers_one_engine',len(roles)==3 and len(plan['processes'])==4)
    check('helper_exact_GPU4_only',[(r['node'],r['gpu']) for r in roles]==[('node27',4),('node27',5),('node30',4)])
    check('mesh_single_sender_parser_supported',all('node27:4-5,node30:4-4' in r['argv'] for r in roles))
    helper=plan['nodes']['node30'];check('helper_additional2_initialfree4_livefree2',helper['task_hbm_cap_bytes']==2*GiB and helper['minimum_initial_gpu_free_bytes']==4*GiB and helper['ongoing_gpu_free_floor_bytes']==2*GiB)
    check('helper96_catalog_and_numa4_7',str(96*GiB) in roles[2]['argv'] and plan['remote_source_numa_by_rank']=={'0':4,'1':7})
    check('bounded_exact_one_helper_query',len(admission_spec(dict(helper,node='node30',roles=[dict(gpu=4,bdf='0000:85:00.0',numa=4)]))['roles'])==1)
    for role in roles:role['boot_id']='CPU_BOOT'
    workers=[]
    for i,role in enumerate(roles):
        budget=plan['nodes'][role['node']]
        workers.append(dict(worker=i,node=role['node'],gpu=role['gpu'],numa=role['numa'],
            runtime_identity=dict(boot_id='CPU_BOOT',actual_bdf=role['bdf'],pci_numa_node=role['numa'],cgroup_memory_current_bytes=GiB,cgroup_memory_max_bytes=budget['container_hard_cap_bytes'],host_memory_available_bytes=300*GiB),
            headroom_bytes=dict(hbm=(4 if i==2 else 60)*GiB,dram=50*GiB),hbm_total_bytes=64*GiB,
            hbm_allocated_bytes=64<<20,hbm_reserved_bytes=128<<20,used_bytes={},numa_headroom_bytes={'4':50*GiB,'7':50*GiB},own_hbm_ledger=dict(known_unique_tensor_bytes=64<<20)))
    def gate(sample=workers,allocation=None,numa=None):return resource_snapshot(sample,roles,plan['nodes'],'CPU_CLOCK',allocation,numa)
    check('foreign_helper60GiB_used_allowed_with_free4',gate()['passed'])
    modified=copy.deepcopy(workers);modified[2]['headroom_bytes']['hbm']=GiB
    check('helper_free_floor_refused',not gate(modified)['passed'])
    modified=copy.deepcopy(workers);modified[2]['hbm_reserved_bytes']=3*GiB
    check('helper_reserved_over2_refused',not gate(modified)['passed'])
    modified=copy.deepcopy(workers);modified[2]['own_hbm_ledger']['known_unique_tensor_bytes']=3*GiB
    check('helper_known_alloc_over2_refused',not gate(modified)['passed'])
    allocation={2:70_329_720_832};numa={2:{4:35_164_860_416,7:35_164_860_416}}
    check('two_rank_allocation_checked_per_numa',gate(allocation=allocation,numa=numa)['passed'])
    check('helper_combined_charged_to_one_numa_refused',not gate(allocation=allocation)['passed'])
    modified=copy.deepcopy(workers);modified[2]['numa_headroom_bytes']['7']=10*GiB
    check('helper_second_source_numa_shortage_refused',not gate(modified,allocation,numa)['passed'])
    modified=copy.deepcopy(workers);modified[0]['headroom_bytes']['hbm']=15*GiB
    check('target_whole_card48_margin_refused',not gate(modified)['passed'])
    check('exact_total_attribution_stays_unknown',gate()['task_hbm_actual_bytes'] is None and not gate()['task_hbm_attribution_complete'])
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        (root/'1').write_text('drm-client-id: 3\ndrm-pdev: 0000:85:00.0\ndrm-memory-vram: 1024 KiB\n')
        (root/'2').write_text((root/'1').read_text())
        measured=own_fdinfo_vram(root)
        check('own_fdinfo_deduplicates_clients',measured['vram_bytes']==1<<20 and not measured['exact_task_attribution'])
        (root/'1').write_text('pos: 0\n');(root/'2').write_text('pos: 0\n')
        check('unsupported_own_fdinfo_unknown',own_fdinfo_vram(root)['vram_bytes'] is None)
    check('fresh_numa_split_all_pass',all(r['passed'] for r in numa_plan_checks({'4':{'free_plus_file_minus_shmem_bytes':50*GiB},'7':{'free_plus_file_minus_shmem_bytes':50*GiB}},{'4':35_164_860_416,'7':35_164_860_416})))
    metadata=RESULTS/'review_simple_metadata_r02'
    if metadata.exists():
      with tempfile.TemporaryDirectory() as temporary:
        frozen=Path(temporary)/'execution_setup_CPU_FIXTURE'
        argv=['prepare.py','--simple','--run-id','online_simple_CPU_FIXTURE','--out',str(frozen),
              '--metadata-root',str(metadata),'--model-metadata',str(RESULTS/'execution_metadata_local_r01.json')]
        with patch.object(prepare,'RESULTS',Path(temporary)),patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
            prepare.main()
        spec=json.loads((frozen/'node30.setup.spec.json').read_text())
        check('actual_prepare_exact_one_helper_role_port',len(spec['roles'])==1 and spec['roles'][0]['gpu']==4 and spec['ports']==[36014])
        check('actual_prepare_three_card_workflow',json.loads((frozen/'launch_plan.unbound.json').read_text())['workflow']=='simple_two_node_single_sender_v1')
        check('actual_prepare_free4_and_hard128',spec['minimum_initial_gpu_free_bytes']==4*GiB and spec['container_hard_cap_bytes']==128*GiB)
        compile((frozen/'setup_host.source.py').read_bytes(),'CPU_frozen_setup','exec')
    # Import only the adapter while substituting the runtime; actual Catalog dict shape is retained.
    class Tensor:
        def __init__(self,n,address):self.n=n;self.address=address;self.device=SimpleNamespace(type='cuda')
        def untyped_storage(self):return self
        def data_ptr(self):return self.address
        def nbytes(self):return self.n
    base=SimpleNamespace(OnlineWorker=type('DummyWorker',(),{'inventory':lambda self,compact=False:dict(hbm_allocated_bytes=128,hbm_reserved_bytes=256)}),native=SimpleNamespace(torch=SimpleNamespace(Tensor=Tensor)),capture_module=SimpleNamespace(),verified_capture=lambda:None)
    topo=SimpleNamespace(memory_node=lambda numa:dict(MemFree=50*GiB,FilePages=0,Shmem=0))
    spec=importlib.util.spec_from_file_location('CPU_SIMPLE_WORKER',HERE/'simple_worker.py');mod=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'worker':base,'fluidckpt.topology.discover':topo}):
        spec.loader.exec_module(mod)
        worker=mod.SimpleWorker.__new__(mod.SimpleWorker);worker.node='node30';worker.a=SimpleNamespace(numa=4)
        shared=Tensor(64,100);worker.staging=shared;worker.service_reserve=None
        cat_spec=importlib.util.spec_from_file_location('CPU_NATIVE_CATALOG',HERE.parents[5]/'main_lib/fluidckpt/cluster/catalog.py')
        catalog=importlib.util.module_from_spec(cat_spec);cat_spec.loader.exec_module(catalog)
        worker.catalog=catalog.Catalog({'hbm':GiB,'dram':GiB})
        worker.catalog.reserve(dict(id='hbm',tier='hbm',n=64),lambda:(shared,None))
        worker.catalog.reserve(dict(id='dram',tier='dram',n=64),lambda:(None,None))
        with patch.object(mod,'own_fdinfo_vram',return_value=dict(available=False,vram_bytes=None)):
            inventory=worker.inventory()
        check('nonempty_native_catalog_dict_inventory',inventory['own_hbm_ledger']['known_unique_tensor_bytes']==64 and len(inventory['own_hbm_ledger']['allocations'])==1)
        check('both_source_numa_inventory',set(inventory['numa_headroom_bytes'])=={'4','7'})
    qualification=dict(passed=all(r['passed'] for r in checks),checks=checks,GPU_launches=0,SSH_commands=0,Docker_commands=0,scope='CPU fixtures only')
    (out/'qualification.json').write_text(json.dumps(qualification,indent=2)+'\n')
    print(json.dumps(dict(passed=qualification['passed'],checks=len(checks),out=str(out))))
    if not qualification['passed']:raise SystemExit(1)


if __name__=='__main__':main()
