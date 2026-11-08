"""Opt-in HIP tracing and host attribution; never used for benchmark claims."""
import cProfile
import ctypes
import json
from pathlib import Path
import pstats
import resource
import threading
import time


def host_sample():
    usage = resource.getrusage(resource.RUSAGE_THREAD)
    sched = Path(f"/proc/self/task/{threading.get_native_id()}/schedstat")
    return {"monotonic_ns": time.monotonic_ns(), "wall_ns": time.time_ns(),
            "thread_cpu_ns": time.thread_time_ns(),
            "scheduler": list(map(int, sched.read_text().split())),
            "voluntary_switches": usage.ru_nvcsw, "involuntary_switches": usage.ru_nivcsw}


class ExecuteProfile:
    def __init__(self, directory, worker, tx):
        self.path = Path(directory) / f"worker{worker}_{tx}"
        self.tracer = ctypes.CDLL("libroctracer64.so")
        self.roctx = ctypes.CDLL("libroctx64.so")
        self.utils = ctypes.CDLL("/opt/dtk/hipprof_utils/lib/libhipprof_utils.so")
        for name in ("roctracer_start", "roctracer_stop"):
            getattr(self.tracer, name).restype = None
        self.utils.hipprof_flush_all.restype = None
        self.roctx.roctxRangePushA.argtypes = [ctypes.c_char_p]
        self.cpu = cProfile.Profile()
        self.operations = []

    def begin_op(self, op):
        self.roctx.roctxRangePushA(f"fluidckpt/op/{op['op_id']}".encode())
        self.operations.append({"op_id": op["op_id"], "begin": host_sample()})

    def end_op(self):
        self.operations[-1]["end"] = host_sample()
        self.roctx.roctxRangePop()

    def __enter__(self):
        self.tracer.roctracer_start()
        self.begin = host_sample()
        self.cpu.enable()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cpu.disable()
        self.end = host_sample()
        self.tracer.roctracer_stop()
        self.tracer.roctracer_flush_activity()
        self.utils.hipprof_flush_all()
        self.cpu.dump_stats(str(self.path)+".pstats")
        stats = pstats.Stats(self.cpu)
        functions = [{"file": key[0], "line": key[1], "function": key[2],
                      "primitive_calls": value[0], "calls": value[1],
                      "self_s": value[2], "cumulative_s": value[3]}
                     for key, value in stats.stats.items()]
        report = {"begin": self.begin, "end": self.end, "operations": self.operations,
                  "error": repr(exc) if exc else None,
                  "cpu_functions": sorted(functions, key=lambda x: -x["self_s"]),
                  "scope": "instrumented execution; API wall time includes waits; GPU kernel time may include peer waits"}
        Path(str(self.path)+".json").write_text(json.dumps(report, indent=2))
