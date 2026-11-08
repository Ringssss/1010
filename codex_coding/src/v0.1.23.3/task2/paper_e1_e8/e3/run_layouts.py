"""Isolated layout experiment; never mutate the protected GLM serving mesh."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from run_system import SystemRun, NODES
from fluidckpt.cluster.control import request

G=1<<30


class LayoutRun(SystemRun):
    def __init__(self, args):
        super().__init__(args)
        self.rank_bytes=args.rank_mib*(1<<20)+17
        self.cache_unit=args.rank_mib*(1<<20)//2

    def preflight(self):
        output=self.out/'node28_topology.json'
        self.remote('node28',['python3','codex_coding/src/task2/cluster/inspect_node.py','--out',str(output)])
        probe=json.loads(output.read_text()); self.probes={'node28':probe}
        self.result['preflight']=probe
        needed={self.base+i for i in (2,3,4,5)}|{self.base+100}
        if needed.intersection(probe['occupied_tcp_ports']): raise RuntimeError('experiment port occupied')
        for i in (2,3,4,5):
            m=probe['gpu_memory'][f'card{i}']
            used=int(m['vram Total Used Memory (MiB)']); total=int(m['vram Total Memory (MiB)'])
            if used>4096 or total-used<12288: raise RuntimeError(f'GPU{i} exceeds shared-keeper admission budget')
        self.check_protected('before')
        topology=probe['topology']; near={g['logical_gpu']:g['numa_node'] for g in topology['gpus']}
        available={n['node']:n['memory_bytes']['MemFree'] for n in topology['nodes']}
        self.numas={g:near[g] for g in (2,3,4,5)}
        for g in (2,3):
            eligible=[n for n,free in available.items() if free>=2*self.rank_bytes+(512<<20)+4096]
            if not eligible: raise MemoryError('no free-only NUMA headroom for base plus initialization scratch')
            chosen=near[g] if near[g] in eligible else max(eligible,key=available.get)
            self.numas[g]=chosen; available[chosen]-=(self.rank_bytes+4095)//4096*4096
        self.result['numa_bindings']=self.numas
        self.event('preflight_passed', physical_gpus=[2,3,4,5], note='low-memory protected keeper contexts coexist; no exclusive GPU claim')

    def check_protected(self, label):
        rows=[]
        for gpu in range(2,8):
            data=request(f'http://12.11.3.28:2970{gpu}', 'inventory?compact=1', timeout=5)
            if data['transactions']: raise RuntimeError('protected mesh currently transferring; defer experiment')
            rows.append({'gpu':gpu, 'epoch':data['epoch'], 'session':data['session'],
                         'replicas':[r['id'] for r in data['replicas'] if r['state']=='VALID'], 'used_bytes':data['used_bytes']})
        if label!='before':
            if rows!=self.result['protected_before']: raise RuntimeError('protected mesh changed; do not claim matched background')
        self.result['protected_'+label]=rows
        self.save()

    def start_workers(self):
        for node,gpu in self.members:
            self.start(node, f'worker_{node}_{gpu}', ['python3','-m','fluidckpt.cluster.worker',
                '--node',node,'--gpu',str(gpu),'--numa',str(self.numas[gpu]),'--port-base',str(self.base),
                '--mesh','node28:2-5','--master',f'12.11.3.28:{self.base+100}',
                '--handoff',str(self.out/'handoff'),'--session',self.args.run,
                '--dram-budget',str(4*G),'--hbm-budget',str(8*G)],
                {'AMD_SERIALIZE_KERNEL':'0','NCCL_DEBUG':'WARN','NCCL_IB_HCA':'=shca_0'})
        for ep in self.endpoints: self.wait_http(ep)
        self.cluster.call_all('mesh',[{} for _ in self.members])
        self.event('mesh_ready',workers=4)

    def fixture(self):
        self.cluster.profile=dict(self.cluster.profile,serial_target_execution=True)
        self.result.update(protocol=json.loads(Path(self.args.protocol).read_text()),
                           observations=[], baseline=[], layouts={})
        self.result['scope']='synthetic region recovery on one host; measured data-fill wall and verified publication, not SGLang T_ready'
        (self.out/'protocol.json').write_text(json.dumps(self.result['protocol'],indent=2))
        sources=[]
        for rank in range(2):
            request(self.endpoints[rank],'synth',{'snap':'paper','tp':rank,'nbytes':self.rank_bytes,'seed':48000+rank})
            sources.append(request(self.endpoints[rank],'adopt',{'snap':'paper','tp':rank,'id':f'paper/base/{rank}'}))
        base_ids=[s['id'] for s in sources]
        # Warm every source/destination combination using small disposable HBM data.
        warm_sources=[]
        for w in range(4):
            t=self.replica_target(sources[w%2],f'warm_source/{w}',w,n=4<<20)
            self.cluster.transfer(self.cluster.plan_restore([t],'nearest',[base_ids[w%2]]))
            warm_sources.append(t)
        for t in warm_sources:
            _,rs=self.cluster.inventory(); source=next(r for r in rs if r['id']==t['id'])
            for dst in range(4):
                out=self.replica_target(source,f'warm/{t["worker"]}/{dst}',dst,n=4<<20)
                f=self.cluster.transfer(self.cluster.plan_restore([out],'nearest',[source['id']]))
                assert f['verification']['passed']; self.cluster.retire(out['id'])
        for t in warm_sources: self.cluster.retire(t['id'])
        self.event('paths_warmed')
        def run_case(name, scenario, ids):
            self.check_protected('checkpoint')
            destinations=[0,1] if scenario=='A' else [2,3]
            targets=[self.replica_target(s,f'output/{name}/{scenario}/{r}',destinations[r]) for r,s in enumerate(sources)]
            plan=self.cluster.plan_restore(targets,'resource_lp',ids)
            if plan['strategy']!='resource_lp' or plan['fallback_reason']: raise RuntimeError('planner fell back')
            flow=self.cluster.transfer(plan)
            assert flow['verification']['passed'] and flow['verification']['checked_bytes']==2*self.rank_bytes
            row={'layout':name,'scenario':scenario,'data_fill_s':flow['prepare_s']+flow['execute_wall_s'],
                 'execute_s':flow['execute_wall_s'],'verified_publication_s':flow['coordinator_wall_s'],
                 'flow':flow}
            for target in targets: self.cluster.retire(target['id'])
            return row
        for repetition in range(2):
            row=run_case(f'base_only_{repetition}','B',base_ids)
            self.result['baseline'].append(row)
            self.event('baseline_measured', repetition=repetition, data_fill_s=row['data_fill_s'])
        unit=self.cache_unit
        layouts={
            'concentrated':[(0,0,unit),(1,0,unit)],
            'distributed':[(0,0,unit),(1,1,unit)],
            'replicated':[(0,0,unit//2),(0,2,unit//2),(1,1,unit//2),(1,3,unit//2)],
            'hybrid':[(0,0,unit),(1,1,unit//2),(1,3,unit//2)]}
        for li,(name, specs) in enumerate(layouts.items()):
            stamp=time.monotonic(); cache_ids=[]
            for i,(rank,owner,n) in enumerate(specs):
                t=self.replica_target(sources[rank],f'cache/{name}/{i}',owner,n=n)
                f=self.cluster.transfer(self.cluster.plan_restore([t],'resource_lp',[base_ids[rank]]))
                assert f['verification']['passed']; cache_ids.append(t['id'])
            build_s=time.monotonic()-stamp
            workers,rs=self.cluster.inventory(compact=True)
            totals={tier:sum(w['used_bytes'][tier] for w in workers) for tier in ('dram','hbm')}
            assert totals=={'dram':2*((self.rank_bytes+4095)//4096*4096),'hbm':2*unit},totals
            self.result['layouts'][name]={'specs':specs,'steady_bytes':totals,'replicas':rs,'build_s':build_s,
                'unique_cache_bytes':sum(max((n for r,w,n in specs if r==rank),default=0) for rank in (0,1))}
            for scenario in (['A','B'] if li%2==0 else ['B','A']):
                row=run_case(name,scenario,base_ids+cache_ids)
                self.result['observations'].append(row)
                self.event('layout_measured',layout=name,scenario=scenario,data_fill_s=row['data_fill_s'],execute_s=row['execute_s'])
            for rid in cache_ids: self.cluster.retire(rid)
        self.check_protected('after')
        self.event('layout_experiment_complete',cases=len(self.result['observations']))


if __name__=='__main__':
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    NODES.clear(); NODES['node28']={'ip':'12.11.3.28','container':'zjn-fluid-glm-full_distributed02','gpus':[2,3,4,5]}
    p=argparse.ArgumentParser(); p.add_argument('--run',required=True); p.add_argument('--protocol',required=True)
    p.add_argument('--port-base',type=int,default=22600)
    p.add_argument('--rank-mib',type=int,default=2048)
    p.set_defaults(worker_serialize=0,handoff_poll=.01,fixture_only=True,development='v0.1.23.3',
        result_root=str(ROOT/'codex_coding/results/v0.1.23.3/task2/paper_e1_e8'),
        source_directories=[str(ROOT/'codex_coding/src/v0.1.23.3/task2/paper_e1_e8')])
    LayoutRun(p.parse_args()).run()
