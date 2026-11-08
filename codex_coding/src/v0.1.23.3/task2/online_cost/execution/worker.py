"""Observe native operations and verify native capture without replacing copy primitives."""
import argparse
import gc
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
spec = importlib.util.spec_from_file_location('online_runtime_worker', ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/runtime_worker.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
native = previous.native
from fluidckpt.cluster import capture as capture_module
from fluidckpt.serving.keeper import HbmTarget

original_capture = capture_module.capture_regions
CLOCK = 'host/' + socket.gethostname() + '/' + Path('/proc/sys/kernel/random/boot_id').read_text().strip() + '/monotonic_ns'


def verified_capture(worker, **kwargs):
    rows = original_capture(worker, **dict(kwargs, publish_marker=False, verify_full_sha=True))
    exports = {meta['tp_rank']: path for path, meta in worker._my_pkls(kwargs['tag'], 'capture')}
    # The native capture owns the sole received storage. A second raw import of
    # the same export token would decrement its IPC counter twice on destruction.
    gc.collect()
    native.torch.cuda.synchronize(worker.dev)
    native.torch.cuda.ipc_collect()
    if kwargs.get('publish_marker', True):
        for row in rows:
            marker = Path(exports[row['manifest']['tp']]).with_suffix('.captured')
            temporary = marker.with_suffix('.captured.tmp')
            with temporary.open('x') as stream:
                json.dump({'bytes': row['manifest']['n'], 'full_sha_passed': True}, stream)
            temporary.replace(marker)
    return rows


class OnlineWorker(previous.RuntimeWorker):
    def inventory(self, compact=False):
        result = super().inventory(compact)
        memory = Path('/proc/meminfo').read_text()
        result['runtime_identity']['host_memory_available_bytes'] = next(int(line.split()[1]) << 10 for line in memory.splitlines() if line.startswith('MemAvailable:'))
        cgroup_root = Path(result['runtime_identity']['cgroup_mount_root'])
        result['runtime_identity']['cgroup_memory_stat'] = {key: int(value) for key, value in
            (line.split() for line in (cgroup_root / 'memory.stat').read_text().splitlines()) if key in ('anon', 'file', 'shmem', 'kernel')}
        return result

    def log(self, **fields):
        if fields.get('ev') in ('cluster_op_begin', 'cluster_op_end'):
            self.online_intervals.append(dict(fields, monotonic_ns=time.monotonic_ns(),
                                              clock_domain=CLOCK, host=socket.gethostname(), pid=os.getpid()))
        return super().log(**fields)

    def _execute(self, body):
        self.online_intervals = []
        result = super()._execute(body)
        result['op_clock_events'] = self.online_intervals
        return result


def main():
    p = argparse.ArgumentParser()
    for name in ('node', 'mesh', 'master', 'handoff', 'session'):
        p.add_argument('--' + name, required=True)
    for name in ('gpu', 'numa', 'port-base', 'dram-budget', 'hbm-budget', 'ssd-budget'):
        p.add_argument('--' + name, required=True, type=int)
    p.set_defaults(memory_backend='numa_registered', replica_budget_bytes=1 << 30)
    capture_module.capture_regions = verified_capture
    native.ClusterWorker = OnlineWorker
    native.main(p.parse_args())


if __name__ == '__main__':
    main()
