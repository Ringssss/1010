"""CPU-only library-resolution probe; no torch import, CDLL load, or HIP invocation."""
import argparse
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import platform
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--iterations', type=int, default=2, choices=(1, 2))
    args = parser.parse_args()
    rows = []
    for sequence in range(args.iterations):
        start = time.monotonic_ns()
        found = ctypes.util.find_library('amdhip64')
        end = time.monotonic_ns()
        rows.append(dict(sequence=sequence+1, start_ns=start, end_ns=end,
                         library_resolution_ns=end-start, found_library=found,
                         effective_library=found or 'libamdhip64.so'))
    body = dict(schema_version=1, scope='CPU-only primitive; not actual worker inventory or HIP qualification',
                pid=os.getpid(), hostname=platform.node(), python=platform.python_version(),
                ctypes_util_source=ctypes.util.__file__,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), rows=rows,
                gpu_calls=0, cdll_loads=0)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        json.dump(body, stream, indent=2)
        stream.write('\n')
    print(json.dumps(body))


if __name__ == '__main__':
    main()
