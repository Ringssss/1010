"""Exercise the worker overlay using a fake native worker, without importing torch."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace

from identity_diagnostics import LibraryCache, Segments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).with_name('worker.py')
    node = next(item for item in ast.parse(source.read_text()).body
                if isinstance(item, ast.ClassDef) and item.name == 'DiagnosticRuntimeWorker')
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)
    class Base:
        def __init__(self, args):
            self.gpu = args.gpu
            self.host_allocations = []
            self.calls = 0
    class Native:
        @staticmethod
        def inventory(worker, compact):
            worker.calls += 1
            return dict(epoch='fresh-epoch-' + str(worker.calls), used_bytes={'dram': worker.calls},
                        compact=compact, transactions=[worker.calls],
                        headroom_sample_ns=time.monotonic_ns())
    def fake_identity(gpu, mode, timings, cache, sequence):
        timings.measure('library_resolution', lambda: None)
        cache.path, cache.origin_sequence = 'fake-lib', 1
        return dict(actual_bdf='0000:09:00.0', cgroup_memory_current_bytes=sequence), mode == 'cached'
    globals_ = dict(original=SimpleNamespace(RuntimeWorker=Base), NATIVE_WORKER=Native,
                    LibraryCache=LibraryCache, Segments=Segments, runtime_identity=fake_identity,
                    threading=threading, time=time, Path=Path, json=json)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), globals_)
    Worker = globals_['DiagnosticRuntimeWorker']
    with tempfile.TemporaryDirectory(prefix='inventory_overlay_cpu_') as temporary:
        mode_file = Path(temporary) / 'mode.json'
        mode_file.write_text('{"mode":"legacy"}')
        worker = Worker(SimpleNamespace(gpu=4, inventory_mode_file=mode_file, allow_static_cache=True))
        first = worker.inventory(compact=True)
        mode_file.write_text('{"mode":"cached"}')
        worker.host_allocations.append({'registered': True})
        second = worker.inventory(compact=True)
        check('mode-file legacy/cached switch applied on next inventory',
              first['inventory_diagnosis']['mode'] == 'legacy' and second['inventory_diagnosis']['mode'] == 'cached')
        check('native inventory invoked once on every call', worker.calls == 2)
        check('native catalog/epoch/transactions remain fresh', second['epoch'] == 'fresh-epoch-2'
              and second['used_bytes']['dram'] == 2 and second['transactions'] == [2])
        check('native compact argument passed through', first['compact'] and second['compact'])
        check('new allocation history retained', second['host_allocations'] == [{'registered': True}])
        check('identity queried for every request', second['runtime_identity']['cgroup_memory_current_bytes'] == 2)
        for index, result in enumerate((first, second), 1):
            profile = result['inventory_diagnosis']
            check(f'sequence {index} and timing conservation', profile['sequence'] == index
                  and profile['worker_inventory_elapsed_ns'] == profile['worker_inventory_end_ns'] - profile['worker_inventory_start_ns']
                  and profile['segment_sum_ns'] == sum(profile['segments_ns'].values())
                  and profile['worker_inventory_elapsed_ns'] == profile['segment_sum_ns'] + profile['residual_ns']
                  and profile['residual_ns'] >= 0)
        refused = Worker(SimpleNamespace(gpu=4, inventory_mode_file=mode_file, allow_static_cache=False))
        try:
            refused.inventory()
        except ValueError:
            check('cached mode explicit gate enforced before native call', refused.calls == 0)
        else:
            raise AssertionError('cached mode unexpectedly enabled')
        mode_file.write_text('{"mode":"cached","extra":1}')
        try:
            worker.inventory()
        except ValueError:
            check('invalid mode schema rejected before native call', worker.calls == 2)
        else:
            raise AssertionError('invalid schema unexpectedly accepted')
    body = dict(passed=True, check_count=len(checks), checks=checks,
                scope='AST-extracted overlay class with fake native and fake identity; no torch/HIP import',
                worker_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(body, stream, indent=2)
        stream.write('\n')
    print(json.dumps(body))


if __name__ == '__main__':
    main()
