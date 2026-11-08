"""Replay real old schemas through native prepare/execute/verify/finish on CPU doubles."""
import argparse
import copy
import ctypes
import hashlib
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from common import ROOT, HERE, CALIBRATION, BOUNDED, PRIOR_PREPARED, CASES, PROTOCOL, native, write, ref, require, fresh_output, load
import runner

class CpuTensor:
    def __init__(self, array, base=None, offset=0):
        self.array = memoryview(array)
        self.base = base if base is not None else array
        self.offset = offset
        self.dtype, self.device, self.shape = 'torch.uint8', 'cuda:0', (len(array),)
    def __getitem__(self, key):
        require(isinstance(key, slice) and key.step in (None, 1), 'CPU double accepts only contiguous slices')
        start = key.start or 0
        return CpuTensor(self.array[key], self.base, self.offset + start)
    def numel(self): return len(self.array)
    def stride(self): return (1,)
    def storage_offset(self): return self.offset
    def is_contiguous(self): return True
    def data_ptr(self): return ctypes.addressof(ctypes.c_ubyte.from_buffer(self.array))
    def copy_(self, source, non_blocking=False):
        self.array[:] = source.array
        require(self.array == source.array, 'CPU double copy bytes differ')
        return self

class CpuEvent:
    def __init__(self, enable_timing=False): self.timestamp = None
    def record(self): self.timestamp = time.monotonic_ns()
    def elapsed_time(self, other): return (other.timestamp-self.timestamp)/1e6

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    cpu_cuda = SimpleNamespace(set_device=lambda *args:None, synchronize=lambda *args:None,
        mem_get_info=lambda *args:(15 << 30, 16 << 30), memory_allocated=lambda *args:0,
        memory_reserved=lambda *args:0, Event=CpuEvent, current_stream=lambda *args:SimpleNamespace(cuda_stream=0),
        empty_cache=lambda:None, ipc_collect=lambda:None)
    fake_torch = ModuleType('torch')
    fake_torch.cuda, fake_torch.uint8 = cpu_cuda, 'torch.uint8'
    sys.modules['torch'] = fake_torch
    overlay = load('local_slope_cpu_overlay', HERE / 'worker.py')
    old_actual = BOUNDED / 'execution_actual_r03'
    workers = native.read(old_actual / 'mesh/mesh.json')['workers']
    setup = native.read(old_actual / 'runtime_setup.json')
    launch = copy.deepcopy(native.read(PRIOR_PREPARED / 'launch_plan.json'))
    session = 'local_slope_runtime_cpu_replay'
    launch.update(session=session, protocol=PROTOCOL, model_consumed=False, setup_directory=str(PRIOR_PREPARED))
    launch_path = out / 'launch_plan.json'
    write(launch_path, launch)
    setup.update(session=session, valid_until=time.time()+300, evidence_refs={'plan':ref(launch_path)}, CPU_REPLAY_ONLY=True)
    setup_path = out / 'runtime_setup.json'
    write(setup_path, setup)
    for w in workers:
        w.update(session=session, epoch='cpu_replay/' + str(w['worker']), replicas=[], used_bytes=dict(hbm=0, dram=0, ssd=0), transactions=[])
    mesh = native.read(old_actual / 'mesh/mesh.json')
    mesh.update(workers=workers, incarnations=runner.new_state(workers)['incarnations'], CPU_REPLAY_ONLY=True)
    (out / 'mesh').mkdir()
    write(out / 'mesh/mesh.json', mesh)
    training = CALIBRATION / 'execution_actual_r02/measure'
    synth = native.read(training / 'journal.json')['synth'][0]
    bootstrap = native.read(training / 'bootstrap0.json')
    sha_by_size = {}
    for name in ('p0_warm_small', 'p0_warm_large'):
        flow = native.read(training / (name + '.json'))['flow']
        row = next(iter(flow['verification']['operations'].values()))
        sha_by_size[row['n']] = row['source_sha256']
    events, fake_workers = [], []
    from fluidckpt.cluster.catalog import Catalog
    # The byte pattern tests copying independently; old hashes are schema oracles, not hashes of this CPU pattern.
    pattern = bytes(range(256)) * ((16 << 20)//256)
    for index, old in enumerate(workers):
        cls = overlay.LocalSlopeWorker if index == 4 else overlay.original.NATIVE_WORKER
        worker = cls.__new__(cls)
        worker.rank, worker.node, worker.gpu, worker.dev = index, old['node'], old['gpu'], 'cuda:0'
        worker.epoch = old['epoch']
        worker.a = SimpleNamespace(numa=old['numa'])
        worker.runs, worker.catalog = {}, Catalog(dict(hbm=1 << 30, dram=1 << 30, ssd=1 << 30))
        worker.execution_profile = None
        worker._slope_events = {}
        worker.log = lambda **row:events.append(row)
        worker.allocate = lambda desc:(CpuTensor(bytearray(desc['n'])), None)
        def inventory(compact=False, index=index, worker=worker):
            result = copy.deepcopy(workers[index])
            result.update(replicas=worker.catalog.inventory(compact=compact), used_bytes=worker.catalog.used(),
                          transactions=list(worker.runs))
            return result
        worker.inventory = inventory
        def digest(entry, off, n):
            tensor = entry['tensor']
            require(tensor.array[off:off+n] == pattern[:n], 'CPU replay actual copied bytes differ')
            return sha_by_size[n]
        worker.extent_digest = digest
        worker.target_digest = digest
        fake_workers.append(worker)
    native_request_count, verification_keys = [], []
    def request(endpoint, operation, body):
        index = next(w['worker'] for w in setup['workers'] if w['endpoint'] == endpoint)
        worker = fake_workers[index]
        native_request_count.append(operation)
        if operation == 'synth':
            require(index == 4 and body['tp'] == 0 and body['seed'] == 3100 and body['nbytes'] == 16 << 20, 'source0 exact frozen generator contract changed')
            return copy.deepcopy(synth['diagnostics'])
        if operation == 'adopt':
            row = copy.deepcopy(synth['row'])
            row.update(id=body['id'], epoch=worker.epoch)
            worker.catalog.reserve(row, lambda:(CpuTensor(bytearray(pattern)), None))
            worker.catalog.publish(row['id'], True)
            return next(r for r in worker.catalog.inventory() if r['id'] == row['id'])
        if operation == 'retire':
            return worker.retire(body)
        raise AssertionError('unexpected request ' + operation)
    class ReplayCluster(runner.Cluster):
        def call_all(self, operation, bodies=None, telemetry=None):
            if operation.startswith('inventory'):
                return [worker.inventory(compact=operation.endswith('compact=1')) for worker in fake_workers]
            reports = []
            for index, worker in enumerate(fake_workers):
                reports.append(getattr(worker, operation)(bodies[index]))
            return reports
        def verify(self, tx):
            result = super().verify(tx)
            verification_keys.append(list(result['operations']))
            return result
    runner.Cluster = ReplayCluster
    native.request = request
    native.ssd_stats = lambda setup:copy.deepcopy(native.read(training / 'final_inventory.json')['ssd'])
    sys.argv = ['runner.py', '--setup', str(setup_path), '--plan', str(launch_path), '--output', str(out / 'measure'), '--stage', 'measure']
    runner.main()
    results = native.read(out / 'measure/measurements.json')
    require(len(results) == 8 and [r['case'] for r in results] == CASES, 'full predeclared eight requests did not run')
    require(verification_keys == [[0]] * 9, 'bootstrap/eight requests native verification integer keys changed')
    require(all(not w._slope_events and not w.runs and not any(w.catalog.used().values()) for w in fake_workers), 'native transaction/catalog/event cleanup failed')
    require(len([e for e in events if e.get('ev') == 'cluster_op_begin']) == 9, 'expected native+diagnostic op logs missing')
    for result in results:
        record = result['flow']['execution'][4]['records'][0]
        if result['case']['mode'] == 'diagnostic':
            diag = record['local_slope_diagnosis']
            require(set(diag['host_phases']) == {'begin_log', 'source_slice', 'target_slice', 'event_begin_record', 'copy_submit', 'event_end_record', 'device_sync'}, 'phase partition differs')
            require(diag['host_residual_ns'] == 0 and diag['gpu_event_elapsed_ms'] >= 0, 'CPU clock bookkeeping invalid')
        else:
            require('local_slope_diagnosis' not in record, 'native control unexpectedly instrumented')
    bad = copy.deepcopy(bootstrap)
    next(iter(bad['verification']['operations'].values()))['target_sha256'] = 'wrong'
    try:
        runner.bootstrap_content_matches(bad, bootstrap)
    except ValueError:
        pass
    else:
        raise AssertionError('wrong checksum accepted')
    qualification = dict(passed=True, CPU_REPLAY_ONLY=True, GPU_launches=0, remote_writes=0,
        schema_source_ref=ref(training / 'journal.json'), bootstrap_schema_ref=ref(training / 'bootstrap0.json'),
        simulated_requests=8, actual_GPU_measurements=0, fixture_digest_policy='old all-byte SHA values used as metadata oracles; CPU patterned buffers independently compared byte-for-byte; CPU clocks/events are not GPU timing evidence',
        checks=['real old source0 schema synth/adopt contract', 'real bootstrap integer/string verification keys',
                'native Cluster.transfer prepare execute verify complete finish for bootstrap plus all8',
                'native unchanged _execute controls plus local diagnostic overlay', 'fresh exact-size flat CPU tensors copy all bytes',
                'host wall/thread_cpu phases and event outputs serialize', 'preregistration precedes first transfer',
                'native exact_release catalog leases transactions events cleared', 'wrong SHA rejected'],
        copied_bytes=(16 << 20)+sum(case['nbytes'] for case in CASES))
    write(out / 'qualification.json', qualification)
    print(str(out / 'qualification.json'))

if __name__ == '__main__':
    main()
