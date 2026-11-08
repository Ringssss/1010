"""Write only exact coordinated STOP markers for this frozen nine-process plan."""
import argparse
import hashlib
from pathlib import Path
from common import native, ops, write, require, ref, fresh_output

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    require(a.execute, 'main-agent STOP requires --execute')
    out = fresh_output(a.out)
    plan = native.read(a.plan)
    require(plan['session'].startswith('local_slope_runtime_') and len(plan['processes']) == 9, 'exact own nine-process plan required')
    result = ops.stops(plan, hashlib.sha256(a.plan.read_bytes()).hexdigest(), out)
    write(out / 'summary.json', dict(plan_ref=ref(a.plan), markers=result, signals_sent=0, old_owners_touched=False))
    if any(row.get('returncode') != 0 for row in result.values()):
        raise SystemExit(2)

if __name__ == '__main__':
    main()
