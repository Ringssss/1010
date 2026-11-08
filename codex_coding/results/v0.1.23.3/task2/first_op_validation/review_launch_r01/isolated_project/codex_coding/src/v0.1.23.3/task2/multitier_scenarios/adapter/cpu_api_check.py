"""Exercise native serial APIs using CPU tensors and mocked GPU collectives only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
from types import SimpleNamespace
import urllib.request

import adapter
import torch
from fluidckpt.cluster.catalog import Catalog
from fluidckpt.cluster.control import Cluster
from fluidckpt.cluster.planning import check_retirement, restore_plan
from fluidckpt.cluster.worker import ClusterWorker
from fluidckpt.cluster.wire import bind_manifests, planning_payload, worker_plan
from fluidckpt.memory.ssd import RemoteExtent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture(contract, endpoints):
    source = Path(adapter.__file__).resolve()
    reference = dict(path=str(source), sha256=sha(source))
    lifetime = time.time() + 600
    binding = dict(contract_hash=adapter.FROZEN, purpose='cpu_api_fixture', valid_until=lifetime,
                   evidence_refs={'cpu_fixture_source': reference}, workers=[], manifests={}, ssd={},
                   base_ids={b['id']: 'cpu_base/' + b['id'] for b in contract['blocks']},
                   paths={}, phase_admission={})
    for i in range(6):
        binding['workers'].append(dict(logical_worker=f'w{i}', worker=i, node=f'cpu_node{i // 2}',
                                       gpu=i % 2, numa=i % 2, epoch=f'cpu_session/epoch{i}', session='cpu_session',
                                       staging_bytes=adapter.STAGING_BYTES, endpoint=f'cpu_mock_worker:{i}',
                                       capabilities=['local_hbm', 'local_dram', 'local_ssd', 'peer', 'network'],
                                       budget_bytes={'hbm': 32 << 20, 'dram': 32 << 20, 'ssd': 32 << 20}))
    for rank in range(4):
        meta = dict(model_identity='CPU_API_PATTERN_ONLY', tp_size=4, tp_rank=rank, tensors={})
        index = {'payload': [0, 2 * contract['block_unit_bytes']]}
        binding['manifests'][str(rank)] = dict(meta=meta, index=index, identity=adapter.identity(meta, index))
    for node in range(3):
        binding['ssd'][f'n{node}'] = dict(endpoint=endpoints[node], storage_node=f'cpu_node{node}',
                                        current_owner_session='cpu_session', backing_evidence_ref='cpu_fixture_source')
    for phase in adapter.projection(contract)['phases']:
        binding['phase_admission'][phase['id']] = dict(status='admitted', valid_until=lifetime,
            evidence_ref='cpu_fixture_source', runtime_staging_inclusion='separate', budget_accounting_complete=True,
            scope='CPU API fixture admission only, no physical runtime budget')
        for req in phase['save_requirements'] + phase['restore_requirements']:
            worker = binding['workers'][req['src_w']]
            key = adapter.path_key(req['src_w'], req['src_tier'], worker['numa'], req['dst_w'], req['dst_tier'])
            row = dict(status='verified', executor='serial', valid_until=lifetime,
                       source_epoch=worker['epoch'], target_epoch=binding['workers'][req['dst_w']]['epoch'],
                       evidence_ref='cpu_fixture_source', scope='mock collective CPU API qualification')
            if req['src_tier'] == 'ssd':
                ssd = binding['ssd'][f'n{req["src_w"] // 2}']
                row.update(store_endpoint=ssd['endpoint'], storage_node=ssd['storage_node'])
            binding['paths'][key] = row
    return binding


class MockCollectives:
    def __init__(self):
        self.local = threading.local()
        self.barriers = {(i, j): threading.Barrier(2, timeout=30) for i in range(6) for j in range(i + 1, 6)}
        self.views = {}

    def broadcast(self, tensor, src, group):
        pair = tuple(sorted(group))
        barrier = self.barriers[pair]
        if self.local.rank == src:
            self.views[pair] = tensor.clone()
        barrier.wait()
        if self.local.rank != src:
            tensor.copy_(self.views[pair])
        barrier.wait()


class CpuCluster(Cluster):
    def __init__(self, binding, collectives, q):
        super().__init__([w['endpoint'] for w in binding['workers']], {}, executor='serial')
        self.binding, self.collectives = binding, collectives
        self.workers = []
        for desc in binding['workers']:
            worker = object.__new__(ClusterWorker)
            worker.rank, worker.node, worker.gpu = desc['worker'], desc['node'], desc['gpu']
            worker.dev, worker.epoch = 'cpu', desc['epoch']
            worker.a = SimpleNamespace(numa=desc['numa'])
            worker.catalog, worker.runs = Catalog(desc['budget_bytes']), {}
            worker.staging = torch.empty(q, dtype=torch.uint8)
            worker.pair_of = {(i, j): tuple(sorted((i, j))) for i in range(6) for j in range(6) if i != j}
            worker.record_targets, worker.log = lambda *a, **kw: None, lambda *a, **kw: None
            worker.allocate = lambda desc: (torch.empty(desc['n'], dtype=torch.uint8), None)
            self.workers.append(worker)

    def inventory(self, compact=False, telemetry=None):
        workers = copy.deepcopy(self.binding['workers'])
        for worker in workers:
            worker['transactions'] = list(self.workers[worker['worker']].catalog.transactions)
        rows = [r for w in self.workers for r in w.catalog.inventory(compact)]
        return workers, rows

    def call_all(self, operation, bodies=None, telemetry=None):
        def invoke(i):
            self.collectives.local.rank = i
            return getattr(self.workers[i], operation)(bodies[i])
        with ThreadPoolExecutor(max_workers=6) as executor:
            return list(executor.map(invoke, range(6)))

    def retire(self, rid):
        _, rows = self.inventory()
        source = check_retirement(rows, rid)
        self.workers[source['worker']].catalog.retire(rid)


def start_services(out):
    processes, endpoints = [], []
    script = adapter.ROOT / 'main_lib/fluidckpt/serving/ssd_store.py'
    for i in range(3):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        log = (out / f'http_service_{i}.log').open('wb')
        process = subprocess.Popen([__import__('sys').executable, str(script), '--directory',
                                    str(out / f'cpu_files_n{i}'), '--port', str(port)], stdout=log, stderr=log)
        log.close()
        processes.append(process)
        endpoint = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(endpoint + '/status', timeout=1) as response:
                    json.load(response)
                break
            except Exception:
                if time.monotonic() > deadline or process.poll() is not None:
                    raise RuntimeError('local HTTP service failed to start')
                time.sleep(0.05)
        endpoints.append(endpoint)
    return processes, endpoints


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    checks, runs, processes = [], [], []
    original_cuda = {name: getattr(torch.cuda, name) for name in ('set_device', 'synchronize', 'empty_cache')}
    original_broadcast = torch.distributed.broadcast
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)
    def rejected(name, action):
        try:
            action()
        except (ValueError, MemoryError, http.client.RemoteDisconnected):
            check(name, True)
        else:
            check(name, False)
    try:
        torch.set_num_threads(1)
        for name in original_cuda:
            setattr(torch.cuda, name, lambda *a, **kw: None)
        collectives = MockCollectives()
        torch.distributed.broadcast = collectives.broadcast
        contract = adapter.load_contract()
        processes, endpoints = start_services(out)
        binding = fixture(contract, endpoints)
        (out / 'binding_cpu_fixture.json').write_text(json.dumps(binding, indent=2) + '\n')
        cluster = CpuCluster(binding, collectives, contract['block_unit_bytes'])
        cm = {c['id']: c for c in adapter.projection(contract)['candidates']}
        payloads = {}
        for c in cm.values():
            if not c['fixed']:
                continue
            desc = adapter.descriptor(c, binding, 'cpu_run', 'C0', True)
            payload = torch.full((desc['n'],), int(c['block'][1:]) + 1, dtype=torch.uint8)
            payloads[c['block']] = payload
            owner = RemoteExtent(desc['store_endpoint'], desc['id'], desc['n'])
            owner.write(0, payload)
            owner.commit()
            catalog = cluster.workers[desc['worker']].catalog
            catalog.reserve(desc, lambda: (None, owner))
            catalog.publish(desc['id'], True)
        _, initial = cluster.inventory()
        (out / 'current_inventory_cpu_fixture.json').write_text(json.dumps(dict(workers=binding['workers'], replicas=initial), indent=2) + '\n')
        rejected('unbound fails closed', lambda: adapter.compile_phase(contract, None, 'C0', 'cpu_run', initial))
        bad = copy.deepcopy(binding)
        bad['paths'] = {}
        rejected('unqualified selected save path refused', lambda: adapter.compile_phase(contract, bad, 'C0', 'cpu_run', initial))
        bad = copy.deepcopy(binding)
        bad['workers'][0]['epoch'] = 'restarted'
        rejected('old base owner incarnation refused', lambda: adapter.compile_phase(contract, bad, 'C0', 'cpu_run', initial))
        bad = copy.deepcopy(binding)
        bad['phase_admission']['C0']['budget_accounting_complete'] = False
        rejected('incomplete runtime accounting refused', lambda: adapter.compile_phase(contract, bad, 'C0', 'cpu_run', initial))
        rejected('CPU fixture cannot invoke physical driver', lambda: adapter.execute_phase(cluster, contract, binding, 'C0', 'cpu_run'))
        for phase in ('C0', 'C1', 'C2a', 'C2b', 'C3', 'SSD_only'):
            _, current = cluster.inventory()
            save = adapter.compile_phase(contract, binding, phase, 'cpu_run', current, 'save')
            if phase != 'SSD_only':
                rejected(f'{phase} restore before materialization refused',
                         lambda: adapter.compile_phase(contract, binding, phase, 'cpu_run', current, 'restore'))
                saved = cluster.transfer(save)
                check(f'{phase} save SHA/native completion', saved['verification']['passed'])
            else:
                saved = None
            _, current = cluster.inventory()
            restore = adapter.compile_phase(contract, binding, phase, 'cpu_run', current, 'restore')
            restored = cluster.transfer(restore)
            check(f'{phase} restore 32MiB SHA/native completion', restored['verification']['checked_bytes'] == contract['payload_bytes'])
            # Exercise real planner compatibility without introducing made-up rates.
            targets = restore['targets']
            wire = planning_payload(current, targets, binding['workers'], {'rates': {}}, 'nearest')
            bound = bind_manifests(dict(restore, targets=wire['targets']), targets)
            check(f'{phase} wire/native manifest binding', bound['targets'] == targets)
            for target in targets:
                worker = cluster.workers[target['worker']]
                actual = worker.catalog.entries[target['id']]['tensor']
                expected = torch.cat([payloads[f'b{2 * target["tp"]}'], payloads[f'b{2 * target["tp"] + 1}']])
                check(f'{phase} rank{target["tp"]} full independent bytes', torch.equal(actual, expected))
            check(f'{phase} all source leases/transactions released', all(not w.catalog.transactions and
                  not w.runs and all(e['leases'] == 0 for e in w.catalog.entries.values()) for w in cluster.workers))
            ids = [t['id'] for t in save['targets'] + restore['targets']]
            cleanup = dict(run_id='cpu_run', phase=phase, owned_ids=ids, base_ids=list(binding['base_ids'].values()))
            cleanup['owned_descriptors'] = [dict(t, node=binding['workers'][t['worker']]['node'],
                gpu=binding['workers'][t['worker']]['gpu'], epoch=binding['workers'][t['worker']]['epoch'])
                for t in save['targets'] + restore['targets']]
            cleanup['base_descriptors'] = initial
            poison = dict(cleanup, owned_ids=ids + [cleanup['base_ids'][0]])
            rejected(f'{phase} base cleanup overlap refused', lambda: adapter.cleanup_owned(cluster, poison))
            adapter.cleanup_owned(cluster, cleanup)
            check(f'{phase} exact owned copies released', all(w.catalog.entries[r]['state'] == 'RELEASED'
                  for w in cluster.workers for r in ids if r in w.catalog.entries))
            check(f'{phase} complete SSD base preserved', all(w.catalog.entries[r]['state'] == 'VALID'
                  for w in cluster.workers for r in cleanup['base_ids'] if r in w.catalog.entries))
            runs.append(dict(phase=phase, save_plan=save, restore_plan=restore,
                             save_result=saved, restore_result=restored, cleanup=cleanup))
            (out / 'native_api_runs.json').write_text(json.dumps(runs, indent=2) + '\n')
        base = cluster.workers[0].catalog.entries[binding['base_ids']['b0']]['owner']
        rejected('cannot construct RemoteExtent over existing base', lambda: RemoteExtent(base.endpoint, binding['base_ids']['b0'], base.nbytes))
        scratch = RemoteExtent(endpoints[0], 'cpu_disposable_protocol_probe', 17)
        scratch.write(0, torch.arange(17, dtype=torch.uint8))
        scratch.commit()
        check('real local HTTP RemoteExtent read protocol', torch.equal(scratch.read(0, 17), torch.arange(17, dtype=torch.uint8)))
        scratch_path = out / 'cpu_files_n0' / (scratch.key + '.bin')
        scratch.close()
        check('RemoteExtent.close deletes disposable extent', not scratch_path.exists())
        check('all eight base files remain after cleanup', sum(len(list((out / f'cpu_files_n{i}').glob('*.bin'))) for i in range(3)) == 8)
        (out / 'native_api_runs.json').write_text(json.dumps(runs, indent=2) + '\n')
    except BaseException as exc:
        (out / 'failure.json').write_text(json.dumps(dict(error=repr(exc), checks=checks), indent=2) + '\n')
        raise
    finally:
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        for name, value in original_cuda.items():
            setattr(torch.cuda, name, value)
        torch.distributed.broadcast = original_broadcast
    sources = [Path(adapter.__file__), Path(__file__)] + [adapter.ROOT / p for p in (
        'main_lib/fluidckpt/cluster/worker.py', 'main_lib/fluidckpt/cluster/control.py',
        'main_lib/fluidckpt/cluster/catalog.py', 'main_lib/fluidckpt/cluster/planning.py',
        'main_lib/fluidckpt/cluster/wire.py', 'main_lib/fluidckpt/memory/ssd.py',
        'main_lib/fluidckpt/serving/ssd_store.py', 'main_lib/fluidckpt/executor/chunks.py')]
    summary = dict(passed=all(c['passed'] for c in checks), checks=checks, check_count=len(checks),
                   contract_hash=adapter.FROZEN, native_methods=['Cluster.transfer/verify/finish',
                   'ClusterWorker.prepare/_execute/land/verify_run/complete/finish', 'Catalog', 'wire'],
                   CPU_mock_components=['GPU allocation/staging limited to one 4MiB block',
                       'CUDA set_device/synchronize/empty_cache', 'NCCL broadcast/group replaced with CPU thread barrier',
                       'worker HTTP control transport replaced by direct threaded calls'],
                   real_local_HTTP=True, GPU_calls=0, remote_operations=0, physical_ready=False,
                   model_ready_measured=False, rate_samples=0,
                   source_sha256={str(p): sha(p) for p in sources})
    (out / 'qualification.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(dict(passed=summary['passed'], check_count=len(checks), output=str(out))))


if __name__ == '__main__':
    main()
