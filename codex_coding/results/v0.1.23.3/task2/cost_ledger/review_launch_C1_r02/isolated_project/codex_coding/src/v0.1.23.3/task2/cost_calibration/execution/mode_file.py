"""Create or atomically update only a new task-owned inventory mode file."""
import argparse
import json
from pathlib import Path
import os
import time

def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=('create','set'));p.add_argument('--path',type=Path,required=True);p.add_argument('--mode',choices=('legacy','cached'),required=True)
    a=p.parse_args();path=a.path.resolve()
    if path.parent.name!='cost_calibration' or not path.name.startswith('costcal_') or not path.name.endswith('.inventory_mode.json'):
        raise ValueError('new task-owned mode path required')
    before=None
    if a.operation=='set':
        before=json.loads(path.read_text())
        if set(before)!={'mode'} or before['mode'] not in ('legacy','cached'):raise ValueError('unexpected existing mode file')
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps({'mode':a.mode})+'\n'
    if a.operation=='create':
        with path.open('x') as stream:stream.write(raw)
    else:
        temp=path.with_name(path.name+f'.{os.getpid()}.{time.monotonic_ns()}.tmp')
        with temp.open('x') as stream:stream.write(raw)
        temp.replace(path)
    print(json.dumps({'operation':a.operation,'path':str(path),'before':before,'after':{'mode':a.mode},'pid':os.getpid()}))
if __name__=='__main__':main()
