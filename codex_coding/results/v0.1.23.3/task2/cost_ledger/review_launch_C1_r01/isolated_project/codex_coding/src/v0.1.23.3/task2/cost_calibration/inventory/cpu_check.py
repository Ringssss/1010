"""Check identity parity and dynamic freshness with a fake HIP function and fake sysfs."""
import argparse
import ast
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from identity_diagnostics import LibraryCache, Segments, runtime_identity
import identity_diagnostics as diagnostics

ROOT = Path(__file__).resolve().parents[6]
LEGACY = ROOT / 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/runtime_worker.py'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    tree = ast.parse(LEGACY.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'runtime_identity')
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)
    with tempfile.TemporaryDirectory(prefix='inventory_cpu_fixture_') as temporary:
        fixture = Path(temporary)
        def source_path(value):
            value = str(value)
            return fixture / value.lstrip('/') if value.startswith(('/sys/', '/proc/')) else Path(value)
        def put(location, text):
            path = source_path(location)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        put('/sys/bus/pci/devices/0000:09:00.0/numa_node', '4\n')
        put('/proc/self/stat', '123 (cpu fixture) ' + ' '.join(['S'] + ['0'] * 18 + ['98765']) + '\n')
        put('/proc/self/cgroup', '0::/nested\n')
        put('/proc/sys/kernel/random/boot_id', 'fixture-boot\n')
        put('/proc/self/statm', '100 10 0 0 0 0 0\n')
        put('/sys/fs/cgroup/nested/memory.current', '1000\n')
        put('/sys/fs/cgroup/nested/memory.max', '8192\n')
        namespace = '/proc/self/ns/cgroup'
        class Hip:
            calls = 0
            def hipDeviceGetPCIBusId(self, buffer, size, gpu):
                self.calls += 1
                buffer.value = b'0000:09:00.0'
                return 0
        hip = Hip()
        globals_ = dict(ctypes=ctypes, Path=source_path, os=os)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(LEGACY), 'exec'), globals_)
        real_readlink = os.readlink
        def readlink(location):
            return 'cgroup:[fake]' if str(location) == namespace else real_readlink(location)
        with patch.object(diagnostics, 'Path', source_path), \
                patch('ctypes.util.find_library', return_value='fixture-libamdhip64.so') as resolution, \
                patch('ctypes.CDLL', return_value=hip) as load, patch('os.readlink', side_effect=readlink):
            reference = globals_['runtime_identity'](4)
            cache = LibraryCache()
            first, first_hit = runtime_identity(4, 'legacy', Segments(), cache, 1)
            check('legacy field/value parity with AST-extracted original', reference == first)
            check('legacy path performs resolution and load', not first_hit and resolution.call_count == 2 and load.call_count == 2)
            put('/sys/fs/cgroup/nested/memory.current', '2000\n')
            put('/proc/self/statm', '100 20 0 0 0 0 0\n')
            put('/sys/bus/pci/devices/0000:09:00.0/numa_node', '7\n')
            timings = Segments()
            second, second_hit = runtime_identity(4, 'cached', timings, cache, 2)
            check('cached path skips only resolution and CDLL', second_hit and resolution.call_count == 2 and load.call_count == 2)
            check('cached path still invokes HIP BDF', hip.calls == 3)
            check('identity field set unchanged', set(reference) == set(second))
            check('cgroup current fresh after fixture change', second['cgroup_memory_current_bytes'] == 2000)
            check('RSS fresh after fixture change', second['process_rss_bytes'] == 20 * os.sysconf('SC_PAGE_SIZE'))
            check('PCI NUMA fresh after fixture change', second['pci_numa_node'] == 7)
            check('all identity segment timers present', set(timings.values) == {'library_resolution', 'cdll_load', 'hip_bdf', 'hca_metadata', 'proc_identity', 'cgroup_metadata', 'pci_metadata', 'environment_and_rss'})
            cache.pid = -1
            _, third_hit = runtime_identity(4, 'cached', Segments(), cache, 3)
            check('changed PID invalidates cache', not third_hit and resolution.call_count == 3 and load.call_count == 3)
            check('cache rebuilt inside calling sequence', cache.origin_sequence == 3)
    body = dict(schema_version=1, passed=True, checks=checks, check_count=len(checks),
                scope='CPU fixture only; fake HIP, no GPU calls; actual container evidence pending',
                legacy_source_sha256=hashlib.sha256(LEGACY.read_bytes()).hexdigest(),
                source_shas={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in Path(__file__).parent.glob('*.py')})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(body, stream, indent=2)
        stream.write('\n')
    print(json.dumps(body))


if __name__ == '__main__':
    main()
