"""Opt-in wall/CPU/GC attribution for target preparation; flush after mapped boundary."""
from contextlib import contextmanager
import gc
import json
from pathlib import Path
import resource
import threading
import time


class PrepareTrace:
    def __init__(self, directory, worker, tx):
        self.directory, self.worker, self.tx = Path(directory), worker, tx
        self.tid = threading.get_ident()
        self.started = time.monotonic_ns()
        self.rows, self.gc_events = [], []
        self.callback = self._gc
        gc.callbacks.append(self.callback)

    def _gc(self, phase, info):
        self.gc_events.append({'phase': phase, 'generation': info.get('generation'),
            'thread': threading.get_ident(), 'time_ns': time.monotonic_ns(),
            'collected': info.get('collected'), 'uncollectable': info.get('uncollectable')})

    @contextmanager
    def span(self, stage, **fields):
        wall, cpu = time.monotonic_ns(), time.thread_time_ns()
        before = resource.getrusage(resource.RUSAGE_THREAD)
        try:
            yield
        finally:
            end, cpu_end = time.monotonic_ns(), time.thread_time_ns()
            after = resource.getrusage(resource.RUSAGE_THREAD)
            self.rows.append(dict(stage=stage, begin_ns=wall, end_ns=end,
                wall_s=(end-wall)/1e9, thread_cpu_s=(cpu_end-cpu)/1e9,
                minor_faults=after.ru_minflt-before.ru_minflt,
                major_faults=after.ru_majflt-before.ru_majflt,
                voluntary_switches=after.ru_nvcsw-before.ru_nvcsw,
                involuntary_switches=after.ru_nivcsw-before.ru_nivcsw, **fields))

    def finish(self):
        gc.callbacks.remove(self.callback)
        self.callback = None
        end = time.monotonic_ns()
        self.directory.mkdir(parents=True, exist_ok=True)
        report = {'worker': self.worker, 'tx': self.tx, 'thread': self.tid,
            'begin_ns': self.started, 'end_ns': end, 'rows': self.rows, 'gc_events': self.gc_events,
            'scope': 'nested wall intervals overlap; CPU counters are for the handling thread; wall-minus-CPU does not identify the blocking subsystem'}
        path = self.directory/f'worker{self.worker}_{self.tx}.json'
        path.write_text(json.dumps(report, indent=2))

    def reader(self, stream):
        trace = self
        class TimedReader:
            def read(self, n=-1):
                with trace.span('file_read', requested=n):
                    return stream.read(n)

            def readline(self, n=-1):
                with trace.span('file_readline', requested=n):
                    return stream.readline(n)

            def readinto(self, buffer):
                with trace.span('file_readinto', requested=len(buffer)):
                    return stream.readinto(buffer)
        return TimedReader()
