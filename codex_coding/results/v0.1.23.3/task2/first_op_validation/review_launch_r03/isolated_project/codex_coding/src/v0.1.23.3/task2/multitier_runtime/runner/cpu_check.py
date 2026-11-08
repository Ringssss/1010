"""Native API CPU verification; this entry cannot generate physical admission."""
import argparse
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import copy
import time

import run
sys.path.insert(0, str(run.ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter'))
import cpu_api_check as fixture
import torch
from fluidckpt.cluster import worker as native
from fluidckpt.memory.ssd import RemoteExtent


def synth_handler_checks():
    captured, allocations, instances = [], [], []
    class FakeWorker:
        def __init__(self, args):
            self.gpu = 0
            self.lock = __import__('threading').Lock()
            self.shards = {}
            instances.append(self)
        def synth(self, snap, tp, nbytes, seed):
            allocations.append((snap, tp))
            self.shards[snap, tp] = SimpleNamespace(meta={'tp_rank': tp, 'synthetic': True})
            return {}
    class FakeServer:
        def __init__(self, address, handler):
            captured.append(handler)
        def serve_forever(self):
            pass
    oldworker, oldserver = native.ClusterWorker, native.ThreadingHTTPServer
    native.ClusterWorker, native.ThreadingHTTPServer = FakeWorker, FakeServer
    try:
        native.main(SimpleNamespace(port_base=0, gpu=0, node='CPU_ONLY'))
        handler = captured[0]
        checks = []
        for index, (size, rank, allowed) in enumerate([(None,0,True),(None,1,True),(4,2,True),(4,3,True),
            (True,0,False),(4.0,0,False),('4',0,False),(0,0,False),(-1,0,False),
            (4,True,False),(4,1.0,False),(4,'1',False),(4,-1,False),(4,4,False)]):
            body = dict(snap=f'cpu{index}', tp=rank, nbytes=1)
            if size is not None:
                body['tp_size'] = size
            request = object.__new__(handler)
            data = json.dumps(body).encode()
            request.rfile, request.headers, request.path = BytesIO(data), {'Content-Length':len(data)}, '/synth'
            replies = []
            request.reply = lambda result, status=200: replies.append((result,status))
            before = len(allocations)
            request.do_POST()
            result, status = replies[0]
            assert (status == 200) == allowed
            assert len(allocations) - before == int(allowed)
            if allowed:
                meta = instances[0].shards[body['snap'],rank].meta
                assert meta['tp_size'] == (2 if size is None else size) and meta['tp_rank'] == rank and result['synthetic'] is True
            checks.append(dict(tp_size=size,tp=rank,accepted=allowed,allocation_before_validation=False))
        return checks
    finally:
        native.ClusterWorker, native.ThreadingHTTPServer = oldworker, oldserver


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    oldcuda = {key:getattr(torch.cuda,key) for key in ('set_device','synchronize','empty_cache')}
    oldbroadcast, oldrequest = torch.distributed.broadcast, run.request
    processes = []
    try:
        torch.set_num_threads(1)
        for key in oldcuda:
            setattr(torch.cuda,key,lambda *args,**kwargs:None)
        synth_checks = synth_handler_checks()
        processes, endpoints = fixture.start_services(out)
        contract = run.adapter.load_contract()
        binding = fixture.fixture(contract,endpoints)
        for i,w in enumerate(binding['workers']):
            w['numa'] = run.EXPECTED[i][2]
        collectives = fixture.MockCollectives()
        torch.distributed.broadcast = collectives.broadcast
        cluster = fixture.CpuCluster(binding,collectives,run.Q)
        for worker in cluster.workers:
            worker.allocate = lambda desc: (None,RemoteExtent(desc['store_endpoint'],desc['id'],desc['n'])) if desc['tier']=='ssd' else (torch.empty(desc['n'],dtype=torch.uint8),None)
        def inventory_all():
            ws, _ = cluster.inventory()
            for i,w in enumerate(ws):
                w['replicas'] = cluster.workers[i].catalog.inventory()
            return ws
        oldcall = cluster.call_all
        cluster.call_all = lambda operation,bodies=None,telemetry=None: inventory_all() if operation=='inventory' else oldcall(operation,bodies,telemetry)
        def direct_request(endpoint,op,body):
            worker = cluster.workers[cluster.endpoints.index(endpoint)]
            if op=='retire':
                worker.catalog.retire(body['id'])
                return {}
            raise ValueError(op)
        run.request = direct_request
        setup = dict(workers=binding['workers'],ssd={f'n{i}':dict(endpoint=endpoints[i],storage_node=f'cpu_node{i}',directory=str(out/f'cpu_files_n{i}')) for i in range(3)})
        paths = run.required_paths(setup)
        rows, journal = [], {}
        for index,(key,route) in enumerate(sorted(paths.items())):
            ws,_ = cluster.inventory()
            owner = route['source_worker']
            worker = cluster.workers[owner]
            meta = dict(model_identity=f'CPU_NATIVE_ROUTE_{index}',synthetic=True,tp_size=4,tp_rank=0)
            manifest = {'payload':[0,run.Q]}
            desc = dict(id=f'cpu_route/{index}/seed',identity=run.adapter.identity(meta,manifest),tp=0,meta=meta,index=manifest,
                        off=0,n=run.Q,tier='dram',worker=owner,node=ws[owner]['node'],gpu=ws[owner]['gpu'],numa=ws[owner]['numa'],epoch=ws[owner]['epoch'])
            worker.catalog.reserve(desc,lambda:(torch.arange(run.Q,dtype=torch.int64).remainder(251).to(torch.uint8),None))
            worker.catalog.publish(desc['id'],True)
            source = next(r for r in worker.catalog.inventory() if r['id']==desc['id'])
            owned=[source]
            if route['source_tier']!='dram':
                target=run.target_from(source,ws,f'cpu_route/{index}/source',owner,route['source_tier'],ssd=setup['ssd'][f'n{owner//2}'])
                run.remember(owned,target,ws)
                bootstrap=cluster.transfer(run.one_plan(source,target,ws),verify_before_ready=True)
                assert bootstrap['verification']['passed']
                _,catalog=cluster.inventory()
                source=next(r for r in catalog if r['id']==target['id'])
            target=run.target_from(source,ws,f'cpu_route/{index}/target',route['target_worker'],route['target_tier'])
            run.remember(owned,target,ws)
            result=cluster.transfer(run.one_plan(source,target,ws),verify_before_ready=True)
            assert result['verification']['passed'] and result['verification']['checked_bytes']==run.Q
            timing=result['controller_timing']
            assert timing['load_begin_ns']<=timing['all_targets_filled_ns']<=timing['all_targets_published_ns']
            rows.append(dict(path=key,checked_bytes=run.Q,verification=result['verification'],controller_timing=timing))
            run.exact_release(cluster,owned,journal,'CPU_ONLY_native_cleanup')
            assert not [r for r in cluster.inventory()[1] if r['state'] != 'RELEASED']
        stats=run.ssd_stats(setup)
        assert all(r['files']==0 for r in stats.values())
        report=dict(status='passed',purpose='cpu_api_fixture',physical_admitted=False,gpu_started=False,ssh_used=False,
                    native_synth_checks=synth_checks,needed_path_count=len(paths),native_route_results=rows,cleanup=journal,ssd_stats=stats,
                    source_shas={str(p):run.sha(p) for p in [Path(run.__file__),Path(__file__),run.ROOT/'main_lib/fluidckpt/cluster/worker.py']})
        run.write(out/'qualification_cpu.json',report)
        print(json.dumps(dict(status='passed',synth_checks=len(synth_checks),native_routes=len(rows),physical_admitted=False)))
    finally:
        run.request=oldrequest
        torch.distributed.broadcast=oldbroadcast
        for key,value in oldcuda.items():
            setattr(torch.cuda,key,value)
        for process in processes:
            process.terminate()
            process.wait(timeout=5)


if __name__=='__main__':
    main()
