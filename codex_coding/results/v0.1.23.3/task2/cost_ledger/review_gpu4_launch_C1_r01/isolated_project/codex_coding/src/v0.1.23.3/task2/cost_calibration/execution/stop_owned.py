"""Write only nine exact current-plan STOP markers using the original helper."""
import argparse
import hashlib
import json
from pathlib import Path
from common import RESULTS, native, ops, write, require, ref

def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--execute',action='store_true')
    a=p.parse_args();require(a.execute,'parent explicit STOP execution required');a.out=a.out.resolve();a.plan=a.plan.resolve()
    require(a.out.parent==RESULTS and a.out.name.startswith('execution_stop_'),'new owned STOP output required')
    plan=native.read(a.plan);require(plan['session'].startswith('costcal_') and len(plan['processes'])==9,'exact new costcal nine-process plan required')
    a.out.mkdir(parents=True,exist_ok=False)
    digest=hashlib.sha256(a.plan.read_bytes()).hexdigest()
    result=ops.stops(plan,digest,a.out)
    write(a.out/'summary.json',dict(plan_ref=ref(a.plan),session=plan['session'],markers=result,signals_sent=0,old_owners_touched=False))
    print(json.dumps(result))
    if any(row.get('returncode')!=0 for row in result.values()):raise SystemExit(2)
if __name__=='__main__':main()
