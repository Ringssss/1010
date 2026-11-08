"""Issue only the nine exact STOP markers for the frozen bounded launch plan."""
import argparse
import hashlib
import json
from pathlib import Path
from common import native, ops, write, require, ref, fresh_output

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    require(a.execute, 'main-agent explicit STOP requires --execute')
    out = fresh_output(a.out)
    plan = native.read(a.plan)
    require(plan['session'].startswith('bounded_cost_runtime_') and len(plan['processes']) == 9, 'exact bounded nine-process plan required')
    result = ops.stops(plan, hashlib.sha256(a.plan.read_bytes()).hexdigest(), out)
    write(out / 'summary.json', dict(plan_ref=ref(a.plan), session=plan['session'], markers=result, signals_sent=0, old_owners_touched=False))
    print(json.dumps(result))
    if any(row.get('returncode') != 0 for row in result.values()):
        raise SystemExit(2)

if __name__ == '__main__':
    main()
