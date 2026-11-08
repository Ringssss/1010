"""Inventory calibration overlay; reuse the established worker and native HTTP handler."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from identity_diagnostics import LibraryCache, Segments, runtime_identity

LEGACY_SOURCE = ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/runtime_worker.py'
spec = importlib.util.spec_from_file_location('inventory_original_runtime_worker', LEGACY_SOURCE)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
NATIVE_WORKER = original.native.ClusterWorker


class DiagnosticRuntimeWorker(original.RuntimeWorker):
    def __init__(self, args):
        self._inventory_cache = LibraryCache()
        self._inventory_sequence = 0
        self._inventory_diagnostic_lock = threading.Lock()
        self._inventory_mode_file = getattr(args, 'inventory_mode_file', None)
        self._inventory_allow_static_cache = getattr(args, 'allow_static_cache', False)
        super().__init__(args)

    def _read_mode(self):
        mode = 'legacy'
        if self._inventory_mode_file:
            body = json.loads(Path(self._inventory_mode_file).read_text())
            if not isinstance(body, dict) or set(body) != {'mode'}:
                raise ValueError('inventory mode file must contain only mode')
            mode = body['mode']
        if mode not in ('legacy', 'cached'):
            raise ValueError('unknown inventory mode')
        if mode == 'cached' and not self._inventory_allow_static_cache:
            raise ValueError('cached inventory requires explicit --allow-static-cache')
        return mode

    def inventory(self, compact=False):
        start = time.monotonic_ns()
        # The native POST lock remains unchanged; this independent lock avoids reentry deadlock.
        with self._inventory_diagnostic_lock:
            sequence = self._inventory_sequence = self._inventory_sequence + 1
            timings = Segments()
            mode = timings.measure('mode_file_read', self._read_mode)
            result = timings.measure('native_inventory', lambda: NATIVE_WORKER.inventory(self, compact))
            identity, cache_hit = runtime_identity(self.gpu, mode, timings,
                                                   self._inventory_cache, sequence)
            result.update(runtime_identity=identity, host_allocations=self.host_allocations,
                          host_allocations_scope='historical successful allocations; not a current residency ledger')
            end = time.monotonic_ns()
            segment_sum = sum(timings.values.values())
            result['inventory_diagnosis'] = dict(
                schema_version=1, mode=mode, sequence=sequence,
                worker_inventory_start_ns=start, worker_inventory_end_ns=end,
                worker_inventory_elapsed_ns=end-start, segments_ns=timings.values,
                segment_sum_ns=segment_sum, residual_ns=end-start-segment_sum,
                library_cache_hit=cache_hit, library_path=self._inventory_cache.path,
                cache_origin_sequence=self._inventory_cache.origin_sequence,
                cache_scope='resolved library path and CDLL handle only; guarded by current PID',
                fresh_fields=['native_inventory', 'HIP BDF', 'PCI NUMA', 'HCA metadata',
                              'boot/PID/start', 'cgroup path/namespace/current/max',
                              'environment/RSS', 'host allocation history'],
                clock_domain='worker process monotonic_ns; do not subtract controller timestamps',
                serialization_in_timer=False,
                timer_boundary='inventory entry through complete original inventory and identity fields; diagnosis assembly and HTTP serialization excluded')
            return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', required=True)
    parser.add_argument('--gpu', required=True, type=int)
    parser.add_argument('--port-base', required=True, type=int)
    parser.add_argument('--mesh', required=True)
    parser.add_argument('--master', required=True)
    parser.add_argument('--handoff', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--numa', required=True, type=int)
    parser.add_argument('--dram-budget', type=int, default=64 << 20)
    parser.add_argument('--hbm-budget', type=int, default=64 << 20)
    parser.add_argument('--ssd-budget', type=int, default=64 << 20)
    parser.add_argument('--inventory-mode-file', type=Path)
    parser.add_argument('--allow-static-cache', action='store_true')
    parser.set_defaults(memory_backend='numa_registered', replica_budget_bytes=64 << 20)
    original.native.ClusterWorker = DiagnosticRuntimeWorker
    original.native.main(parser.parse_args())


if __name__ == '__main__':
    main()
