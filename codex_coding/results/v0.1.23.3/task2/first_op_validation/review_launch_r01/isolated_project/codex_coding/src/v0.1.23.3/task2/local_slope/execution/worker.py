"""Local-only phase overlay; native controls call the unchanged native executor."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[6]
OLD = ROOT / 'codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/worker.py'
sys.path.insert(0, str(OLD.parent))
spec = importlib.util.spec_from_file_location('local_slope_inventory_worker', OLD)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
native = original.original.native
torch = native.torch

def tensor_metadata(tensor):
    pointer = tensor.data_ptr()
    return dict(data_ptr=pointer, numel=tensor.numel(), dtype=str(tensor.dtype),
                device=str(tensor.device), shape=list(tensor.shape), stride=list(tensor.stride()),
                storage_offset=tensor.storage_offset(), contiguous=tensor.is_contiguous(),
                alignment_remainders={str(n):pointer % n for n in (256, 4096, 2 << 20)})

def memory_metadata(dev):
    free, total = torch.cuda.mem_get_info(dev)
    return dict(allocated_bytes=torch.cuda.memory_allocated(dev), reserved_bytes=torch.cuda.memory_reserved(dev),
                hip_free_bytes=free, hip_total_bytes=total)

def clocks():
    return time.monotonic_ns(), time.thread_time_ns()

class LocalSlopeWorker(original.DiagnosticRuntimeWorker):
    def __init__(self, args):
        self._slope_events = {}
        super().__init__(args)

    def prepare(self, body):
        result = super().prepare(body)
        plan = body['plan']
        if 'local_slope_mode' not in plan:
            return result
        try:
            if self.rank != 4 or plan['local_slope_mode'] not in ('native', 'diagnostic'):
                raise ValueError('diagnostic overlay restricted to worker4 and two explicit modes')
            if len(plan['ops']) != 1 or plan['executor'] != 'serial' or plan['chunk_bytes'] != 32 << 20:
                raise ValueError('single local serial operation required')
            op = plan['ops'][0]
            target = self.runs[body['tx']]['targets'][op['target']]
            source = self.catalog.entries[op['replica_id']]
            if (op['src_w'], op['dst_w'], op['kind'], source['tier'], target['tier']) != (4, 4, 'local_hbm', 'hbm', 'hbm'):
                raise ValueError('exact localHBM replica path required')
            if source['n'] != 16 << 20 or op['n'] not in (4 << 20, 16 << 20) or op['source_offset'] != 0 or op['off'] != 0:
                raise ValueError('fixed 16MiB source and zero-offset 4/16MiB destination required')
            if target['tensor'].numel() != op['n'] or source['tensor'].numel() != 16 << 20:
                raise ValueError('exact source/destination allocations required')
            metadata = dict(mode=plan['local_slope_mode'], pid=os.getpid(), prepare_native_tid=threading.get_native_id(),
                prepare_thread_name=threading.current_thread().name, prepare_thread_ident=threading.get_ident(),
                source=tensor_metadata(source['tensor']), target=tensor_metadata(target['tensor']),
                source_identity={key:source[key] for key in ('id', 'identity', 'epoch', 'generation', 'off', 'n')},
                source_offset=op['source_offset'], target_offset=op['off']-target['off'], bytes=op['n'],
                tensor_manifest_index=source['index'], manifest_is_copy_path=False,
                memory_after_target_allocate=memory_metadata(self.dev), metadata_outside_service=True)
            if plan['local_slope_mode'] == 'diagnostic':
                begin = time.monotonic_ns()
                events = [torch.cuda.Event(enable_timing=True) for _ in range(2)]
                for event in events:
                    event.record()
                torch.cuda.synchronize(self.dev)
                self._slope_events[body['tx']] = events
                metadata.update(event_prepare_elapsed_ns=time.monotonic_ns()-begin,
                                events_created_and_initialized_in_prepare=True,
                                extra_prepare_event_initialization_sync=True,
                                event_prepare_native_tid=threading.get_native_id())
            self.runs[body['tx']]['local_slope_prepare'] = metadata
            return dict(result, local_slope_prepare=metadata)
        except BaseException:
            self.abort({'tx':body['tx']})
            raise

    def _execute(self, body):
        run = self.runs[body['tx']]
        mode = run['plan'].get('local_slope_mode')
        if mode != 'diagnostic':
            # This is the exact original method, including its stamp/log/copy/sync boundaries.
            result = original.NATIVE_WORKER._execute(self, body)
            if mode == 'native':
                result['local_slope_prepare'] = run['local_slope_prepare']
                result['executor_source'] = 'unchanged native ClusterWorker._execute'
                result['execute_thread'] = dict(native_tid=threading.get_native_id(), name=threading.current_thread().name,
                                                ident=threading.get_ident(), sampled_after_native_execute=True)
            return result
        torch.cuda.set_device(self.gpu)
        if run['phase'] != 'PREPARED':
            raise ValueError('transaction is not prepared')
        started = time.monotonic()
        run['phase'] = 'EXECUTING'
        chunk = native.execution_chunk(run['plan'])
        self.record_targets(run, 'target_execute_begin')
        op = run['plan']['ops'][0]
        source = self.catalog.entries[op['replica_id']]
        target = run['targets'][op['target']]
        event_begin, event_end = self._slope_events[body['tx']]
        execute_tid = threading.get_native_id()
        execute_thread_name = threading.current_thread().name
        execute_thread_ident = threading.get_ident()
        boundaries = [clocks()]
        names = []
        def boundary(name):
            boundaries.append(clocks())
            names.append(name)
        try:
            self.log(ev='cluster_op_begin', tx=body['tx'], op_id=op['op_id'],
                     src=op['src_w'], dst=op['dst_w'], n=op['n'], kind=op['kind'])
            boundary('begin_log')
            n = min(chunk, op['n'])
            a = op['source_offset']
            view = source['tensor'][a:a+n]
            boundary('source_slice')
            offset = op['off']-target['off']
            destination = target['tensor'][offset:offset+view.numel()]
            boundary('target_slice')
            event_begin.record()
            boundary('event_begin_record')
            destination.copy_(view, non_blocking=True)
            boundary('copy_submit')
            event_end.record()
            boundary('event_end_record')
            torch.cuda.synchronize(self.dev)
            boundary('device_sync')
            service_end_ns = boundaries[-1][0]
            elapsed = (service_end_ns-boundaries[0][0])/1e9
            record = dict(op_id=op['op_id'], role='both', replica_id=op['replica_id'], bytes=op['n'],
                          kind=op['kind'], elapsed_s=elapsed, pipeline={})
            self.log(ev='cluster_op_end', tx=body['tx'], op_id=op['op_id'], elapsed_s=time.monotonic()-boundaries[0][0]/1e9)
            run.update(phase='FILLED', records=[record])
            self.record_targets(run, 'target_copy_complete')
            execute_s = time.monotonic()-started
            post_service_diagnosis_begin_ns = time.monotonic_ns()
            phases = {name:dict(wall_ns=b[0]-a[0], thread_cpu_ns=b[1]-a[1])
                      for name, a, b in zip(names, boundaries, boundaries[1:])}
            phase_sum = sum(row['wall_ns'] for row in phases.values())
            diagnosis = dict(host_phases=phases, host_phase_sum_ns=phase_sum,
                service_begin_ns=boundaries[0][0], service_end_ns=service_end_ns,
                host_residual_ns=service_end_ns-boundaries[0][0]-phase_sum,
                host_thread_cpu_ns=boundaries[-1][1]-boundaries[0][1],
                phase_boundary_clock='monotonic_ns and thread_time_ns, same ordered boundary pairs',
                phase_observer_overhead_included=True, execute_native_tid=execute_tid,
                execute_thread_name=execute_thread_name, execute_thread_ident=execute_thread_ident,
                source_view=tensor_metadata(view), target_view=tensor_metadata(destination),
                memory_after_sync=memory_metadata(self.dev),
                gpu_event_elapsed_ms=event_begin.elapsed_time(event_end),
                gpu_event_scope='stream event window includes copy submission/host gaps; not a pure kernel duration',
                stream=int(torch.cuda.current_stream(self.dev).cuda_stream), metadata_outside_service=True,
                extra_execute_sync_before_service=False, extra_prepare_event_initialization_sync=True,
                residual_scope='adjacent phase boundaries telescope; zero is not proof of zero observer overhead',
                executor_difference='expanded single local branch bypasses native land dispatch checks; timestamps/events add observer work',
                control_limitation='native block precedes diagnostic block; instrumentation and temporal drift confounded')
            diagnosis['post_service_diagnosis_wall_ns'] = time.monotonic_ns()-post_service_diagnosis_begin_ns
            diagnosis['post_service_diagnosis_scope'] = 'included in HTTP/controller execute wall; excluded from reported worker execute_s and op service_s; not controller dispatch cost'
            record['local_slope_diagnosis'] = diagnosis
            return dict(worker=self.rank, records=[record], execute_s=execute_s, executor='serial',
                        staging_bytes=native.STAGING_BYTES, chunk_bytes=chunk,
                        local_slope_prepare=run['local_slope_prepare'], executor_source='expanded_single_local_branch_with_timing_observer')
        except BaseException:
            run['phase'] = 'FAILED'
            raise

    def finish(self, body):
        try:
            return super().finish(body)
        finally:
            self._slope_events.pop(body['tx'], None)

    def abort(self, body):
        try:
            return super().abort(body)
        finally:
            self._slope_events.pop(body['tx'], None)

def main():
    p = argparse.ArgumentParser()
    for key in ('node', 'mesh', 'master', 'handoff', 'session'):
        p.add_argument('--' + key, required=True)
    for key in ('gpu', 'port-base', 'numa'):
        p.add_argument('--' + key, type=int, required=True)
    for tier in ('dram', 'hbm', 'ssd'):
        p.add_argument('--' + tier + '-budget', type=int, default=64 << 20)
    p.add_argument('--inventory-mode-file', type=Path)
    p.add_argument('--allow-static-cache', action='store_true')
    p.set_defaults(memory_backend='numa_registered', replica_budget_bytes=64 << 20)
    native.ClusterWorker = LocalSlopeWorker
    native.main(p.parse_args())

if __name__ == '__main__':
    main()
