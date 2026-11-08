"""Two identical host-timed local copies in one handler, without event/prewarm work."""
import argparse
import importlib.util
from pathlib import Path
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('local_slope_frozen_worker', HERE / 'worker.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
native, torch = previous.native, previous.torch

class RepeatWorker(previous.LocalSlopeWorker):
    def prepare(self, body):
        result = previous.original.NATIVE_WORKER.prepare(self, body)
        if 'local_slope_repeat' not in body['plan']:
            return result
        try:
            plan = body['plan']
            op = plan['ops'][0]
            source = self.catalog.entries[op['replica_id']]
            target = self.runs[body['tx']]['targets'][op['target']]
            if self.rank != 4 or len(plan['ops']) != 1 or plan['local_slope_repeat'] != 2:
                raise ValueError('exactly two copies of one worker4 local operation required')
            if (op['src_w'], op['dst_w'], op['kind'], source['tier'], target['tier']) != (4,4,'local_hbm','hbm','hbm'):
                raise ValueError('same worker HBM replica path required')
            if source['n'] != 16 << 20 or op['n'] not in (4 << 20,16 << 20) or op['source_offset'] != 0 or op['off'] != 0:
                raise ValueError('frozen source and size contract differs')
            if plan['executor'] != 'serial' or plan['chunk_bytes'] != 32 << 20:
                raise ValueError('original serial chunk required')
            # No pointer/stream/memory query, events, warm-up, or new view is performed here.
            return result
        except BaseException:
            self.abort({'tx':body['tx']})
            raise

    def _execute(self, body):
        run = self.runs[body['tx']]
        if 'local_slope_repeat' not in run['plan']:
            return previous.original.NATIVE_WORKER._execute(self, body)
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
        raw_clocks, raw_views = [], []
        service_begin_ns = time.monotonic_ns()
        try:
            for round_index in range(2):
                boundaries = [previous.clocks()]
                self.log(ev='cluster_op_begin', tx=body['tx'], op_id=op['op_id'],
                         src=op['src_w'], dst=op['dst_w'], n=op['n'], kind=op['kind'], repeat_round=round_index)
                boundaries.append(previous.clocks())
                n = min(chunk, op['n'])
                a = op['source_offset']
                view = source['tensor'][a:a+n]
                boundaries.append(previous.clocks())
                offset = op['off']-target['off']
                destination = target['tensor'][offset:offset+view.numel()]
                boundaries.append(previous.clocks())
                destination.copy_(view, non_blocking=True)
                boundaries.append(previous.clocks())
                torch.cuda.synchronize(self.dev)
                boundaries.append(previous.clocks())
                raw_clocks.append(boundaries)
                raw_views.append((view, destination))
                self.log(ev='cluster_op_end', tx=body['tx'], op_id=op['op_id'],
                         elapsed_s=(boundaries[-1][0]-boundaries[0][0])/1e9, repeat_round=round_index)
            service_end_ns = time.monotonic_ns()
            record = dict(op_id=op['op_id'], role='both', replica_id=op['replica_id'], bytes=op['n'],
                actual_copy_bytes=2*op['n'], logical_SHA_bytes=op['n'], copy_rounds=2, kind=op['kind'],
                elapsed_s=(service_end_ns-service_begin_ns)/1e9, pipeline={})
            run.update(phase='FILLED', records=[record])
            self.record_targets(run, 'target_copy_complete')
            execute_s = time.monotonic()-started
            post_begin = time.monotonic_ns()
            names = ('begin_log','source_slice','target_slice','copy_submit','device_sync')
            rounds = []
            for index, boundaries in enumerate(raw_clocks):
                phases = {name:dict(wall_ns=b[0]-a[0], thread_cpu_ns=b[1]-a[1]) for name,a,b in zip(names,boundaries,boundaries[1:])}
                elapsed_ns = boundaries[-1][0]-boundaries[0][0]
                rounds.append(dict(round=index, bytes=op['n'], elapsed_ns=elapsed_ns,
                    host_phases=phases, host_residual_ns=elapsed_ns-sum(row['wall_ns'] for row in phases.values()),
                    source_view=previous.tensor_metadata(raw_views[index][0]),
                    target_view=previous.tensor_metadata(raw_views[index][1]),
                    native_tid=threading.get_native_id(), thread_ident=threading.get_ident(),
                    thread_name=threading.current_thread().name, metadata_after_both_rounds=True))
            diagnosis = dict(rounds=rounds, same_source_view_address=rounds[0]['source_view']['data_ptr']==rounds[1]['source_view']['data_ptr'],
                same_target_view_address=rounds[0]['target_view']['data_ptr']==rounds[1]['target_view']['data_ptr'],
                service_begin_ns=service_begin_ns, service_end_ns=service_end_ns,
                between_round_and_bookkeeping_ns=service_end_ns-service_begin_ns-sum(row['elapsed_ns'] for row in rounds),
                actual_copy_bytes=2*op['n'], logical_SHA_bytes=op['n'], CUDA_events=0,
                extra_prewarm=False, extra_prepare_sync=False, phase_observer_overhead_included=True,
                stream_after_both_rounds=int(torch.cuda.current_stream(self.dev).cuda_stream),
                memory_after_both_rounds=previous.memory_metadata(self.dev),
                limitations='second round changes host first-use and target first-touch/cache; does not isolate pure copy bandwidth or constitute optimization')
            diagnosis['post_service_diagnosis_scope'] = 'within controller/RPC window, outside reported worker execute/service'
            record['local_slope_diagnosis'] = diagnosis
            metadata = dict(mode='host_repeat', source=previous.tensor_metadata(source['tensor']),
                target=previous.tensor_metadata(target['tensor']), same_handler=True, copy_rounds=2,
                metadata_after_both_rounds=True, source_offset=op['source_offset'], target_offset=op['off']-target['off'])
            diagnosis['post_service_diagnosis_wall_ns'] = time.monotonic_ns()-post_begin
            return dict(worker=self.rank, records=[record], execute_s=execute_s, executor='serial',
                staging_bytes=native.STAGING_BYTES, chunk_bytes=chunk, local_slope_prepare=metadata,
                executor_source='host_only_same_handler_two_copy_rounds')
        except BaseException:
            run['phase'] = 'FAILED'
            raise

def main():
    p = argparse.ArgumentParser()
    for key in ('node','mesh','master','handoff','session'):
        p.add_argument('--'+key, required=True)
    for key in ('gpu','port-base','numa'):
        p.add_argument('--'+key, type=int, required=True)
    for tier in ('dram','hbm','ssd'):
        p.add_argument('--'+tier+'-budget', type=int, default=64 << 20)
    p.add_argument('--inventory-mode-file', type=Path)
    p.add_argument('--allow-static-cache', action='store_true')
    p.set_defaults(memory_backend='numa_registered', replica_budget_bytes=64 << 20)
    native.ClusterWorker = RepeatWorker
    native.main(p.parse_args())

if __name__ == '__main__':
    main()
