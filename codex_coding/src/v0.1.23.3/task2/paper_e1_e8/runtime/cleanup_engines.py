"""Stop only fresh-engine groups explicitly owned by the supplied overlays."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

ROOT=Path(__file__).resolve().parents[6]
RESULT_ROOT=ROOT/'codex_coding/results/v0.1.23.3/task2/paper_e1_e8'


def main(args):
    out=args.out.resolve(); out.mkdir(parents=True,exist_ok=False)
    rows=[]; seen=set()
    for source in args.session:
        state=json.loads(source.read_text())
        for node,path in state['new_engine_processes']:
            spec=Path(path).resolve()
            if str(spec) in seen: continue
            seen.add(str(spec))
            if RESULT_ROOT/'runtime' not in spec.parents or not spec.name.startswith('fresh_engine_'):
                raise ValueError('refusing a process outside this task fresh-engine namespace')
            payload=json.loads(spec.read_text())
            if 'codex_coding/src/serving/sglang_fluid_launch.py' not in payload['command']:
                raise ValueError('not a recorded model engine')
            argv=['docker','exec',state['nodes'][node]['container'],'python3',
                  str(ROOT/'codex_coding/src/task2/cluster/process.py'),'stop','--spec',str(spec)]
            if node!='node27': argv=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',node,shlex.join(argv)]
            result=subprocess.run(argv,capture_output=True,text=True,timeout=55)
            rows.append({'node':node,'spec':str(spec),'command':argv,'returncode':result.returncode,
                         'stdout':result.stdout,'stderr':result.stderr,'time':time.time()})
            (out/'summary.json').write_text(json.dumps({'operations':rows,
                'status':'passed' if all(r['returncode']==0 for r in rows) else 'failed',
                'scope':'only new_engine_processes; CPU owners, keepers and original session untouched'},indent=2))
            if result.returncode: raise RuntimeError(result.stderr)
    print(json.dumps({'status':'passed','engine_groups_stopped':len(rows)}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,action='append',required=True)
    p.add_argument('--out',type=Path,required=True)
    main(p.parse_args())
