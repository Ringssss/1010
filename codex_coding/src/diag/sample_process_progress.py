"""Sample loader CPU/fault/I/O counters without attaching to GPU streams."""
import argparse
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    directory = Path('/proc')/str(args.pid)
    with args.out.open('x') as stream:
        for sample in range(args.samples):
            fields = (directory/'stat').read_text().split(') ', 1)[1].split()
            io = {k: int(v) for line in (directory/'io').read_text().splitlines()
                  for k, v in [line.split(':')]}
            row = {'time': time.time(), 'pid': args.pid, 'state': fields[0],
                   'minor_faults': int(fields[7]), 'major_faults': int(fields[9]),
                   'user_ticks': int(fields[11]), 'system_ticks': int(fields[12]),
                   'rss_pages': int(fields[21]), 'io': io}
            stream.write(json.dumps(row)+'\n'); stream.flush()
            print(json.dumps(row), flush=True)
            if sample+1 < args.samples:
                time.sleep(args.interval)


if __name__ == '__main__':
    main()
